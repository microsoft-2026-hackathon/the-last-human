from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from authlib.oauth2.base import OAuth2Error
from flask.testing import FlaskClient

from lasthuman.config import Config
from lasthuman.models import Answer, DiffResult, FileChange, Hunk, Question, RiskResult
from lasthuman.server import auth as auth_module
from lasthuman.server.app import create_app
from lasthuman.server.config import Settings
from lasthuman.server.github import GitHubError
from lasthuman.server.service import BotService
from lasthuman.server.snapshot import Snapshot
from lasthuman.server.store import Store
from lasthuman.structure import StructureContext, SymbolUse

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40


class FakeClock:
    def __init__(self, start: float = 1_725_798_000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeOAuth:
    def __init__(self) -> None:
        self.tokens_by_code: dict[str, dict[str, object]] = {
            "author-code": {
                "access_token": "author-token",
                "expires_at": 4_000_000_000,
                "refresh_token": "discard-me",
            },
            "other-code": {
                "access_token": "other-token",
                "expires_at": 4_000_000_000,
            },
            "revoked-code": {
                "access_token": "revoked-token",
                "expires_at": 4_000_000_000,
            },
        }
        self.state_to_verifier: dict[str, str] = {}

    def create_authorization_url(self, *, state: str, code_verifier: str) -> str:
        self.state_to_verifier[state] = code_verifier
        return (
            "https://github.com/login/oauth/authorize"
            f"?state={state}&code_challenge={code_verifier[:12]}&code_challenge_method=S256"
        )

    def fetch_token(self, *, code: str, code_verifier: str) -> dict[str, object]:
        assert code_verifier
        return dict(self.tokens_by_code[code])


@dataclass
class FakeReader:
    snapshot: Snapshot

    def read(self, pr: int) -> Snapshot:
        if pr != self.snapshot.pr:
            raise AssertionError("wrong PR requested")
        return self.snapshot


class FakeGitHub:
    def __init__(self, settings: Settings, pull_payload: dict[str, object]) -> None:
        self.settings = settings
        self.current_pull = dict(pull_payload)
        self.revoked_tokens: set[str] = set()
        self.status_calls: list[dict[str, object]] = []
        self.comment_calls: list[dict[str, object]] = []
        self.dispatch_calls: list[str] = []
        self.users_by_token = {
            "author-token": {"id": 7, "login": "octocat"},
            "other-token": {"id": 99, "login": "someone-else"},
            "revoked-token": {"id": 7, "login": "octocat"},
        }

    def repository_info(self, user_token: str | None = None) -> dict[str, object]:
        self._assert_token_valid(user_token)
        return {
            "id": self.settings.repository_id,
            "full_name": self.settings.repository,
            "owner": {"id": self.settings.owner_id},
        }

    def user(self, access_token: str) -> dict[str, object]:
        self._assert_token_valid(access_token)
        return dict(self.users_by_token[access_token])

    def pull(self, pr: int, user_token: str | None = None) -> dict[str, object]:
        self._assert_token_valid(user_token)
        if pr != self.current_pull["number"]:
            raise AssertionError("wrong PR requested")
        return dict(self.current_pull)

    def ensure_comment(self, pr: int, body: str, publication_id: str) -> dict[str, object]:
        self.comment_calls.append(
            {"pr": pr, "body": body, "publication_id": publication_id}
        )
        return {"id": len(self.comment_calls), "html_url": "https://example.com/comment"}

    def set_status(
        self,
        sha: str,
        state: str,
        description: str,
        target_url: str,
    ) -> dict[str, object]:
        self.status_calls.append(
            {
                "sha": sha,
                "state": state,
                "description": description,
                "target_url": target_url,
            }
        )
        return {"sha": sha, "state": state}

    def dispatch_verification(self, receipt_id: str) -> None:
        self.dispatch_calls.append(receipt_id)

    def _assert_token_valid(self, token: str | None) -> None:
        if token is None:
            return
        if token in self.revoked_tokens:
            raise GitHubError("revoked RAW_SECRET token", status_code=401)
        if token not in self.users_by_token:
            raise GitHubError("unknown RAW_SECRET token", status_code=401)


def make_settings(tmp_path: Path, *, live: bool = False) -> Settings:
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
        base_url="https://app.example" if live else "http://localhost:8000",
        secret_key="k" * 32,
        database=tmp_path / "state" / "lasthuman.sqlite3",
        mode="live" if live else "development",
        status_context="comprehension-gate" if live else "comprehension-gate-dev",
        workflow="lasthuman-app.yml",
        workflow_ref="refs/heads/main",
        oidc_audience="hunhoon21/the-last-human",
    )


def make_snapshot(
    *,
    pr: int = 7,
    author_id: int = 7,
    author_login: str = "octocat",
) -> Snapshot:
    auth_hunk = Hunk(
        file="app/auth/token.py",
        new_start=10,
        old_start=10,
        anchor="app/auth/token.py:L10",
        added=("+    return refresh(token)",),
        removed=("-    return token",),
        body=(
            "@@ -10,1 +10,1 @@\n"
            "-    return token\n"
            "+    return refresh(token)\n"
        ),
        file_status="modified",
    )
    docs_hunk = Hunk(
        file="docs/guide.md",
        new_start=3,
        old_start=3,
        anchor="docs/guide.md:L3",
        added=("+Updated guide",),
        removed=("-Old guide",),
        body=(
            "@@ -3,1 +3,1 @@\n"
            "-Old guide\n"
            "+Updated guide\n"
        ),
        file_status="modified",
    )
    diff = DiffResult(
        hunks=(auth_hunk, docs_hunk),
        files=(
            FileChange(
                file="app/auth/token.py",
                status="modified",
                binary=False,
                additions=1,
                deletions=1,
                hunk_count=1,
            ),
            FileChange(
                file="docs/guide.md",
                status="modified",
                binary=False,
                additions=1,
                deletions=1,
                hunk_count=1,
            ),
        ),
    )
    return Snapshot.create(
        repo="hunhoon21/the-last-human",
        repo_id=1361123778,
        pr=pr,
        head_sha=HEAD_SHA,
        base_sha=BASE_SHA,
        author_id=author_id,
        author_login=author_login,
        title="Handle refresh",
        body="Explains refresh handling.",
        risk=RiskResult(
            score=80,
            triggered=True,
            reasons=("critical path changed",),
            top_hunks=(auth_hunk, docs_hunk),
        ),
        config=Config(),
        diff=diff,
        structure=StructureContext(
            changed_files=("app/auth/token.py",),
            importers={"app/auth/token.py": ("app/main.py",)},
            symbols=(SymbolUse("refresh", "app/auth/token.py", ("app/main.py",)),),
            sibling_files=("app/main.py", "README.md"),
        ),
        zones=("app/auth/", "docs/"),
        policy_version="1" * 64,
    )


def make_questions() -> list[Question]:
    return [
        Question(
            type="claim",
            anchor="app/auth/token.py:L10",
            text="What does refresh return now?",
            expected_evidence="refresh(token)",
            choices=("token", "refresh(token)", "None", "exception"),
            answer_index=1,
        ),
        Question(
            type="structure",
            anchor="app/auth/token.py:L10",
            text="Who calls refresh?",
            expected_evidence="app/main.py",
            choices=("app/main.py", "docs/guide.md", "README.md", "tests/test_app.py"),
            answer_index=0,
        ),
        Question(
            type="rationale",
            anchor="docs/guide.md:L3",
            text="What changed in the guide?",
            expected_evidence="Updated guide",
            choices=(),
            answer_index=-1,
        ),
    ]


def make_pull(snapshot: Snapshot, *, state: str = "open") -> dict[str, object]:
    return {
        "number": snapshot.pr,
        "state": state,
        "merged": False,
        "merged_at": None,
        "merge_commit_sha": None,
        "user": {"id": snapshot.author_id, "login": snapshot.author_login},
        "head": {"sha": snapshot.head_sha, "ref": "feat/runtime"},
        "base": {"sha": snapshot.base_sha, "ref": "main"},
        "title": snapshot.title,
        "body": snapshot.body,
    }


def make_service(
    tmp_path: Path,
    clock: FakeClock,
    *,
    author_id: int = 7,
    author_login: str = "octocat",
) -> tuple[Settings, BotService, FakeGitHub, FakeOAuth]:
    settings = make_settings(tmp_path)
    snapshot = make_snapshot(author_id=author_id, author_login=author_login)
    github = FakeGitHub(settings, make_pull(snapshot))
    store = Store(settings.database)

    def generate(risk: Any, title: Any, body: Any, count: Any, *, structure: Any) -> list[Question]:
        assert risk == snapshot.risk
        assert title == snapshot.title
        assert body == snapshot.body
        assert count == settings.question_count
        assert structure == snapshot.structure
        return list(make_questions())

    def grade(question: Question, answer_text: str, hunk: Hunk, *, choice: int | None = None) -> Answer:
        del question, hunk, choice
        return Answer(anchor="anchor", text=answer_text, verdict="pass")

    service = BotService(
        settings,
        github,
        FakeReader(snapshot),
        store,
        generate=generate,
        grade=grade,
        clock=clock,
    )
    return settings, service, github, FakeOAuth()


def login(client: FlaskClient, code: str, next_path: str = "/dashboard") -> tuple[str, str]:
    begin = client.get(f"/auth/github?next={next_path}")
    location = begin.headers["Location"]
    state = parse_qs(urlsplit(location).query)["state"][0]
    callback = client.get(f"/auth/github/callback?state={state}&code={code}")
    sid = extract_cookie_value(callback.headers.getlist("Set-Cookie"), "lasthuman_sid")
    return sid, state


def extract_cookie_value(set_cookies: list[str], name: str) -> str:
    for header in set_cookies:
        if header.startswith(name + "="):
            return header.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"missing cookie {name}")


def extract_csrf(response_text: str) -> str:
    match = re.search(r'<meta name="csrf-token" content="([^"]+)"', response_text)
    assert match is not None
    return match.group(1)


def test_oauth_begin_callback_and_logout_enforce_safe_state_sid_and_csrf(tmp_path: Path) -> None:
    clock = FakeClock()
    settings, service, github, oauth = make_service(tmp_path, clock)
    app = create_app(
        settings,
        service=service,
        github=github,
        oauth=oauth,
        start_worker=False,
    )
    client = app.test_client()

    unsafe = client.get("/auth/github?next=https://evil.example/path")
    assert unsafe.status_code == 400

    begin = client.get("/auth/github?next=/dashboard")
    assert begin.status_code == 302
    assert begin.headers["Location"].startswith("https://github.com/login/oauth/authorize?")
    assert "code_challenge_method=S256" in begin.headers["Location"]
    initial_cookie = extract_cookie_value(begin.headers.getlist("Set-Cookie"), "lasthuman_sid")
    state = parse_qs(urlsplit(begin.headers["Location"]).query)["state"][0]
    assert "author-token" not in begin.headers["Set-Cookie"]

    wrong = client.get("/auth/github/callback?state=wrong-state&code=author-code")
    assert wrong.status_code == 400
    assert "Max-Age=0" not in ",".join(wrong.headers.getlist("Set-Cookie"))

    callback = client.get(f"/auth/github/callback?state={state}&code=author-code")
    assert callback.status_code == 302
    assert callback.headers["Location"].endswith("/dashboard")
    rotated_cookie = extract_cookie_value(callback.headers.getlist("Set-Cookie"), "lasthuman_sid")
    assert rotated_cookie != initial_cookie
    assert "author-token" not in callback.headers["Set-Cookie"]
    assert "refresh_token" not in callback.headers["Set-Cookie"]

    replay = client.get(f"/auth/github/callback?state={state}&code=author-code")
    assert replay.status_code == 400
    assert "Max-Age=0" not in ",".join(replay.headers.getlist("Set-Cookie"))

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    csrf_token = extract_csrf(dashboard.get_data(as_text=True))

    forbidden = client.post("/auth/logout")
    assert forbidden.status_code == 403

    logout = client.post("/auth/logout", data={"csrf_token": csrf_token})
    assert logout.status_code == 303
    assert "Max-Age=0" in ",".join(logout.headers.getlist("Set-Cookie"))


def test_oauth_callback_with_unbound_state_preserves_original_login(tmp_path: Path) -> None:
    clock = FakeClock()
    settings, service, github, oauth = make_service(tmp_path, clock)
    app = create_app(
        settings,
        service=service,
        github=github,
        oauth=oauth,
        start_worker=False,
    )
    primary = app.test_client()
    intruder = app.test_client()

    begin = primary.get("/auth/github?next=/dashboard")
    initial_sid = extract_cookie_value(begin.headers.getlist("Set-Cookie"), "lasthuman_sid")
    state = parse_qs(urlsplit(begin.headers["Location"]).query)["state"][0]

    unbound = intruder.get(f"/auth/github/callback?state={state}&code=author-code")
    assert unbound.status_code == 400
    assert "Max-Age=0" not in ",".join(unbound.headers.getlist("Set-Cookie"))

    callback = primary.get(f"/auth/github/callback?state={state}&code=author-code")
    assert callback.status_code == 302
    rotated_sid = extract_cookie_value(callback.headers.getlist("Set-Cookie"), "lasthuman_sid")
    assert rotated_sid != initial_sid


def test_oauth_invalid_code_requires_fresh_state_but_preserves_prelogin_cookie(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    settings, service, github, oauth = make_service(tmp_path, clock)
    app = create_app(
        settings,
        service=service,
        github=github,
        oauth=oauth,
        start_worker=False,
    )
    client = app.test_client()

    begin = client.get("/auth/github?next=/dashboard")
    sid = extract_cookie_value(begin.headers.getlist("Set-Cookie"), "lasthuman_sid")
    state = parse_qs(urlsplit(begin.headers["Location"]).query)["state"][0]

    invalid = client.get(f"/auth/github/callback?state={state}&code=missing-code")
    assert invalid.status_code == 401
    assert invalid.get_data(as_text=True) == "GitHub login failed"
    assert "Max-Age=0" not in ",".join(invalid.headers.getlist("Set-Cookie"))

    replay = client.get(f"/auth/github/callback?state={state}&code=author-code")
    assert replay.status_code == 400

    retry = client.get("/auth/github?next=/dashboard")
    assert extract_cookie_value(retry.headers.getlist("Set-Cookie"), "lasthuman_sid") == sid
    fresh_state = parse_qs(urlsplit(retry.headers["Location"]).query)["state"][0]

    success = client.get(f"/auth/github/callback?state={fresh_state}&code=author-code")
    assert success.status_code == 302


@pytest.mark.parametrize(
    "error",
    (
        OAuth2Error(error="invalid_grant", description="RAW_SECRET oauth token"),
        requests.RequestException("RAW_SECRET network token"),
    ),
    ids=("oauth2", "network"),
)
def test_oauth_callback_sanitizes_exchange_failures_without_leaking_tokens(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    error: BaseException,
) -> None:
    class FailingOAuth(FakeOAuth):
        def fetch_token(self, *, code: str, code_verifier: str) -> dict[str, object]:
            del code, code_verifier
            raise error

    clock = FakeClock()
    settings, service, github, _oauth = make_service(tmp_path, clock)
    app = create_app(
        settings,
        service=service,
        github=github,
        oauth=FailingOAuth(),
        start_worker=False,
    )
    client = app.test_client()

    caplog.set_level("INFO")
    begin = client.get("/auth/github?next=/dashboard")
    sid = extract_cookie_value(begin.headers.getlist("Set-Cookie"), "lasthuman_sid")
    state = parse_qs(urlsplit(begin.headers["Location"]).query)["state"][0]

    failed = client.get(f"/auth/github/callback?state={state}&code=author-code")
    assert failed.status_code == 401
    assert failed.get_data(as_text=True) == "GitHub login failed"
    assert "RAW_SECRET" not in failed.get_data(as_text=True)
    assert "RAW_SECRET" not in caplog.text
    assert "Max-Age=0" not in ",".join(failed.headers.getlist("Set-Cookie"))

    retry = client.get("/auth/github?next=/dashboard")
    assert extract_cookie_value(retry.headers.getlist("Set-Cookie"), "lasthuman_sid") == sid


def test_auth_repr_hides_sid_tokens_and_verifiers() -> None:
    session = auth_module.AuthorizedSession(
        sid="sid-secret",
        actor_id=7,
        actor_login="octocat",
        access_token="access-secret",
        csrf_token="csrf-secret",
        expires_at=4_000_000_000.0,
    )
    record = auth_module._SessionRecord(
        sid="sid-secret",
        csrf_token="csrf-secret",
        created_at=1.0,
        expires_at=2.0,
        access_token="access-secret",
    )
    pending = auth_module._PendingState(
        sid="sid-secret",
        code_verifier="verifier-secret",
        next_path="/dashboard",
        expires_at=3.0,
    )

    rendered = "\n".join((repr(session), repr(record), repr(pending)))
    for secret in ("sid-secret", "access-secret", "csrf-secret", "verifier-secret"):
        assert secret not in rendered


def test_authlib_fetch_token_uses_hardened_request_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeOAuth2Session:
        def __init__(self, **kwargs: object) -> None:
            captured["init"] = dict(kwargs)

        def fetch_token(self, url: str, **kwargs: object) -> dict[str, object]:
            captured["fetch_url"] = url
            captured["fetch_kwargs"] = dict(kwargs)
            return {"access_token": "token", "expires_at": 4_000_000_000}

    monkeypatch.setattr(auth_module, "OAuth2Session", FakeOAuth2Session)

    oauth = auth_module.AuthlibGitHubOAuth(make_settings(tmp_path))
    token = oauth.fetch_token(code="author-code", code_verifier="verifier-secret")

    assert token["access_token"] == "token"
    assert captured["fetch_url"] == "https://github.com/login/oauth/access_token"
    assert captured["fetch_kwargs"] == {
        "code": "author-code",
        "code_verifier": "verifier-secret",
        "headers": {"Accept": "application/json"},
        "timeout": (5, 30),
        "allow_redirects": False,
    }


def test_auth_rejects_wrong_author_revoked_ttl_and_restart_without_leaking_secrets(tmp_path: Path) -> None:
    clock = FakeClock()
    settings, service, github, oauth = make_service(tmp_path, clock)
    app = create_app(
        settings,
        service=service,
        github=github,
        oauth=oauth,
        start_worker=False,
    )
    client = app.test_client()

    _, _state = login(client, "other-code", "/prs/7")
    wrong_author = client.get("/prs/7")
    assert wrong_author.status_code == 403

    sid, _state = login(client, "author-code", "/dashboard")
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    csrf_token = extract_csrf(dashboard.get_data(as_text=True))

    github.revoked_tokens.add("author-token")
    revoked = client.get("/api/dashboard", headers={"X-CSRF-Token": csrf_token})
    assert revoked.status_code == 401
    assert "RAW_SECRET" not in revoked.get_data(as_text=True)

    github.revoked_tokens.clear()
    clock.advance(settings.session_ttl.total_seconds() + 1)
    expired = client.get("/dashboard")
    assert expired.status_code == 302
    assert "/auth/github?next=/dashboard" in expired.headers["Location"]

    restarted_app = create_app(
        settings,
        service=service,
        github=github,
        oauth=oauth,
        start_worker=False,
    )
    restarted_client = restarted_app.test_client()
    restarted_client.set_cookie("lasthuman_sid", sid)
    restarted = restarted_client.get("/dashboard")
    assert restarted.status_code == 302
    assert "/auth/github?next=/dashboard" in restarted.headers["Location"]
