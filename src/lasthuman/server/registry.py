"""Durable, bounded registration of independently verified repository contexts."""

from __future__ import annotations

import os
import re
import sqlite3
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Protocol
from urllib.parse import quote

from .config import GatewaySettings
from .events import VerifiedActionsIdentity
from .registration import (
    RegistrationDeniedError, RegistrationNotFoundError, RegistrationOperationalError,
    RepositoryContext, RepositoryInstallation,
)
from .runtime_lock import prepare_private_directory

_REPOSITORY_COLUMNS = (
    "repository_id", "repository", "owner_id", "installation_id", "state",
    "revision", "generation", "verified_at", "last_verified_at", "created_at", "updated_at",
)


class InstallationDiscovery(Protocol):
    def discover(
        self, repository: str, repository_id: int, owner_id: int, *, verify_opt_in: bool = True,
    ) -> RepositoryInstallation:
        ...


@dataclass
class _RepositoryLockSlot:
    lock: RLock
    references: int = 0


@dataclass(frozen=True)
class LegacyActivation:
    context: RepositoryContext
    inserted: bool
    previous_row: dict[str, object] | None = None
    activated_row: dict[str, object] | None = None


def owner_allowed(allowed_owner_ids: frozenset[int], owner_id: int) -> bool:
    return not allowed_owner_ids or owner_id in allowed_owner_ids


def _require_capacity(count: int, maximum: int) -> None:
    if count >= maximum:
        raise RegistrationDeniedError("registered repository limit reached")


@contextmanager
def readonly_registry(path: Path) -> Iterator[sqlite3.Connection]:
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(f"{path}{suffix}").exists():
            raise RegistrationOperationalError("registration database must be stopped and checkpointed before import")
    connection = sqlite3.connect(f"file:{quote(str(path.absolute()), safe='/:')}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def preflight_legacy_import(
    settings: GatewaySettings, installation: RepositoryInstallation, *, connection: sqlite3.Connection | None = None,
) -> RepositoryContext:
    settings.validate_runtime_paths(installation.repository_id, allow_pending_import=True)
    _validate_installation(installation)
    if not owner_allowed(settings.allowed_owner_ids, installation.owner_id):
        raise RegistrationDeniedError("repository owner is not allowed to register")
    if connection is None and settings.database.exists():
        with readonly_registry(settings.database) as readonly:
            return preflight_legacy_import(settings, installation, connection=readonly)
    row = None
    count = 0
    if connection is not None:
        _require_registry_schema(connection)
        tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if tables and "repositories" not in tables:
            raise RegistrationOperationalError("registration database has an unknown schema")
        if "repositories" in tables:
            row = connection.execute(
                "SELECT * FROM repositories WHERE repository_id = ?", (installation.repository_id,),
            ).fetchone()
            count = connection.execute("SELECT COUNT(*) FROM repositories").fetchone()[0]
    if row is None:
        _require_capacity(count, settings.max_registered_repositories)
        return RepositoryContext(
            installation.repository_id, installation.repository, installation.owner_id, installation.installation_id, 1,
        )
    context = _context_from_row(row)
    if (
        context.repository.casefold() != installation.repository.casefold()
        or context.owner_id != installation.owner_id or context.installation_id != installation.installation_id
    ):
        raise RegistrationDeniedError("registered repository identity does not match legacy import")
    return context


def read_legacy_row(settings: GatewaySettings, repository_id: int) -> dict[str, object] | None:
    with readonly_registry(settings.database) as connection:
        row = connection.execute("SELECT * FROM repositories WHERE repository_id = ?", (repository_id,)).fetchone()
    return None if row is None else dict(row)


def legacy_activation_from_rows(previous: object, activated: object) -> LegacyActivation:
    for record in (previous, activated):
        if record is None:
            continue
        if not isinstance(record, dict) or set(record) != set(_REPOSITORY_COLUMNS):
            raise RegistrationOperationalError("legacy import registry journal is invalid")
        for key in ("repository_id", "owner_id", "installation_id", "revision", "generation"):
            _positive_int(record[key], key)
        for key in ("repository", "state", "verified_at", "last_verified_at", "created_at", "updated_at"):
            if not isinstance(record[key], str) or not record[key]:
                raise RegistrationOperationalError("legacy import registry journal is invalid")
    if not isinstance(activated, dict):
        raise RegistrationOperationalError("legacy import registry journal has no activation")
    context = _context_from_row(activated)
    if isinstance(previous, dict) and _context_from_row(previous) != context:
        raise RegistrationOperationalError("legacy import journal changes repository identity")
    return LegacyActivation(context, previous is None, previous, activated)


def _insert_registry_row(connection: sqlite3.Connection, record: dict[str, object]) -> None:
    placeholders = ", ".join("?" for _ in _REPOSITORY_COLUMNS)
    connection.execute(
        f"INSERT INTO repositories ({', '.join(_REPOSITORY_COLUMNS)}) VALUES ({placeholders})",
        tuple(record[column] for column in _REPOSITORY_COLUMNS),
    )


def restore_legacy_activation(settings: GatewaySettings, activation: LegacyActivation) -> None:
    settings.validate_runtime_paths(activation.context.repository_id, allow_pending_import=True)
    uri = f"file:{quote(str(settings.database.absolute()), safe='/:')}?mode=rw"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM repositories WHERE repository_id = ?", (activation.context.repository_id,),
            ).fetchone()
            current = None if row is None else dict(row)
            if current == activation.previous_row:
                return
            if current != activation.activated_row or activation.activated_row is None:
                raise RegistrationOperationalError("legacy import registry row changed; recovery refused")
            connection.execute("DELETE FROM repositories WHERE repository_id = ?", (activation.context.repository_id,))
            if activation.previous_row is not None:
                _insert_registry_row(connection, activation.previous_row)


def recover_legacy_registry(settings: GatewaySettings, activation: LegacyActivation) -> None:
    settings.validate_runtime_paths(activation.context.repository_id, allow_pending_import=True)
    uri = f"file:{quote(str(settings.database.absolute()), safe='/:')}?mode=rw"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM repositories WHERE repository_id = ?", (activation.context.repository_id,),
            ).fetchone()
            current = None if row is None else dict(row)
            if current not in (activation.previous_row, activation.activated_row):
                raise RegistrationOperationalError("legacy import recovery found changed registry metadata")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            connection.execute(f"PRAGMA user_version = {version}")


class RepositoryRegistry:
    def __init__(
        self, gateway_settings: GatewaySettings, discovery: InstallationDiscovery,
        *, clock: Callable[[], float] = time.time, allow_pending_import: bool = False,
    ) -> None:
        self.settings = gateway_settings
        self.discovery = discovery
        self.clock = clock
        self.path = gateway_settings.database
        self._allow_pending_import = allow_pending_import
        self._positive_ttl = gateway_settings.positive_ttl_seconds
        self._negative_ttl = gateway_settings.negative_ttl_seconds
        self._max_registered = gateway_settings.max_registered_repositories
        self._allowed_owner_ids = gateway_settings.allowed_owner_ids
        self._lock_limit = min(max(self._max_registered * 2, 8), 64)
        self._lock = RLock()
        self._repo_locks: OrderedDict[int, _RepositoryLockSlot] = OrderedDict()
        self._positive_cache: OrderedDict[int, tuple[float, RepositoryContext]] = OrderedDict()
        self._negative_cache: OrderedDict[int, float] = OrderedDict()
        self.settings.validate_runtime_paths(allow_pending_import=allow_pending_import)
        if self.path.exists() and not any(
            Path(f"{self.path}{suffix}").exists() for suffix in ("-journal", "-wal", "-shm")
        ):
            with readonly_registry(self.path) as connection:
                _require_registry_schema(connection)
        prepare_private_directory(self.path.parent)
        self._initialize()

    def register(self, verified_identity: VerifiedActionsIdentity) -> RepositoryContext:
        if verified_identity.event_name != "pull_request_target":
            raise RegistrationDeniedError("only pull_request_target can register a repository")
        repository_id = _positive_int(verified_identity.repository_id, "repository_id")
        repository = _repository_name(verified_identity.repository)
        owner_id = _positive_int(verified_identity.owner_id, "owner_id")
        expected = f"{repository}/.github/workflows/{self.settings.workflow}@{self.settings.workflow_ref}"
        if verified_identity.workflow_ref != expected:
            raise RegistrationDeniedError("repository workflow identity is untrusted")
        if not owner_allowed(self._allowed_owner_ids, owner_id):
            self._remember_negative(repository_id)
            raise RegistrationDeniedError("repository owner is not allowed to register")
        with self._repo_lock(repository_id):
            cached = self._fresh_positive(repository_id)
            if cached is not None and cached.repository == repository and cached.owner_id == owner_id:
                return cached
            if self._negative_is_fresh(repository_id) or self._retired_negative_is_fresh(repository_id):
                raise RegistrationDeniedError("repository registration was recently denied")
            try:
                installation = self.discovery.discover(repository, repository_id, owner_id, verify_opt_in=True)
                if (
                    installation.repository_id != repository_id or installation.repository != repository
                    or installation.owner_id != owner_id
                ):
                    raise RegistrationDeniedError("verified discovery identity mismatch")
            except RegistrationDeniedError:
                stored = self._load_context(repository_id)
                if stored is not None:
                    self._retire(stored)
                self._remember_negative(repository_id)
                raise
            return self._activate(installation)

    def resolve(self, repository_id: int) -> RepositoryContext:
        repo_id = _positive_int(repository_id, "repository_id")
        with self._repo_lock(repo_id):
            if self._negative_is_fresh(repo_id):
                raise RegistrationNotFoundError("repository is not registered")
            cached = self._fresh_positive(repo_id)
            if cached is not None:
                if not owner_allowed(self._allowed_owner_ids, cached.owner_id):
                    self._retire(cached)
                    raise RegistrationDeniedError("repository owner is no longer allowed")
                return cached
            stored = self._load_context(repo_id)
            if stored is None:
                raise RegistrationNotFoundError("repository is not registered")
            if not owner_allowed(self._allowed_owner_ids, stored.owner_id):
                self._retire(stored)
                raise RegistrationDeniedError("repository owner is no longer allowed")
            try:
                installation = self.discovery.discover(
                    stored.repository, repo_id, stored.owner_id, verify_opt_in=True,
                )
                if (
                    installation.repository_id != repo_id or installation.repository != stored.repository
                    or installation.owner_id != stored.owner_id
                ):
                    raise RegistrationDeniedError("verified discovery identity mismatch")
            except RegistrationDeniedError:
                self._retire(stored)
                raise
            return self._activate(installation)

    def require_current(self, context: RepositoryContext) -> None:
        stored = self._load_context(_positive_int(context.repository_id, "repository_id"))
        if stored != context:
            raise RegistrationDeniedError("repository context generation or identity is stale")
        if not owner_allowed(self._allowed_owner_ids, stored.owner_id):
            self._retire(stored)
            raise RegistrationDeniedError("repository owner is no longer allowed")
        if self._fresh_positive(context.repository_id) == context:
            return
        if self.resolve(context.repository_id) != context:
            raise RegistrationDeniedError("repository context generation is stale")

    def list_registered(self) -> tuple[RepositoryContext, ...]:
        with self._read_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM repositories WHERE state = 'active' ORDER BY repository_id",
            ).fetchall()
        return tuple(_context_from_row(row) for row in rows)

    def activate_legacy(self, installation: RepositoryInstallation) -> LegacyActivation:
        with self.legacy_activation(installation) as activation:
            return activation

    @contextmanager
    def legacy_activation(self, installation: RepositoryInstallation) -> Iterator[LegacyActivation]:
        with self._repo_lock(installation.repository_id):
            with self._write_connection() as connection:
                context = preflight_legacy_import(self.settings, installation, connection=connection)
                row = connection.execute(
                    "SELECT * FROM repositories WHERE repository_id = ?", (installation.repository_id,),
                ).fetchone()
                activated = dict(row) if row is not None else self._new_row(installation)
                if row is not None:
                    activated.update(
                        revision=int(row["revision"]) + 1, last_verified_at=self._now_iso(), updated_at=self._now_iso(),
                    )
                yield LegacyActivation(context, row is None, None if row is None else dict(row), activated)
                if row is None:
                    _insert_registry_row(connection, activated)
                else:
                    connection.execute(
                        """UPDATE repositories SET revision = ?, last_verified_at = ?, updated_at = ?
                           WHERE repository_id = ?""",
                        (
                            activated["revision"], activated["last_verified_at"], activated["updated_at"],
                            installation.repository_id,
                        ),
                    )
        self._remember_positive(context)

    def rollback_legacy_activation(self, activation: LegacyActivation) -> None:
        with self._repo_lock(activation.context.repository_id):
            restore_legacy_activation(self.settings, activation)
            with self._lock:
                self._positive_cache.pop(activation.context.repository_id, None)

    def shutdown(self) -> None:
        with self._lock:
            self._positive_cache.clear()
            self._negative_cache.clear()
            for repo_id, slot in tuple(self._repo_locks.items()):
                if slot.references == 0:
                    self._repo_locks.pop(repo_id)

    def _new_row(self, installation: RepositoryInstallation) -> dict[str, object]:
        now = self._now_iso()
        return dict(zip(_REPOSITORY_COLUMNS, (
            installation.repository_id, installation.repository, installation.owner_id, installation.installation_id,
            "active", 1, 1, now, now, now, now,
        )))

    def _activate(self, installation: RepositoryInstallation) -> RepositoryContext:
        _validate_installation(installation)
        if not owner_allowed(self._allowed_owner_ids, installation.owner_id):
            raise RegistrationDeniedError("repository owner is not allowed to register")
        with self._write_connection() as connection:
            row = connection.execute(
                "SELECT * FROM repositories WHERE repository_id = ?", (installation.repository_id,),
            ).fetchone()
            if row is None:
                count = connection.execute("SELECT COUNT(*) FROM repositories").fetchone()[0]
                _require_capacity(count, self._max_registered)
                record = self._new_row(installation)
                _insert_registry_row(connection, record)
            else:
                record = dict(row)
                changed = (
                    record["repository"] != installation.repository or record["owner_id"] != installation.owner_id
                    or record["installation_id"] != installation.installation_id or record["state"] != "active"
                )
                record.update(
                    repository=installation.repository, owner_id=installation.owner_id,
                    installation_id=installation.installation_id, state="active",
                    revision=int(record["revision"]) + 1, generation=int(record["generation"]) + int(changed),
                    last_verified_at=self._now_iso(), updated_at=self._now_iso(),
                )
                connection.execute(
                    """UPDATE repositories SET repository = ?, owner_id = ?, installation_id = ?, state = ?,
                       revision = ?, generation = ?, last_verified_at = ?, updated_at = ? WHERE repository_id = ?""",
                    tuple(record[key] for key in (
                        "repository", "owner_id", "installation_id", "state", "revision", "generation",
                        "last_verified_at", "updated_at", "repository_id",
                    )),
                )
        context = _context_from_row(record)
        self._remember_positive(context)
        return context

    def _retire(self, context: RepositoryContext) -> None:
        with self._write_connection() as connection:
            cursor = connection.execute(
                """UPDATE repositories SET state = 'retired', revision = revision + 1,
                   generation = generation + 1, updated_at = ?
                   WHERE repository_id = ? AND generation = ? AND state = 'active'""",
                (self._now_iso(), context.repository_id, context.generation),
            )
            retired = cursor.rowcount == 1
        with self._lock:
            cached = self._positive_cache.get(context.repository_id)
            if cached is not None and cached[1] == context:
                self._positive_cache.pop(context.repository_id)
        if retired:
            self._remember_negative(context.repository_id)

    def _load_context(self, repository_id: int) -> RepositoryContext | None:
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM repositories WHERE repository_id = ? AND state = 'active'", (repository_id,),
            ).fetchone()
        return None if row is None else _context_from_row(row)

    def _fresh_positive(self, repository_id: int) -> RepositoryContext | None:
        with self._lock:
            item = self._positive_cache.get(repository_id)
            if item is None:
                return None
            if item[0] <= self.clock():
                self._positive_cache.pop(repository_id)
                return None
            if self._load_context(repository_id) != item[1]:
                self._positive_cache.pop(repository_id)
                return None
            self._positive_cache.move_to_end(repository_id)
            return item[1]

    def _remember_positive(self, context: RepositoryContext) -> None:
        with self._lock:
            self._positive_cache[context.repository_id] = (self.clock() + self._positive_ttl, context)
            self._positive_cache.move_to_end(context.repository_id)
            while len(self._positive_cache) > self._max_registered:
                self._positive_cache.popitem(last=False)
            self._negative_cache.pop(context.repository_id, None)

    def _negative_is_fresh(self, repository_id: int) -> bool:
        with self._lock:
            expires = self._negative_cache.get(repository_id)
            if expires is None:
                return False
            if expires <= self.clock():
                self._negative_cache.pop(repository_id)
                return False
            self._negative_cache.move_to_end(repository_id)
            return True

    def _remember_negative(self, repository_id: int) -> None:
        with self._lock:
            self._negative_cache[repository_id] = self.clock() + self._negative_ttl
            self._negative_cache.move_to_end(repository_id)
            while len(self._negative_cache) > self._lock_limit:
                self._negative_cache.popitem(last=False)

    def _retired_negative_is_fresh(self, repository_id: int) -> bool:
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT updated_at FROM repositories WHERE repository_id = ? AND state = 'retired'", (repository_id,),
            ).fetchone()
        if row is None:
            return False
        try:
            retired_at = datetime.strptime(str(row["updated_at"]), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            raise RegistrationOperationalError("registered repository timestamp is invalid") from None
        return retired_at.timestamp() + self._negative_ttl > self.clock()

    @contextmanager
    def _repo_lock(self, repository_id: int) -> Iterator[None]:
        with self._lock:
            slot = self._repo_locks.get(repository_id)
            if slot is None:
                self._prune_idle_repo_locks()
                if len(self._repo_locks) >= self._lock_limit:
                    raise RegistrationOperationalError("repository registration lock capacity reached")
                slot = _RepositoryLockSlot(RLock())
                self._repo_locks[repository_id] = slot
            slot.references += 1
            self._repo_locks.move_to_end(repository_id)
        try:
            with slot.lock:
                yield
        finally:
            with self._lock:
                slot.references -= 1
                self._prune_idle_repo_locks()

    def _prune_idle_repo_locks(self) -> None:
        while len(self._repo_locks) >= self._lock_limit:
            for repository_id, slot in tuple(self._repo_locks.items()):
                if slot.references == 0:
                    self._repo_locks.pop(repository_id)
                    break
            else:
                return

    def _initialize(self) -> None:
        with self._write_connection() as connection:
            # A normal crash may leave a hot SQLite journal. Let the engine recover
            # it, then check the schema before making any application-level changes.
            _require_registry_schema(connection)
            connection.execute(
                """CREATE TABLE IF NOT EXISTS repositories (
                    repository_id INTEGER PRIMARY KEY, repository TEXT NOT NULL, owner_id INTEGER NOT NULL,
                    installation_id INTEGER NOT NULL, state TEXT NOT NULL CHECK (state IN ('active', 'retired')),
                    revision INTEGER NOT NULL, generation INTEGER NOT NULL, verified_at TEXT NOT NULL,
                    last_verified_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )""",
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_repositories_state_updated ON repositories(state, updated_at)",
            )
        os.chmod(self.path, 0o600)

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            with closing(self._connect()) as connection:
                yield connection

    @contextmanager
    def _write_connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            with closing(self._connect()) as connection:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    yield connection

    def _connect(self) -> sqlite3.Connection:
        self.settings.validate_runtime_paths(allow_pending_import=self._allow_pending_import)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _now_iso(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock()))


def _require_registry_schema(connection: sqlite3.Connection) -> None:
    tables = {
        row["name"] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'",
        )
    }
    if tables and tables != {"repositories"}:
        raise RegistrationOperationalError("registration database has an unknown schema")
    if "repositories" in tables and tuple(
        row["name"] for row in connection.execute("PRAGMA table_info(repositories)")
    ) != _REPOSITORY_COLUMNS:
        raise RegistrationOperationalError("registration database has an unknown schema")


def _context_from_row(row: sqlite3.Row | dict[str, object]) -> RepositoryContext:
    if row["state"] == "retired":
        raise RegistrationNotFoundError("repository is retired")
    if row["state"] != "active":
        raise RegistrationOperationalError("registered repository state is invalid")
    return RepositoryContext(
        _positive_int(row["repository_id"], "repository_id"), _repository_name(row["repository"]),
        _positive_int(row["owner_id"], "owner_id"), _positive_int(row["installation_id"], "installation_id"),
        _positive_int(row["generation"], "generation"),
    )


def _validate_installation(installation: RepositoryInstallation) -> None:
    _repository_name(installation.repository)
    for name, value in (
        ("repository_id", installation.repository_id), ("owner_id", installation.owner_id),
        ("installation_id", installation.installation_id),
    ):
        _positive_int(value, name)


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < (1 << 63):
        raise RegistrationDeniedError(f"{field_name} must be a positive integer")
    return value


def _repository_name(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", value):
        raise RegistrationDeniedError("repository name is invalid")
    return value
