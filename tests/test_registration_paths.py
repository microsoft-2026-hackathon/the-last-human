"""Tenant path aliases must fail before they can mutate another repository."""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

from registration_transport import Integration, integration
from test_registration_migration import Legacy, database_image, legacy
from lasthuman.server.config import ConfigurationError
from lasthuman.server.migration import MigrationError
from lasthuman.server.registration import RegistrationOperationalError, RepositoryContext
from lasthuman.server.runtime_lock import DataDirectoryLock, StatePathError
from lasthuman.server.store import Store

__all__ = ["integration", "legacy"]


def test_new_tenant_paths_validate_before_creation(integration: Integration) -> None:
    context = RepositoryContext(101, "acme/one", 77, 201, 1)
    tenant_root = integration.settings.state_root / "repos/101"
    integration.settings.validate_runtime_paths(context.repository_id)
    unprepared = integration.settings.tenant_settings(context, prepare=False)
    assert not tenant_root.exists()
    prepared = integration.settings.tenant_settings(context)
    assert prepared == unprepared
    assert not prepared.database.exists()
    assert all((tenant_root / name).is_dir() for name in ("cache", "snapshot-cache", "runtime"))


@pytest.mark.parametrize(
    "component", ["repos", "tenant", "cache", "snapshot-cache", "runtime", "database", "cache-file"],
)
def test_symlinked_tenant_paths_cannot_change_another_store(integration: Integration, component: str) -> None:
    settings = integration.settings
    other = integration.root / "other"
    other.mkdir()
    database = other / "lasthuman.sqlite"
    Store(database)
    before = database_image(database)
    tenant = settings.state_root / "repos/101"
    paths = {"repos": settings.state_root / "repos", "tenant": tenant, "cache": tenant / "cache",
             "snapshot-cache": tenant / "snapshot-cache", "runtime": tenant / "runtime",
             "database": tenant / "lasthuman.sqlite", "cache-file": tenant / "snapshot-cache/file.json"}
    selected = paths[component]
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.symlink_to(database if component in {"database", "cache-file"} else other)
    with pytest.raises(RegistrationOperationalError):
        settings.tenant_settings(RepositoryContext(101, "acme/one", 77, 201, 1))
    assert database_image(database) == before


def test_hardlinked_tenant_database_is_rejected(integration: Integration) -> None:
    one = integration.settings.tenant_settings(RepositoryContext(101, "acme/one", 77, 201, 1))
    Store(one.database)
    context = RepositoryContext(102, "acme/two", 77, 202, 1)
    two = integration.settings.tenant_settings(context)
    os.link(one.database, two.database)
    before = database_image(one.database)
    with pytest.raises(RegistrationOperationalError, match="hardlinks"):
        integration.settings.tenant_settings(context)
    assert database_image(one.database) == before


@pytest.mark.parametrize("location", [
    ".lasthuman-runtime.lock", "repos/101/lasthuman.sqlite", "repos/102/cache/registry.sqlite",
    ".lasthuman-import-101.json",
])
def test_registry_cannot_alias_reserved_tenant_paths(integration: Integration, location: str) -> None:
    with pytest.raises(ConfigurationError):
        replace(integration.settings, database=integration.settings.state_root / location)


@pytest.mark.parametrize("repo_id", [0, -1, True, "101", "101/../102", 1 << 63])
def test_context_id_cannot_escape_numeric_namespace(integration: Integration, repo_id: object) -> None:
    with pytest.raises((ConfigurationError, ValueError)):
        integration.settings.tenant_root(RepositoryContext(repo_id, "acme/one", 77, 201, 1))
    assert not (integration.settings.state_root / "repos").exists()


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
@pytest.mark.parametrize("target", ["registry", "destination", "lock"])
@pytest.mark.parametrize("dry_run", [True, False])
def test_legacy_alias_preflight_never_modifies_either_file(
    legacy: Legacy, alias: str, target: str, dry_run: bool,
) -> None:
    settings = legacy.harness.settings
    chosen = {"registry": settings.database, "destination": legacy.destination,
              "lock": settings.state_root / ".lasthuman-runtime.lock"}[target]
    if chosen.exists():
        chosen.unlink()
    chosen.parent.mkdir(parents=True, exist_ok=True)
    if alias == "symlink":
        chosen.symlink_to(legacy.source)
    else:
        os.link(legacy.source, chosen)
    before = database_image(legacy.source)
    with pytest.raises(MigrationError):
        legacy.run(dry_run=dry_run)
    assert database_image(legacy.source) == before
    assert database_image(chosen) == before


def test_lock_never_truncates_sqlite_file(integration: Integration) -> None:
    integration.manager.shutdown()
    lock = DataDirectoryLock(integration.settings.state_root)
    source = integration.root / "unrelated.sqlite"
    Store(source)
    lock.path.write_bytes(source.read_bytes())
    lock.path.chmod(0o600)
    before = database_image(lock.path)
    with pytest.raises(StatePathError):
        lock.acquire()
    assert database_image(lock.path) == before
