"""Offline import of real fixed-mode settings/stores, including process death."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import requests

from registration_transport import Integration, integration
from lasthuman.models import Question
from lasthuman.server import migration
from lasthuman.server.config import Settings, load_settings
from lasthuman.server.github import GitHubInstallationDiscovery
from lasthuman.server.migration import LegacyImportResult, MigrationError, import_legacy_database
from lasthuman.server.registration import RegistrationOperationalError
from lasthuman.server.registry import RepositoryRegistry, read_legacy_row
from lasthuman.server.runtime_lock import DataDirectoryLock, StatePathError, pending_import_journals
from lasthuman.server.store import PublicationRequest, ReceiptAnswer, Store

__all__ = ["integration"]


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def receipt_rows(path: Path) -> tuple[tuple[object, ...], ...]:
    with sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True) as connection:
        return tuple(connection.execute(
            f"SELECT {', '.join(migration._RECEIPT_COLUMNS)} FROM receipts ORDER BY receipt_id",
        ))


def database_image(path: Path) -> tuple[str, tuple[tuple[object, ...], ...]]:
    with sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True) as connection:
        schema = tuple(connection.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name"))
    return file_hash(path), schema


@dataclass
class Legacy:
    harness: Integration
    fixed: Settings
    source: Path

    @property
    def destination(self) -> Path:
        return self.harness.settings.state_root / "repos/101/lasthuman.sqlite"

    def run(self, *, dry_run: bool = False) -> LegacyImportResult:
        return import_legacy_database(
            self.harness.settings, source_db=self.source, repository_id=self.fixed.repository_id,
            repository=self.fixed.repository, owner_id=self.fixed.owner_id,
            discovery=GitHubInstallationDiscovery(self.harness.settings, requests.Session()), dry_run=dry_run,
        )


@pytest.fixture
def legacy(integration: Integration, monkeypatch: pytest.MonkeyPatch) -> Legacy:
    source = integration.root / "legacy-fixed.sqlite"
    for name, value in {
        "TLH_REGISTRATION_MODE": "fixed", "TLH_REPOSITORY": "acme/one", "TLH_REPOSITORY_ID": "101",
        "TLH_OWNER_ID": "77", "TLH_INSTALLATION_ID": "201", "TLH_DATABASE": str(source),
    }.items():
        monkeypatch.setenv(name, value)
    fixed = load_settings()
    assert isinstance(fixed, Settings)
    assert fixed.tenant_generation is None and fixed.path_prefix == ""
    snapshot = integration.snapshot(101)
    assert snapshot.repo_id == fixed.repository_id and snapshot.repo == fixed.repository
    store = Store(fixed.database)
    anchor = snapshot.risk.top_hunks[0].anchor
    questions = [
        Question(type="consequence", anchor=anchor, text="What is returned after RuntimeError?",
                 expected_evidence="Original token", choices=("Original token", "None"), answer_index=0,
                 evidence_path="app/auth/token.py"),
        Question(type="structure", anchor=anchor, text="Which callee raises RuntimeError?",
                 expected_evidence="app/transport.py", choices=("post_json", "refresh"), answer_index=0,
                 evidence_path="app/transport.py"),
    ]
    record = store.save_snapshot(snapshot, questions, "pending", now="2026-09-16T07:00:00Z")
    receipt = store.save_receipt(
        record, actor_id=snapshot.author_id, actor_login=snapshot.author_login,
        answers=tuple(
            ReceiptAnswer(item.id, item.question.anchor, "Legacy successful evidence") for item in record.questions
        ),
        app_id=fixed.app_id, installation_id=fixed.installation_id, now="2026-09-16T07:05:00Z",
    )
    store.mark_receipt_verified(receipt.receipt_id, publications=(), now="2026-09-16T07:06:00Z")
    store.queue_publication(PublicationRequest(
        event_id="legacy-pending-status", kind="pending_status", pr=1, snapshot_id=snapshot.snapshot_id,
        payload={"description": "Awaiting author explanation", "target_url": fixed.public_base_url + "/prs/1"},
    ), now="2026-09-16T07:07:00Z")
    integration.manager.shutdown()
    integration.http.calls.clear()
    return Legacy(integration, fixed, source)


def test_real_fixed_config_import_preserves_source_receipts_and_rebinds_only_queue(legacy: Legacy) -> None:
    before, receipts = database_image(legacy.source), receipt_rows(legacy.source)
    registry_before = database_image(legacy.harness.settings.database)
    preview = legacy.run(dry_run=True)
    assert preview.to_dict()["state"] == "dry-run"
    assert preview.source_sha256 == before[0] and preview.receipts == len(receipts) == 1
    assert preview.snapshots == 1 and preview.rebound_outbox_events > 0
    assert not legacy.destination.exists()
    assert database_image(legacy.source) == before
    assert database_image(legacy.harness.settings.database) == registry_before
    result = legacy.run()
    assert result.to_dict()["state"] == "imported"
    assert result.generation == 1 and result.installation_id == legacy.fixed.installation_id
    assert result.rebound_outbox_events == preview.rebound_outbox_events
    assert database_image(legacy.source) == before
    assert receipt_rows(legacy.destination) == receipts
    assert legacy.destination.stat().st_mode & 0o777 == 0o600
    assert legacy.destination.parent.stat().st_mode & 0o777 == 0o700
    with sqlite3.connect(legacy.destination) as connection:
        assert connection.execute("SELECT tenant_generation FROM receipts").fetchall() == [(1,)]
        rows = connection.execute("SELECT payload_json, tenant_generation FROM outbox").fetchall()
        assert all(row[1] == 1 for row in rows)
        assert any("/repos/101/prs/1" in row[0] for row in rows)
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    discovery = GitHubInstallationDiscovery(legacy.harness.settings, requests.Session())
    registry = RepositoryRegistry(legacy.harness.settings, discovery)
    try:
        context = registry.resolve(101)
        assert registry.list_registered() == (context,)
        assert context.repository == legacy.fixed.repository
        assert context.installation_id == legacy.fixed.installation_id
    finally:
        registry.shutdown()
    calls = legacy.harness.http.calls
    assert any(call.path.endswith("/contents/.lasthuman.yml") for call in calls)
    assert all(call.method == "GET" or call.path.endswith("/access_tokens") for call in calls)
    assert not legacy.harness.chat.calls


@pytest.mark.parametrize("dimensions", [3, 5])
def test_original_receipt_schemas_without_generation_import_as_current_private_provenance(
    legacy: Legacy, dimensions: int,
) -> None:
    with sqlite3.connect(legacy.source) as connection:
        columns = """receipt_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, pr INTEGER NOT NULL,
            repo TEXT NOT NULL, repo_id INTEGER NOT NULL, head_sha TEXT NOT NULL, base_sha TEXT NOT NULL,
            policy_version TEXT NOT NULL, question_version TEXT NOT NULL, actor_id INTEGER NOT NULL,
            actor_login TEXT NOT NULL, app_id INTEGER NOT NULL, installation_id INTEGER NOT NULL,
            created_at TEXT NOT NULL, verified_at TEXT, successful_answers_json TEXT NOT NULL"""
        keys = ("snapshot_id", "question_version", "actor_id", "app_id", "installation_id")
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            f"CREATE TABLE receipts_legacy ({columns}, UNIQUE({', '.join(keys[:dimensions])}), "
            "FOREIGN KEY(snapshot_id) REFERENCES snapshots(snapshot_id))",
        )
        connection.execute(
            f"INSERT INTO receipts_legacy SELECT {', '.join(migration._RECEIPT_COLUMNS)} FROM receipts",
        )
        connection.execute("DROP TABLE receipts")
        connection.execute("ALTER TABLE receipts_legacy RENAME TO receipts")
        connection.execute("CREATE INDEX idx_receipts_snapshot ON receipts(snapshot_id, created_at ASC)")
        connection.execute("ALTER TABLE outbox DROP COLUMN tenant_generation")
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    before, receipts = database_image(legacy.source), receipt_rows(legacy.source)
    preview = legacy.run(dry_run=True)
    assert preview.generation == 1 and not legacy.destination.exists()
    assert database_image(legacy.source) == before
    result = legacy.run()
    assert result.receipts == 1 and result.rebound_outbox_events == preview.rebound_outbox_events
    assert database_image(legacy.source) == before
    assert receipt_rows(legacy.destination) == receipts
    store = Store(legacy.destination, tenant_generation=result.generation)
    receipt = store.load_receipt(str(receipts[0][0]))
    assert receipt is not None and receipt.verified
    assert store.receipt_is_current(receipt.receipt_id)
    assert store.load_receipt_by_key(receipt.snapshot_id, receipt.question_version, receipt.actor_id) == receipt
    dispatch = store.load_verifier_dispatch(receipt.receipt_id)
    assert dispatch is not None and dispatch.tenant_generation == result.generation
    assert receipt_rows(legacy.destination) == receipts
    with sqlite3.connect(legacy.destination) as connection:
        assert connection.execute("SELECT tenant_generation FROM receipts").fetchall() == [(result.generation,)]
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    later = Store(legacy.destination, tenant_generation=result.generation + 1)
    assert later.load_receipt_by_key(receipt.snapshot_id, receipt.question_version, receipt.actor_id) is None
    assert later.load_receipt(receipt.receipt_id) == receipt


def test_same_target_generation_import_preserves_origin(legacy: Legacy) -> None:
    with sqlite3.connect(legacy.source) as connection:
        connection.execute("UPDATE receipts SET tenant_generation = 1")
        connection.execute("UPDATE outbox SET tenant_generation = 1")
    before, receipts = database_image(legacy.source), receipt_rows(legacy.source)
    assert legacy.run(dry_run=True).generation == 1
    assert legacy.run().generation == 1
    assert database_image(legacy.source) == before
    assert receipt_rows(legacy.destination) == receipts
    assert Store(legacy.destination, tenant_generation=1).receipt_is_current(str(receipts[0][0]))


@pytest.mark.parametrize("dry_run", [True, False])
def test_legacy_and_target_generation_collision_fails_preflight_without_mutation(
    legacy: Legacy, dry_run: bool,
) -> None:
    current = Store(legacy.source, tenant_generation=1)
    record = current.load_current_snapshot(1)
    assert record is not None
    original = current.load_receipts_for_snapshot(record.snapshot.snapshot_id)[0]
    current.save_receipt(
        record, actor_id=original.actor_id, actor_login=original.actor_login,
        answers=original.successful_answers, app_id=original.app_id, installation_id=original.installation_id,
        now="2026-09-16T08:00:00Z",
    )
    before, registry_before = database_image(legacy.source), database_image(legacy.harness.settings.database)
    with pytest.raises(MigrationError, match="generations collide"):
        legacy.run(dry_run=dry_run)
    assert database_image(legacy.source) == before
    assert database_image(legacy.harness.settings.database) == registry_before
    assert not legacy.destination.exists()
    assert not pending_import_journals(legacy.harness.settings.state_root)


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("corruption", ["repository", "actor", "installation", "generation", "receipt-generation"])
def test_legacy_binding_corruption_is_rejected_without_mutation(
    legacy: Legacy, dry_run: bool, corruption: str,
) -> None:
    with sqlite3.connect(legacy.source) as connection:
        statements = {
            "repository": "UPDATE snapshots SET repo_id = 999",
            "actor": "UPDATE receipts SET actor_id = actor_id + 1",
            "installation": "UPDATE receipts SET installation_id = 999",
            "generation": "UPDATE outbox SET tenant_generation = 9",
            "receipt-generation": "UPDATE receipts SET tenant_generation = 9",
        }
        connection.execute(statements[corruption])
    before, registry_before = database_image(legacy.source), database_image(legacy.harness.settings.database)
    with pytest.raises(MigrationError):
        legacy.run(dry_run=dry_run)
    assert database_image(legacy.source) == before
    assert database_image(legacy.harness.settings.database) == registry_before
    assert not legacy.destination.exists()
    assert not pending_import_journals(legacy.harness.settings.state_root)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["replace", "directory-fsync", "staged-chmod"])
def test_import_failure_rolls_back_exact_destination_and_registry_then_retries(
    legacy: Legacy, monkeypatch: pytest.MonkeyPatch, existing: bool, failure: str,
) -> None:
    if existing:
        Store(legacy.destination)
    before_source = database_image(legacy.source)
    previous_row = read_legacy_row(legacy.harness.settings, 101)
    before_dest = database_image(legacy.destination) if existing else None
    inode = legacy.destination.stat().st_ino if existing else None
    original_replace, original_fsync, original_chmod = os.replace, migration._fsync_parent, os.chmod
    failed = False

    def fail_replace(staged: Path, target: Path) -> None:
        nonlocal failed
        original_replace(staged, target)
        if not failed and target == legacy.destination and str(staged).endswith(".sqlite.tmp"):
            failed = True
            raise OSError("After real atomic replacement")

    def fail_fsync(path: Path) -> None:
        nonlocal failed
        original_fsync(path)
        if not failed and path == legacy.destination:
            failed = True
            raise OSError("After real directory fsync")

    def fail_chmod(path: Path, mode: int) -> None:
        if str(path).endswith(".sqlite.tmp"):
            raise OSError("Setting staging permissions")
        original_chmod(path, mode)

    with monkeypatch.context() as patch:
        if failure == "replace":
            patch.setattr(migration.os, "replace", fail_replace)
        elif failure == "directory-fsync":
            patch.setattr(migration, "_fsync_parent", fail_fsync)
        else:
            patch.setattr(migration.os, "chmod", fail_chmod)
        with pytest.raises((MigrationError, OSError)):
            legacy.run()
    assert database_image(legacy.source) == before_source
    assert read_legacy_row(legacy.harness.settings, 101) == previous_row
    if before_dest is None:
        assert not legacy.destination.exists()
    else:
        assert database_image(legacy.destination) == before_dest and legacy.destination.stat().st_ino == inode
    assert not pending_import_journals(legacy.harness.settings.state_root)
    assert not tuple(legacy.destination.parent.glob(".legacy-import-*"))
    assert legacy.run().receipts == 1
    assert database_image(legacy.source) == before_source


class SimulatedProcessExit(BaseException):
    """Retain the journal across the recoverable-error handling boundary."""


@pytest.mark.parametrize("phase", ["after-backup", "after-replace", "after-commit", "after-marker"])
def test_journal_blocks_startup_and_dry_run_until_identical_command_recovers(
    legacy: Legacy, monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    Store(legacy.destination)
    before = database_image(legacy.source)
    original_replace, original_journal = os.replace, migration._write_journal

    def crash_replace(staged: Path, target: Path) -> None:
        original_replace(staged, target)
        if phase == "after-backup" and str(target).endswith(".previous"):
            raise SimulatedProcessExit()
        if phase == "after-replace" and target == legacy.destination:
            raise SimulatedProcessExit()

    def crash_journal(path: Path, journal: migration._ImportJournal) -> None:
        if journal.phase == "committed" and phase == "after-commit":
            raise SimulatedProcessExit()
        original_journal(path, journal)
        if journal.phase == "committed" and phase == "after-marker":
            raise SimulatedProcessExit()

    with monkeypatch.context() as patch:
        patch.setattr(migration.os, "replace", crash_replace)
        patch.setattr(migration, "_write_journal", crash_journal)
        with pytest.raises(SimulatedProcessExit):
            legacy.run()
    journals = pending_import_journals(legacy.harness.settings.state_root)
    assert len(journals) == 1
    journal_before = journals[0].read_bytes()
    assert b"Legacy successful evidence" not in journal_before and b"successful_answers" not in journal_before
    with pytest.raises(StatePathError, match="pending legacy import"):
        DataDirectoryLock(legacy.harness.settings.state_root).acquire()
    with pytest.raises(RegistrationOperationalError, match="pending legacy import"):
        RepositoryRegistry(legacy.harness.settings, GitHubInstallationDiscovery(legacy.harness.settings))
    with pytest.raises(MigrationError, match="without --dry-run"):
        legacy.run(dry_run=True)
    assert journals[0].read_bytes() == journal_before
    assert legacy.run().to_dict()["state"] == "imported"
    assert database_image(legacy.source) == before
    assert receipt_rows(legacy.destination) == receipt_rows(legacy.source)
    assert not pending_import_journals(legacy.harness.settings.state_root)
    assert not tuple(legacy.destination.parent.glob(".legacy-import-*"))
    with DataDirectoryLock(legacy.harness.settings.state_root):
        assert legacy.destination.exists()


def test_real_process_death_recovers_hot_sqlite_journal_without_touching_source(legacy: Legacy) -> None:
    before = database_image(legacy.source)
    script = """
import os
import sqlite3
import sys
from pathlib import Path
sys.path[:0] = [str(Path.cwd() / "src"), str(Path.cwd() / "tests")]
import requests
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from registration_transport import GitCorpus, RegistrationHTTP
from lasthuman.server.config import GatewaySettings
from lasthuman.server.github import GitHubInstallationDiscovery
from lasthuman.server.migration import import_legacy_database
settings = GatewaySettings.from_env()
root = Path(sys.argv[1])
key = load_pem_private_key(settings.private_key_file.read_bytes(), password=None)
http = RegistrationHTTP(settings, GitCorpus(root), key)
requests.adapters.HTTPAdapter.send = lambda adapter, request, **kwargs: http.send(request, **kwargs)
original = sqlite3.connect
class CrashConnection(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        result = super().execute(sql, parameters)
        if "INSERT INTO repositories" in sql:
            os._exit(77)
        return result
def crash_connect(database, *args, **kwargs):
    return original(database, *args, factory=CrashConnection, **kwargs)
sqlite3.connect = crash_connect
import_legacy_database(settings, source_db=root / "legacy-fixed.sqlite",
    repository_id=101, repository="acme/one", owner_id=77,
    discovery=GitHubInstallationDiscovery(settings, requests.Session()))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(legacy.harness.root)],
        cwd=Path(__file__).resolve().parent.parent, env=dict(os.environ),
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 77, result.stderr
    assert pending_import_journals(legacy.harness.settings.state_root)
    assert Path(f"{legacy.harness.settings.database}-journal").exists()
    with pytest.raises(StatePathError, match="pending legacy import"):
        DataDirectoryLock(legacy.harness.settings.state_root).acquire()
    assert legacy.run().to_dict()["state"] == "imported"
    assert database_image(legacy.source) == before
    assert receipt_rows(legacy.destination) == receipt_rows(legacy.source)
    assert not pending_import_journals(legacy.harness.settings.state_root)
    assert not tuple(legacy.harness.root.glob("registry.sqlite-*"))


def test_offline_import_refuses_wal_source_without_modifying_evidence(legacy: Legacy) -> None:
    wal = Path(f"{legacy.source}-wal")
    wal.write_bytes(b"uncheckpointed data")
    before = file_hash(legacy.source)
    with pytest.raises(MigrationError, match="checkpointed"):
        legacy.run(dry_run=True)
    assert file_hash(legacy.source) == before and wal.read_bytes() == b"uncheckpointed data"
    assert not legacy.destination.exists()


def test_recovery_does_not_delete_unrecognized_destination(legacy: Legacy, monkeypatch: pytest.MonkeyPatch) -> None:
    original = migration._write_journal

    def crash(path: Path, journal: migration._ImportJournal) -> None:
        if journal.phase == "committed":
            raise SimulatedProcessExit()
        original(path, journal)

    with monkeypatch.context() as patch:
        patch.setattr(migration, "_write_journal", crash)
        with pytest.raises(SimulatedProcessExit):
            legacy.run()
    with sqlite3.connect(legacy.destination) as connection:
        connection.execute("UPDATE receipts SET actor_login = 'unrelated-change'")
    before = database_image(legacy.destination)
    journal = pending_import_journals(legacy.harness.settings.state_root)[0]
    journal_before = json.loads(journal.read_text(encoding="utf-8"))
    with pytest.raises(MigrationError, match="unknown destination"):
        legacy.run()
    assert database_image(legacy.destination) == before
    assert json.loads(journal.read_text(encoding="utf-8")) == journal_before
