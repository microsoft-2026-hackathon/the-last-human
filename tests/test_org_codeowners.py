from __future__ import annotations

from base64 import b64encode
from collections.abc import Callable
from pathlib import Path
from typing import cast
from unittest.mock import Mock
from urllib.parse import quote

import pytest
import requests

from test_app_github import FakeResponse, FakeSession, api_url
from lasthuman.ledger import CODEOWNERS_PATHS, parse_codeowners
from lasthuman.server.config import GatewaySettings, Settings
from lasthuman.server.github import (
    GitHubClient,
    GitHubError,
    GitHubInstallationDiscovery,
    JsonObject,
)
from lasthuman.server.registration import RegistrationDeniedError

_USER_TOKEN = "fixture-user-token-not-an-installation-token"
_SHA = "a" * 40
_BRANCH = "release/current-owners"
_REPO_PATH = "repos/example/project"
_LIMIT = 128 * 1024


def _client(
    session: FakeSession, *, access_guard: Callable[[], None] | None = None,
) -> GitHubClient:
    settings = Settings(
        app_id=101,
        client_id="fixture-client",
        client_secret="unused-fixture-secret",
        private_key_file=Path("unused-fixture-key"),
        installation_id=202,
        repository="example/project",
        repository_id=303,
        owner_id=404,
        base_url="https://bot.example",
        secret_key="unused-fixture-session-secret",
        database=Path("unused-fixture-database"),
        mode="internal",
        status_context="lasthuman",
        workflow="lasthuman-app.yml",
        workflow_ref="refs/heads/main",
        oidc_audience="fixture-audience",
    )
    return GitHubClient(settings, cast(requests.Session, session), access_guard=access_guard)


def _repository(**changes: object) -> JsonObject:
    return {
        "id": 303, "full_name": "example/project", "owner": {"id": 404},
        "default_branch": _BRANCH, **changes,
    }


def _contents(raw: bytes = b"/auth/ @example/current\n", **changes: object) -> JsonObject:
    return {
        "type": "file", "encoding": "base64", "size": len(raw),
        "content": b64encode(raw).decode("ascii"), **changes,
    }


def _commit_url(branch: str = _BRANCH) -> str:
    return api_url(f"{_REPO_PATH}/commits/{quote(branch, safe='')}")


def _contents_url(candidate: str, sha: str = _SHA) -> str:
    return api_url(f"{_REPO_PATH}/contents/{candidate}?ref={sha}")


def _enqueue_commit(session: FakeSession) -> None:
    session.enqueue("GET", _commit_url(), FakeResponse(200, {"sha": _SHA}))


def _assert_user_gets(session: FakeSession) -> None:
    assert session.calls
    for call in session.calls:
        assert call.method == "GET"
        assert call.url.startswith(api_url(_REPO_PATH) + "/") or call.url == api_url(_REPO_PATH)
        assert call.headers["Authorization"] == f"Bearer {_USER_TOKEN}"
        assert call.json_body is None
        assert call.allow_redirects is False
        assert call.timeout == (5, 30)


@pytest.mark.parametrize("candidate_index", range(len(CODEOWNERS_PATHS)))
@pytest.mark.parametrize("reuse_metadata", [False, True])
def test_current_codeowners_uses_one_default_branch_sha_and_candidate_priority(
    candidate_index: int, reuse_metadata: bool,
) -> None:
    session = FakeSession()
    client = _client(session)
    repository = _repository()
    expected_urls = []
    if not reuse_metadata:
        session.enqueue("GET", api_url(_REPO_PATH), FakeResponse(200, repository))
        expected_urls.append(api_url(_REPO_PATH))
    _enqueue_commit(session)
    expected_urls.append(_commit_url())
    for index, candidate in enumerate(CODEOWNERS_PATHS):
        session.enqueue(
            "GET", _contents_url(candidate),
            FakeResponse(404, {"message": "missing"}) if index < candidate_index else FakeResponse(200, _contents()),
        )
        if index <= candidate_index:
            expected_urls.append(_contents_url(candidate))

    assert client.current_codeowners(
        _USER_TOKEN, repository_info=repository if reuse_metadata else None,
    ) == {"/auth/": "@example/current"}
    assert [call.url for call in session.calls] == expected_urls
    _assert_user_gets(session)


def test_current_codeowners_missing_files_are_not_metadata_failures() -> None:
    session = FakeSession()
    _enqueue_commit(session)
    for candidate in CODEOWNERS_PATHS:
        session.enqueue("GET", _contents_url(candidate), FakeResponse(404))

    assert _client(session).current_codeowners(_USER_TOKEN, repository_info=_repository()) == {}
    assert [call.url for call in session.calls] == [
        _commit_url(), *(_contents_url(candidate) for candidate in CODEOWNERS_PATHS),
    ]
    _assert_user_gets(session)


@pytest.mark.parametrize("raw", [b"", b"# No declared contacts\n", b"/auth/\n"])
def test_current_codeowners_existing_empty_file_does_not_fall_back(raw: bytes) -> None:
    session = FakeSession()
    _enqueue_commit(session)
    session.enqueue("GET", _contents_url(CODEOWNERS_PATHS[0]), FakeResponse(200, _contents(raw)))
    session.enqueue("GET", _contents_url(CODEOWNERS_PATHS[1]), FakeResponse(200, _contents()))

    expected = {"/auth/": ""} if raw == b"/auth/\n" else {}
    assert _client(session).current_codeowners(_USER_TOKEN, repository_info=_repository()) == expected
    assert len(session.calls) == 2


def test_current_codeowners_reuses_parser_and_preserves_last_rule_order() -> None:
    session = FakeSession()
    _enqueue_commit(session)
    raw = (
        "# 현재 담당\n/auth/ @old\n* @example/general\n/auth/ @current @example/security # comment\n"
    ).encode("utf-8")
    payload = _contents(raw)
    encoded = str(payload["content"])
    payload["content"] = "\n".join(encoded[index:index + 60] for index in range(0, len(encoded), 60)) + "\n"
    session.enqueue("GET", _contents_url(CODEOWNERS_PATHS[0]), FakeResponse(200, payload))

    owners = _client(session).current_codeowners(_USER_TOKEN, repository_info=_repository())
    assert list(owners.items()) == [("*", "@example/general"), ("/auth/", "@current @example/security")]


@pytest.mark.parametrize(
    "payload",
    [
        [],
        "not-an-object",
        None,
        {},
        _contents(type="dir"),
        _contents(type="symlink"),
        _contents(type="submodule"),
        _contents(target="CODEOWNERS"),
        _contents(submodule_git_url="https://example.invalid/repository"),
        _contents(encoding="utf-8"),
        _contents(encoding="none"),
        _contents(content=None),
        _contents(content=123),
        _contents(content="not valid base64!"),
        _contents(content="é"),
        _contents(b"\xff\xfe"),
        _contents(size=None),
        _contents(size=True),
        _contents(size=-1),
        _contents(size="23"),
        _contents(size=23.0),
        _contents(size=0),
    ],
)
def test_current_codeowners_invalid_content_is_unavailable_not_empty(payload: object) -> None:
    session = FakeSession()
    _enqueue_commit(session)
    session.enqueue("GET", _contents_url(CODEOWNERS_PATHS[0]), FakeResponse(200, payload))
    session.enqueue("GET", _contents_url(CODEOWNERS_PATHS[1]), FakeResponse(200, _contents()))

    with pytest.raises(GitHubError, match="current CODEOWNERS metadata is unavailable") as error:
        _client(session).current_codeowners(_USER_TOKEN, repository_info=_repository())
    assert "@example" not in str(error.value)
    assert len(session.calls) == 2


@pytest.mark.parametrize(
    "payload",
    [
        _contents(size=_LIMIT + 1),
        _contents(b"#" * (_LIMIT + 1), size=_LIMIT),
        _contents(b"#" * (_LIMIT + 4), size=0),
        _contents(("#" + "é" * _LIMIT).encode("utf-8"), size=_LIMIT),
    ],
)
def test_current_codeowners_bounds_declared_and_decoded_bytes(payload: JsonObject) -> None:
    session = FakeSession()
    _enqueue_commit(session)
    session.enqueue("GET", _contents_url(CODEOWNERS_PATHS[0]), FakeResponse(200, payload))

    with pytest.raises(GitHubError, match="current CODEOWNERS metadata is unavailable"):
        _client(session).current_codeowners(_USER_TOKEN, repository_info=_repository())
    assert len(session.calls) == 2


def test_current_codeowners_accepts_exact_128_kib_limit() -> None:
    session = FakeSession()
    _enqueue_commit(session)
    raw = b"/auth/ @example/current\n#"
    raw += b"x" * (_LIMIT - len(raw))
    session.enqueue("GET", _contents_url(CODEOWNERS_PATHS[0]), FakeResponse(200, _contents(raw)))

    assert _client(session).current_codeowners(
        _USER_TOKEN, repository_info=_repository(),
    ) == {"/auth/": "@example/current"}


@pytest.mark.parametrize(
    "changes",
    [
        {"id": 999}, {"id": "303"}, {"id": True}, {"owner": {"id": 999}},
        {"owner": {"id": "404"}}, {"owner": {"id": True}}, {"owner": None},
        {"full_name": "other/project"}, {"full_name": "example/other"},
        {"full_name": "example/project/../other"},
    ],
)
@pytest.mark.parametrize("reuse_metadata", [False, True])
def test_current_codeowners_revalidates_repository_and_owner_identity(
    changes: JsonObject, reuse_metadata: bool,
) -> None:
    session = FakeSession()
    metadata = _repository(**changes)
    if not reuse_metadata:
        session.enqueue("GET", api_url(_REPO_PATH), FakeResponse(200, metadata))

    with pytest.raises(GitHubError):
        _client(session).current_codeowners(_USER_TOKEN, repository_info=metadata if reuse_metadata else None)
    assert len(session.calls) == (0 if reuse_metadata else 1)


def test_current_codeowners_accepts_case_insensitive_repository_identity() -> None:
    session = FakeSession()
    _enqueue_commit(session)
    session.enqueue("GET", _contents_url(CODEOWNERS_PATHS[0]), FakeResponse(200, _contents()))
    assert _client(session).current_codeowners(
        _USER_TOKEN, repository_info=_repository(full_name="EXAMPLE/PROJECT"),
    ) == {"/auth/": "@example/current"}


@pytest.mark.parametrize("default_branch", [None, "", 123, [], "\ud800"])
def test_current_codeowners_requires_current_default_branch(default_branch: object) -> None:
    session = FakeSession()
    with pytest.raises(GitHubError):
        _client(session).current_codeowners(_USER_TOKEN, repository_info=_repository(default_branch=default_branch))
    assert not session.calls


@pytest.mark.parametrize("sha", [None, "", "a" * 39, "A" * 40, "main", "../other", True])
def test_current_codeowners_requires_immutable_commit_sha(sha: object) -> None:
    session = FakeSession()
    session.enqueue("GET", _commit_url(), FakeResponse(200, {"sha": sha}))
    with pytest.raises(GitHubError):
        _client(session).current_codeowners(_USER_TOKEN, repository_info=_repository())
    assert len(session.calls) == 1


def test_current_codeowners_quotes_branch_without_widening_repository_scope() -> None:
    branch = "feature/owners?ref=other#fragment"
    session = FakeSession()
    session.enqueue("GET", _commit_url(branch), FakeResponse(200, {"sha": _SHA}))
    session.enqueue("GET", _contents_url(CODEOWNERS_PATHS[0]), FakeResponse(200, _contents()))

    assert _client(session).current_codeowners(
        _USER_TOKEN, repository_info=_repository(default_branch=branch),
    ) == {"/auth/": "@example/current"}
    assert session.calls[0].url.endswith("feature%2Fowners%3Fref%3Dother%23fragment")
    _assert_user_gets(session)


@pytest.mark.parametrize("stage", ["repository", "commit", "contents"])
@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_current_codeowners_preserves_upstream_status_without_leaking_payload(stage: str, status: int) -> None:
    session = FakeSession()
    failure = FakeResponse(
        status, {"message": f"private content {_USER_TOKEN}"},
        headers={"X-RateLimit-Remaining": "0"},
    )
    session.enqueue("GET", api_url(_REPO_PATH), failure if stage == "repository" else FakeResponse(200, _repository()))
    if stage != "repository":
        session.enqueue("GET", _commit_url(), failure if stage == "commit" else FakeResponse(200, {"sha": _SHA}))
    if stage == "contents":
        session.enqueue("GET", _contents_url(CODEOWNERS_PATHS[0]), failure)

    with pytest.raises(GitHubError) as error:
        _client(session).current_codeowners(_USER_TOKEN)
    assert error.value.status_code == status
    assert str(error.value) == f"GitHub current CODEOWNERS metadata is unavailable (HTTP {status})"
    assert _USER_TOKEN not in str(error.value)
    assert "private content" not in str(error.value)
    assert len(session.calls) == ["repository", "commit", "contents"].index(stage) + 1
    _assert_user_gets(session)


@pytest.mark.parametrize("stage", ["repository", "commit"])
def test_current_codeowners_does_not_confuse_metadata_404_with_missing_file(stage: str) -> None:
    session = FakeSession()
    session.enqueue(
        "GET", api_url(_REPO_PATH),
        FakeResponse(404) if stage == "repository" else FakeResponse(200, _repository()),
    )
    if stage == "commit":
        session.enqueue("GET", _commit_url(), FakeResponse(404))

    with pytest.raises(GitHubError) as error:
        _client(session).current_codeowners(_USER_TOKEN)
    assert error.value.status_code == 404
    assert len(session.calls) == (1 if stage == "repository" else 2)


@pytest.mark.parametrize(
    "failure",
    [
        requests.exceptions.Timeout(f"private content {_USER_TOKEN}"),
        requests.exceptions.ConnectionError(f"private content {_USER_TOKEN}"),
        requests.exceptions.RequestException(f"private content {_USER_TOKEN}"),
        FakeResponse(200, b"private invalid JSON"),
        FakeResponse(200, {"sha": _SHA}, headers={"Content-Length": str(2 * 1024 * 1024 + 1)}),
    ],
)
def test_current_codeowners_sanitizes_transport_and_response_failures(failure: FakeResponse | Exception) -> None:
    session = FakeSession()
    session.enqueue("GET", _commit_url(), failure)
    with pytest.raises(GitHubError) as error:
        _client(session).current_codeowners(_USER_TOKEN, repository_info=_repository())
    assert "private" not in str(error.value)
    assert _USER_TOKEN not in str(error.value)
    assert len(session.calls) == 1


@pytest.mark.parametrize("token", [None, "", " \t\n", 123])
def test_current_codeowners_never_falls_back_to_installation_token(
    token: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    client = _client(session)
    installation_token = Mock(side_effect=AssertionError("installation token must not be requested"))
    monkeypatch.setattr(client, "installation_token", installation_token)

    with pytest.raises(GitHubError, match="requires a user access token"):
        client.current_codeowners(cast(str, token), repository_info=_repository())
    installation_token.assert_not_called()
    assert not session.calls


def test_current_codeowners_checks_access_before_cached_metadata_use() -> None:
    session = FakeSession()
    guard = Mock(side_effect=RuntimeError("retired"))
    with pytest.raises(RuntimeError, match="retired"):
        _client(session, access_guard=guard).current_codeowners(_USER_TOKEN, repository_info=_repository())
    guard.assert_called_once_with()
    assert not session.calls


def test_current_codeowners_checks_access_again_before_returning(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    _enqueue_commit(session)
    response = FakeResponse(200, _contents())
    retired = False

    def retire_during_read() -> JsonObject:
        nonlocal retired
        retired = True
        return _contents()

    def guard() -> None:
        if retired:
            raise RuntimeError("retired")

    monkeypatch.setattr(response, "json", retire_during_read)
    session.enqueue("GET", _contents_url(CODEOWNERS_PATHS[0]), response)
    with pytest.raises(RuntimeError, match="retired"):
        _client(session, access_guard=guard).current_codeowners(_USER_TOKEN, repository_info=_repository())
    assert len(session.calls) == 2


def test_current_codeowners_resolves_again_on_the_next_read_not_from_history() -> None:
    session = FakeSession()
    client = _client(session)
    next_sha = "b" * 40
    session.enqueue("GET", _commit_url(), FakeResponse(200, {"sha": _SHA}), FakeResponse(200, {"sha": next_sha}))
    session.enqueue("GET", _contents_url(CODEOWNERS_PATHS[0]), FakeResponse(200, _contents()))
    session.enqueue(
        "GET", _contents_url(CODEOWNERS_PATHS[0], next_sha),
        FakeResponse(200, _contents(b"/auth/ @example/new\n")),
    )

    assert client.current_codeowners(_USER_TOKEN, repository_info=_repository()) == {"/auth/": "@example/current"}
    assert client.current_codeowners(_USER_TOKEN, repository_info=_repository()) == {"/auth/": "@example/new"}
    assert [call.url for call in session.calls] == [
        _commit_url(), _contents_url(CODEOWNERS_PATHS[0]),
        _commit_url(), _contents_url(CODEOWNERS_PATHS[0], next_sha),
    ]


def test_current_contact_parser_preserves_exclusions_without_changing_legacy_defaults() -> None:
    text = "/auth/ @example/security\n/auth/\n"
    assert parse_codeowners(text) == [("auth/", "@example/security")]
    assert parse_codeowners(text, include_unowned=True, preserve_patterns=True) == [
        ("/auth/", "@example/security"), ("/auth/", ""),
    ]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("aGVsbG8=", "hello"),
        (" aGVs\nbG8=\r\n", "hello"),
        (" \t\n", ""),
        (b64encode("한글".encode("utf-8")).decode("ascii"), "한글"),
        (b64encode(b"x" * (_LIMIT + 1)).decode("ascii"), "x" * (_LIMIT + 1)),
    ],
)
def test_shared_decoder_preserves_discovery_text_semantics(content: str, expected: str) -> None:
    session = FakeSession()
    session.enqueue(
        "GET", api_url(f"{_REPO_PATH}/contents/.lasthuman.yml?ref=main"),
        FakeResponse(200, {"type": "file", "encoding": "base64", "content": content}),
    )
    discovery = GitHubInstallationDiscovery(Mock(spec=GatewaySettings), cast(requests.Session, session))
    assert discovery._read_contents_text(  # pylint: disable=protected-access
        "example/project", ".lasthuman.yml", "main", _USER_TOKEN,
    ) == expected


@pytest.mark.parametrize(
    ("payload", "error_type"),
    [
        ({"type": "symlink", "encoding": "base64", "content": "aGVsbG8="}, RegistrationDeniedError),
        ({"type": "file", "encoding": "none", "content": "aGVsbG8="}, RegistrationDeniedError),
        ({"type": "file", "encoding": "base64", "content": ""}, GitHubError),
        ({"type": "file", "encoding": "base64"}, GitHubError),
        ({"type": "file", "encoding": "base64", "content": None}, GitHubError),
        ({"type": "file", "encoding": "base64", "content": 123}, GitHubError),
        ({"type": "file", "encoding": "base64", "content": "!!!"}, RegistrationDeniedError),
        ({"type": "file", "encoding": "base64", "content": "/w=="}, RegistrationDeniedError),
    ],
)
def test_shared_decoder_preserves_discovery_error_classification(
    payload: JsonObject, error_type: type[Exception],
) -> None:
    session = FakeSession()
    session.enqueue("GET", api_url(f"{_REPO_PATH}/contents/.lasthuman.yml?ref=main"), FakeResponse(200, payload))
    discovery = GitHubInstallationDiscovery(Mock(spec=GatewaySettings), cast(requests.Session, session))

    with pytest.raises(error_type):
        discovery._read_contents_text(  # pylint: disable=protected-access
            "example/project", ".lasthuman.yml", "main", _USER_TOKEN,
        )
