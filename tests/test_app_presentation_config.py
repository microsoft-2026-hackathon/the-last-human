from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
import requests

from test_app_github import (
    FakeResponse,
    FakeSession,
    api_url,
    bootstrap_app_auth,
    bootstrap_check_auth,
    make_settings,
)
from test_app_github import private_key_pair  # pylint: disable=unused-import  # Shared pytest fixture.

from lasthuman.server import ConfigurationError, GitHubClient, GitHubError


def test_presentation_settings_accept_explicit_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(
        monkeypatch,
        tmp_path,
        private_key_file,
        TLH_CHECK_RUNS="true",
        TLH_CHECK_NAME="The Last Human Checks",
        TLH_PRESENTATION_NAME="TLH Presentation",
        TLH_PRESENTATION_LOCALE="en",
        TLH_PRESENTATION_MAX_CHARS="12000",
        TLH_PRESENTATION_REASON_LIMIT="5",
        TLH_PRESENTATION_DETAIL_LIMIT="20",
        TLH_PRESENTATION_PATHS_PER_GROUP="4",
        TLH_QUESTION_COUNT="2",
    )

    assert settings.question_count == 2
    assert settings.checks_enabled is True
    assert settings.check_name == "The Last Human Checks"
    assert settings.presentation_name == "TLH Presentation"
    assert settings.presentation_locale == "en"
    assert settings.presentation_max_chars == 12000
    assert settings.presentation_reason_limit == 5
    assert settings.presentation_detail_limit == 20
    assert settings.presentation_paths_per_group == 4


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"TLH_CHECK_RUNS": "True"}, "TLH_CHECK_RUNS"),
        ({"TLH_CHECK_RUNS": "1"}, "TLH_CHECK_RUNS"),
        ({"TLH_CHECK_NAME": "last-human/human-verified-dev"}, "differ"),
        ({"TLH_CHECK_NAME": ""}, "TLH_CHECK_NAME"),
        ({"TLH_CHECK_NAME": "x\nname"}, "single line"),
        ({"TLH_PRESENTATION_NAME": ""}, "TLH_PRESENTATION_NAME"),
        ({"TLH_PRESENTATION_LOCALE": "fr"}, "TLH_PRESENTATION_LOCALE"),
        ({"TLH_PRESENTATION_MAX_CHARS": "999"}, "between 1000 and 100000"),
        ({"TLH_PRESENTATION_MAX_CHARS": "100001"}, "between 1000 and 100000"),
        ({"TLH_PRESENTATION_REASON_LIMIT": "0"}, "between 1 and 1000"),
        ({"TLH_PRESENTATION_DETAIL_LIMIT": "1001"}, "between 1 and 1000"),
        ({"TLH_PRESENTATION_PATHS_PER_GROUP": "many"}, "integer"),
        ({"TLH_QUESTION_COUNT": "0"}, "between 1 and 5"),
        ({"TLH_QUESTION_COUNT": "6"}, "between 1 and 5"),
    ],
)
def test_presentation_settings_fail_fast_without_silent_clamping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
    overrides: dict[str, str],
    expected: str,
) -> None:
    private_key_file, _ = private_key_pair

    with pytest.raises(ConfigurationError, match=expected):
        make_settings(monkeypatch, tmp_path, private_key_file, **overrides)


def test_disabled_installation_token_scopes_do_not_request_checks_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    session = FakeSession()
    bootstrap_app_auth(session, settings)

    GitHubClient(settings, cast(requests.Session, session)).installation_token()

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


def test_enabled_core_installation_token_scopes_do_not_request_checks_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file, TLH_CHECK_RUNS="true")
    session = FakeSession()
    bootstrap_app_auth(session, settings)

    GitHubClient(settings, cast(requests.Session, session)).installation_token()

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


def test_enabled_check_token_scopes_request_only_checks_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file, TLH_CHECK_RUNS="true")
    session = FakeSession()
    bootstrap_app_auth(session, settings, token="core-token")
    bootstrap_check_auth(session, settings, token="checks-token")

    client = GitHubClient(settings, cast(requests.Session, session))
    assert client._check_token() == "checks-token"  # pylint: disable=protected-access

    post_calls = [
        call
        for call in session.calls
        if call.method == "POST"
        and call.url == api_url(f"app/installations/{settings.installation_id}/access_tokens")
    ]
    assert [call.json_body for call in post_calls] == [
        {
            "repository_ids": [settings.repository_id],
            "permissions": {
                "contents": "read",
                "pull_requests": "write",
                "statuses": "write",
                "actions": "write",
            },
        },
        {
            "repository_ids": [settings.repository_id],
            "permissions": {"checks": "write"},
        },
    ]


def test_enabled_checks_token_permission_errors_are_not_silently_downgraded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file, TLH_CHECK_RUNS="true")
    session = FakeSession()
    bootstrap_app_auth(session, settings, token="core-token")
    session.enqueue(
        "POST",
        api_url(f"app/installations/{settings.installation_id}/access_tokens"),
        FakeResponse(403, {"message": "Resource not accessible by integration"}),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    with pytest.raises(GitHubError, match="HTTP 403"):
        client.ensure_check_run(
            "a" * 40,
            "pr-7-snapshot-1",
            status="in_progress",
            conclusion=None,
            title="Provided title",
            summary="Provided summary",
            details_url="http://localhost:8000/pr/7",
        )

    post_calls = [
        call
        for call in session.calls
        if call.method == "POST"
        and call.url == api_url(f"app/installations/{settings.installation_id}/access_tokens")
    ]
    assert post_calls[-1].json_body == {
        "repository_ids": [settings.repository_id],
        "permissions": {"checks": "write"},
    }
