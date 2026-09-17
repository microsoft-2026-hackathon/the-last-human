"""Repository-scoped demo history, without manufacturing gate evidence."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import fields, replace
from pathlib import Path
from typing import cast

import pytest
import requests
from flask.testing import FlaskClient

from registration_transport import PASS, Integration, integration, obj
from lasthuman.server import relay, service as service_module
from lasthuman.server.config import ConfigurationError, GatewaySettings, Settings, load_settings
from lasthuman.server.coverage import Seed, load_seed
from lasthuman.server.registration import RepositoryContext

__all__ = ["integration"]

STORE_TABLES = (
    "snapshots", "pr_snapshots", "receipts", "outbox", "snapshot_operations", "presentation_check_runs", "merges",
)


@pytest.fixture
def gateway_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GatewaySettings:
    for name in tuple(os.environ):
        if name.startswith(("TLH_", "LASTHUMAN_")):
            monkeypatch.delenv(name)
    key = tmp_path / "configuration-only.pem"
    key.write_text("Configuration test placeholder; never used for signing.", encoding="utf-8")
    key.chmod(0o600)
    for name, value in {
        "TLH_REGISTRATION_MODE": "first-event", "TLH_MODE": "live", "TLH_APP_ID": "901",
        "TLH_CLIENT_ID": "Iv1.offline", "TLH_CLIENT_SECRET": "offline-client-secret-" * 2,
        "TLH_PRIVATE_KEY_FILE": str(key), "TLH_BASE_URL": "https://bot.example",
        "TLH_SECRET_KEY": "offline-session-key-" * 2,
        "TLH_REGISTRATION_DATABASE": str(tmp_path / "registry" / "registry.sqlite"),
        "TLH_STATE_ROOT": str(tmp_path / "tenants"),
    }.items():
        monkeypatch.setenv(name, value)
    settings = load_settings()
    assert isinstance(settings, GatewaySettings)
    return settings


@pytest.fixture
def seed_path(tmp_path: Path) -> Path:
    path = tmp_path / "dashboard-seed.json"
    path.write_text(json.dumps({
        "totals": {"merged": 10, "gated": 6, "attested": 5, "forced": 1, "waiting": 0},
        "zones": [
            {"zone": "app/auth/", "owner": "@seed-team", "merged": 1, "gated": 1, "forced": 1, "prs": [27]},
            {"zone": "app/orders/", "owner": "@orders-team", "merged": 5, "gated": 5,
             "attested": 5, "answerers": 2, "prs": [26]},
            {"zone": "docs/", "merged": 4},
        ],
    }), encoding="utf-8")
    return path


def assert_no_runtime_paths(settings: GatewaySettings) -> None:
    assert not settings.database.parent.exists()
    assert not settings.state_root.exists()


@pytest.mark.parametrize("raw_seed", [None, "", "   "])
def test_first_event_without_seed_remains_disabled(
    gateway_config: GatewaySettings, monkeypatch: pytest.MonkeyPatch, raw_seed: str | None,
) -> None:
    if raw_seed is not None:
        monkeypatch.setenv("TLH_DEMO_SEED", raw_seed)
    settings = load_settings()
    assert isinstance(settings, GatewaySettings)
    assert settings.demo_seed is None and settings.demo_seed_repository_id is None
    assert settings.tenant_settings(RepositoryContext(101, "acme/one", 77, 201, 1), prepare=False).demo_seed is None
    assert_no_runtime_paths(gateway_config)


@pytest.mark.parametrize(("raw_seed", "target"), [
    ("seed", None), (None, "101"), ("", "101"), ("   ", "101"),
    ("seed", ""), ("seed", " "), ("seed", "0"), ("seed", "-1"),
    ("seed", "+101"), ("seed", "0101"), ("seed", "1.0"), ("seed", "1e2"),
    ("seed", "true"), ("seed", "repository"), ("seed", "١٠١"), ("seed", "１０１"),
    ("seed", str(1 << 63)), ("seed", "9" * 100),
])
def test_first_event_rejects_incomplete_or_invalid_seed_environment_before_storage(
    gateway_config: GatewaySettings, seed_path: Path, monkeypatch: pytest.MonkeyPatch,
    raw_seed: str | None, target: str | None,
) -> None:
    if raw_seed is not None:
        monkeypatch.setenv("TLH_DEMO_SEED", str(seed_path) if raw_seed == "seed" else raw_seed)
    if target is not None:
        monkeypatch.setenv("TLH_DEMO_SEED_REPOSITORY_ID", target)
    with pytest.raises(ConfigurationError, match="TLH_DEMO_SEED"):
        load_settings()
    assert_no_runtime_paths(gateway_config)


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_first_event_rejects_non_file_seed_before_storage(
    gateway_config: GatewaySettings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    path = tmp_path / "missing.json" if kind == "missing" else tmp_path
    monkeypatch.setenv("TLH_DEMO_SEED", str(path))
    monkeypatch.setenv("TLH_DEMO_SEED_REPOSITORY_ID", "101")
    with pytest.raises(ConfigurationError, match="TLH_DEMO_SEED must point to an existing file"):
        load_settings()
    assert_no_runtime_paths(gateway_config)


@pytest.mark.parametrize("construction", ["constructor", "replace"])
@pytest.mark.parametrize(("seed_kind", "target"), [
    ("file", None), ("none", 101), ("file", 0), ("file", -1), ("file", True), ("file", False),
    ("file", "101"), ("file", 1.5), ("file", 1 << 63),
    ("missing", 101), ("directory", 101), ("string", 101),
])
def test_gateway_seed_contract_cannot_be_bypassed_by_construction_or_replace(
    gateway_config: GatewaySettings, seed_path: Path, construction: str, seed_kind: str, target: object,
) -> None:
    seed = {
        "file": seed_path, "none": None, "missing": seed_path.with_name("absent.json"),
        "directory": seed_path.parent, "string": str(seed_path),
    }[seed_kind]
    with pytest.raises(ConfigurationError, match="TLH_DEMO_SEED"):
        if construction == "replace":
            replace(gateway_config, demo_seed=cast(Path | None, seed), demo_seed_repository_id=cast(int | None, target))
        else:
            values = {field.name: getattr(gateway_config, field.name) for field in fields(gateway_config)}
            values.update(demo_seed=seed, demo_seed_repository_id=target)
            GatewaySettings(**values)
    assert_no_runtime_paths(gateway_config)


@pytest.mark.parametrize("target", [1, 1371498352, (1 << 63) - 1])
@pytest.mark.parametrize("prepare", [False, True])
def test_seed_target_is_numeric_configuration_not_repository_name_or_hardcoded_id(
    gateway_config: GatewaySettings, seed_path: Path, monkeypatch: pytest.MonkeyPatch, target: int, prepare: bool,
) -> None:
    monkeypatch.setenv("TLH_DEMO_SEED", str(seed_path))
    monkeypatch.setenv("TLH_DEMO_SEED_REPOSITORY_ID", f" {target} ")
    settings = load_settings()
    assert isinstance(settings, GatewaySettings)
    assert settings.demo_seed == seed_path and settings.demo_seed_repository_id == target
    selected = RepositoryContext(target, "microsoft-2026-hackathon/the-last-human", 77, 201, 1)
    same_name_other_id = replace(selected, repository_id=2)
    assert settings.tenant_settings(selected, prepare=prepare).demo_seed == seed_path
    assert settings.tenant_settings(same_name_other_id, prepare=prepare).demo_seed is None
    renamed = replace(selected, repository="acme/renamed")
    assert settings.tenant_settings(renamed, prepare=prepare).demo_seed == seed_path
    assert not settings.database.parent.exists()
    if not prepare:
        assert_no_runtime_paths(gateway_config)


def test_bundled_demo_seed_is_accepted_for_configured_repository(
    gateway_config: GatewaySettings, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = Path(__file__).resolve().parents[1] / "docs" / "demo" / "dashboard-seed.json"
    monkeypatch.setenv("TLH_DEMO_SEED", str(path))
    monkeypatch.setenv("TLH_DEMO_SEED_REPOSITORY_ID", "1371498352")
    settings = load_settings()
    assert isinstance(settings, GatewaySettings) and settings.demo_seed == path
    assert load_seed(path).merged == 41
    assert_no_runtime_paths(gateway_config)


@pytest.mark.parametrize("target", [None, "", "invalid", "1371498352"])
def test_fixed_mode_seed_still_works_without_target_and_ignores_target_environment(
    gateway_config: GatewaySettings, seed_path: Path, monkeypatch: pytest.MonkeyPatch, target: str | None,
) -> None:
    for name, value in {
        "TLH_REGISTRATION_MODE": "fixed", "TLH_REPOSITORY": "acme/one", "TLH_REPOSITORY_ID": "101",
        "TLH_OWNER_ID": "77", "TLH_INSTALLATION_ID": "201", "TLH_DEMO_SEED": str(seed_path),
        "TLH_DATABASE": str(seed_path.parent / "fixed.sqlite"),
    }.items():
        monkeypatch.setenv(name, value)
    if target is not None:
        monkeypatch.setenv("TLH_DEMO_SEED_REPOSITORY_ID", target)
    settings = load_settings()
    assert isinstance(settings, Settings) and settings.demo_seed == seed_path
    assert settings.repository_id == 101 and settings.path_prefix == ""
    assert not settings.database.exists()
    assert_no_runtime_paths(gateway_config)


def configure_seed(harness: Integration, monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setenv("TLH_DEMO_SEED", str(path))
    monkeypatch.setenv("TLH_DEMO_SEED_REPOSITORY_ID", "101")
    settings = load_settings()
    assert isinstance(settings, GatewaySettings)
    harness.settings = harness.http.settings = settings
    harness.restart()


def database_rows(path: Path, tables: tuple[str, ...]) -> dict[str, list[tuple[object, ...]]]:
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
        return {table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() for table in tables}


def dashboard(browser: FlaskClient, repository_id: int, csrf: str) -> dict[str, object]:
    response = browser.get(f"/repos/{repository_id}/api/dashboard", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.get_data(as_text=True)
    return obj(response.get_json())


@pytest.mark.parametrize("admission", ["unregistered", "removed", "not-opted-in"])
def test_seed_configuration_does_not_register_or_bypass_app_admission(
    integration: Integration, seed_path: Path, monkeypatch: pytest.MonkeyPatch, admission: str,
) -> None:
    configure_seed(integration, monkeypatch, seed_path)
    before = database_rows(integration.settings.database, ("repositories",))
    assert before == {"repositories": []}
    for route in ("/dashboard", "/api/dashboard"):
        assert integration.client().get("/repos/101" + route).status_code == 404
    assert not integration.http.calls
    if admission != "unregistered":
        body = integration.body(101)
        repo = integration.http.repositories[101]
        if admission == "removed":
            repo.removed = True
        else:
            repo.opted_in = False
        response = integration.client().post("/api/actions/events", json=body, headers=integration.headers(101))
        assert response.status_code == 403, response.get_data(as_text=True)
    assert database_rows(integration.settings.database, ("repositories",)) == before
    assert not integration.manager.containers() and not integration.chat.calls
    assert not (integration.settings.state_root / "repos").exists()
    assert all(not repo.statuses and not repo.checks and not repo.dispatches
               for repo in integration.http.repositories.values())


def test_selected_tenant_dashboard_adds_seed_to_real_verified_coverage_without_writes(
    integration: Integration, seed_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for repository_id in (101, 102):
        integration.open_event(repository_id)
    browser, csrf = integration.browser(101)
    _, passed = integration.submit(browser, csrf, 101, PASS, "real-pass-before-demo")
    assert passed["state"] == "awaiting_verification"
    receipt_id = str(passed["receipt_id"])
    integration.five_steps(101, receipt_id)
    selected = integration.manager.resolve(101)
    receipt = selected.service.store.load_receipt(receipt_id)
    assert receipt is not None and receipt.verified and receipt.head_sha == integration.corpus.head_sha
    repo = integration.http.repositories[101]
    repo.merged = True
    environment = integration.environment(
        101, {"repository": repo.metadata(), "action": "closed", "pull_request": repo.pull(integration.corpus)},
        "pull_request_target",
    )
    relay.run(environ=environment, session=requests.Session(), github_session=requests.Session(),
              cache_dir=integration.root / "closed-relay-cache")
    integration.drain()
    merged = selected.service.store.load_merge(1)
    assert merged is not None and merged.measured
    other_browser, other_csrf = integration.browser(102)
    live = dashboard(browser, 101, csrf)
    other_live = dashboard(other_browser, 102, other_csrf)
    assert {key: live[f"{key}_total"] for key in ("merged", "gated", "attested", "forced", "waiting")} == {
        "merged": 1, "gated": 1, "attested": 1, "forced": 0, "waiting": 0,
    }
    assert other_live["merged_total"] == 0 and other_live["waiting_total"] == 1
    assert live["demo_seeded"] is other_live["demo_seeded"] is False
    stores_before_config = {
        tenant.context.repository_id: database_rows(tenant.settings.database, STORE_TABLES)
        for tenant in integration.manager.containers()
    }

    configure_seed(integration, monkeypatch, seed_path)
    tenants = {repository_id: integration.manager.resolve(repository_id) for repository_id in (101, 102)}
    for tenant in tenants.values():
        tenant.service.clock = integration.clock
        tenant.github.installation_token()
    assert tenants[101].settings.demo_seed == seed_path
    assert tenants[102].settings.demo_seed is None
    assert stores_before_config == {
        repository_id: database_rows(tenant.settings.database, STORE_TABLES)
        for repository_id, tenant in tenants.items()
    }
    registry_before = database_rows(integration.settings.database, ("repositories",))
    models_before, calls_before = list(integration.chat.calls), len(integration.http.calls)
    seed_reads: list[Path] = []

    def tracked_seed_read(path: Path) -> Seed:
        seed_reads.append(path)
        return load_seed(path)

    monkeypatch.setattr(service_module, "load_seed", tracked_seed_read)
    browser, csrf = integration.browser(101)
    seeded = dashboard(browser, 101, csrf)
    page = browser.get("/repos/101/dashboard")
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and "Demo data" in html
    assert "app/auth/" in html and "@security-team" in html
    assert "app/orders/" in html and "@orders-team" in html
    assert {key: seeded[f"{key}_total"] for key in ("merged", "gated", "attested", "forced", "waiting")} == {
        "merged": 11, "gated": 7, "attested": 6, "forced": 1, "waiting": 0,
    }
    assert seeded["demo_seeded"] is True and seeded["attested_rate"] == pytest.approx(6 / 7)
    assert seeded["measured_total"] == live["measured_total"] == 1
    assert seeded["unmeasured_total"] == live["unmeasured_total"] == 0
    zones = {obj(zone)["zone"]: obj(zone) for zone in cast(list[object], seeded["zones"])}
    assert {key: zones["app/auth/"][key] for key in ("merged", "gated", "attested", "forced", "answerers")} == {
        "merged": 2, "gated": 2, "attested": 1, "forced": 1, "answerers": 1,
    }
    assert zones["app/auth/"]["owner"] == "@security-team" and zones["app/auth/"]["prs"] == [27, 1]
    assert zones["app/orders/"]["attested"] == 5 and zones["docs/"]["merged"] == 4
    assert "pr-author" not in json.dumps(seeded) and "actor_id" not in json.dumps(seeded)
    assert seed_reads == [seed_path] * 3
    other_browser, other_csrf = integration.browser(102)
    assert dashboard(other_browser, 102, other_csrf) == other_live
    other_page = other_browser.get("/repos/102/dashboard")
    assert other_page.status_code == 200 and "Demo data" not in other_page.get_data(as_text=True)
    assert "app/orders/" not in other_page.get_data(as_text=True)
    assert seed_reads == [seed_path] * 3

    writer, writer_csrf = integration.browser(101, actor=99)
    assert dashboard(writer, 101, writer_csrf) == seeded
    assert writer.get(f"/repos/101/receipts/{receipt_id}").status_code == 403
    assert writer.get("/repos/101/prs/1").status_code == 403
    assert writer.post("/repos/101/api/prs/1/submissions", json={},
                       headers={"X-CSRF-Token": writer_csrf}).status_code == 403
    assert other_browser.get("/repos/101/api/dashboard", headers={"X-CSRF-Token": other_csrf}).status_code == 401
    assert browser.get("/repos/101/api/dashboard", headers={"X-CSRF-Token": other_csrf}).status_code == 403
    assert stores_before_config[101]["receipts"] and stores_before_config[101]["merges"]
    assert stores_before_config[101]["outbox"] and stores_before_config[102]["snapshots"]
    assert stores_before_config == {
        repository_id: database_rows(tenant.settings.database, STORE_TABLES)
        for repository_id, tenant in tenants.items()
    }
    assert database_rows(integration.settings.database, ("repositories",)) == registry_before
    assert integration.chat.calls == models_before
    assert all(call.method == "GET" for call in integration.http.calls[calls_before:])
