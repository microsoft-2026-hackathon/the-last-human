"""Legacy and immutable default subjects through real RSA, HTTP and SQLite."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace

import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import rsa

from registration_transport import ORIGIN, PASS, Integration, integration, obj
from lasthuman.server import gateway
from lasthuman.server.events import ActionsIdentity, OIDCError, expected_actions_subjects
from lasthuman.server.github import GitHubClient
from lasthuman.server.registration import RepositoryContext

__all__ = ["integration"]

LEGACY = "repo:acme/one:ref:refs/heads/main"
IMMUTABLE = "repo:acme@77/one@101:ref:refs/heads/main"


def test_default_subjects_are_two_exact_strings() -> None:
    assert expected_actions_subjects("acme/one", 101, 77, "refs/heads/main") == (LEGACY, IMMUTABLE)
    assert expected_actions_subjects(
        "microsoft-2026-hackathon/the-last-human", 1371498352, 321220983, "refs/heads/main",
    ) == (
        "repo:microsoft-2026-hackathon/the-last-human:ref:refs/heads/main",
        "repo:microsoft-2026-hackathon@321220983/the-last-human@1371498352:ref:refs/heads/main",
    )


@pytest.mark.parametrize("subject,event", [
    (LEGACY, "pull_request_target"), (IMMUTABLE, "pull_request_target"),
    (LEGACY, "workflow_dispatch"), (IMMUTABLE, "workflow_dispatch"),
    ("repo:acme/one:pull_request", "pull_request_target"),
    ("repo:acme@77/one@101:pull_request", "pull_request_target"),
])
def test_rsa_subjects_in_dynamic_fallback_and_cached_tenant_verifiers(
    integration: Integration, subject: str, event: str,
) -> None:
    token = integration.http.signed_token(101, event=event, claims={"sub": subject})
    identity = integration.verifier.verify(token)
    assert (identity.repository, identity.repository_id, identity.owner_id, identity.sub) == (
        "acme/one", 101, 77, subject,
    )
    context = RepositoryContext(101, "acme/one", 77, 201, 1)
    settings = integration.settings.tenant_settings(context, prepare=False)
    verifier = gateway.TenantOIDCVerifier(settings, integration.verifier)
    with integration.app.test_request_context():
        fallback = verifier.verify(token)
    assert isinstance(fallback, ActionsIdentity) and fallback.sub == subject and fallback.event_name == event
    assert integration.http.jwks_fetches > 0
    fetches = integration.http.jwks_fetches
    verified = gateway._VerifiedRequest(identity, context, token)
    with integration.app.test_request_context(environ_overrides={gateway._VERIFIED_ENVIRON_KEY: verified}):
        assert verifier.verify(token) == fallback
        with pytest.raises(OIDCError):
            verifier.verify(token + "different-token")
    assert integration.http.jwks_fetches == fetches
    stale = replace(verified, context=replace(context, generation=2))
    with integration.app.test_request_context(environ_overrides={gateway._VERIFIED_ENVIRON_KEY: stale}):
        with pytest.raises(OIDCError):
            verifier.verify(token)
    assert not integration.http.calls and not integration.registry.list_registered()


@pytest.mark.parametrize("claims", [
    {"sub": "repo:acme@78/one@101:ref:refs/heads/main"},
    {"sub": "repo:acme@77/one@102:ref:refs/heads/main"},
    {"sub": "repo:other@77/one@101:ref:refs/heads/main"},
    {"sub": "repo:acme@77/two@101:ref:refs/heads/main"},
    {"sub": "repo:acme/two:ref:refs/heads/main"},
    {"sub": "repo:acme@77/one@101:ref:refs/heads/feature"},
    {"sub": "repo:acme@77/one@101:ref:refs/pull/1/merge"},
    {"sub": "repo:acme@77/one@101:environment:production"},
    {"sub": "repo:acme@77/one@101:pull_request", "event_name": "workflow_dispatch"},
    {"sub": "repo:acme/one:pull_request", "event_name": "workflow_dispatch"},
    {"sub": "repo:acme@77/one@101:pull_request", "ref": "refs/heads/feature"},
    {"sub": "repo:acme@77/one@101:pull_request",
     "workflow_ref": "acme/one/.github/workflows/lasthuman-app.yml@refs/heads/feature"},
    {"sub": "repo:acme@77/one@101:pull_request", "event_name": "pull_request"},
    {"sub": IMMUTABLE + ":actor:pr-author"},
    {"sub": IMMUTABLE + "extra"},
    {"sub": IMMUTABLE + "\n"},
    {"sub": "repo:acme@077/one@101:ref:refs/heads/main"},
    {"sub": "repo:acme@77/one@0101:ref:refs/heads/main"},
    {"sub": "repo:acme@/one@101:ref:refs/heads/main"},
    {"sub": "repo:acme@@77/one@101:ref:refs/heads/main"},
    {"sub": "repo:acme%4077/one%40101:ref:refs/heads/main"},
    {"sub": ""}, {"sub": 101}, {"sub": [IMMUTABLE]},
    {"sub": IMMUTABLE, "repository_id": "102"},
    {"sub": IMMUTABLE, "repository_owner_id": "78"},
    {"sub": IMMUTABLE, "repository_id": True},
    {"sub": IMMUTABLE, "repository_owner_id": 77.0},
    {"sub": IMMUTABLE, "ref": "refs/heads/feature"},
    {"sub": IMMUTABLE, "workflow_ref": "acme/one/.github/workflows/other.yml@refs/heads/main"},
    {"sub": IMMUTABLE, "aud": "acme/two"},
    {"sub": IMMUTABLE, "iss": "https://foreign.example"},
    {"sub": IMMUTABLE, "exp": 1},
    {"sub": IMMUTABLE, "event_name": "pull_request"},
])
def test_invalid_signed_subjects_fail_before_registration_and_in_fallback(
    integration: Integration, claims: dict[str, object],
) -> None:
    token = integration.http.signed_token(101, claims=claims)
    settings = integration.settings.tenant_settings(RepositoryContext(101, "acme/one", 77, 201, 1), prepare=False)
    with pytest.raises(OIDCError):
        integration.verifier.verify(token)
    with integration.app.test_request_context(), pytest.raises(OIDCError):
        gateway.TenantOIDCVerifier(settings, integration.verifier).verify(token)
    result = integration.client().post("/api/actions/events", json={}, headers={"Authorization": "Bearer " + token})
    assert result.status_code == 401 and "error" in obj(result.get_json())
    assert integration.http.jwks_fetches > 0
    assert not integration.http.calls and not integration.chat.calls
    assert not integration.registry.list_registered() and not integration.manager.containers()
    assert not (integration.settings.state_root / "repos").exists()


@pytest.mark.parametrize("field,value", [("repository_id", 102), ("owner_id", 78), ("repository", "acme/two")])
def test_valid_immutable_identity_cannot_cross_fixed_tenant_binding(
    integration: Integration, field: str, value: int | str,
) -> None:
    token = integration.http.signed_token(101, claims={"sub": IMMUTABLE})
    identity = integration.verifier.verify(token)
    context = replace(RepositoryContext(101, "acme/one", 77, 201, 1), **{field: value})
    settings = integration.settings.tenant_settings(context, prepare=False)
    verifier = gateway.TenantOIDCVerifier(settings, integration.verifier)
    with integration.app.test_request_context(), pytest.raises(OIDCError):
        verifier.verify(token)
    cached = gateway._VerifiedRequest(identity, context, token)
    with integration.app.test_request_context(environ_overrides={gateway._VERIFIED_ENVIRON_KEY: cached}):
        with pytest.raises(OIDCError):
            verifier.verify(token)
    assert not integration.http.calls and not integration.registry.list_registered()


@pytest.mark.parametrize("immutable", [False, True], ids=["legacy", "immutable"])
def test_registration_card_refresh_private_pass_and_five_steps(
    integration: Integration, monkeypatch: pytest.MonkeyPatch, immutable: bool,
) -> None:
    original_signer = integration.http.signed_token
    emitted: list[tuple[int, str, str]] = []

    def signed_token(
        repository_id: int, *, event: str = "pull_request_target", run_id: str = "1001",
        claims: Mapping[str, object] | None = None, key: rsa.RSAPrivateKey | None = None,
    ) -> str:
        repo = integration.http.repositories[repository_id]
        owner, name = repo.name.split("/", 1)
        prefix = f"{owner}@{repo.owner_id}/{name}@{repository_id}" if immutable else repo.name
        context = "pull_request" if event == "pull_request_target" else "ref:refs/heads/main"
        subject = f"repo:{prefix}:{context}"
        emitted.append((repository_id, event, subject))
        return original_signer(
            repository_id, event=event, run_id=run_id, claims={"sub": subject, **(claims or {})}, key=key,
        )

    monkeypatch.setattr(integration.http, "signed_token", signed_token)
    assert not integration.registry.list_registered()
    settings = integration.settings.tenant_settings(RepositoryContext(101, "acme/one", 77, 201, 1), prepare=False)
    GitHubClient(settings, requests.Session()).ensure_pr_card(
        1, f"[Start interview]({ORIGIN}/repos/101/prs/1)",
    )
    repo = integration.http.repositories[101]
    assert len(repo.comments) == 1
    card = repo.comments[0]
    comment_id, current_body = card["id"], str(card["body"])
    assert ORIGIN + "/repos/101/prs/1" in current_body
    # Only remote fixture state changes: simulate an existing App-owned legacy card.
    card["body"] = current_body.replace(ORIGIN + "/repos/101/prs/1", ORIGIN + "/prs/1")
    assert card["body"] != current_body
    before_calls = len(integration.http.calls)
    refreshed = integration.client().post(
        "/api/actions/events", json=integration.body(101), headers=integration.headers(101, run_id="1002"),
    )
    assert refreshed.status_code == 202
    refresh_job = obj(refreshed.get_json())
    integration.manager.resolve(101).service.clock = integration.clock
    integration.drain()
    completed = integration.client().get(str(refresh_job["url"]), headers=integration.headers(101, run_id="1002"))
    assert completed.status_code == 200 and obj(completed.get_json())["state"] == "completed"
    assert len(repo.comments) == 1 and repo.comments[0]["id"] == comment_id
    refreshed_body = str(repo.comments[0]["body"])
    assert ORIGIN + "/repos/101/prs/1" in refreshed_body and ORIGIN + "/prs/1" not in refreshed_body
    patches = [call for call in integration.http.calls[before_calls:]
               if call.method == "PATCH" and call.path == f"/repos/acme/one/issues/comments/{comment_id}"]
    assert len(patches) == 1 and patches[0].body["body"] == refreshed_body
    assert integration.http.tokens[patches[0].token][:2] == (101, 201)
    jobs = {101: refresh_job, 102: integration.open_event(102)}
    assert {context.repository_id for context in integration.registry.list_registered()} == {101, 102}
    assert integration.http.jwks_fetches > 0

    first, csrf1 = integration.browser(101)
    second, csrf2 = integration.browser(102)
    payload = integration.answers(first, 101, PASS, "denied-answer")
    writer, writer_csrf = integration.browser(101, actor=99)
    model_before = list(integration.chat.calls)
    assert first.post("/repos/101/api/prs/1/submissions", json=payload).status_code == 403
    assert first.post("/repos/101/api/prs/1/submissions", json=payload,
                      headers={"X-CSRF-Token": csrf2}).status_code == 403
    assert writer.get("/repos/101/prs/1").status_code == 403
    assert writer.post("/repos/101/api/prs/1/submissions", json=payload,
                       headers={"X-CSRF-Token": writer_csrf}).status_code == 403
    assert integration.chat.calls == model_before
    _, passed1 = integration.submit(first, csrf1, 101, PASS, "same-private-request")
    _, passed2 = integration.submit(second, csrf2, 102, PASS, "same-private-request")
    assert passed1["state"] == passed2["state"] == "awaiting_verification"
    receipts = {101: str(passed1["receipt_id"]), 102: str(passed2["receipt_id"])}
    assert receipts[101] != receipts[102] and integration.chat.calls.count("pass") == 4
    one, two = (integration.manager.resolve(repo_id) for repo_id in (101, 102))
    assert one.settings.database != two.settings.database
    assert one.settings.database.is_file() and two.settings.database.is_file()
    assert two.service.store.load_receipt(receipts[101]) is None
    assert second.get(f"/repos/102/receipts/{receipts[101]}").status_code == 404
    assert writer.get(f"/repos/101/receipts/{receipts[101]}").status_code == 403
    assert integration.client().get(str(jobs[101]["url"]), headers=integration.headers(102)).status_code == 404
    foreign_headers = integration.headers(102, event="workflow_dispatch")
    assert integration.client().get(
        f"/api/actions/receipts/{receipts[101]}", headers=foreign_headers,
    ).status_code == 404
    assert integration.client().get(
        f"/repos/101/api/actions/receipts/{receipts[101]}", headers=foreign_headers,
    ).status_code == 403
    assert not any(status["state"] == "success" for item in integration.http.repositories.values()
                   for status in item.statuses)
    for repo_id, tenant in ((101, one), (102, two)):
        saved = tenant.service.store.load_receipt(receipts[repo_id])
        assert saved is not None and not saved.verified
        assert saved.repo_id == repo_id and saved.head_sha == integration.corpus.head_sha
        state = integration.five_steps(repo_id, receipts[repo_id])
        assert obj(obj(state["receipt"])["binding"])["repository_id"] == repo_id
        verified_receipt = tenant.service.store.load_receipt(receipts[repo_id])
        assert verified_receipt is not None and verified_receipt.verified
        assert verified_receipt.successful_answers == saved.successful_answers
        assert {(event, subject) for emitted_id, event, subject in emitted if emitted_id == repo_id} == {
            (event, (f"repo:acme@77/{'one' if repo_id == 101 else 'two'}@{repo_id}:"
                     if immutable else f"repo:acme/{'one' if repo_id == 101 else 'two'}:")
             + ("pull_request" if event == "pull_request_target" else "ref:refs/heads/main"))
            for event in ("pull_request_target", "workflow_dispatch")
        }
    public = [item for repository in integration.http.repositories.values()
              for item in (repository.comments, repository.statuses, repository.dispatches)]
    assert PASS not in json.dumps(public)
