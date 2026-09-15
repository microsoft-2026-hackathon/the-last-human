from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import jwt
import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric import rsa

from lasthuman.server import relay
from lasthuman.server.config import Settings
from lasthuman.server.events import (
    ActionsIdentity,
    EventError,
    OIDCError,
    OIDCVerifier,
    decode_event,
)

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40


@dataclass
class FakeSigningKey:
    key: object


class FakeJwksClient:
    def __init__(self, key: object) -> None:
        self._key = key

    def get_signing_key_from_jwt(self, token: str) -> FakeSigningKey:
        assert token
        return FakeSigningKey(self._key)


@dataclass
class RecordedCall:
    method: str
    url: str
    headers: dict[str, str]
    json_body: object | None
    allow_redirects: bool
    timeout: tuple[int, int]


class FakeResponse:
    def __init__(self, status_code: int, payload: object | None = None) -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self._payload = payload
        if payload is None:
            self.content = b""
        else:
            self.content = json.dumps(payload).encode("utf-8")
            self.headers["Content-Type"] = "application/json"
        self.headers["Content-Length"] = str(len(self.content))

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("empty body")
        return self._payload


class FakeSession:
    def __init__(self) -> None:
        self._routes: dict[tuple[str, str], list[FakeResponse]] = {}
        self.calls: list[RecordedCall] = []

    def enqueue(self, method: str, url: str, *responses: FakeResponse) -> None:
        self._routes.setdefault((method.upper(), url), []).extend(responses)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: object | None = None,
        timeout: tuple[int, int] | None = None,
        allow_redirects: bool = True,
    ) -> FakeResponse:
        if timeout is None:
            raise AssertionError("timeout must be set")
        self.calls.append(
            RecordedCall(
                method=method.upper(),
                url=url,
                headers=dict(headers or {}),
                json_body=json,
                allow_redirects=allow_redirects,
                timeout=timeout,
            )
        )
        queue = self._routes.get((method.upper(), url))
        if not queue:
            raise AssertionError(f"unexpected request: {method.upper()} {url}")
        return queue.pop(0)


@dataclass(frozen=True)
class FakeSnapshot:
    pr: int
    author_id: int
    expected_binding: dict[str, object]

    def binding(self) -> dict[str, object]:
        return dict(self.expected_binding)


class FakeTimer:
    def __init__(self, start: float = 1_000.0) -> None:
        self.value = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


@pytest.fixture()
def rsa_key_pair() -> tuple[object, object]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def make_settings(tmp_path: Path) -> Settings:
    key_file = tmp_path / "app.pem"
    key_file.write_text("not used", encoding="utf-8")
    return Settings(
        app_id=101,
        client_id="Iv1.test",
        client_secret="s" * 32,
        private_key_file=key_file,
        installation_id=202,
        repository="hunhoon21/the-last-human",
        repository_id=1361123778,
        owner_id=36983960,
        base_url="https://bot.example",
        secret_key="k" * 32,
        database=tmp_path / "lasthuman.sqlite3",
        mode="live",
        status_context="last-human/human-verified",
        workflow="lasthuman-app.yml",
        workflow_ref="refs/heads/main",
        oidc_audience="hunhoon21/the-last-human",
    )


def make_identity(settings: Settings, *, event_name: str = "pull_request_target") -> ActionsIdentity:
    return ActionsIdentity(
        event_name=event_name,
        run_id="1001",
        run_attempt="1",
        jti="jti-123",
        workflow_ref=(
            f"{settings.repository}/.github/workflows/{settings.workflow}@"
            f"{settings.workflow_ref}"
        ),
    )


def make_claims(settings: Settings, *, now: int | None = None) -> dict[str, object]:
    issued_at = int(time.time()) if now is None else now
    return {
        "iss": "https://token.actions.githubusercontent.com",
        "aud": settings.oidc_audience,
        "sub": f"repo:{settings.repository}:ref:{settings.workflow_ref}",
        "jti": "jti-123",
        "repository": settings.repository.upper(),
        "repository_id": str(settings.repository_id),
        "repository_owner_id": str(settings.owner_id),
        "workflow_ref": (
            f"{settings.repository}/.github/workflows/{settings.workflow}@"
            f"{settings.workflow_ref}"
        ),
        "ref": settings.workflow_ref,
        "event_name": "pull_request_target",
        "run_id": "1001",
        "run_attempt": "1",
        "iat": issued_at - 10,
        "nbf": issued_at - 10,
        "exp": issued_at + 300,
    }


def encode_token(private_key: object, claims: dict[str, object]) -> str:
    return cast(
        str,
        jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"}),
    )


def test_oidc_verifier_accepts_trusted_github_actions_claims(
    tmp_path: Path,
    rsa_key_pair: tuple[object, object],
) -> None:
    settings = make_settings(tmp_path)
    private_key, public_key = rsa_key_pair
    token = encode_token(private_key, make_claims(settings))

    identity = OIDCVerifier(settings, jwks_client=FakeJwksClient(public_key)).verify(token)

    assert identity == make_identity(settings)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda settings, claims: claims.__setitem__("aud", "other-audience"),
        lambda settings, claims: claims.__setitem__("iss", "https://evil.example"),
        lambda settings, claims: claims.__setitem__("repository", "hunhoon21/other-repo"),
        lambda settings, claims: claims.__setitem__("repository_id", "9"),
        lambda settings, claims: claims.__setitem__("repository_owner_id", "99"),
        lambda settings, claims: claims.__setitem__(
            "workflow_ref",
            "hunhoon21/the-last-human/.github/workflows/other.yml@refs/heads/main",
        ),
        lambda settings, claims: claims.__setitem__("ref", "refs/heads/feature"),
        lambda settings, claims: claims.__setitem__("event_name", "pull_request"),
        lambda settings, claims: claims.update(
            {"iat": 1_700_000_000, "nbf": 1_700_000_000, "exp": 1_700_000_100}
        ),
    ],
)
def test_oidc_verifier_rejects_untrusted_claims(
    tmp_path: Path,
    rsa_key_pair: tuple[object, object],
    mutate,
) -> None:
    settings = make_settings(tmp_path)
    private_key, public_key = rsa_key_pair
    claims = make_claims(settings)
    mutate(settings, claims)
    token = encode_token(private_key, claims)

    with pytest.raises(OIDCError):
        OIDCVerifier(settings, jwks_client=FakeJwksClient(public_key)).verify(token)


def test_oidc_verifier_error_does_not_leak_token_value(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    token = "abc.def.ghi"

    with pytest.raises(OIDCError) as error:
        OIDCVerifier(settings, jwks_client=FakeJwksClient(object())).verify(token)

    assert token not in str(error.value)


def test_decode_event_accepts_open_and_closed_payloads(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    identity = make_identity(settings)
    binding = {
        "repository_id": settings.repository_id,
        "pr": 7,
        "head_sha": HEAD_SHA,
        "base_sha": BASE_SHA,
        "policy_version": "1" * 64,
        "snapshot_id": "2" * 64,
        "score": 80,
        "triggered": True,
    }

    opened = decode_event(
        {
            "repository_id": settings.repository_id,
            "pr": 7,
            "action": "opened",
            "binding": binding,
        },
        identity,
        settings,
    )
    closed = decode_event(
        {
            "repository_id": settings.repository_id,
            "pr": 7,
            "action": "closed",
            "head_sha": HEAD_SHA,
        },
        identity,
        settings,
    )

    assert opened.pr == 7
    assert opened.action == "opened"
    assert opened.expected_binding == binding
    assert closed.expected_binding == {
        "repository_id": settings.repository_id,
        "pr": 7,
        "head_sha": HEAD_SHA,
    }


def test_decode_event_rejects_bool_pr(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    identity = make_identity(settings)

    with pytest.raises(EventError, match="positive integer"):
        decode_event(
            {
                "repository_id": settings.repository_id,
                "pr": True,
                "action": "opened",
                "binding": {
                    "repository_id": settings.repository_id,
                    "pr": 7,
                    "head_sha": HEAD_SHA,
                    "base_sha": BASE_SHA,
                    "policy_version": "1" * 64,
                    "snapshot_id": "2" * 64,
                    "score": 80,
                    "triggered": True,
                },
            },
            identity,
            settings,
        )


def test_relay_verification_stops_on_binding_mismatch_without_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_path = tmp_path / "dispatch.json"
    event_path.write_text(json.dumps({"inputs": {"receipt_id": "receipt-5"}}), encoding="utf-8")
    binding = {
        "repository_id": 1361123778,
        "pr": 7,
        "head_sha": HEAD_SHA,
        "base_sha": BASE_SHA,
        "policy_version": "1" * 64,
        "snapshot_id": "2" * 64,
        "score": 80,
        "triggered": True,
    }
    fake_snapshot = FakeSnapshot(
        pr=7,
        author_id=7,
        expected_binding=dict(binding, head_sha="c" * 40),
    )

    class FakeSnapshotReader:
        def __init__(self, client: object, cache_dir: Path) -> None:
            del client, cache_dir

        def read(self, pr: int) -> FakeSnapshot:
            assert pr == 7
            return fake_snapshot

    monkeypatch.setattr(relay, "SnapshotReader", FakeSnapshotReader)

    bot_session = FakeSession()
    token_url = "https://token.actions.githubusercontent.com/id"
    bot_session.enqueue(
        "GET",
        token_url + "?audience=hunhoon21%2Fthe-last-human",
        FakeResponse(200, {"value": "oidc-token"}),
    )
    bot_session.enqueue(
        "GET",
        "https://bot.example/api/actions/receipts/receipt-5",
        FakeResponse(
            200,
            {
                "receipt_id": "receipt-5",
                "binding": binding,
                "actor_id": 7,
                "question_version": "v1",
                "issued_at": "2026-09-08T00:00:00Z",
                "app_id": 101,
                "installation_id": 202,
            },
        ),
    )

    github_session = FakeSession()
    github_session.enqueue(
        "GET",
        "https://api.github.com/repos/hunhoon21/the-last-human",
        FakeResponse(
            200,
            {
                "id": 1361123778,
                "full_name": "hunhoon21/the-last-human",
                "owner": {"id": 36983960},
            },
        ),
    )

    with pytest.raises(relay.RelayError, match="binding mismatch"):
        relay.run(
            environ={
                "ACTIONS_ID_TOKEN_REQUEST_URL": token_url,
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token",
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_EVENT_PATH": str(event_path),
                "GITHUB_REPOSITORY": "hunhoon21/the-last-human",
                "GITHUB_REPOSITORY_ID": "1361123778",
                "GITHUB_REPOSITORY_OWNER_ID": "36983960",
                "GITHUB_WORKFLOW_REF": (
                    "hunhoon21/the-last-human/.github/workflows/lasthuman-app.yml"
                    "@refs/heads/main"
                ),
                "GITHUB_REF": "refs/heads/main",
                "GH_TOKEN": "ghs_test-token",
                "TLH_BOT_URL": "https://bot.example",
                "TLH_OIDC_AUDIENCE": "hunhoon21/the-last-human",
            },
            session=bot_session,
            github_session=github_session,
            cache_dir=tmp_path / ".work" / "relay-cache",
        )

    assert [call.method for call in bot_session.calls] == ["GET", "GET"]
    assert bot_session.calls[0].headers["Authorization"] == "Bearer request-token"
    assert bot_session.calls[0].allow_redirects is False
    assert bot_session.calls[1].headers["Authorization"] == "Bearer oidc-token"
    assert all("/verify" not in call.url for call in bot_session.calls)
    assert github_session.calls[0].headers["Authorization"] == "Bearer ghs_test-token"


def test_relay_pull_request_polls_short_job_until_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_path = tmp_path / "pull_request.json"
    event_path.write_text(
        json.dumps(
            {
                "action": "opened",
                "number": 7,
                "repository": {
                    "id": 1361123778,
                    "full_name": "hunhoon21/the-last-human",
                    "owner": {"id": 36983960},
                },
            }
        ),
        encoding="utf-8",
    )
    binding = {
        "repository_id": 1361123778,
        "pr": 7,
        "head_sha": HEAD_SHA,
        "base_sha": BASE_SHA,
        "policy_version": "1" * 64,
        "snapshot_id": "2" * 64,
        "score": 80,
        "triggered": True,
    }
    fake_snapshot = FakeSnapshot(pr=7, author_id=7, expected_binding=binding)

    class FakeSnapshotReader:
        def __init__(self, client: object, cache_dir: Path) -> None:
            del client, cache_dir

        def read(self, pr: int) -> FakeSnapshot:
            assert pr == 7
            return fake_snapshot

    timer = FakeTimer()
    monkeypatch.setattr(relay, "SnapshotReader", FakeSnapshotReader)
    monkeypatch.setattr(relay.time, "monotonic", timer.monotonic)
    monkeypatch.setattr(relay.time, "sleep", timer.sleep)

    bot_session = FakeSession()
    token_url = "https://token.actions.githubusercontent.com/id"
    bot_session.enqueue(
        "GET",
        token_url + "?audience=hunhoon21%2Fthe-last-human",
        FakeResponse(200, {"value": "oidc-token-1"}),
    )
    bot_session.enqueue(
        "POST",
        "https://bot.example/api/actions/events",
        FakeResponse(202, {"job_id": "job-1", "url": "/api/actions/jobs/job-1"}),
    )
    bot_session.enqueue(
        "GET",
        "https://bot.example/api/actions/jobs/job-1",
        FakeResponse(200, {"job_id": "job-1", "state": "running"}),
        FakeResponse(
            200,
            {
                "job_id": "job-1",
                "state": "completed",
                "result": {
                    "state": "pending",
                    "pr": 7,
                    "snapshot_id": "2" * 64,
                    "question_count": 3,
                },
            },
        ),
    )

    github_session = FakeSession()
    github_session.enqueue(
        "GET",
        "https://api.github.com/repos/hunhoon21/the-last-human",
        FakeResponse(
            200,
            {
                "id": 1361123778,
                "full_name": "hunhoon21/the-last-human",
                "owner": {"id": 36983960},
            },
        ),
    )

    relay.run(
        environ={
            "ACTIONS_ID_TOKEN_REQUEST_URL": token_url,
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token",
            "GITHUB_EVENT_NAME": "pull_request_target",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_REPOSITORY": "hunhoon21/the-last-human",
            "GITHUB_REPOSITORY_ID": "1361123778",
            "GITHUB_REPOSITORY_OWNER_ID": "36983960",
            "GITHUB_WORKFLOW_REF": (
                "hunhoon21/the-last-human/.github/workflows/lasthuman-app.yml"
                "@refs/heads/main"
            ),
            "GITHUB_REF": "refs/heads/main",
            "GH_TOKEN": "ghs_test-token",
            "TLH_BOT_URL": "https://bot.example",
            "TLH_OIDC_AUDIENCE": "hunhoon21/the-last-human",
        },
        session=bot_session,
        github_session=github_session,
        cache_dir=tmp_path / ".work" / "relay-cache",
    )

    assert [call.method for call in bot_session.calls] == ["GET", "POST", "GET", "GET"]
    assert bot_session.calls[0].headers["Authorization"] == "Bearer request-token"
    assert bot_session.calls[1].headers["Authorization"] == "Bearer oidc-token-1"
    assert bot_session.calls[2].headers["Authorization"] == "Bearer oidc-token-1"
    assert bot_session.calls[3].headers["Authorization"] == "Bearer oidc-token-1"
    assert bot_session.calls[1].json_body == {
        "repository_id": 1361123778,
        "pr": 7,
        "action": "opened",
        "binding": binding,
    }
    assert all(call.allow_redirects is False for call in bot_session.calls)
    assert timer.sleeps == [2.0]


def test_relay_resubmits_exact_payload_after_job_disappears(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_path = tmp_path / "pull_request.json"
    event_path.write_text(
        json.dumps(
            {
                "action": "opened",
                "number": 7,
                "repository": {
                    "id": 1361123778,
                    "full_name": "hunhoon21/the-last-human",
                    "owner": {"id": 36983960},
                },
            }
        ),
        encoding="utf-8",
    )
    binding = {
        "repository_id": 1361123778,
        "pr": 7,
        "head_sha": HEAD_SHA,
        "base_sha": BASE_SHA,
        "policy_version": "1" * 64,
        "snapshot_id": "2" * 64,
        "score": 80,
        "triggered": True,
    }
    fake_snapshot = FakeSnapshot(pr=7, author_id=7, expected_binding=binding)

    class FakeSnapshotReader:
        def __init__(self, client: object, cache_dir: Path) -> None:
            del client, cache_dir

        def read(self, pr: int) -> FakeSnapshot:
            assert pr == 7
            return fake_snapshot

    timer = FakeTimer()
    monkeypatch.setattr(relay, "SnapshotReader", FakeSnapshotReader)
    monkeypatch.setattr(relay.time, "monotonic", timer.monotonic)
    monkeypatch.setattr(relay.time, "sleep", timer.sleep)

    bot_session = FakeSession()
    token_url = "https://token.actions.githubusercontent.com/id"
    bot_session.enqueue(
        "GET",
        token_url + "?audience=hunhoon21%2Fthe-last-human",
        FakeResponse(200, {"value": "oidc-token-1"}),
    )
    bot_session.enqueue(
        "POST",
        "https://bot.example/api/actions/events",
        FakeResponse(202, {"job_id": "job-1", "url": "/api/actions/jobs/job-1"}),
        FakeResponse(202, {"job_id": "job-2", "url": "/api/actions/jobs/job-2"}),
    )
    bot_session.enqueue(
        "GET",
        "https://bot.example/api/actions/jobs/job-1",
        FakeResponse(404, {"error": "job not found"}),
    )
    bot_session.enqueue(
        "GET",
        "https://bot.example/api/actions/jobs/job-2",
        FakeResponse(
            200,
            {
                "job_id": "job-2",
                "state": "completed",
                "result": {
                    "state": "pending",
                    "pr": 7,
                    "snapshot_id": "2" * 64,
                    "question_count": 3,
                },
            },
        ),
    )

    github_session = FakeSession()
    github_session.enqueue(
        "GET",
        "https://api.github.com/repos/hunhoon21/the-last-human",
        FakeResponse(
            200,
            {
                "id": 1361123778,
                "full_name": "hunhoon21/the-last-human",
                "owner": {"id": 36983960},
            },
        ),
    )

    relay.run(
        environ={
            "ACTIONS_ID_TOKEN_REQUEST_URL": token_url,
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token",
            "GITHUB_EVENT_NAME": "pull_request_target",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_REPOSITORY": "hunhoon21/the-last-human",
            "GITHUB_REPOSITORY_ID": "1361123778",
            "GITHUB_REPOSITORY_OWNER_ID": "36983960",
            "GITHUB_WORKFLOW_REF": (
                "hunhoon21/the-last-human/.github/workflows/lasthuman-app.yml"
                "@refs/heads/main"
            ),
            "GITHUB_REF": "refs/heads/main",
            "GH_TOKEN": "ghs_test-token",
            "TLH_BOT_URL": "https://bot.example",
            "TLH_OIDC_AUDIENCE": "hunhoon21/the-last-human",
        },
        session=bot_session,
        github_session=github_session,
        cache_dir=tmp_path / ".work" / "relay-cache",
    )

    assert [call.method for call in bot_session.calls] == ["GET", "POST", "GET", "POST", "GET"]
    assert bot_session.calls[1].json_body == bot_session.calls[3].json_body
    assert all(
        call.headers["Authorization"] == "Bearer oidc-token-1"
        for call in bot_session.calls[1:]
    )
    assert timer.sleeps == []


@pytest.mark.parametrize(
    ("state", "message"),
    [
        ("error", "Model processing failed; retry later"),
        ("conflict", "closed event head does not match the current pull request"),
        ("stale", "Pull request is no longer open"),
    ],
)
def test_relay_surfaces_terminal_job_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    message: str,
) -> None:
    event_path = tmp_path / "pull_request.json"
    event_path.write_text(
        json.dumps(
            {
                "action": "opened",
                "number": 7,
                "repository": {
                    "id": 1361123778,
                    "full_name": "hunhoon21/the-last-human",
                    "owner": {"id": 36983960},
                },
            }
        ),
        encoding="utf-8",
    )
    binding = {
        "repository_id": 1361123778,
        "pr": 7,
        "head_sha": HEAD_SHA,
        "base_sha": BASE_SHA,
        "policy_version": "1" * 64,
        "snapshot_id": "2" * 64,
        "score": 80,
        "triggered": True,
    }
    fake_snapshot = FakeSnapshot(pr=7, author_id=7, expected_binding=binding)

    class FakeSnapshotReader:
        def __init__(self, client: object, cache_dir: Path) -> None:
            del client, cache_dir

        def read(self, pr: int) -> FakeSnapshot:
            assert pr == 7
            return fake_snapshot

    monkeypatch.setattr(relay, "SnapshotReader", FakeSnapshotReader)

    bot_session = FakeSession()
    token_url = "https://token.actions.githubusercontent.com/id"
    bot_session.enqueue(
        "GET",
        token_url + "?audience=hunhoon21%2Fthe-last-human",
        FakeResponse(200, {"value": "oidc-token-1"}),
    )
    bot_session.enqueue(
        "POST",
        "https://bot.example/api/actions/events",
        FakeResponse(202, {"job_id": "job-1", "url": "/api/actions/jobs/job-1"}),
    )
    bot_session.enqueue(
        "GET",
        "https://bot.example/api/actions/jobs/job-1",
        FakeResponse(
            200,
            {
                "job_id": "job-1",
                "state": state,
                "error": message,
                "private_detail": "do-not-leak",
            },
        ),
    )

    github_session = FakeSession()
    github_session.enqueue(
        "GET",
        "https://api.github.com/repos/hunhoon21/the-last-human",
        FakeResponse(
            200,
            {
                "id": 1361123778,
                "full_name": "hunhoon21/the-last-human",
                "owner": {"id": 36983960},
            },
        ),
    )

    with pytest.raises(
        relay.RelayError,
        match=re.escape(f"relay job {state}: {message}"),
    ) as error:
        relay.run(
            environ={
                "ACTIONS_ID_TOKEN_REQUEST_URL": token_url,
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token",
                "GITHUB_EVENT_NAME": "pull_request_target",
                "GITHUB_EVENT_PATH": str(event_path),
                "GITHUB_REPOSITORY": "hunhoon21/the-last-human",
                "GITHUB_REPOSITORY_ID": "1361123778",
                "GITHUB_REPOSITORY_OWNER_ID": "36983960",
                "GITHUB_WORKFLOW_REF": (
                    "hunhoon21/the-last-human/.github/workflows/lasthuman-app.yml"
                    "@refs/heads/main"
                ),
                "GITHUB_REF": "refs/heads/main",
                "GH_TOKEN": "ghs_test-token",
                "TLH_BOT_URL": "https://bot.example",
                "TLH_OIDC_AUDIENCE": "hunhoon21/the-last-human",
            },
            session=bot_session,
            github_session=github_session,
            cache_dir=tmp_path / ".work" / "relay-cache",
        )

    assert "do-not-leak" not in str(error.value)


def test_relay_receipt_verification_fetches_fresh_oidc_before_post_and_poll(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_path = tmp_path / "dispatch.json"
    event_path.write_text(json.dumps({"inputs": {"receipt_id": "receipt-5"}}), encoding="utf-8")
    binding = {
        "repository_id": 1361123778,
        "pr": 7,
        "head_sha": HEAD_SHA,
        "base_sha": BASE_SHA,
        "policy_version": "1" * 64,
        "snapshot_id": "2" * 64,
        "score": 80,
        "triggered": True,
    }
    fake_snapshot = FakeSnapshot(pr=7, author_id=7, expected_binding=binding)

    class FakeSnapshotReader:
        def __init__(self, client: object, cache_dir: Path) -> None:
            del client, cache_dir

        def read(self, pr: int) -> FakeSnapshot:
            assert pr == 7
            return fake_snapshot

    monkeypatch.setattr(relay, "SnapshotReader", FakeSnapshotReader)

    bot_session = FakeSession()
    token_url = "https://token.actions.githubusercontent.com/id"
    bot_session.enqueue(
        "GET",
        token_url + "?audience=hunhoon21%2Fthe-last-human",
        FakeResponse(200, {"value": "oidc-token-1"}),
        FakeResponse(200, {"value": "oidc-token-2"}),
    )
    bot_session.enqueue(
        "GET",
        "https://bot.example/api/actions/receipts/receipt-5",
        FakeResponse(
            200,
            {
                "receipt_id": "receipt-5",
                "binding": binding,
                "actor_id": 7,
                "question_version": "v1",
                "issued_at": "2026-09-08T00:00:00Z",
                "app_id": 101,
                "installation_id": 202,
            },
        ),
    )
    bot_session.enqueue(
        "POST",
        "https://bot.example/api/actions/receipts/receipt-5/verify",
        FakeResponse(202, {"job_id": "job-verify", "url": "/api/actions/jobs/job-verify"}),
    )
    bot_session.enqueue(
        "GET",
        "https://bot.example/api/actions/jobs/job-verify",
        FakeResponse(
            200,
            {
                "job_id": "job-verify",
                "state": "completed",
                "result": {
                    "state": "verified",
                    "pr": 7,
                    "receipt_id": "receipt-5",
                    "verified_at": "2026-09-08T12:00:00Z",
                },
            },
        ),
    )

    github_session = FakeSession()
    github_session.enqueue(
        "GET",
        "https://api.github.com/repos/hunhoon21/the-last-human",
        FakeResponse(
            200,
            {
                "id": 1361123778,
                "full_name": "hunhoon21/the-last-human",
                "owner": {"id": 36983960},
            },
        ),
    )

    relay.run(
        environ={
            "ACTIONS_ID_TOKEN_REQUEST_URL": token_url,
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_REPOSITORY": "hunhoon21/the-last-human",
            "GITHUB_REPOSITORY_ID": "1361123778",
            "GITHUB_REPOSITORY_OWNER_ID": "36983960",
            "GITHUB_WORKFLOW_REF": (
                "hunhoon21/the-last-human/.github/workflows/lasthuman-app.yml"
                "@refs/heads/main"
            ),
            "GITHUB_REF": "refs/heads/main",
            "GH_TOKEN": "ghs_test-token",
            "TLH_BOT_URL": "https://bot.example",
            "TLH_OIDC_AUDIENCE": "hunhoon21/the-last-human",
        },
        session=bot_session,
        github_session=github_session,
        cache_dir=tmp_path / ".work" / "relay-cache",
    )

    assert [call.method for call in bot_session.calls] == ["GET", "GET", "GET", "POST", "GET"]
    assert bot_session.calls[0].headers["Authorization"] == "Bearer request-token"
    assert bot_session.calls[1].headers["Authorization"] == "Bearer oidc-token-1"
    assert bot_session.calls[2].headers["Authorization"] == "Bearer request-token"
    assert bot_session.calls[3].headers["Authorization"] == "Bearer oidc-token-2"
    assert bot_session.calls[4].headers["Authorization"] == "Bearer oidc-token-2"


def test_relay_times_out_after_bounded_polling_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_path = tmp_path / "pull_request.json"
    event_path.write_text(
        json.dumps(
            {
                "action": "opened",
                "number": 7,
                "repository": {
                    "id": 1361123778,
                    "full_name": "hunhoon21/the-last-human",
                    "owner": {"id": 36983960},
                },
            }
        ),
        encoding="utf-8",
    )
    binding = {
        "repository_id": 1361123778,
        "pr": 7,
        "head_sha": HEAD_SHA,
        "base_sha": BASE_SHA,
        "policy_version": "1" * 64,
        "snapshot_id": "2" * 64,
        "score": 80,
        "triggered": True,
    }
    fake_snapshot = FakeSnapshot(pr=7, author_id=7, expected_binding=binding)

    class FakeSnapshotReader:
        def __init__(self, client: object, cache_dir: Path) -> None:
            del client, cache_dir

        def read(self, pr: int) -> FakeSnapshot:
            assert pr == 7
            return fake_snapshot

    timer = FakeTimer()
    monkeypatch.setattr(relay, "SnapshotReader", FakeSnapshotReader)
    monkeypatch.setattr(relay.time, "monotonic", timer.monotonic)
    monkeypatch.setattr(relay.time, "sleep", timer.sleep)

    bot_session = FakeSession()
    token_url = "https://token.actions.githubusercontent.com/id"
    bot_session.enqueue(
        "GET",
        token_url + "?audience=hunhoon21%2Fthe-last-human",
        FakeResponse(200, {"value": "oidc-token-1"}),
    )
    bot_session.enqueue(
        "POST",
        "https://bot.example/api/actions/events",
        FakeResponse(202, {"job_id": "job-1", "url": "/api/actions/jobs/job-1"}),
    )
    bot_session.enqueue(
        "GET",
        "https://bot.example/api/actions/jobs/job-1",
        *(FakeResponse(200, {"job_id": "job-1", "state": "running"}) for _ in range(90)),
    )

    github_session = FakeSession()
    github_session.enqueue(
        "GET",
        "https://api.github.com/repos/hunhoon21/the-last-human",
        FakeResponse(
            200,
            {
                "id": 1361123778,
                "full_name": "hunhoon21/the-last-human",
                "owner": {"id": 36983960},
            },
        ),
    )

    with pytest.raises(relay.RelayError, match="timed out"):
        relay.run(
            environ={
                "ACTIONS_ID_TOKEN_REQUEST_URL": token_url,
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token",
                "GITHUB_EVENT_NAME": "pull_request_target",
                "GITHUB_EVENT_PATH": str(event_path),
                "GITHUB_REPOSITORY": "hunhoon21/the-last-human",
                "GITHUB_REPOSITORY_ID": "1361123778",
                "GITHUB_REPOSITORY_OWNER_ID": "36983960",
                "GITHUB_WORKFLOW_REF": (
                    "hunhoon21/the-last-human/.github/workflows/lasthuman-app.yml"
                    "@refs/heads/main"
                ),
                "GITHUB_REF": "refs/heads/main",
                "GH_TOKEN": "ghs_test-token",
                "TLH_BOT_URL": "https://bot.example",
                "TLH_OIDC_AUDIENCE": "hunhoon21/the-last-human",
            },
            session=bot_session,
            github_session=github_session,
            cache_dir=tmp_path / ".work" / "relay-cache",
        )

    assert sum(timer.sleeps) == 180.0
    assert all(seconds == 2.0 for seconds in timer.sleeps)


def test_lasthuman_app_workflow_is_metadata_only_and_trusted() -> None:
    workflow_path = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "lasthuman-app.yml"
    )
    data = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    workflow = cast(dict[object, object], data)
    triggers = cast(dict[object, object], workflow.get("on") or workflow.get(True))
    permissions = cast(dict[str, str], workflow["permissions"])
    relay_job = cast(dict[str, object], cast(dict[str, object], workflow["jobs"])["relay"])
    steps = cast(list[dict[str, object]], relay_job["steps"])
    checkout = next(step for step in steps if step.get("uses") == "actions/checkout@v4")
    install = next(step for step in steps if "pip install -e '.[bot]'" in str(step.get("run", "")))
    run_step = next(
        step for step in steps if step.get("name") == "Relay metadata-only event"
    )

    assert workflow["name"] == "Last Human · relay"
    assert permissions == {
        "contents": "read",
        "pull-requests": "read",
        "statuses": "read",
        "id-token": "write",
    }
    assert set(cast(dict[str, object], triggers["pull_request_target"])["types"]) == {
        "opened",
        "synchronize",
        "reopened",
        "edited",
        "labeled",
        "unlabeled",
        "closed",
    }
    assert (
        cast(dict[str, object], cast(dict[str, object], triggers["workflow_dispatch"])["inputs"])[
            "receipt_id"
        ]["required"]
        is True
    )
    assert "LASTHUMAN_RUNTIME" in str(relay_job["if"])
    assert checkout["with"]["ref"] == "${{ github.event.repository.default_branch }}"
    assert checkout["with"]["persist-credentials"] is False
    assert "TLH_BOT_URL" in cast(dict[str, object], relay_job["env"])
    assert "TLH_OIDC_AUDIENCE" in cast(dict[str, object], relay_job["env"])
    assert cast(dict[str, object], relay_job["env"])["GH_TOKEN"] == "${{ github.token }}"
    assert "python-version" in cast(dict[str, object], next(
        step for step in steps if step.get("uses") == "actions/setup-python@v5"
    ))["with"]
    assert ".[bot]" in str(install["run"])
    assert run_step["run"] == "python -m lasthuman.server.relay"
    assert run_step["if"] == "github.event_name == 'pull_request_target'"
    raw = workflow_path.read_text(encoding="utf-8").lower()
    assert "statuses: write" not in raw
    assert "app_id" not in raw
    assert "private_key" not in raw
    assert "lasthuman_api_key" not in raw
