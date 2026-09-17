"""Offline real Authlib, registry, GitHub-client, Flask and Store integration."""

from __future__ import annotations

import base64
import json
import sqlite3
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from typing import cast
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from flask import Response, template_rendered
from flask.testing import FlaskClient
from werkzeug.test import TestResponse

from registration_transport import Call, Integration, Repository, integration, response
from test_app_http import FakeOAuth, FakeReader, FakeVerifier, FakeGitHub, make_settings, make_snapshot, login
from test_org_dashboard import repository_demo_zone, set_repository_demo_zones
from test_org_dashboard_render import Page
from lasthuman.server.app import create_app
from lasthuman.server.auth import AuthError, normalize_next_path
from lasthuman.server.gateway import GatewayRuntimeError, TenantContainer, _rewrite_location
from lasthuman.server.organization import OrganizationView, Summary
from lasthuman.server.registration import RegistrationDeniedError
from lasthuman.server.service import BotError, BotService
from lasthuman.server.store import Store

__all__ = ["integration"]
ORG = "/repos/101/dashboard/organization"


@dataclass
class OrganizationHarness:
    runtime: Integration
    metadata_errors: dict[int, int | str] = field(default_factory=dict)
    wrong_identities: dict[int, str] = field(default_factory=dict)
    contact_errors: dict[int, int] = field(default_factory=dict)
    contact_text: dict[int, str | None] = field(default_factory=dict)

    def register(self, repo_id: int, *, measured: bool = True) -> TenantContainer:
        runtime = self.runtime
        identity = runtime.verifier.verify(runtime.http.signed_token(repo_id))
        tenant = runtime.manager.register(identity)
        tenant.service.clock = runtime.clock
        if measured:
            snapshot = tenant.service.reader.read(1)
            when = datetime.now(timezone.utc) - timedelta(hours=1)
            stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
            tenant.service.store.save_snapshot(snapshot, (), "pending", now=stamp)
            tenant.service.store.save_merge(
                pr=1, snapshot_id=snapshot.snapshot_id, merged_at=stamp,
                merge_commit_sha=snapshot.head_sha, head_sha=snapshot.head_sha, measured=True, now=stamp,
            )
        return tenant

    def browser(self, *, allowed: set[int] | None = None, actor: int = 99) -> tuple[FlaskClient, str]:
        return self.runtime.browser(101, allowed=allowed if allowed is not None else {101, 102}, actor=actor)

    def token(self, browser: FlaskClient) -> str:
        tenant = self.runtime.manager.resolve(101)
        cookie = browser.get_cookie(tenant.settings.session_cookie_name, domain="bot.example")
        assert cookie is not None
        session = tenant.app.extensions["auth"].sessions.get(cookie.value)
        assert session is not None
        return session.access_token


@pytest.fixture
def organization_runtime(integration: Integration, monkeypatch: pytest.MonkeyPatch) -> Iterator[OrganizationHarness]:
    enabled = replace(integration.settings, org_demo_enabled=True)
    integration.settings = integration.manager.settings = integration.http.settings = enabled
    harness = OrganizationHarness(integration)
    original = integration.http.github

    def metadata(prepared: requests.PreparedRequest, call: Call) -> requests.Response:
        for repo_id, repo in integration.http.repositories.items():
            prefix = "/repos/" + repo.name
            if not call.path.startswith(prefix + "/") and call.path != prefix:
                continue
            if call.token not in integration.http.users:
                break
            _actor, allowed = integration.http.users[call.token]
            if repo_id not in allowed:
                return response(prepared, 404, {"message": "Repository unavailable"})
            if call.path == prefix:
                error = harness.metadata_errors.get(repo_id)
                if error == "transport":
                    raise requests.ConnectionError("private upstream details")
                if isinstance(error, int):
                    return response(prepared, error, {"message": "private upstream details"})
                payload = repo.metadata()
                identity = harness.wrong_identities.get(repo_id)
                if identity == "repository":
                    payload["id"] = 999
                if identity == "owner":
                    payload["owner"] = {"id": 999, "login": "private-owner"}
                if identity == "boolean":
                    payload["id"] = True
                return response(prepared, 200, payload)
            if call.path == prefix + "/commits/main":
                if repo_id in harness.contact_errors:
                    return response(prepared, harness.contact_errors[repo_id], {"message": "private metadata details"})
                return response(prepared, 200, {"sha": integration.corpus.base_sha})
            if call.path.startswith(prefix + "/contents/"):
                query = parse_qs(urlsplit(prepared.url or "").query)
                assert query == {"ref": [integration.corpus.base_sha]}
                path = call.path.removeprefix(prefix + "/contents/")
                text = harness.contact_text.get(repo_id, f"/app/auth/ @acme/contact-{repo_id}\n")
                if path != "CODEOWNERS" or text is None:
                    return response(prepared, 404, {"message": "Not found"})
                return response(prepared, 200, {
                    "type": "file", "encoding": "base64", "size": len(text.encode()),
                    "content": base64.b64encode(text.encode()).decode(),
                })
            break
        return original(prepared, call)

    monkeypatch.setattr(integration.http, "github", metadata)
    yield harness
    assert not integration.chat.calls, "Organization setup or requests invoked a model"
    for repo in integration.http.repositories.values():
        assert not repo.statuses and not repo.checks and not repo.dispatches and not repo.comments


def rendered_view(browser: FlaskClient, path: str = ORG) -> tuple[TestResponse, OrganizationView]:
    captured: list[OrganizationView] = []

    def capture(_sender: object, template: object, context: dict[str, object], **_extra: object) -> None:
        del template
        if isinstance(context.get("view"), OrganizationView):
            captured.append(cast(OrganizationView, context["view"]))

    with template_rendered.connected_to(capture):
        result = browser.get(path)
    assert result.status_code == 200, result.get_data(as_text=True)
    assert len(captured) == 1
    return result, captured[0]


def database_contents(tenant: TenantContainer) -> str:
    with sqlite3.connect(tenant.settings.database) as connection:
        return "\n".join(connection.iterdump())


def test_same_owner_two_contexts_equal_pr_sha_and_module_are_separately_authorized(
    organization_runtime: OrganizationHarness,
) -> None:
    harness = organization_runtime
    one, two = harness.register(101), harness.register(102)
    first = one.service.store.load_current_snapshot(1)
    second = two.service.store.load_current_snapshot(1)
    assert first is not None and second is not None
    assert first.snapshot.head_sha == second.snapshot.head_sha
    assert first.snapshot.zones == second.snapshot.zones
    assert first.snapshot.repo_id != second.snapshot.repo_id
    browser, _csrf = harness.browser(actor=99)
    before = [database_contents(tenant) for tenant in (one, two)]
    start = len(harness.runtime.http.calls)
    result, view = rendered_view(browser)
    assert [repo.id for repo in view.repositories] == ["101", "102"]
    assert view.summary == Summary(zero=2)
    assert all(repo.modules[0].gated == 1 and repo.modules[0].answerers == 0 for repo in view.repositories)
    assert all(repo.modules[0].rate is None for repo in view.repositories)
    assert view.repositories[0].modules[0].contacts[0].url == "https://github.com/orgs/acme/teams/contact-101"
    assert view.repositories[1].dashboard_url == "/repos/102/dashboard?data=repo"
    assert result.headers["Cache-Control"] == "no-store" and "nonce-" in result.headers["Content-Security-Policy"]
    assert before == [database_contents(tenant) for tenant in (one, two)]
    new_calls = harness.runtime.http.calls[start:]
    assert all(call.method == "GET" for call in new_calls)
    for repo in (one, two):
        assert sum(call.path == f"/repos/{repo.settings.repository}/commits/main" for call in new_calls) == 1
    assert browser.get_cookie(two.settings.session_cookie_name, domain="bot.example") is None
    assert not two.app.extensions["auth"].sessions._sessions
    assert browser.get("/repos/102/prs/1").status_code == 302
    assert browser.get("/repos/102/receipts/nonexistent").status_code == 302
    _, filtered = rendered_view(browser, ORG + "?source=actual&repository=102&bucket=zero")
    assert filtered.summary == Summary(zero=1)
    assert [repo.id for repo in filtered.repository_options] == ["101", "102"]


def test_denied_foreign_unknown_selections_share_non_disclosing_404_and_no_denied_reads(
    organization_runtime: OrganizationHarness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = organization_runtime
    harness.register(101)
    denied = harness.register(102)
    harness.runtime.http.repositories[103] = Repository(103, "foreign/private", 88, 203)
    foreign = harness.register(103)
    browser, _csrf = harness.browser(allowed={101})
    for tenant in (denied, foreign):
        monkeypatch.setattr(tenant.service, "dashboard", lambda **_kwargs: pytest.fail("Denied business read"))
    start = len(harness.runtime.http.calls)
    result, view = rendered_view(browser)
    assert [repo.id for repo in view.repository_options] == ["101"]
    assert view.summary == Summary(zero=1) and not view.notice
    html = result.get_data(as_text=True)
    assert "acme/two" not in html and "foreign/private" not in html and "contact-102" not in html
    calls = harness.runtime.http.calls[start:]
    assert not any("/foreign/private" in call.path for call in calls)
    assert not any(call.path.startswith("/repos/acme/two/") for call in calls)
    failures = [browser.get(ORG, query_string={"repository": repo_id}) for repo_id in ("102", "103", "999")]
    assert {failure.status_code for failure in failures} == {404}
    assert {failure.get_data(as_text=True) for failure in failures} == {"Repository not found"}


@pytest.mark.parametrize("failure", [403, 429, 500, 503, "transport"])
def test_unconfirmed_upstream_failures_are_generic_partial_not_denials_or_names(
    organization_runtime: OrganizationHarness, monkeypatch: pytest.MonkeyPatch, failure: int | str,
) -> None:
    harness = organization_runtime
    harness.register(101)
    second = harness.register(102)
    browser, _csrf = harness.browser()
    harness.metadata_errors[102] = failure
    monkeypatch.setattr(second.service, "dashboard", lambda **_kwargs: pytest.fail("Unconfirmed business read"))
    result, view = rendered_view(browser)
    assert view.notice == "Some data is temporarily unavailable."
    assert view.summary == Summary(zero=1)
    assert [repo.id for repo in view.repository_options] == ["101"]
    assert "acme/two" not in result.get_data(as_text=True)
    assert "private upstream" not in result.get_data(as_text=True)
    assert browser.get(ORG + "?repository=102").status_code == 404


@pytest.mark.parametrize("identity", ["repository", "owner", "boolean"])
def test_numeric_repository_and_owner_identity_must_match_before_metrics(
    organization_runtime: OrganizationHarness, monkeypatch: pytest.MonkeyPatch, identity: str,
) -> None:
    harness = organization_runtime
    harness.register(101)
    second = harness.register(102)
    browser, _csrf = harness.browser()
    harness.wrong_identities[102] = identity
    monkeypatch.setattr(second.service, "dashboard", lambda **_kwargs: pytest.fail("Wrong identity business read"))
    result, view = rendered_view(browser)
    assert [repo.id for repo in view.repository_options] == ["101"] and view.notice
    assert "acme/two" not in result.get_data(as_text=True) and "private-owner" not in result.get_data(as_text=True)


def test_401_on_peer_invalidates_only_anchor_session(organization_runtime: OrganizationHarness) -> None:
    harness = organization_runtime
    one, _two = harness.register(101), harness.register(102)
    browser, _csrf = harness.browser()
    cookie = browser.get_cookie(one.settings.session_cookie_name, domain="bot.example")
    assert cookie is not None
    harness.metadata_errors[102] = 401
    result = browser.get(ORG)
    assert result.status_code == 401 and result.get_data(as_text=True) == "Sign in required"
    assert one.app.extensions["auth"].sessions.get(cookie.value) is None
    assert browser.get(ORG).status_code == 302


@pytest.mark.parametrize("error", [403, 500])
def test_current_contact_failure_preserves_coverage_without_historical_fallback(
    organization_runtime: OrganizationHarness, error: int,
) -> None:
    harness = organization_runtime
    harness.register(101)
    browser, _csrf = harness.browser(allowed={101})
    harness.contact_errors[101] = error
    result, view = rendered_view(browser)
    module = view.repositories[0].modules[0]
    assert module.gated == 1 and module.answerers == 0
    assert module.contacts == () and module.contact_status == "Contact unavailable"
    assert "@security-team" not in result.get_data(as_text=True)


def test_missing_current_codeowners_is_not_declared(organization_runtime: OrganizationHarness) -> None:
    harness = organization_runtime
    harness.register(101)
    browser, _csrf = harness.browser(allowed={101})
    harness.contact_text[101] = None
    _result, view = rendered_view(browser)
    assert view.repositories[0].modules[0].contact_status == "Not declared"


def test_ownerless_codeowners_exclusion_does_not_revive_earlier_contacts(
    organization_runtime: OrganizationHarness,
) -> None:
    harness = organization_runtime
    harness.register(101)
    browser, _csrf = harness.browser(allowed={101})
    harness.contact_text[101] = "* @acme/global\n/app/auth/\n"
    _result, view = rendered_view(browser)
    module = view.repositories[0].modules[0]
    assert module.contacts == () and module.contact_status == "Not declared"


def test_current_contact_root_rule_is_not_cleared_by_an_unrelated_root_rule(
    organization_runtime: OrganizationHarness,
) -> None:
    harness = organization_runtime
    harness.register(101)
    browser, _csrf = harness.browser(allowed={101})
    harness.contact_text[101] = "/app/auth/ @acme/security\n/docs/ @acme/docs\n"
    _result, view = rendered_view(browser)
    assert view.repositories[0].modules[0].contacts[0].label == "@acme/security"


def test_unauthorized_cold_peer_never_initializes_its_business_database(
    organization_runtime: OrganizationHarness,
) -> None:
    harness = organization_runtime
    harness.register(101)
    runtime = harness.runtime
    identity = runtime.verifier.verify(runtime.http.signed_token(102))
    peer = runtime.registry.register(identity)
    database = runtime.settings.tenant_settings(peer, prepare=False).database
    assert not database.exists()
    browser, _csrf = harness.browser(allowed={101})

    _result, view = rendered_view(browser)

    assert [repository.id for repository in view.repositories] == ["101"]
    assert not database.exists()
    assert [container.context.repository_id for container in runtime.manager.containers()] == [101]


def test_authorized_metric_failure_is_visible_only_in_its_confirmed_group(
    organization_runtime: OrganizationHarness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = organization_runtime
    harness.register(101)
    second = harness.register(102)
    browser, _csrf = harness.browser()

    def fail(**_kwargs: object) -> dict[str, object]:
        raise BotError("private database details", code="unavailable", status_code=503)

    monkeypatch.setattr(second.service, "dashboard", fail)
    result, view = rendered_view(browser)
    assert view.repositories[1].status == "Data unavailable"
    assert view.repositories[1].modules == ()
    assert view.summary == Summary(zero=1)
    assert "private database" not in result.get_data(as_text=True)


@pytest.mark.parametrize("revocation", ["permission", "generation", "retirement"])
def test_revocation_during_real_read_discards_peer_projection(
    organization_runtime: OrganizationHarness, monkeypatch: pytest.MonkeyPatch, revocation: str,
) -> None:
    harness = organization_runtime
    harness.register(101)
    second = harness.register(102)
    browser, _csrf = harness.browser()
    token = harness.token(browser)
    original = second.service.dashboard

    def revoke(**kwargs: object) -> dict[str, object]:
        result = original(**kwargs)
        if revocation == "permission":
            actor, _allowed = harness.runtime.http.users[token]
            harness.runtime.http.users[token] = (actor, {101})
        else:
            repo = harness.runtime.http.repositories[102]
            if revocation == "generation":
                repo.installation_id = 302
                harness.runtime.advance(61)
                current = harness.runtime.registry.register(
                    harness.runtime.verifier.verify(harness.runtime.http.signed_token(102)),
                )
                assert current.generation != second.context.generation
            else:
                repo.removed = True
                harness.runtime.advance(61)
                with pytest.raises(RegistrationDeniedError):
                    harness.runtime.registry.resolve(102)
        return result

    monkeypatch.setattr(second.service, "dashboard", revoke)
    result, view = rendered_view(browser)
    assert [repo.id for repo in view.repository_options] == ["101"]
    assert view.summary == Summary(zero=1) and "acme/two" not in result.get_data(as_text=True)


def test_anchor_retirement_during_peer_read_never_returns_aggregate(
    organization_runtime: OrganizationHarness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = organization_runtime
    harness.register(101)
    second = harness.register(102)
    browser, _csrf = harness.browser()
    original = second.service.dashboard

    def retire_anchor(**kwargs: object) -> dict[str, object]:
        result = original(**kwargs)
        harness.runtime.http.repositories[101].removed = True
        harness.runtime.advance(61)
        with pytest.raises(RegistrationDeniedError):
            harness.runtime.registry.resolve(101)
        return result

    monkeypatch.setattr(second.service, "dashboard", retire_anchor)
    result = browser.get(ORG)
    assert result.status_code != 200
    assert "acme/two" not in result.get_data(as_text=True)


def test_anchor_user_permission_revoked_during_peer_read_invalidates_login(
    organization_runtime: OrganizationHarness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = organization_runtime
    one, second = harness.register(101), harness.register(102)
    browser, _csrf = harness.browser()
    token = harness.token(browser)
    cookie = browser.get_cookie(one.settings.session_cookie_name, domain="bot.example")
    assert cookie is not None
    original = second.service.dashboard

    def revoke_anchor(**kwargs: object) -> dict[str, object]:
        result = original(**kwargs)
        actor, _allowed = harness.runtime.http.users[token]
        harness.runtime.http.users[token] = (actor, {102})
        return result

    monkeypatch.setattr(second.service, "dashboard", revoke_anchor)
    result = browser.get(ORG)
    assert result.status_code == 401
    assert result.get_data(as_text=True) == "Sign in required"
    assert one.app.extensions["auth"].sessions.get(cookie.value) is None


def test_source_isolation_legacy_seed_and_org_demo_repo_demo_then_actual_journey(
    organization_runtime: OrganizationHarness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = organization_runtime
    one, two = harness.register(101), harness.register(102)
    seed = harness.runtime.root / "legacy-seed.json"
    seed.write_text(json.dumps({
        "zones": [{"zone": "legacy-only/", "merged": 20, "gated": 20, "attested": 20, "answerers": 10}],
        "totals": {"merged": 20, "gated": 20, "attested": 20},
    }), encoding="utf-8")
    for tenant in (one, two):
        tenant.service.settings = replace(tenant.service.settings, demo_seed=seed)
    browser, csrf = harness.browser()
    legacy = browser.get("/repos/101/api/dashboard", headers={"X-CSRF-Token": csrf}).get_json()
    assert legacy["demo_seeded"] is True and legacy["gated_total"] == 21
    legacy_html = browser.get("/repos/101/dashboard").get_data(as_text=True)
    assert "legacy-only/" in legacy_html
    _, actual = rendered_view(browser)
    assert actual.summary == Summary(zero=2)
    assert all(module.zone != "legacy-only/" for repo in actual.repositories for module in repo.modules)
    before = [database_contents(tenant) for tenant in (one, two)]
    original_one = one.service.dashboard
    original_two = two.service.dashboard
    original_merges = one.service.store.load_merges_since
    original_inventory = harness.runtime.registry.list_registered
    monkeypatch.setattr(one.service, "dashboard", lambda **_kwargs: pytest.fail("Demo read anchor business records"))
    monkeypatch.setattr(two.service, "dashboard", lambda **_kwargs: pytest.fail("Demo read peer business records"))
    monkeypatch.setattr(
        one.service.store, "load_merges_since", lambda **_kwargs: pytest.fail("Demo read actual merge history"),
    )
    monkeypatch.setattr(
        harness.runtime.registry, "list_registered", lambda: pytest.fail("Demo enumerated actual repository inventory"),
    )
    set_repository_demo_zones(monkeypatch, [
        repository_demo_zone(zone="ledger/", owner="@sample-organization/platform", answerers=3, forced=2),
        repository_demo_zone(),
    ])
    start = len(harness.runtime.http.calls)
    _, demo = rendered_view(browser, ORG + "?source=demo&bucket=many")
    assert demo.summary == Summary(zero=1, one=1, many=5)
    assert all(repo.dashboard_url == "/repos/101/dashboard?data=demo" for repo in demo.repositories)
    assert not any(call.path.startswith("/repos/acme/two") for call in harness.runtime.http.calls[start:])
    destination = browser.get(demo.repositories[0].dashboard_url)
    assert destination.status_code == 200
    html = destination.get_data(as_text=True)
    assert one.settings.repository in html and "Demo data" in html
    assert "sample-payments" not in html and "legacy-only/" not in html
    assert "/pull/" not in html and "/receipts/" not in html
    assert "@sample-organization/payments" in html and "@sample-organization/platform" in html
    assert html.index("#907") < html.index("#903") < html.index("#901")
    assert "One more verified change in this zone" in html
    assert "Verify 2 exceptions after the fact" in html
    payload = browser.get("/repos/101/api/dashboard?data=demo", headers={"X-CSRF-Token": csrf}).get_json()
    assert payload["source"] == "demo" and payload["repo"] == one.settings.repository
    assert [(row["zone"], row["owner"], row["prs"]) for row in payload["zones"]] == [
        ("auth/", "@sample-organization/payments", [907, 903, 901]),
        ("ledger/", "@sample-organization/platform", [907, 903, 901]),
    ]
    assert payload["actions"] == [
        {"zone": "auth/", "owner": "@sample-organization/payments",
         "action": "One more verified change in this zone", "from": 1, "to": 2},
        {"zone": "ledger/", "owner": "@sample-organization/platform",
         "action": "Verify 2 exceptions after the fact", "from": 3, "to": 4},
    ]
    assert before == [database_contents(tenant) for tenant in (one, two)]
    monkeypatch.setattr(one.service, "dashboard", original_one)
    monkeypatch.setattr(two.service, "dashboard", original_two)
    monkeypatch.setattr(one.service.store, "load_merges_since", original_merges)
    monkeypatch.setattr(
        "lasthuman.server.organization._demo_fixture", lambda: pytest.fail("Actual read the demo fixture"),
    )
    actual_payload = browser.get("/repos/101/api/dashboard?data=repo", headers={"X-CSRF-Token": csrf}).get_json()
    assert actual_payload["source"] == "repo" and actual_payload["gated_total"] == 1
    assert actual_payload["demo_seeded"] is False
    assert {row["zone"] for row in actual_payload["zones"]} == {"app/auth/"}
    assert browser.get("/repos/101/dashboard?data=repo").status_code == 200
    monkeypatch.setattr(harness.runtime.registry, "list_registered", original_inventory)
    _, reset = rendered_view(browser, demo.links["actual"])
    assert reset.source == "actual" and reset.selected_repository == reset.selected_bucket == "all"
    _, selected = rendered_view(browser, ORG + "?source=actual&repository=101&bucket=zero")
    assert selected.summary == Summary(zero=1) and [repo.id for repo in selected.repositories] == ["101"]


def test_bundled_demo_routes_preserve_six_zone_payload_and_org_filters_without_actual_reads(
    organization_runtime: OrganizationHarness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = organization_runtime
    tenants = (harness.register(101), harness.register(102))
    browser, csrf = harness.browser()
    before = [database_contents(tenant) for tenant in tenants]
    images = [tenant.settings.database.read_bytes() for tenant in tenants]
    for tenant in tenants:
        monkeypatch.setattr(tenant.service, "dashboard", lambda **_kwargs: pytest.fail("Demo read Actual metrics"))
        monkeypatch.setattr(
            tenant.service.store, "load_merges_since", lambda **_kwargs: pytest.fail("Demo read Actual merges"),
        )
    monkeypatch.setattr(
        harness.runtime.registry, "list_registered", lambda: pytest.fail("Demo enumerated Actual repositories"),
    )
    start = len(harness.runtime.http.calls)
    result, demo = rendered_view(browser, ORG + "?source=demo")
    page = Page(result.get_data(as_text=True))
    assert demo.summary == Summary(zero=1, one=1, many=5)
    assert len(demo.repositories) == 4 and sum(len(repo.modules) for repo in demo.repositories) == 8
    assert [card.words for card in page.by_class("a", "summary-card")] == [
        "0 confirmed authors 1 module", "1 confirmed author 1 module", "2+ confirmed authors 5 modules",
    ]
    for bucket, expected in (
        ("zero", [("sample-orders", "shipping/")]),
        ("one", [("sample-identity", "auth/")]),
        ("many", [
            ("sample-identity", "session/"), ("sample-orders", "checkout/"),
            ("sample-payments", "billing/"), ("sample-payments", "ledger/"), ("sample-platform", "jobs/"),
        ]),
    ):
        filtered_html, filtered = rendered_view(browser, demo.links[bucket])
        assert filtered.summary == demo.summary
        assert [(repo.id, module.zone) for repo in filtered.repositories for module in repo.modules] == expected
        assert len(Page(filtered_html.get_data(as_text=True)).select("th", scope="row")) == len(expected)
    for repository, summary, zones in (
        ("sample-payments", Summary(many=2), []),
        ("sample-identity", Summary(one=1, many=1), ["auth/"]),
    ):
        _, filtered = rendered_view(browser, ORG + f"?source=demo&repository={repository}&bucket=one")
        assert filtered.summary == summary and len(filtered.repository_options) == 4
        assert [repo.id for repo in filtered.repositories] == [repository]
        assert [module.zone for module in filtered.repositories[0].modules] == zones
    _, platform = rendered_view(browser, ORG + "?source=demo&repository=sample-platform")
    assert [(module.zone, module.status) for module in platform.repositories[0].modules] == [
        ("events/", "Collection delayed"), ("jobs/", "Sample too small"),
    ]
    destination = browser.get("/repos/101/dashboard?data=demo")
    assert destination.status_code == 200
    html = destination.get_data(as_text=True)
    repo_page = Page(html)
    assert [span.words for span in repo_page.by_class("span", "v")] == ["21/ 28 gated 75%", "3", "0"]
    assert len(repo_page.by_class("td", "zone")) == 6
    assert [span.words for span in repo_page.by_class("span", "low")] == ["Sample too small"]
    assert [cell.words for cell in repo_page.select("td")[2::7]] == ["1", "2", "3", "4", "—", "—"]
    visible_text = repo_page.select("body")[0].words
    assert all(value in visible_text for value in ("83%", "86%", "20%"))
    assert "100%" not in visible_text and "/pull/" not in html and "/receipts/" not in html
    assert repo_page.by_class("td", "prs")[3].words == "#48 #45 #41 #38 #34 #30"
    api_response = browser.get("/repos/101/api/dashboard?data=demo", headers={"X-CSRF-Token": csrf})
    assert api_response.status_code == 200
    payload = api_response.get_json()
    assert payload["source"] == "demo" and payload["repo"] == tenants[0].settings.repository
    assert payload["zone_count"] == 6 and payload["zero_answerer_zones"] == 0
    assert payload["attested_rate"] == 0.75
    assert (payload["merged_total"], payload["gated_total"], payload["attested_total"],
            payload["forced_total"], payload["waiting_total"]) == (34, 28, 21, 3, 1)
    assert (payload["measured_total"], payload["unmeasured_total"]) == (34, 0)
    for field in ("merged", "gated", "attested", "forced"):
        assert payload[f"{field}_total"] == sum(row[field] or 0 for row in payload["zones"])
    assert [row["zone"] for row in payload["zones"]] == [
        "sample-app/app/auth/", "sample-app/migrations/", "sample-app/app/orders/",
        "sample-app/app/ledger/", "docs/", ".github/workflows/",
    ]
    assert [row["rate"] for row in payload["zones"]] == [0.2, None, 6 / 7, 10 / 12, None, None]
    assert payload["zones"][1]["low_sample"] is True
    for row in payload["zones"][-2:]:
        assert row["gated"] is None and row["attested"] is None and row["answerers"] is None
        assert row["prs"] == [] and row["forced"] == 0
    assert before == [database_contents(tenant) for tenant in tenants]
    assert images == [tenant.settings.database.read_bytes() for tenant in tenants]
    assert not any(call.path.startswith("/repos/acme/two") for call in harness.runtime.http.calls[start:])


def test_actual_captures_one_utc_as_of_for_both_real_aggregations(
    organization_runtime: OrganizationHarness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = organization_runtime
    one, two = harness.register(101), harness.register(102)
    browser, _csrf = harness.browser()
    calls: list[dict[str, object]] = []

    def wrap(original: Callable[..., dict[str, object]]) -> Callable[..., dict[str, object]]:
        def dashboard(**kwargs: object) -> dict[str, object]:
            calls.append(kwargs)
            return original(**kwargs)
        return dashboard

    for tenant in (one, two):
        monkeypatch.setattr(tenant.service, "dashboard", wrap(tenant.service.dashboard))
    _, view = rendered_view(browser)
    assert len(calls) == 2 and calls[0] == calls[1]
    assert calls[0] == {"days": 30, "include_seed": False, "as_of": view.as_of}
    assert view.as_of.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "query",
    ["source=bad", "source=actual&source=demo", "repository=101&repository=102",
     "bucket=one&bucket=zero", "owner_id=77", "data=demo", "days=7", "bucket=unknown"],
)
def test_invalid_org_queries_are_400_before_authentication(
    organization_runtime: OrganizationHarness, query: str,
) -> None:
    organization_runtime.register(101, measured=False)
    result = organization_runtime.runtime.client().get(ORG + "?" + query)
    assert result.status_code == 400 and "Location" not in result.headers


def test_disabled_demo_and_source_scope_reset_never_fallback(
    organization_runtime: OrganizationHarness,
) -> None:
    harness = organization_runtime
    disabled = replace(harness.runtime.settings, org_demo_enabled=False)
    harness.runtime.manager.settings = disabled
    harness.register(101)
    browser, csrf = harness.browser(allowed={101})
    for path in (ORG + "?source=demo", "/repos/101/dashboard?data=demo"):
        result = browser.get(path)
        assert result.status_code == 404 and result.get_data(as_text=True) == "Demo data is unavailable"
    assert browser.get("/repos/101/api/dashboard?data=demo", headers={"X-CSRF-Token": csrf}).status_code == 404
    assert browser.get("/repos/101/api/dashboard?data=repo").status_code == 403
    assert browser.get("/repos/101/dashboard?data=repo&repository=101").status_code == 400
    assert browser.get(ORG + "?source=actual&repository=sample-payments").status_code == 404
    _, actual = rendered_view(browser, ORG + "?source=actual&repository=101&bucket=zero")
    assert parse_qs(urlsplit(actual.links["demo"]).query) == {
        "source": ["demo"], "repository": ["all"], "bucket": ["all"],
    }


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard?data=demo",
        "/dashboard?data=repo&days=7",
        "/dashboard/organization?source=demo&repository=sample-payments&bucket=one",
        "/repos/101/dashboard/organization?source=actual&repository=102&bucket=many",
    ],
)
def test_real_pkce_callback_preserves_only_exact_supported_source_paths(
    organization_runtime: OrganizationHarness, path: str,
) -> None:
    harness = organization_runtime
    harness.register(101, measured=False)
    browser = harness.runtime.client()
    begin = browser.get("/repos/101/auth/github", query_string={"next": path})
    assert begin.status_code == 302
    state = parse_qs(urlsplit(begin.location).query)["state"][0]
    code = harness.runtime.http.oauth_code(begin.location, allowed={101})
    callback = browser.get("/auth/github/callback", query_string={"state": state, "code": code})
    expected = path if path.startswith("/repos/101/") else "/repos/101" + path
    assert callback.status_code == 302 and callback.location == expected


def test_unauthenticated_direct_org_and_repo_demo_links_preserve_source(
    organization_runtime: OrganizationHarness,
) -> None:
    harness = organization_runtime
    harness.register(101, measured=False)
    browser = harness.runtime.client()
    for path in (ORG + "?source=demo&repository=sample-payments&bucket=one", "/repos/101/dashboard?data=demo"):
        redirect = browser.get(path)
        assert redirect.status_code == 302
        next_path = parse_qs(urlsplit(redirect.location).query)["next"][0]
        assert next_path == path.removeprefix("/repos/101")


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard/organization/extra?source=demo", "/dashboard/organization?source=bad",
        "/dashboard/organization?source=demo&source=actual", "/dashboard?data=repo&data=demo",
        "/dashboard?source=demo", "/dashboard?repository=101",
        "/dashboard/organization?owner_id=999", "/prs/1?data=demo", "/receipts/abc?data=repo",
        "/repos/102/dashboard?data=demo", "https://evil.example/dashboard?data=demo",
        "//evil.example/dashboard?data=demo", "/dashboard?data=demo#evil",
        "/dashboard/organization?source=demo%26repository%3D999", "/dashboard?data=demo\\evil",
        "/dashboard/organization?source=demo&&bucket=one", "/dashboard/organization?source=%0ademo",
    ],
)
def test_oauth_return_validation_rejects_arbitrary_query_redirects(path: str) -> None:
    with pytest.raises(AuthError) as failure:
        normalize_next_path(path, path_prefix="/repos/101")
    assert failure.value.status_code == 400


def test_gateway_rewrites_only_exact_new_organization_path() -> None:
    result = Response(status=302, headers={"Location": "/dashboard/organization?source=demo&bucket=one"})
    _rewrite_location(result, "/repos/101")
    assert result.location == "/repos/101/dashboard/organization?source=demo&bucket=one"
    with pytest.raises(GatewayRuntimeError):
        _rewrite_location(Response(status=302, headers={"Location": "/dashboard/organization/evil"}), "/repos/101")


def test_peer_aggregate_lock_does_not_hold_anchor_or_unrelated_tenant(
    organization_runtime: OrganizationHarness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = organization_runtime
    one, two = harness.register(101), harness.register(102)
    browser, _csrf = harness.browser()
    independent, _other_csrf = harness.browser(allowed={101})
    entered, release = Event(), Event()
    original = two.service.dashboard

    def blocked(**kwargs: object) -> dict[str, object]:
        entered.set()
        assert release.wait(timeout=10)
        return original(**kwargs)

    monkeypatch.setattr(two.service, "dashboard", blocked)
    with ThreadPoolExecutor(max_workers=2) as executor:
        org_request = executor.submit(browser.get, ORG)
        try:
            assert entered.wait(timeout=5)
            anchor_request = executor.submit(independent.get, "/repos/101/dashboard?data=repo")
            assert anchor_request.result(timeout=5).status_code == 200
            assert one._lifecycle_lock.acquire(timeout=1)
            one._lifecycle_lock.release()
        finally:
            release.set()
        assert org_request.result(timeout=5).status_code == 200


def test_fixed_mode_uses_only_configured_repository_and_sample_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(make_settings(tmp_path), org_demo_enabled=True)
    snapshot = make_snapshot()
    github = FakeGitHub(settings, snapshot)
    monkeypatch.setattr(github, "current_codeowners", lambda *_args, **_kwargs: {}, raising=False)
    service = BotService(settings, github, FakeReader(snapshot), Store(settings.database))
    app = create_app(
        settings, service=service, github=github, oauth=FakeOAuth(),
        verifier=FakeVerifier(settings), start_worker=False,
    )
    try:
        browser = app.test_client()
        login(browser)
        _, actual = rendered_view(browser, "/dashboard/organization")
        assert [repo.id for repo in actual.repository_options] == [str(settings.repository_id)]
        assert actual.repositories[0].status == "No measured data"
        _, demo = rendered_view(browser, "/dashboard/organization?source=demo")
        assert {repo.dashboard_url for repo in demo.repositories} == {"/dashboard?data=demo"}
        with monkeypatch.context() as demo_only:
            set_repository_demo_zones(demo_only, [repository_demo_zone(forced=1)])
            demo_only.setattr(
                service, "dashboard", lambda **_kwargs: pytest.fail("Demo invoked Actual dashboard provider"),
            )
            demo_only.setattr(
                service.store, "load_merges_since", lambda **_kwargs: pytest.fail("Demo read Actual store"),
            )
            assert browser.get("/dashboard?data=demo").status_code == 200
            cookie = browser.get_cookie(settings.session_cookie_name)
            assert cookie is not None
            session = app.extensions["auth"].sessions.get(cookie.value)
            assert session is not None
            response = browser.get(
                "/api/dashboard?data=demo", headers={"X-CSRF-Token": session.csrf_token},
            )
            assert response.status_code == 200
            payload = response.get_json()
            assert payload["zones"][0]["owner"] == "@sample-organization/payments"
            assert payload["zones"][0]["prs"] == [907, 903, 901]
            assert payload["actions"] == [{
                "zone": "auth/", "owner": "@sample-organization/payments",
                "action": "Verify 1 exception after the fact", "from": 1, "to": 2,
            }]
        assert browser.get("/dashboard?data=repo").status_code == 200
    finally:
        app.extensions["runtime"].shutdown()
