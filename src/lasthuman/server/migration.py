"""Explicit, offline, journaled import of a same-context legacy Store."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from urllib.parse import quote

from lasthuman.models import Question
from .config import GatewaySettings
from .registration import RegistrationError, RepositoryContext, RepositoryInstallation
from .registry import (
    InstallationDiscovery, LegacyActivation, RepositoryRegistry, legacy_activation_from_rows,
    preflight_legacy_import, read_legacy_row, recover_legacy_registry, restore_legacy_activation,
)
from .runtime_lock import (
    RUNTIME_LOCK_NAME, DataDirectoryLock, StatePathError, pending_import_journals,
    prepare_private_directory, reject_path_aliases, validate_state_path,
)
from .snapshot import Snapshot, SnapshotError

_SNAPSHOT_COLUMNS = (
    "snapshot_id", "pr", "repo", "repo_id", "head_sha", "base_sha", "author_id", "author_login", "title",
    "state", "question_version", "question_count", "snapshot_json", "questions_json", "created_at", "updated_at",
)
_RECEIPT_COLUMNS = (
    "receipt_id", "snapshot_id", "pr", "repo", "repo_id", "head_sha", "base_sha", "policy_version",
    "question_version", "actor_id", "actor_login", "app_id", "installation_id", "created_at", "verified_at",
    "successful_answers_json",
)
_OUTBOX_COLUMNS = (
    "event_id", "kind", "pr", "snapshot_id", "receipt_id", "payload_json", "status", "attempts", "due_at",
    "created_at", "updated_at", "last_error_code", "last_error", "remote_json",
)
_EXPECTED_COLUMNS = {
    "snapshots": (_SNAPSHOT_COLUMNS,),
    "pr_snapshots": (("pr", "snapshot_id", "updated_at"),),
    "receipts": (_RECEIPT_COLUMNS, (*_RECEIPT_COLUMNS, "tenant_generation")),
    "outbox": (_OUTBOX_COLUMNS, (*_OUTBOX_COLUMNS, "tenant_generation")),
    "snapshot_operations": (("snapshot_id", "preparation_error_code", "preparation_error", "updated_at"),),
    "presentation_check_runs": ((
        "pr", "snapshot_id", "head_sha", "external_id", "check_run_id", "status", "conclusion", "updated_at",
    ),),
    "merges": (("pr", "snapshot_id", "merged_at", "merge_commit_sha", "head_sha", "measured", "updated_at"),),
}
_STORE_TABLES = tuple(_EXPECTED_COLUMNS)
_PRESENTATION_KINDS = {
    "pending_status", "neutral_status", "success_status", "start_comment", "success_comment",
    "presentation_card", "presentation_check",
}
_OUTBOX_KINDS = _PRESENTATION_KINDS | {"verifier_dispatch", "presentation_check_cancel", "closed_projection"}


class MigrationError(RuntimeError):
    """Source, identity or crash-recovery checks failed without guessing ownership."""


@dataclass(frozen=True)
class LegacyImportResult:
    dry_run: bool
    source_db: Path
    destination_db: Path
    source_sha256: str
    repository_id: int
    repository: str
    owner_id: int
    installation_id: int
    generation: int
    snapshots: int
    receipts: int
    outbox_events: int
    rebound_outbox_events: int
    retired_outbox_events: int

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result.pop("dry_run")
        result.update(
            state="dry-run" if self.dry_run else "imported",
            source_db=str(self.source_db), destination_db=str(self.destination_db),
        )
        return result


@dataclass(frozen=True)
class _ExpectedIdentity:
    repository_id: int
    repository: str
    owner_id: int
    app_id: int


@dataclass(frozen=True)
class _SourceSummary:
    snapshots: int
    receipts: int
    outbox_events: int
    installation_ids: frozenset[int]


@dataclass(frozen=True)
class _OutboxChange:
    event_id: str
    payload_json: str
    retire: bool
    rebind: bool


@dataclass(frozen=True)
class _FileStamp:
    device: int
    inode: int
    size: int
    sha256: str
    mode: int


@dataclass(frozen=True)
class _ImportJournal:
    source_db: str
    source_sha256: str
    registry_db: str
    registry_device: int
    registry_inode: int
    base_url: str
    app_id: int
    activation: LegacyActivation
    staged_name: str
    staged_stamp: _FileStamp
    previous_stamp: _FileStamp | None
    rebound: int
    retired: int
    phase: str = "prepared"
    version: int = 1


def import_legacy_database(
    settings: GatewaySettings, *, source_db: str | Path, repository_id: int, repository: str, owner_id: int,
    discovery: InstallationDiscovery, dry_run: bool = False,
) -> LegacyImportResult:
    expected = _ExpectedIdentity(
        _positive_int(repository_id, "repository_id"), _repository_name(repository),
        _positive_int(owner_id, "owner_id"), _positive_int(settings.app_id, "app_id"),
    )
    source = _source_path(source_db)
    destination = settings.state_root / "repos" / str(repository_id) / "lasthuman.sqlite"
    _validate_import_paths(settings, source, destination, repository_id)
    before_hash = _file_sha256(source)
    with DataDirectoryLock(settings.state_root, allow_pending_import=True):
        summary = _validate_source_database(source, expected)
        recovered = _recover_pending_import(settings, source, expected, dry_run=dry_run)
        if recovered is not None:
            return _import_result(False, source, destination, before_hash, summary, recovered)
        installation = discovery.discover(repository, repository_id, owner_id, verify_opt_in=True)
        _require_installation_matches_request(expected, installation)
        if summary.installation_ids and summary.installation_ids != {installation.installation_id}:
            raise MigrationError("legacy source receipt installation does not match verified GitHub installation")
        try:
            context = preflight_legacy_import(settings, installation)
        except (RegistrationError, sqlite3.Error) as error:
            raise MigrationError(str(error)) from error
        _reject_nonempty_destination(destination)
        with _readonly_connection(source) as connection:
            changes = _plan_legacy_outbox(connection, settings=settings, context=context)
        if _file_sha256(source) != before_hash:
            raise MigrationError("legacy source database changed during validation")
        if dry_run:
            return LegacyImportResult(
                True, source, destination, before_hash, repository_id, installation.repository,
                owner_id, installation.installation_id, context.generation, summary.snapshots, summary.receipts,
                summary.outbox_events, sum(item.rebind for item in changes), sum(item.retire for item in changes),
            )
        try:
            journal = _commit_import(
                settings, source, destination, expected, installation, discovery, before_hash, changes,
            )
        except (OSError, sqlite3.Error, RegistrationError) as error:
            raise MigrationError("legacy import could not be committed") from error
        return _import_result(False, source, destination, before_hash, summary, journal)


def _import_result(
    dry_run: bool, source: Path, destination: Path, source_hash: str, summary: _SourceSummary, journal: _ImportJournal,
) -> LegacyImportResult:
    context = journal.activation.context
    return LegacyImportResult(
        dry_run, source, destination, source_hash, context.repository_id, context.repository,
        context.owner_id, context.installation_id, context.generation,
        summary.snapshots, summary.receipts, summary.outbox_events, journal.rebound, journal.retired,
    )


def _commit_import(
    settings: GatewaySettings, source: Path, destination: Path, expected: _ExpectedIdentity,
    installation: RepositoryInstallation, discovery: InstallationDiscovery, source_hash: str,
    changes: tuple[_OutboxChange, ...],
) -> _ImportJournal:
    for directory in (settings.state_root / "repos", destination.parent):
        prepare_private_directory(directory)
        _fsync_parent(directory)
    staged = _backup_source_to_temp(source, destination.parent, expected.repository_id)
    journal_path = _journal_path(settings, expected.repository_id)
    registry: RepositoryRegistry | None = None
    try:
        _validate_source_database(staged, expected)
        registry = RepositoryRegistry(settings, discovery, allow_pending_import=True)
        with registry.legacy_activation(installation) as activation:
            with _readonly_connection(staged) as connection:
                if _plan_legacy_outbox(connection, settings=settings, context=activation.context) != changes:
                    raise MigrationError("legacy source or registry changed during import")
            _apply_legacy_outbox(staged, changes, activation.context)
            _validate_source_database(staged, expected)
            os.chmod(staged, 0o600)
            _fsync_file(staged)
            _reject_nonempty_destination(destination)
            if destination.exists():
                _fsync_file(destination)
            journal = _ImportJournal(
                source_db=str(source), source_sha256=source_hash, registry_db=str(settings.database.absolute()),
                registry_device=settings.database.stat().st_dev, registry_inode=settings.database.stat().st_ino,
                base_url=settings.base_url, app_id=settings.app_id, activation=activation,
                staged_name=staged.name, staged_stamp=_file_stamp(staged),
                previous_stamp=_file_stamp(destination) if destination.exists() else None,
                rebound=sum(item.rebind for item in changes), retired=sum(item.retire for item in changes),
            )
            _write_journal(journal_path, journal)
            if journal.previous_stamp is not None:
                os.replace(destination, _backup_path(destination, journal))
            os.replace(staged, destination)
            _fsync_parent(destination)
            if _file_sha256(source) != source_hash:
                raise MigrationError("legacy source database changed during import")
        # Until this durable marker exists, even an ambiguous registry commit rolls back.
        journal = replace(journal, phase="committed")
        _write_journal(journal_path, journal)
    except (OSError, sqlite3.Error, RegistrationError, MigrationError) as error:
        if journal_path.exists():
            saved = _load_journal(journal_path)
            if saved.phase == "prepared":
                try:
                    _rollback_import(settings, destination, saved)
                except (OSError, sqlite3.Error, RegistrationError, MigrationError) as recovery_error:
                    raise MigrationError(
                        "legacy import recovery is pending; keep the gateway stopped and retry import-legacy",
                    ) from recovery_error
        raise MigrationError(
            "legacy import could not be committed; retry import-legacy if a journal remains",
        ) from error
    finally:
        if registry is not None:
            registry.shutdown()
        if not journal_path.exists():
            _unlink_known_temp(staged)
    try:
        _finish_committed_import(settings, destination, journal)
    except (OSError, sqlite3.Error, RegistrationError, MigrationError) as error:
        raise MigrationError(
            "legacy import committed; cleanup is pending, retry import-legacy before startup",
        ) from error
    return journal


def _journal_path(settings: GatewaySettings, repository_id: int) -> Path:
    return settings.state_root / f".lasthuman-import-{repository_id}.json"


def _backup_path(destination: Path, journal: _ImportJournal) -> Path:
    return destination.parent / f"{journal.staged_name}.previous"


def _file_stamp(path: Path) -> _FileStamp:
    validate_state_path(path)
    before = path.lstat()
    digest = _file_sha256(path)
    after = path.lstat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
    ):
        raise MigrationError("legacy import file changed during inspection")
    return _FileStamp(before.st_dev, before.st_ino, before.st_size, digest, stat.S_IMODE(before.st_mode))


def _write_journal(path: Path, journal: _ImportJournal) -> None:
    validate_state_path(path)
    staged = path.parent / f".legacy-journal-{secrets.token_hex(12)}.tmp"
    descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(asdict(journal), handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, path)
        _fsync_parent(path)
    finally:
        staged.unlink(missing_ok=True)


def _stamp_from_payload(payload: object) -> _FileStamp:
    if not isinstance(payload, dict) or set(payload) != {"device", "inode", "size", "sha256", "mode"}:
        raise MigrationError("legacy import journal file identity is invalid")
    for name in ("device", "inode", "size", "mode"):
        value = payload[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise MigrationError("legacy import journal file identity is invalid")
    if (
        not isinstance(payload["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", payload["sha256"]) is None
        or payload["mode"] > 0o777
    ):
        raise MigrationError("legacy import journal file identity is invalid")
    return _FileStamp(payload["device"], payload["inode"], payload["size"], payload["sha256"], payload["mode"])


def _load_journal(path: Path) -> _ImportJournal:
    validate_state_path(path)
    if path.stat().st_size > 65536 or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise MigrationError("legacy import journal must be bounded and private")
    payload = _json_object(path.read_text(encoding="utf-8"), "legacy import journal")
    if set(payload) != {
        "source_db", "source_sha256", "registry_db", "registry_device", "registry_inode", "base_url", "app_id",
        "activation", "staged_name", "staged_stamp", "previous_stamp", "rebound", "retired", "phase", "version",
    } or payload["version"] != 1 or isinstance(payload["version"], bool):
        raise MigrationError("legacy import journal format is invalid")
    for name in ("source_db", "source_sha256", "registry_db", "base_url", "staged_name", "phase"):
        if not isinstance(payload[name], str) or not payload[name]:
            raise MigrationError("legacy import journal metadata is invalid")
    raw = payload["activation"]
    if not isinstance(raw, dict) or set(raw) != {"context", "inserted", "previous_row", "activated_row"}:
        raise MigrationError("legacy import journal activation is invalid")
    activation = legacy_activation_from_rows(raw["previous_row"], raw["activated_row"])
    if not all((
        raw["context"] == asdict(activation.context), raw["inserted"] is activation.inserted,
        payload["phase"] in {"prepared", "committed"},
        re.fullmatch(r"[0-9a-f]{64}", payload["source_sha256"]) is not None,
        re.fullmatch(
            rf"\.legacy-import-{activation.context.repository_id}-[a-z0-9_]+\.sqlite\.tmp", payload["staged_name"],
        ) is not None,
        path.name == f".lasthuman-import-{activation.context.repository_id}.json",
    )):
        raise MigrationError("legacy import journal binding is invalid")
    for name in ("rebound", "retired", "registry_device", "registry_inode"):
        count = payload[name]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise MigrationError("legacy import journal counts are invalid")
    return _ImportJournal(
        source_db=payload["source_db"], source_sha256=payload["source_sha256"], registry_db=payload["registry_db"],
        registry_device=payload["registry_device"], registry_inode=payload["registry_inode"],
        base_url=payload["base_url"], app_id=_positive_int(payload["app_id"], "app_id"), activation=activation,
        staged_name=payload["staged_name"], staged_stamp=_stamp_from_payload(payload["staged_stamp"]),
        previous_stamp=None if payload["previous_stamp"] is None else _stamp_from_payload(payload["previous_stamp"]),
        rebound=payload["rebound"], retired=payload["retired"], phase=payload["phase"],
    )


def _validate_journal_files(
    settings: GatewaySettings, destination: Path, journal: _ImportJournal, *, recover_registry: bool = False,
) -> _FileStamp | None:
    settings.validate_runtime_paths(journal.activation.context.repository_id, allow_pending_import=True)
    details = settings.database.stat()
    if (details.st_dev, details.st_ino) != (journal.registry_device, journal.registry_inode):
        raise MigrationError("legacy import recovery found a replaced registry database; refusing changes")
    staged = destination.parent / journal.staged_name
    backup = _backup_path(destination, journal)
    for path, expected in ((staged, journal.staged_stamp), (backup, journal.previous_stamp)):
        validate_state_path(path)
        if path.exists() and (expected is None or _file_stamp(path) != expected):
            raise MigrationError("legacy import recovery found an unknown staging or backup file; refusing changes")
    current = _file_stamp(destination) if destination.exists() else None
    if current is not None and current not in (journal.staged_stamp, journal.previous_stamp):
        raise MigrationError("legacy import recovery found an unknown destination; refusing changes")
    if recover_registry:
        recover_legacy_registry(settings, journal.activation)
    if read_legacy_row(settings, journal.activation.context.repository_id) not in (
        journal.activation.previous_row, journal.activation.activated_row,
    ):
        raise MigrationError("legacy import recovery found changed registry metadata; refusing changes")
    return current


def _remove_journal(settings: GatewaySettings, journal: _ImportJournal) -> None:
    path = _journal_path(settings, journal.activation.context.repository_id)
    path.unlink()
    _fsync_parent(path)


def _rollback_import(settings: GatewaySettings, destination: Path, journal: _ImportJournal) -> None:
    current = _validate_journal_files(settings, destination, journal)
    backup = _backup_path(destination, journal)
    if journal.previous_stamp is not None and current != journal.previous_stamp and not backup.exists():
        raise MigrationError("legacy import recovery needs its original empty destination backup; refusing changes")
    restore_legacy_activation(settings, journal.activation)
    if backup.exists():
        os.replace(backup, destination)
    elif journal.previous_stamp is None and current == journal.staged_stamp:
        destination.unlink()
    _unlink_known_temp(destination.parent / journal.staged_name)
    _fsync_parent(destination)
    _remove_journal(settings, journal)


def _finish_committed_import(settings: GatewaySettings, destination: Path, journal: _ImportJournal) -> None:
    current = _validate_journal_files(settings, destination, journal)
    if current != journal.staged_stamp or read_legacy_row(
        settings, journal.activation.context.repository_id,
    ) != journal.activation.activated_row:
        raise MigrationError("committed legacy import no longer matches its journal; refusing changes")
    _backup_path(destination, journal).unlink(missing_ok=True)
    _unlink_known_temp(destination.parent / journal.staged_name)
    _fsync_parent(destination)
    _remove_journal(settings, journal)


def _recover_pending_import(
    settings: GatewaySettings, source: Path, expected: _ExpectedIdentity, *, dry_run: bool,
) -> _ImportJournal | None:
    journals = pending_import_journals(settings.state_root)
    if not journals:
        return None
    if dry_run:
        raise MigrationError("pending legacy import; retry the same import-legacy command without --dry-run to recover")
    if journals != (_journal_path(settings, expected.repository_id),):
        raise MigrationError("a different legacy import is pending; retry that import before starting another")
    try:
        journal = _load_journal(journals[0])
        context = journal.activation.context
        if (
            journal.source_db, journal.source_sha256, journal.registry_db, journal.base_url, journal.app_id,
            context.repository_id, context.repository.casefold(), context.owner_id,
        ) != (
            str(source), _file_sha256(source), str(settings.database.absolute()), settings.base_url, expected.app_id,
            expected.repository_id, expected.repository.casefold(), expected.owner_id,
        ):
            raise MigrationError("pending legacy import must be recovered using its original source and settings")
        destination = settings.state_root / "repos" / str(expected.repository_id) / "lasthuman.sqlite"
        _validate_journal_files(settings, destination, journal, recover_registry=True)
        if journal.phase == "committed":
            _finish_committed_import(settings, destination, journal)
            return journal
        _rollback_import(settings, destination, journal)
    except (OSError, sqlite3.Error, RegistrationError) as error:
        raise MigrationError(
            "legacy import recovery refused; keep the gateway stopped and preserve its journal",
        ) from error
    return None


def _validate_source_database(path: Path, expected: _ExpectedIdentity) -> _SourceSummary:
    with _readonly_connection(path) as connection:
        _require_store_schema(connection)
        result = connection.execute("PRAGMA quick_check").fetchone()
        if result is None or str(result[0]).lower() != "ok":
            raise MigrationError("legacy source database integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise MigrationError("legacy source database foreign key check failed")
        snapshots = _validate_snapshots(connection, expected)
        receipts, installation_ids = _validate_receipts(connection, expected)
        outbox = _validate_outbox(connection)
        _validate_references(connection)
    if snapshots == 0 and receipts == 0:
        raise MigrationError("legacy source database is empty and cannot prove repository identity")
    return _SourceSummary(snapshots, receipts, outbox, frozenset(installation_ids))


def _require_store_schema(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
    ).fetchall()
    if tuple(row["name"] for row in rows) != tuple(sorted(_STORE_TABLES)):
        raise MigrationError("legacy source database schema is not a known Store schema")
    for table in _STORE_TABLES:
        columns = tuple(row["name"] for row in connection.execute(f"PRAGMA table_info({table})"))
        if columns not in _EXPECTED_COLUMNS[table]:
            raise MigrationError("legacy source database schema is not a known Store schema")
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger', 'view')").fetchone() is not None:
        raise MigrationError("legacy source contains unsupported executable schema objects")


def _validate_snapshots(connection: sqlite3.Connection, expected: _ExpectedIdentity) -> int:
    rows = connection.execute("SELECT * FROM snapshots ORDER BY snapshot_id").fetchall()
    for row in rows:
        snapshot = _snapshot_from_row(row)
        if row["repo"] != expected.repository or row["repo_id"] != expected.repository_id:
            raise MigrationError("legacy source contains a different repository")
        if snapshot.repo != expected.repository or snapshot.repo_id != expected.repository_id:
            raise MigrationError("legacy source snapshot JSON contains a different repository")
        if row["snapshot_id"] != snapshot.snapshot_id:
            raise MigrationError("legacy source snapshot id does not match snapshot JSON")
        for key, value in {
            "pr": snapshot.pr, "repo": snapshot.repo, "repo_id": snapshot.repo_id,
            "head_sha": snapshot.head_sha, "base_sha": snapshot.base_sha,
            "author_id": snapshot.author_id, "author_login": snapshot.author_login, "title": snapshot.title,
        }.items():
            if row[key] != value:
                raise MigrationError("legacy source snapshot row does not match snapshot JSON")
        if row["state"] not in {"pending", "neutral"}:
            raise MigrationError("legacy source snapshot state is invalid")
        questions = _questions_from_json(row["questions_json"])
        if row["question_count"] != len(questions):
            raise MigrationError("legacy source question count is invalid")
        if row["question_version"] != hashlib.sha256(_json_dumps(_questions_payload(questions)).encode()).hexdigest():
            raise MigrationError("legacy source question version is invalid")
    return len(rows)


def _validate_receipts(connection: sqlite3.Connection, expected: _ExpectedIdentity) -> tuple[int, set[int]]:
    rows = connection.execute("SELECT * FROM receipts ORDER BY receipt_id").fetchall()
    installation_ids: set[int] = set()
    for row in rows:
        if row["repo"] != expected.repository or row["repo_id"] != expected.repository_id:
            raise MigrationError("legacy source contains a different receipt repository")
        if row["app_id"] != expected.app_id:
            raise MigrationError("legacy source receipt was issued by a different GitHub App")
        installation_ids.add(_positive_int(row["installation_id"], "installation_id"))
        snapshot_row = connection.execute(
            "SELECT * FROM snapshots WHERE snapshot_id = ?", (row["snapshot_id"],),
        ).fetchone()
        if snapshot_row is None:
            raise MigrationError("legacy source receipt references a missing snapshot")
        snapshot = _snapshot_from_row(snapshot_row)
        if tuple(row[key] for key in (
            "pr", "repo", "repo_id", "head_sha", "base_sha", "policy_version", "question_version", "actor_id",
        )) != (
            snapshot.pr, snapshot.repo, snapshot.repo_id, snapshot.head_sha, snapshot.base_sha,
            snapshot.policy_version, snapshot_row["question_version"], snapshot.author_id,
        ):
            raise MigrationError("legacy source receipt binding does not match its snapshot")
        answers = _json_list(row["successful_answers_json"], "legacy source receipt answers JSON")
        for answer in answers:
            if not isinstance(answer, dict) or any(
                not isinstance(answer.get(key), str) for key in ("question_id", "anchor", "text")
            ):
                raise MigrationError("legacy source receipt answers JSON is invalid")
    return len(rows), installation_ids


def _validate_outbox(connection: sqlite3.Connection) -> int:
    rows = connection.execute("SELECT * FROM outbox ORDER BY event_id").fetchall()
    for row in rows:
        if row["status"] not in {"pending", "sent"} or row["kind"] not in _OUTBOX_KINDS:
            raise MigrationError("legacy source outbox state or kind is invalid")
        if not isinstance(row["attempts"], int) or row["attempts"] < 0:
            raise MigrationError("legacy source outbox attempts are invalid")
        payload = _json_object(row["payload_json"], "legacy source outbox payload")
        if row["remote_json"] is not None:
            _json_object(row["remote_json"], "legacy source outbox remote")
        if row["snapshot_id"] is None and row["receipt_id"] is None and row["kind"] not in {
            "closed_projection", "pending_status", "neutral_status",
        }:
            raise MigrationError("legacy source outbox event is missing a binding")
        if payload.get("target_url") is not None and not isinstance(payload["target_url"], str):
            raise MigrationError("legacy source outbox target URL is invalid")
        if "tenant_generation" in row.keys() and row["tenant_generation"] is not None:
            _positive_int(row["tenant_generation"], "tenant_generation")
    return len(rows)


def _validate_references(connection: sqlite3.Connection) -> None:
    for table in ("pr_snapshots", "presentation_check_runs", "merges", "outbox", "snapshot_operations"):
        for row in connection.execute(f"SELECT * FROM {table} WHERE snapshot_id IS NOT NULL"):
            snapshot = connection.execute(
                "SELECT pr FROM snapshots WHERE snapshot_id = ?", (row["snapshot_id"],),
            ).fetchone()
            if snapshot is None or "pr" in row.keys() and row["pr"] != snapshot["pr"]:
                raise MigrationError("legacy source contains an invalid snapshot reference")
    for row in connection.execute("SELECT * FROM outbox WHERE receipt_id IS NOT NULL"):
        receipt = connection.execute(
            "SELECT pr, snapshot_id FROM receipts WHERE receipt_id = ?", (row["receipt_id"],),
        ).fetchone()
        if receipt is None or row["pr"] != receipt["pr"] or row["snapshot_id"] != receipt["snapshot_id"]:
            raise MigrationError("legacy source outbox contains an invalid receipt reference")


def _plan_legacy_outbox(
    connection: sqlite3.Connection, *, settings: GatewaySettings, context: RepositoryContext,
) -> tuple[_OutboxChange, ...]:
    _validate_receipt_generations(connection, context)
    old_origin = settings.base_url.rstrip("/")
    new_origin = f"{old_origin}/repos/{context.repository_id}"
    has_generation = _has_column(connection, "outbox", "tenant_generation")
    proven_installation = connection.execute(
        "SELECT 1 FROM receipts WHERE app_id = ? AND installation_id = ? LIMIT 1",
        (settings.app_id, context.installation_id),
    ).fetchone() is not None
    changes: list[_OutboxChange] = []
    for row in connection.execute("SELECT * FROM outbox ORDER BY event_id"):
        generation = row["tenant_generation"] if has_generation else None
        if generation is not None and generation != context.generation:
            raise MigrationError("legacy source outbox generation does not match this repository")
        payload = _json_object(row["payload_json"], "legacy source outbox payload")
        updated, retire = _rebased_payload(
            payload, old_origin=old_origin, new_origin=new_origin, status=row["status"], kind=row["kind"],
        )
        # Snapshot-only legacy stores cannot prove an old App/installation identity.
        # Preserve their evidence, but require a fresh sync instead of reviving writes.
        retire = retire or (not proven_installation and row["status"] == "pending")
        if updated != payload or generation is None or retire:
            changes.append(_OutboxChange(
                row["event_id"], _json_dumps(updated), retire, generation is None and proven_installation,
            ))
    return tuple(changes)


def _validate_receipt_generations(connection: sqlite3.Connection, context: RepositoryContext) -> None:
    if _has_column(connection, "receipts", "tenant_generation"):
        for row in connection.execute("SELECT tenant_generation FROM receipts"):
            generation = row["tenant_generation"]
            if (
                not isinstance(generation, int)
                or generation not in (0, context.generation)
            ):
                raise MigrationError("legacy source receipt generation does not match this repository")
    if connection.execute(
        """SELECT 1 FROM receipts
           GROUP BY snapshot_id, question_version, actor_id, app_id, installation_id
           HAVING COUNT(*) > 1 LIMIT 1""",
    ).fetchone() is not None:
        raise MigrationError("legacy source receipt generations collide after binding")


def _apply_legacy_outbox(path: Path, changes: tuple[_OutboxChange, ...], context: RepositoryContext) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            _validate_receipt_generations(connection, context)
            if not _has_column(connection, "receipts", "tenant_generation"):
                connection.execute("ALTER TABLE receipts ADD COLUMN tenant_generation INTEGER NOT NULL DEFAULT 0")
            # Only this offline, fully identity-validated import may bind legacy provenance.
            connection.execute(
                "UPDATE receipts SET tenant_generation = ? WHERE tenant_generation = 0",
                (context.generation,),
            )
            if not _has_column(connection, "outbox", "tenant_generation"):
                connection.execute("ALTER TABLE outbox ADD COLUMN tenant_generation INTEGER")
            for change in changes:
                connection.execute(
                    """UPDATE outbox SET payload_json = ?,
                       tenant_generation = CASE WHEN ? THEN ? ELSE tenant_generation END,
                       status = CASE WHEN ? THEN 'sent' ELSE status END,
                       last_error_code = CASE WHEN ? THEN 'legacy_import_unscoped_url' ELSE last_error_code END,
                       last_error = CASE WHEN ? THEN 'Legacy pending publication needs an explicit resync.'
                                   ELSE last_error END,
                       remote_json = CASE WHEN ? THEN ? ELSE remote_json END WHERE event_id = ?""",
                    (
                        change.payload_json, change.rebind, context.generation, change.retire, change.retire,
                        change.retire, change.retire,
                        _json_dumps({"skipped": True, "reason": "legacy_import_resync_required"}), change.event_id,
                    ),
                )


def _rebased_payload(
    payload: dict[str, object], *, old_origin: str, new_origin: str, status: str, kind: str,
) -> tuple[dict[str, object], bool]:
    target = payload.get("target_url")
    if target is None:
        return payload, False
    if not isinstance(target, str):
        raise MigrationError("legacy source outbox target URL is invalid")
    if target.startswith(new_origin + "/"):
        return payload, False
    if target.startswith(old_origin + "/prs/") or target.startswith(old_origin + "/receipts/"):
        return {**payload, "target_url": new_origin + target[len(old_origin):]}, False
    return payload, status == "pending" and kind in _PRESENTATION_KINDS


def _require_installation_matches_request(expected: _ExpectedIdentity, installation: RepositoryInstallation) -> None:
    if (
        installation.repository_id != expected.repository_id
        or installation.repository.casefold() != expected.repository.casefold()
        or installation.owner_id != expected.owner_id
    ):
        raise MigrationError("verified GitHub installation does not match requested repository")
    _positive_int(installation.installation_id, "installation_id")


def _reject_nonempty_destination(destination: Path) -> None:
    validate_state_path(destination)
    if not destination.exists():
        return
    with _readonly_connection(destination) as connection:
        _require_store_schema(connection)
        for table in _STORE_TABLES:
            if connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > 0:
                raise MigrationError("destination repository store is not empty")


def _validate_import_paths(settings: GatewaySettings, source: Path, destination: Path, repository_id: int) -> None:
    try:
        paths = [source, settings.database, destination, settings.state_root / RUNTIME_LOCK_NAME]
        for database in (settings.database, destination):
            paths.extend(Path(f"{database}{suffix}") for suffix in ("-wal", "-shm", "-journal"))
        reject_path_aliases(*paths)
        validate_state_path(source)
        settings.validate_runtime_paths(repository_id, allow_pending_import=True)
    except (RegistrationError, OSError) as error:
        raise MigrationError(str(error)) from error


def _backup_source_to_temp(source: Path, destination_dir: Path, repository_id: int) -> Path:
    path = destination_dir / f".legacy-import-{repository_id}-{secrets.token_hex(12)}.sqlite.tmp"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    try:
        with _readonly_connection(source) as source_connection:
            with closing(sqlite3.connect(path)) as destination_connection:
                source_connection.backup(destination_connection)
        return path
    except BaseException:
        _unlink_known_temp(path)
        raise


def _source_path(source_db: str | Path) -> Path:
    path = Path(source_db).absolute()
    try:
        validate_state_path(path)
    except (StatePathError, OSError) as error:
        raise MigrationError(
            "legacy source must be a regular file without linked ancestors or hardlinks",
        ) from error
    if not path.is_file():
        raise MigrationError("legacy source database does not exist or is not a file")
    return path


@contextmanager
def _readonly_connection(path: Path) -> Iterator[sqlite3.Connection]:
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(f"{path}{suffix}").exists():
            raise MigrationError("legacy SQLite database must be stopped and checkpointed before import")
    try:
        connection = sqlite3.connect(_readonly_uri(path), uri=True)
    except sqlite3.Error as error:
        raise MigrationError("legacy SQLite database could not be opened read-only") from error
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    except sqlite3.Error as error:
        raise MigrationError("legacy SQLite database could not be read") from error
    finally:
        connection.close()


def _readonly_uri(path: Path) -> str:
    return f"file:{quote(str(path.absolute()), safe='/:')}?mode=ro&immutable=1"


def _snapshot_from_row(row: sqlite3.Row) -> Snapshot:
    try:
        return Snapshot.from_dict(_json_object(row["snapshot_json"], "legacy source snapshot JSON"))
    except (KeyError, TypeError, ValueError, SnapshotError) as error:
        raise MigrationError("legacy source snapshot JSON is invalid") from error


def _questions_from_json(value: object) -> tuple[Question, ...]:
    questions: list[Question] = []
    for item in _json_list(value, "legacy source questions JSON"):
        if not isinstance(item, Mapping) or not isinstance(item.get("choices", []), list):
            raise MigrationError("legacy source questions JSON is invalid")
        try:
            questions.append(Question(
                type=str(item["type"]), anchor=str(item["anchor"]), text=str(item["text"]),
                expected_evidence=str(item["expected_evidence"]),
                choices=tuple(str(choice) for choice in item.get("choices", [])),
                answer_index=int(item["answer_index"]), evidence_path=str(item.get("evidence_path", "") or ""),
            ))
        except (KeyError, TypeError, ValueError) as error:
            raise MigrationError("legacy source questions JSON is invalid") from error
    return tuple(questions)


def _questions_payload(questions: tuple[Question, ...]) -> list[dict[str, object]]:
    return [{
        "id": str(index), "ordinal": index, "type": question.type, "anchor": question.anchor,
        "text": question.text, "expected_evidence": question.expected_evidence,
        "choices": list(question.choices), "answer_index": question.answer_index,
        "evidence_path": question.evidence_path,
    } for index, question in enumerate(questions)]


def _json_object(value: object, label: str) -> dict[str, object]:
    try:
        data = json.loads(str(value))
    except json.JSONDecodeError as error:
        raise MigrationError(f"{label} is invalid") from error
    if not isinstance(data, dict):
        raise MigrationError(f"{label} is invalid")
    return data


def _json_list(value: object, label: str) -> list[object]:
    try:
        data = json.loads(str(value))
    except json.JSONDecodeError as error:
        raise MigrationError(f"{label} is invalid") from error
    if not isinstance(data, list):
        raise MigrationError(f"{label} is invalid")
    return data


def _has_column(connection: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row["name"] == column for row in connection.execute(f"PRAGMA table_info({table})"))


def _repository_name(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", value) is None:
        raise MigrationError("repository must be an owner/name value")
    return value


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < (1 << 63):
        raise MigrationError(f"{field_name} must be a positive integer")
    return value


def _json_dumps(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_parent(path: Path) -> None:
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _unlink_known_temp(path: Path) -> None:
    if path.name.startswith(".legacy-import-") and path.name.endswith(".sqlite.tmp"):
        path.unlink(missing_ok=True)
