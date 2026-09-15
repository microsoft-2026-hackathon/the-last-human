from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import jwt
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from lasthuman.server import (
    ConfigurationError,
    GitHubClient,
    GitHubError,
    GitHubUncertainResultError,
    Settings,
)

ENV_NAMES = (
    "TLH_APP_ID",
    "TLH_CLIENT_ID",
    "TLH_CLIENT_SECRET",
    "TLH_PRIVATE_KEY_FILE",
    "TLH_INSTALLATION_ID",
    "TLH_REPOSITORY",
    "TLH_REPOSITORY_ID",
    "TLH_OWNER_ID",
    "TLH_BASE_URL",
    "TLH_SECRET_KEY",
    "TLH_DATABASE",
    "TLH_MODE",
    "TLH_STATUS_CONTEXT",
    "TLH_WORKFLOW",
    "TLH_WORKFLOW_REF",
    "TLH_OIDC_AUDIENCE",
)


@dataclass
class RecordedCall:
    method: str
    url: str
    headers: dict[str, str]
    json_body: object | None
    allow_redirects: bool
    timeout: tuple[int, int]


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: object | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = dict(headers or {})
        self._payload = payload
        if payload is None:
            self.content = b""
        elif isinstance(payload, bytes):
            self.content = payload
        else:
            self.content = json.dumps(payload).encode("utf-8")
            self.headers.setdefault("Content-Type", "application/json")
        self.headers.setdefault("Content-Length", str(len(self.content)))

    def json(self) -> object:
        if self._payload is None or isinstance(self._payload, bytes):
            raise ValueError("empty body")
        return self._payload


class FakeSession:
    def __init__(self) -> None:
        self._routes: dict[tuple[str, str], list[FakeResponse | Exception]] = {}
        self.calls: list[RecordedCall] = []

    def enqueue(self, method: str, url: str, *outcomes: FakeResponse | Exception) -> None:
        self._routes.setdefault((method.upper(), url), []).extend(outcomes)

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
        key = (method.upper(), url)
        queue = self._routes.get(key)
        if not queue:
            raise AssertionError(f"unexpected request: {method.upper()} {url}")
        outcome = queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture()
def private_key_pair(tmp_path: Path) -> tuple[Path, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    path = tmp_path / "github-app.pem"
    path.write_bytes(private_pem)
    path.chmod(0o600)
    return path, public_pem


def apply_env(monkeypatch: pytest.MonkeyPatch, values: dict[str, str], cwd: Path) -> None:
    monkeypatch.chdir(cwd)
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def build_env(tmp_path: Path, private_key_file: Path, **overrides: str) -> dict[str, str]:
    env = {
        "TLH_APP_ID": "101",
        "TLH_CLIENT_ID": "Iv1.abc123",
        "TLH_CLIENT_SECRET": "c" * 32,
        "TLH_PRIVATE_KEY_FILE": str(private_key_file),
        "TLH_INSTALLATION_ID": "202",
        "TLH_REPOSITORY": "hunhoon21/the-last-human",
        "TLH_REPOSITORY_ID": "1361123778",
        "TLH_OWNER_ID": "36983960",
        "TLH_BASE_URL": "http://localhost:8000/",
        "TLH_SECRET_KEY": "s" * 32,
    }
    env.update(overrides)
    return env


def make_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_file: Path,
    **overrides: str,
) -> Settings:
    apply_env(monkeypatch, build_env(tmp_path, private_key_file, **overrides), tmp_path)
    return Settings.from_env()


def api_url(path: str) -> str:
    return f"https://api.github.com/{path}"


def iso8601_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def repo_payload(
    settings: Settings,
    *,
    repo_id: int | None = None,
    full_name: str | None = None,
    owner_id: int | None = None,
) -> dict[str, object]:
    return {
        "id": settings.repository_id if repo_id is None else repo_id,
        "full_name": settings.repository if full_name is None else full_name,
        "owner": {"id": settings.owner_id if owner_id is None else owner_id},
    }


def installation_payload(
    settings: Settings,
    *,
    installation_id: int | None = None,
    owner_id: int | None = None,
    app_id: int | None = None,
) -> dict[str, object]:
    return {
        "id": settings.installation_id if installation_id is None else installation_id,
        "account": {"id": settings.owner_id if owner_id is None else owner_id},
        "app_id": settings.app_id if app_id is None else app_id,
    }


def pull_payload(settings: Settings, number: int, sha: str) -> dict[str, object]:
    return {
        "number": number,
        "base": {"repo": repo_payload(settings)},
        "head": {"sha": sha},
    }


def comment_payload(
    settings: Settings,
    publication_id: str,
    *,
    comment_id: int,
    bot: bool = True,
    app_id: int | None = None,
) -> dict[str, object]:
    return {
        "id": comment_id,
        "html_url": (
            "https://github.com/hunhoon21/the-last-human/pull/7"
            f"#issuecomment-{comment_id}"
        ),
        "body": f"Started\n\n<!-- lasthuman:publication:{publication_id} -->",
        "user": {"type": "Bot" if bot else "User"},
        "performed_via_github_app": {
            "id": settings.app_id if app_id is None else app_id
        },
    }


def bootstrap_app_auth(session: FakeSession, settings: Settings, token: str = "inst-token") -> None:
    session.enqueue(
        "GET",
        api_url(f"repos/{settings.repository}/installation"),
        FakeResponse(200, installation_payload(settings)),
    )
    session.enqueue(
        "POST",
        api_url(f"app/installations/{settings.installation_id}/access_tokens"),
        FakeResponse(201, {"token": token, "expires_at": iso8601_after(3600)}),
    )
    session.enqueue(
        "GET",
        api_url(f"repos/{settings.repository}"),
        FakeResponse(200, repo_payload(settings)),
    )


def test_settings_from_env_applies_defaults_and_fixed_ttl(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)

    assert settings.app_id == 101
    assert settings.client_id == "Iv1.abc123"
    assert settings.installation_id == 202
    assert settings.repository == "hunhoon21/the-last-human"
    assert settings.base_url == "http://localhost:8000"
    assert settings.database == tmp_path / ".work" / "lasthuman.sqlite3"
    assert settings.mode == "development"
    assert settings.status_context == "comprehension-gate-dev"
    assert settings.workflow == "lasthuman-app.yml"
    assert settings.workflow_ref == "refs/heads/main"
    assert settings.oidc_audience == settings.repository
    assert settings.question_count == 3
    assert settings.session_ttl == timedelta(minutes=30)
    assert "client_secret" not in repr(settings)
    assert "private_key_file" not in repr(settings)
    assert "secret_key" not in repr(settings)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"TLH_SECRET_KEY": "short"}, "TLH_SECRET_KEY"),
        ({"TLH_BASE_URL": "http://example.com"}, "loopback"),
        ({"TLH_STATUS_CONTEXT": "comprehension-gate"}, "non-production"),
        (
            {
                "TLH_MODE": "live",
                "TLH_BASE_URL": "http://localhost:8000",
            },
            "https",
        ),
        (
            {
                "TLH_MODE": "live",
                "TLH_STATUS_CONTEXT": "comprehension-gate-dev",
            },
            "status context",
        ),
    ],
)
def test_settings_from_env_fails_fast_for_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
    overrides: dict[str, str],
    expected: str,
) -> None:
    private_key_file, _ = private_key_pair
    apply_env(monkeypatch, build_env(tmp_path, private_key_file, **overrides), tmp_path)

    with pytest.raises(ConfigurationError, match=expected):
        Settings.from_env()


def test_settings_from_env_rejects_symlinked_private_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    link_path = tmp_path / "private-key-link.pem"
    link_path.symlink_to(private_key_file)
    apply_env(monkeypatch, build_env(tmp_path, link_path), tmp_path)

    with pytest.raises(ConfigurationError, match="symlink"):
        Settings.from_env()


def test_settings_from_env_rejects_world_readable_private_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    private_key_file.chmod(0o644)
    apply_env(monkeypatch, build_env(tmp_path, private_key_file), tmp_path)

    with pytest.raises(ConfigurationError, match="permissions"):
        Settings.from_env()


def test_installation_token_is_narrowed_and_app_jwt_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, public_pem = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    session = FakeSession()
    bootstrap_app_auth(session, settings, token="inst-token")

    client = GitHubClient(settings, cast(requests.Session, session))
    assert client.installation_token() == "inst-token"
    assert client.installation_token() == "inst-token"
    assert [(call.method, call.url) for call in session.calls] == [
        ("GET", api_url(f"repos/{settings.repository}/installation")),
        ("POST", api_url(f"app/installations/{settings.installation_id}/access_tokens")),
        ("GET", api_url(f"repos/{settings.repository}")),
    ]

    post_call = next(
        call
        for call in session.calls
        if call.method == "POST"
        and call.url == api_url(f"app/installations/{settings.installation_id}/access_tokens")
    )
    assert post_call.json_body == {
        "repository_ids": [settings.repository_id],
        "permissions": {
            "contents": "read",
            "pull_requests": "write",
            "statuses": "write",
            "actions": "write",
        },
    }
    assert post_call.allow_redirects is False
    assert post_call.timeout == (5, 30)
    assert len(
        [
            call
            for call in session.calls
            if call.method == "POST"
            and call.url == api_url(f"app/installations/{settings.installation_id}/access_tokens")
        ]
    ) == 1

    app_jwt = session.calls[0].headers["Authorization"].split(" ", 1)[1]
    claims = jwt.decode(
        app_jwt,
        public_pem,
        algorithms=["RS256"],
        options={"verify_aud": False, "verify_exp": False},
    )
    now = int(time.time())
    assert claims["iss"] == settings.client_id
    assert claims["exp"] - claims["iat"] == 600
    assert now - 70 <= claims["iat"] <= now


def test_installation_token_refreshes_when_expiry_is_too_close(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    session = FakeSession()
    session.enqueue(
        "GET",
        api_url(f"repos/{settings.repository}/installation"),
        FakeResponse(200, installation_payload(settings)),
        FakeResponse(200, installation_payload(settings)),
    )
    session.enqueue(
        "POST",
        api_url(f"app/installations/{settings.installation_id}/access_tokens"),
        FakeResponse(201, {"token": "too-soon", "expires_at": iso8601_after(30)}),
        FakeResponse(201, {"token": "fresh-token", "expires_at": iso8601_after(3600)}),
    )
    session.enqueue(
        "GET",
        api_url(f"repos/{settings.repository}"),
        FakeResponse(200, repo_payload(settings)),
        FakeResponse(200, repo_payload(settings)),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    assert client.installation_token() == "too-soon"
    assert client.installation_token() == "fresh-token"


def test_verify_repository_refreshes_cached_binding_when_token_expires(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    session = FakeSession()
    session.enqueue(
        "GET",
        api_url(f"repos/{settings.repository}/installation"),
        FakeResponse(200, installation_payload(settings)),
        FakeResponse(200, installation_payload(settings)),
    )
    session.enqueue(
        "POST",
        api_url(f"app/installations/{settings.installation_id}/access_tokens"),
        FakeResponse(201, {"token": "too-soon", "expires_at": iso8601_after(30)}),
        FakeResponse(201, {"token": "fresh-token", "expires_at": iso8601_after(3600)}),
    )
    session.enqueue(
        "GET",
        api_url(f"repos/{settings.repository}"),
        FakeResponse(200, repo_payload(settings)),
        FakeResponse(200, repo_payload(settings)),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    client.verify_repository()
    client.verify_repository()

    assert [
        (call.method, call.url)
        for call in session.calls
        if call.url in {
            api_url(f"repos/{settings.repository}/installation"),
            api_url(f"app/installations/{settings.installation_id}/access_tokens"),
            api_url(f"repos/{settings.repository}"),
        }
    ] == [
        ("GET", api_url(f"repos/{settings.repository}/installation")),
        ("POST", api_url(f"app/installations/{settings.installation_id}/access_tokens")),
        ("GET", api_url(f"repos/{settings.repository}")),
        ("GET", api_url(f"repos/{settings.repository}/installation")),
        ("POST", api_url(f"app/installations/{settings.installation_id}/access_tokens")),
        ("GET", api_url(f"repos/{settings.repository}")),
    ]


@pytest.mark.parametrize(
    ("repo", "installation", "expected"),
    [
        ({"repo_id": 9}, {}, "repository id"),
        ({"full_name": "hunhoon21/other-repo"}, {}, "repository name"),
        ({}, {"installation_id": 99}, "installation id"),
        ({}, {"owner_id": 99}, "installation owner"),
        ({}, {"app_id": 99}, "app id"),
    ],
)
def test_verify_repository_rejects_mismatched_bindings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
    repo: dict[str, int | str],
    installation: dict[str, int],
    expected: str,
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    session = FakeSession()
    session.enqueue(
        "GET",
        api_url(f"repos/{settings.repository}/installation"),
        FakeResponse(200, installation_payload(settings, **installation)),
    )
    session.enqueue(
        "POST",
        api_url(f"app/installations/{settings.installation_id}/access_tokens"),
        FakeResponse(201, {"token": "inst-token", "expires_at": iso8601_after(3600)}),
    )
    session.enqueue(
        "GET",
        api_url(f"repos/{settings.repository}"),
        FakeResponse(200, repo_payload(settings, **repo)),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    with pytest.raises(GitHubError, match=expected):
        client.verify_repository()


@pytest.mark.parametrize(
    "relative_path",
    [
        "../user",
        "https://api.github.com/user",
        "//user:pass@api.github.com/user",
        f"repos/hunhoon21/the-last-human/%2e%2e/{'user'}",
    ],
)
def test_request_rejects_cross_origin_and_parent_traversal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
    relative_path: str,
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    client = GitHubClient(settings, cast(requests.Session, FakeSession()))

    with pytest.raises(GitHubError, match="relative API path"):
        client.request("GET", relative_path)


def test_request_preserves_relative_query_paths_and_rejects_public_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    session = FakeSession()
    query_path = f"/repos/{settings.repository}/issues?per_page=1&page=2"
    session.enqueue(
        "GET",
        api_url(f"repos/{settings.repository}/issues?per_page=1&page=2"),
        FakeResponse(200, {"ok": True}),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    payload = client.request("GET", query_path, token_override="user-access-token")

    assert payload == {"ok": True}
    assert session.calls[0].url == api_url(f"repos/{settings.repository}/issues?per_page=1&page=2")
    with pytest.raises(GitHubError, match="scoped methods"):
        client.request("POST", f"repos/{settings.repository}/issues", token_override="user-access-token")


@pytest.mark.parametrize("value", [True, "7"])
def test_pull_rejects_bool_and_non_integer_numbers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
    value: object,
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    client = GitHubClient(settings, cast(requests.Session, FakeSession()))

    with pytest.raises(GitHubError, match="positive"):
        client.pull(value, user_token="user-access-token")


def test_verify_repository_reports_private_rsa_key_errors_safely(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    private_key_file = tmp_path / "github-app.pem"
    private_key_file.write_text("not a private key", encoding="utf-8")
    private_key_file.chmod(0o600)
    settings = make_settings(monkeypatch, tmp_path, private_key_file)

    client = GitHubClient(settings, cast(requests.Session, FakeSession()))
    with pytest.raises(GitHubError, match="private RSA key") as error:
        client.verify_repository()

    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True


def test_verify_repository_reports_timeouts_explicitly_without_leaking_cause(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    session = FakeSession()
    session.enqueue(
        "GET",
        api_url(f"repos/{settings.repository}/installation"),
        requests.exceptions.Timeout("network timed out"),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    with pytest.raises(GitHubError, match="timed out") as error:
        client.verify_repository()

    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True


def test_user_reads_only_with_user_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    session = FakeSession()
    access_token = "user-access-token"
    session.enqueue("GET", api_url("user"), FakeResponse(200, {"id": 7, "login": "author1"}))

    client = GitHubClient(settings, cast(requests.Session, session))
    user = client.user(access_token)

    assert user["id"] == 7
    assert len(session.calls) == 1
    assert session.calls[0].method == "GET"
    assert session.calls[0].url == api_url("user")
    assert session.calls[0].headers["Accept"] == "application/vnd.github+json"
    scheme, sent_token = session.calls[0].headers["Authorization"].split(" ", 1)
    assert scheme == "Bearer"
    assert sent_token == access_token
    assert session.calls[0].json_body is None
    assert session.calls[0].allow_redirects is False
    assert session.calls[0].timeout == (5, 30)


def test_pull_and_pulls_with_head_are_scoped_to_the_configured_repo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    session = FakeSession()
    bootstrap_app_auth(session, settings)
    head_sha = "a" * 40
    session.enqueue(
        "GET",
        api_url(f"repos/{settings.repository}/pulls/7"),
        FakeResponse(200, pull_payload(settings, 7, head_sha)),
    )
    session.enqueue(
        "GET",
        api_url(f"repos/{settings.repository}/pulls?state=open&per_page=100&page=1"),
        FakeResponse(
            200,
            [
                pull_payload(settings, 7, head_sha),
                pull_payload(settings, 8, "b" * 40),
            ],
        ),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    pull = client.pull(7)
    matches = client.pulls_with_head(head_sha)

    assert pull["number"] == 7
    assert [item["number"] for item in matches] == [7]


def test_files_raises_when_the_pagination_cap_is_exceeded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    session = FakeSession()
    bootstrap_app_auth(session, settings)
    for page in range(1, 11):
        session.enqueue(
            "GET",
            api_url(f"repos/{settings.repository}/pulls/7/files?per_page=100&page={page}"),
            FakeResponse(
                200,
                [
                    {"filename": f"file-{page}-{index}.py"}
                    for index in range(100)
                ],
            ),
        )

    client = GitHubClient(settings, cast(requests.Session, session))
    with pytest.raises(GitHubError, match="pagination cap"):
        client.files(7)


def test_ensure_comment_reuses_app_comment_and_ignores_copied_markers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    session = FakeSession()
    bootstrap_app_auth(session, settings)
    session.enqueue(
        "GET",
        api_url(f"repos/{settings.repository}/issues/7/comments?per_page=100&page=1"),
        FakeResponse(
            200,
            [
                comment_payload(settings, "receipt-1", comment_id=1, bot=False),
                comment_payload(settings, "receipt-12", comment_id=2),
                comment_payload(settings, "receipt-1", comment_id=3),
            ],
        ),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    comment = client.ensure_comment(7, "Started", "receipt-1")

    assert comment["id"] == 3
    assert not [
        call
        for call in session.calls
        if call.method == "POST"
        and call.url == api_url(f"repos/{settings.repository}/issues/7/comments")
    ]


def test_ensure_comment_requires_app_attribution_on_new_comments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    session = FakeSession()
    bootstrap_app_auth(session, settings)
    session.enqueue(
        "GET",
        api_url(f"repos/{settings.repository}/issues/7/comments?per_page=100&page=1"),
        FakeResponse(200, []),
    )
    session.enqueue(
        "POST",
        api_url(f"repos/{settings.repository}/issues/7/comments"),
        FakeResponse(201, comment_payload(settings, "receipt-2", comment_id=9, app_id=999)),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    with pytest.raises(GitHubError, match="attribution"):
        client.ensure_comment(7, "Started", "receipt-2")


def test_ensure_comment_reconciles_after_timeout_without_duplicate_post(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    session = FakeSession()
    bootstrap_app_auth(session, settings)
    comments_url = api_url(f"repos/{settings.repository}/issues/7/comments?per_page=100&page=1")
    session.enqueue("GET", comments_url, FakeResponse(200, []), FakeResponse(200, [comment_payload(settings, "receipt-3", comment_id=13)]))
    session.enqueue(
        "POST",
        api_url(f"repos/{settings.repository}/issues/7/comments"),
        requests.exceptions.Timeout("network timed out"),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    comment = client.ensure_comment(7, "Started", "receipt-3")

    assert comment["id"] == 13
    assert len(
        [
            call
            for call in session.calls
            if call.method == "POST"
            and call.url == api_url(f"repos/{settings.repository}/issues/7/comments")
        ]
    ) == 1


def test_ensure_comment_raises_explicit_uncertain_outcome_when_reconciliation_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    session = FakeSession()
    bootstrap_app_auth(session, settings)
    comments_url = api_url(f"repos/{settings.repository}/issues/7/comments?per_page=100&page=1")
    session.enqueue("GET", comments_url, FakeResponse(200, []), FakeResponse(200, []))
    session.enqueue(
        "POST",
        api_url(f"repos/{settings.repository}/issues/7/comments"),
        requests.exceptions.ConnectionError("connection dropped"),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    with pytest.raises(GitHubUncertainResultError, match="uncertain"):
        client.ensure_comment(7, "Started", "receipt-4")


def test_set_status_posts_even_when_matching_status_already_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    session = FakeSession()
    bootstrap_app_auth(session, settings)
    sha = "f" * 40
    target_url = "http://localhost:8000/pr/7"
    session.enqueue(
        "GET",
        api_url(f"repos/{settings.repository}/commits/{sha}/status"),
        FakeResponse(
            200,
            {
                "statuses": [
                    {
                        "context": settings.status_context,
                        "state": "pending",
                        "description": "In progress",
                        "target_url": target_url,
                        "creator": {"login": "other-bot"},
                    }
                ]
            },
        ),
    )
    session.enqueue(
        "POST",
        api_url(f"repos/{settings.repository}/statuses/{sha}"),
        FakeResponse(
            201,
            {
                "context": settings.status_context,
                "state": "pending",
                "description": "In progress",
                "target_url": target_url,
            },
        ),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    status = client.set_status(sha, "pending", "In progress", target_url)

    assert status["state"] == "pending"
    assert len(
        [
            call
            for call in session.calls
            if call.method == "POST"
            and call.url == api_url(f"repos/{settings.repository}/statuses/{sha}")
        ]
    ) == 1


def test_github_errors_scrub_response_bodies_and_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    session = FakeSession()
    session.enqueue(
        "GET",
        api_url(f"repos/{settings.repository}/installation"),
        FakeResponse(500, {"message": f"boom {settings.client_secret} secret-token"}),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    with pytest.raises(GitHubError) as error:
        client.verify_repository()

    message = str(error.value)
    assert settings.client_secret not in message
    assert "secret-token" not in message
    assert "boom" not in message


def test_github_errors_suppress_invalid_json_causes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    session = FakeSession()
    session.enqueue("GET", api_url("user"), FakeResponse(200, b"not-json"))

    client = GitHubClient(settings, cast(requests.Session, session))
    with pytest.raises(GitHubError, match="invalid JSON") as error:
        client.user("user-access-token")

    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True


def test_set_status_rejects_invalid_sha_and_cross_origin_targets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    client = GitHubClient(settings, cast(requests.Session, FakeSession()))

    with pytest.raises(GitHubError, match="40-character"):
        client.set_status("abc", "pending", "In progress", "http://localhost:8000/pr/7")
    with pytest.raises(GitHubError, match="target URL"):
        client.set_status("a" * 40, "pending", "In progress", "https://evil.example/pr/7")


def test_dispatch_verification_posts_trusted_ref_and_receipt_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    session = FakeSession()
    bootstrap_app_auth(session, settings)
    dispatch_url = api_url(
        f"repos/{settings.repository}/actions/workflows/{settings.workflow}/dispatches"
    )
    session.enqueue("POST", dispatch_url, FakeResponse(204))

    client = GitHubClient(settings, cast(requests.Session, session))
    client.dispatch_verification("receipt-5")

    post_call = next(
        call
        for call in session.calls
        if call.method == "POST" and call.url == dispatch_url
    )
    assert post_call.json_body == {
        "ref": settings.workflow_ref,
        "inputs": {"receipt_id": "receipt-5"},
    }
