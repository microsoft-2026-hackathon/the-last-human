"""AC11: real gateway, RSA OIDC, GitHub transport, SQLite and five relay stages."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from typing import cast

import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import rsa

from registration_transport import (
    CORE, HOLD, PASS, Integration, integration, obj,
)
from lasthuman.server import relay
from lasthuman.server.github import GitHubClient
from lasthuman.server.snapshot import SnapshotReader

# Importing the fixture deliberately makes it available without a global conftest.
__all__ = ["integration"]


def dump(path: Path) -> str:
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
        return "\n".join(connection.iterdump())


def registry_row(harness: Integration, repo_id: int) -> sqlite3.Row:
    with sqlite3.connect(f"{harness.settings.database.as_uri()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM repositories WHERE repository_id = ?", (repo_id,)).fetchone()
    assert row is not None
    return row


@pytest.mark.parametrize("checks", [False, True], ids=["core-grants-only", "optional-checks"])
def test_first_event_private_hold_pass_five_steps_publication_and_merge(
    integration: Integration, caplog: pytest.LogCaptureFixture, checks: bool,
) -> None:
    if checks:
        integration.settings = replace(integration.settings, checks_enabled=True)
        integration.http.settings = integration.settings
        for repo in integration.http.repositories.values():
            repo.permissions["checks"] = "write"
        integration.restart()
    assert not integration.registry.list_registered()
    assert not integration.manager.containers()
    integration.open_event(101)
    tenant = integration.manager.resolve(101)
    assert isinstance(tenant.github, GitHubClient)
    assert isinstance(tenant.service.reader, SnapshotReader)
    stored = tenant.service.store.load_current_snapshot(1)
    assert stored is not None and stored.question_count == 2
    assert stored.snapshot.risk.triggered and stored.snapshot.risk.reasons
    assert not stored.snapshot.structure.is_empty()
    assert integration.chat.anchors == {hunk.anchor for hunk in stored.snapshot.risk.top_hunks}
    assert integration.http.jwks_fetches > 0
    assert tenant.settings.oidc_audience == "acme/one" and tenant.settings.tenant_generation == 1
    assert tenant.runtime.scheduler_state == "manual"
    browser, csrf = integration.browser(101)
    _, hold = integration.submit(browser, csrf, 101, HOLD, "hold-request")
    assert hold["state"] == "needs_followup", hold
    assert len(cast(list[object], hold["feedback"])) == 2
    assert "receipt_id" not in hold and integration.chat.calls.count("hold") == 2
    assert HOLD not in dump(tenant.settings.database)
    assert tenant.service.store.load_receipts_for_snapshot(stored.snapshot.snapshot_id) == ()
    repo = integration.http.repositories[101]
    assert not any(status["state"] == "success" for status in repo.statuses)
    accepted, passed = integration.submit(browser, csrf, 101, PASS, "pass-request")
    assert passed["state"] == "awaiting_verification", passed
    assert integration.chat.calls.count("pass") == 2
    receipt_id = str(passed["receipt_id"])
    before = tenant.service.store.load_receipt(receipt_id)
    assert before is not None and not before.verified
    assert before.repo_id == 101 and before.head_sha == integration.corpus.head_sha
    assert all(answer.text == PASS for answer in before.successful_answers)
    assert browser.get(f"/repos/101/receipts/{receipt_id}").status_code == 200
    assert not any(status["state"] == "success" for status in repo.statuses)
    integration.five_steps(101, receipt_id)
    final = browser.get(str(accepted["url"]), headers={"X-CSRF-Token": csrf}).get_json()
    assert final["state"] == "verified"
    after = tenant.service.store.load_receipt(receipt_id)
    assert after is not None and after.verified and after.successful_answers == before.successful_answers
    if checks:
        assert any(check.get("conclusion") == "success" for check in repo.checks)
        assert any(call.body.get("permissions") == {"checks": "write"} for call in integration.http.calls)
    else:
        assert not repo.checks
        assert all(call.body.get("permissions") != {"checks": "write"} for call in integration.http.calls)
    repo.merged = True
    environment = integration.environment(
        101, {"repository": repo.metadata(), "action": "closed", "pull_request": repo.pull(integration.corpus)},
        "pull_request_target",
    )
    relay.run(environ=environment, session=requests.Session(), github_session=requests.Session(),
              cache_dir=integration.root / "closed-relay-cache")
    integration.drain()
    merged = tenant.service.store.load_merge(1)
    assert merged is not None and merged.measured
    dashboard = browser.get("/repos/101/api/dashboard", headers={"X-CSRF-Token": csrf})
    assert dashboard.status_code == 200
    coverage = obj(dashboard.get_json())
    assert coverage["merged_total"] == coverage["attested_total"] == 1
    assert all("actor_id" not in obj(zone) for zone in cast(list[object], coverage["zones"]))
    assert "pr-author" not in json.dumps(coverage)
    assert HOLD not in dump(tenant.settings.database)
    assert HOLD not in caplog.text and PASS not in caplog.text
    public = json.dumps([repo.comments, repo.statuses, repo.checks, repo.dispatches])
    assert HOLD not in public and PASS not in public


def test_identical_pr_sha_author_request_ids_are_isolated_and_writer_cannot_answer(integration: Integration) -> None:
    jobs = {repo_id: integration.open_event(repo_id) for repo_id in (101, 102)}
    first, csrf1 = integration.browser(101)
    second, csrf2 = integration.browser(102)
    job1, result1 = integration.submit(first, csrf1, 101, PASS, "identical-request-id")
    job2, result2 = integration.submit(second, csrf2, 102, PASS, "identical-request-id")
    assert result1["state"] == result2["state"] == "awaiting_verification"
    receipts = (str(result1["receipt_id"]), str(result2["receipt_id"]))
    assert receipts[0] != receipts[1]
    one, two = (integration.manager.resolve(repo_id) for repo_id in (101, 102))
    snapshot1, snapshot2 = (tenant.service.store.load_current_snapshot(1) for tenant in (one, two))
    assert snapshot1 is not None and snapshot2 is not None
    assert snapshot1.snapshot.snapshot_id != snapshot2.snapshot.snapshot_id
    assert snapshot1.snapshot.head_sha == snapshot2.snapshot.head_sha
    assert snapshot1.snapshot.author_id == snapshot2.snapshot.author_id == 7
    assert one.settings.database != two.settings.database
    assert one.settings.session_cookie_name != two.settings.session_cookie_name
    assert two.service.store.load_receipt(receipts[0]) is None
    assert one.service.store.load_receipt(receipts[1]) is None
    assert second.get(f"/repos/102/receipts/{receipts[0]}").status_code == 404
    assert job1["url"] != job2["url"]
    local = second.get(f"/repos/102/api/submissions/{job1['job_id']}", headers={"X-CSRF-Token": csrf2})
    if job1["job_id"] == job2["job_id"]:
        assert local.status_code == 200 and local.get_json()["receipt_id"] == receipts[1]
    else:
        assert local.status_code == 410
    unique, _ = integration.submit(first, csrf1, 101, PASS, "only-first-repository")
    assert second.get(f"/repos/102/api/submissions/{unique['job_id']}",
                      headers={"X-CSRF-Token": csrf2}).status_code == 410
    assert first.get(str(job2["url"]), headers={"X-CSRF-Token": csrf1}).status_code == 401
    assert second.get(str(jobs[101]["url"]), headers=integration.headers(102)).status_code == 404
    assert second.get(f"/api/actions/receipts/{receipts[0]}/publication",
                      headers=integration.headers(102, event="workflow_dispatch")).status_code == 404
    assert second.get(f"/repos/101/api/actions/receipts/{receipts[0]}/publication",
                      headers=integration.headers(102, event="workflow_dispatch")).status_code == 403
    foreign = integration.answers(second, 102, PASS, "copied-snapshot")
    foreign["snapshot_id"] = snapshot1.snapshot.snapshot_id
    assert second.post("/repos/102/api/prs/1/submissions", json=foreign,
                       headers={"X-CSRF-Token": csrf2}).status_code == 409
    assert second.post("/repos/102/api/prs/1/submissions",
                       json=integration.answers(second, 102, PASS, "wrong-csrf"),
                       headers={"X-CSRF-Token": csrf1}).status_code == 403
    writer, writer_csrf = integration.browser(101, actor=99)
    assert writer.get("/repos/101/dashboard").status_code == 200
    assert writer.get("/repos/101/prs/1").status_code == 403
    assert writer.get(f"/repos/101/receipts/{receipts[0]}").status_code == 403
    assert writer.get(str(job1["url"]), headers={"X-CSRF-Token": writer_csrf}).status_code == 403
    payload = integration.answers(first, 101, PASS, "writer")
    model_before = list(integration.chat.calls)
    assert writer.post("/repos/101/api/prs/1/submissions", json=payload,
                       headers={"X-CSRF-Token": writer_csrf}).status_code == 403
    assert integration.chat.calls == model_before
    assert writer.get("/repos/102/dashboard").status_code == 302
    integration.five_steps(101, receipts[0])
    other = two.service.store.load_receipt(receipts[1])
    assert other is not None and not other.verified
    integration.five_steps(102, receipts[1])
    assert "/repos/101/" in str(integration.http.repositories[101].statuses[0]["target_url"])
    assert "/repos/102/" in str(integration.http.repositories[102].statuses[0]["target_url"])


@pytest.mark.parametrize("failure", [
    "signature", "audience", "workflow", "issuer", "expired", "future", "repository-id", "body",
])
def test_untrusted_first_event_has_zero_discovery_model_and_tenant_writes(
    integration: Integration, failure: str,
) -> None:
    body = {"repository_id": 101, "pr": 1, "action": "opened",
            "binding": {"repository_id": 101, "pr": 1, "head_sha": integration.corpus.head_sha,
                        "base_sha": integration.corpus.base_sha, "snapshot_id": "1" * 64,
                        "policy_version": "2" * 64, "score": 60, "triggered": True}}
    overrides = {
        "audience": {"aud": "foreign/repo"},
        "workflow": {"workflow_ref": "acme/one/.github/workflows/untrusted.yml@refs/heads/main"},
        "issuer": {"iss": "https://foreign.example"}, "expired": {"exp": 1},
        "future": {"nbf": 4_000_000_000}, "repository-id": {"repository_id": "102"},
    }.get(failure, {})
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048) if failure == "signature" else None
    if failure == "body":
        body["repository_id"] = 102
    token = integration.http.signed_token(101, claims=overrides, key=key)
    result = integration.client().post("/api/actions/events", json=body, headers={"Authorization": "Bearer " + token})
    assert result.status_code == (400 if failure in {"body", "repository-id"} else 401)
    assert not integration.http.calls and not integration.chat.calls
    assert not integration.registry.list_registered() and not integration.manager.containers()
    assert not (integration.settings.state_root / "repos").exists()


@pytest.mark.parametrize("failure", ["owner", "scope", "opt-in", "core-grant", "issued-grant", "expired-token"])
def test_discovery_denial_does_not_create_tenant(integration: Integration, failure: str) -> None:
    body = integration.body(101)
    integration.http.calls.clear()
    repo = integration.http.repositories[101]
    if failure == "owner":
        integration.settings = replace(integration.settings, allowed_owner_ids=frozenset({88}))
        integration.restart()
    elif failure == "scope":
        repo.extra_scope = True
    elif failure == "opt-in":
        repo.opted_in = False
    elif failure == "core-grant":
        repo.permissions["statuses"] = "read"
    elif failure == "issued-grant":
        repo.token_permissions = {**CORE, "statuses": "read"}
    else:
        repo.token_expired = True
    result = integration.client().post("/api/actions/events", json=body, headers=integration.headers(101))
    assert result.status_code == (503 if failure == "expired-token" else 403), result.get_data(as_text=True)
    assert not integration.registry.list_registered() and not integration.manager.containers()
    assert not integration.chat.calls
    assert not (integration.settings.state_root / "repos").exists()
    assert not repo.statuses and not repo.comments and not repo.dispatches
    if failure == "owner":
        assert not integration.http.calls
    if failure == "scope":
        calls = [call for call in integration.http.calls if call.method == "POST"]
        assert len(calls) == 1
        assert calls[0].body == {"repository_ids": [101], "permissions": CORE}


@pytest.mark.parametrize("path", [
    "/api/actions/receipts/unknown", "/api/actions/receipts/unknown/publication",
    "/api/actions/jobs/unknown", "/repos/101/receipts/unknown",
    "/repos/101/api/actions/jobs/unknown", "/repos/101/dashboard",
])
def test_unknown_scoped_ids_cannot_register(integration: Integration, path: str) -> None:
    result = integration.client().get(path, headers=integration.headers(101, event="workflow_dispatch"))
    assert result.status_code == 404
    assert not integration.registry.list_registered()
    assert not integration.http.calls and not integration.chat.calls


@pytest.mark.parametrize("repo_ids", [(101, 101), (101, 102)], ids=["same-first-event", "different-tenants"])
def test_simultaneous_first_events_are_singleflight_and_isolated(
    integration: Integration, repo_ids: tuple[int, int],
) -> None:
    bodies = {repo_id: integration.body(repo_id) for repo_id in set(repo_ids)}
    integration.http.calls.clear()
    barrier = Barrier(2)

    def send(repo_id: int) -> dict[str, object]:
        client = integration.client()
        barrier.wait(timeout=10)
        result = client.post("/api/actions/events", json=bodies[repo_id], headers=integration.headers(repo_id))
        assert result.status_code == 202, result.get_data(as_text=True)
        return obj(result.get_json())

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(send, repo_id) for repo_id in repo_ids]
        results = [future.result(timeout=20) for future in futures]
    integration.drain()
    assert len(integration.manager.containers()) == len(set(repo_ids))
    assert len(integration.registry.list_registered()) == len(set(repo_ids))
    for repo_id in set(repo_ids):
        row = registry_row(integration, repo_id)
        assert row["generation"] == row["revision"] == 1
        stored = integration.manager.resolve(repo_id).service.store.load_current_snapshot(1)
        assert stored is not None and stored.question_count == 2
    assert integration.chat.calls.count("questions") == len(set(repo_ids))
    assert (results[0]["job_id"] == results[1]["job_id"]) == (repo_ids[0] == repo_ids[1])
    opt_ins = [call for call in integration.http.calls if "/contents/.lasthuman.yml" in call.path]
    assert len(opt_ins) == len(set(repo_ids))


def test_fixture_refuses_unknown_requests_without_external_network(integration: Integration) -> None:
    with pytest.raises(AssertionError, match="Unscripted external HTTP"):
        requests.get("https://not-a-scripted-peer.invalid/path", timeout=1)
    assert not integration.http.calls and not integration.chat.calls
    assert not integration.registry.list_registered()
