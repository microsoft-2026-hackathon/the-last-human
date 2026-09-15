from __future__ import annotations

from pathlib import Path
from typing import cast
from urllib.parse import quote

import pytest
import requests

from test_app_github import (
    FakeResponse,
    FakeSession,
    api_url,
    bootstrap_app_auth,
    bootstrap_check_auth,
    make_settings,
    repo_payload,
)
from test_app_github import private_key_pair  # pylint: disable=unused-import  # Shared pytest fixture.

from lasthuman.server import GitHubClient, GitHubError, GitHubUncertainResultError, Settings

SHA = "a" * 40
EXTERNAL_ID = "pr-7-snapshot-one"
DETAILS_URL = "http://localhost:8000/pr/7"


def pr_card_payload(
    settings: Settings,
    pr: int,
    *,
    body: str,
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
        "body": f"{body.rstrip()}\n\n<!-- lasthuman:pr-card:{pr} -->",
        "user": {"type": "Bot" if bot else "User"},
        "performed_via_github_app": {
            "id": settings.app_id if app_id is None else app_id
        },
    }


def check_runs_url(settings: Settings, sha: str = SHA) -> str:
    encoded_name = quote(settings.check_name, safe="")
    return api_url(
        f"repos/{settings.repository}/commits/{sha}/check-runs?"
        f"check_name={encoded_name}&filter=all&per_page=100&page=1"
    )


def check_run_payload(
    settings: Settings,
    *,
    check_run_id: int = 51,
    sha: str = SHA,
    external_id: str = EXTERNAL_ID,
    name: str | None = None,
    app_id: int | None = None,
    status: str = "in_progress",
    conclusion: str | None = None,
    title: str = "Provided title",
    summary: str = "Provided summary",
    details_url: str = DETAILS_URL,
) -> dict[str, object]:
    return {
        "id": check_run_id,
        "html_url": f"https://github.com/hunhoon21/the-last-human/runs/{check_run_id}",
        "name": settings.check_name if name is None else name,
        "head_sha": sha,
        "external_id": external_id,
        "status": status,
        "conclusion": conclusion,
        "details_url": details_url,
        "output": {"title": title, "summary": summary},
        "app": {"id": settings.app_id if app_id is None else app_id},
    }


def check_runs_response(*runs: dict[str, object]) -> dict[str, object]:
    return {"total_count": len(runs), "check_runs": list(runs)}


def test_find_pr_card_uses_owned_canonical_marker_and_ignores_spoofs(
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
                pr_card_payload(settings, 7, body="User copy", comment_id=1, bot=False),
                pr_card_payload(settings, 7, body="Wrong app", comment_id=2, app_id=999),
                pr_card_payload(settings, 7, body="Current card", comment_id=3),
            ],
        ),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    card = client.find_pr_card(7)

    assert card is not None
    assert card["id"] == 3
    assert card["body"] == "Current card\n\n<!-- lasthuman:pr-card:7 -->"


def test_find_pr_card_rejects_ambiguous_owned_markers(
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
                pr_card_payload(settings, 7, body="First", comment_id=1),
                pr_card_payload(settings, 7, body="Second", comment_id=2),
            ],
        ),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    with pytest.raises(GitHubError, match="ambiguous"):
        client.find_pr_card(7)


def test_ensure_pr_card_creates_reuses_and_updates_canonical_comment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    session = FakeSession()
    bootstrap_app_auth(session, settings)
    comments_url = api_url(f"repos/{settings.repository}/issues/7/comments?per_page=100&page=1")
    comments_post_url = api_url(f"repos/{settings.repository}/issues/7/comments")
    comments_patch_url = api_url(f"repos/{settings.repository}/issues/comments/11")
    created = pr_card_payload(settings, 7, body="Initial card", comment_id=11)
    updated = pr_card_payload(settings, 7, body="Updated card", comment_id=11)
    session.enqueue("GET", comments_url, FakeResponse(200, []))
    session.enqueue("POST", comments_post_url, FakeResponse(201, created))
    session.enqueue("GET", comments_url, FakeResponse(200, [created]))
    session.enqueue("GET", comments_url, FakeResponse(200, [created]))
    session.enqueue("PATCH", comments_patch_url, FakeResponse(200, updated))

    client = GitHubClient(settings, cast(requests.Session, session))
    assert client.ensure_pr_card(7, "Initial card")["id"] == 11
    assert client.ensure_pr_card(7, "Initial card")["id"] == 11
    assert client.ensure_pr_card(7, "Updated card")["id"] == 11

    assert [
        (call.method, call.url)
        for call in session.calls
        if call.url in {comments_post_url, comments_patch_url}
    ] == [("POST", comments_post_url), ("PATCH", comments_patch_url)]
    assert session.calls[-1].json_body == {
        "body": "Updated card\n\n<!-- lasthuman:pr-card:7 -->"
    }


def test_ensure_pr_card_reconciles_successful_patch_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    session = FakeSession()
    bootstrap_app_auth(session, settings)
    comments_url = api_url(f"repos/{settings.repository}/issues/7/comments?per_page=100&page=1")
    comments_patch_url = api_url(f"repos/{settings.repository}/issues/comments/11")
    old = pr_card_payload(settings, 7, body="Old card", comment_id=11)
    updated = pr_card_payload(settings, 7, body="New card", comment_id=11)
    session.enqueue(
        "GET",
        comments_url,
        FakeResponse(200, [old]),
        FakeResponse(200, [updated]),
    )
    session.enqueue("PATCH", comments_patch_url, requests.exceptions.Timeout("network timed out"))

    client = GitHubClient(settings, cast(requests.Session, session))
    card = client.ensure_pr_card(7, "New card")

    assert card["id"] == 11
    assert "network timed out" not in str(card)


def test_ensure_pr_card_rejects_bodies_exceeding_complete_marker_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(
        monkeypatch,
        tmp_path,
        private_key_file,
        TLH_PRESENTATION_MAX_CHARS="1000",
    )
    client = GitHubClient(settings, cast(requests.Session, FakeSession()))

    with pytest.raises(GitHubError, match="character budget"):
        client.ensure_pr_card(7, "x" * 1000)


def test_ensure_check_run_rejects_when_disabled_without_requesting_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file)
    session = FakeSession()
    client = GitHubClient(settings, cast(requests.Session, session))

    with pytest.raises(GitHubError, match="disabled"):
        client.ensure_check_run(
            SHA,
            EXTERNAL_ID,
            status="in_progress",
            conclusion=None,
            title="Provided title",
            summary="Provided summary",
            details_url=DETAILS_URL,
        )
    with pytest.raises(GitHubError, match="disabled"):
        client.cancel_check_run(
            51,
            sha=SHA,
            external_id=EXTERNAL_ID,
            title="Provided title",
            summary="Provided summary",
            details_url=DETAILS_URL,
        )
    assert session.calls == []


def test_check_token_grant_failure_preserves_core_token_and_retry_recovers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file, TLH_CHECK_RUNS="true")
    session = FakeSession()
    bootstrap_app_auth(session, settings, token="core-token")
    access_token_url = api_url(f"app/installations/{settings.installation_id}/access_tokens")
    session.enqueue(
        "POST",
        access_token_url,
        FakeResponse(403, {"message": "Resource not accessible by integration"}),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    with pytest.raises(GitHubError, match="HTTP 403"):
        client.ensure_check_run(
            SHA,
            EXTERNAL_ID,
            status="in_progress",
            conclusion=None,
            title="Provided title",
            summary="Provided summary",
            details_url=DETAILS_URL,
        )

    repo_url = api_url(f"repos/{settings.repository}")
    comments_url = api_url(f"repos/{settings.repository}/issues/7/comments?per_page=100&page=1")
    comments_post_url = api_url(f"repos/{settings.repository}/issues/7/comments")
    status_url = api_url(f"repos/{settings.repository}/statuses/{SHA}")
    card = pr_card_payload(settings, 7, body="Healthy core card", comment_id=23)
    session.enqueue("GET", repo_url, FakeResponse(200, repo_payload(settings)))
    session.enqueue("GET", comments_url, FakeResponse(200, []))
    session.enqueue("POST", comments_post_url, FakeResponse(201, card))
    session.enqueue(
        "POST",
        status_url,
        FakeResponse(
            201,
            {
                "context": settings.status_context,
                "state": "pending",
                "description": "In progress",
                "target_url": DETAILS_URL,
            },
        ),
    )

    assert client.repository_info()["id"] == settings.repository_id
    assert client.ensure_pr_card(7, "Healthy core card")["id"] == 23
    assert client.set_status(SHA, "pending", "In progress", DETAILS_URL)["state"] == "pending"

    bootstrap_check_auth(session, settings, token="checks-token")
    create_url = api_url(f"repos/{settings.repository}/check-runs")
    session.enqueue("GET", check_runs_url(settings), FakeResponse(200, check_runs_response()))
    session.enqueue("POST", create_url, FakeResponse(201, check_run_payload(settings)))

    assert client.ensure_check_run(
        SHA,
        EXTERNAL_ID,
        status="in_progress",
        conclusion=None,
        title="Provided title",
        summary="Provided summary",
        details_url=DETAILS_URL,
    )["id"] == 51

    core_urls = {repo_url, comments_url, comments_post_url, status_url}
    assert {
        call.headers["Authorization"].split(" ", 1)[1]
        for call in session.calls
        if call.url in core_urls
    } == {"core-token"}
    assert {
        call.headers["Authorization"].split(" ", 1)[1]
        for call in session.calls
        if call.url in {check_runs_url(settings), create_url}
    } == {"checks-token"}
    access_token_bodies = [
        call.json_body
        for call in session.calls
        if call.method == "POST" and call.url == access_token_url
    ]
    assert access_token_bodies == [
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
        {
            "repository_ids": [settings.repository_id],
            "permissions": {"checks": "write"},
        },
    ]


def test_ensure_check_run_creates_reuses_and_updates_one_owned_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file, TLH_CHECK_RUNS="true")
    session = FakeSession()
    bootstrap_app_auth(session, settings, token="core-token")
    bootstrap_check_auth(session, settings, token="checks-token")
    list_url = check_runs_url(settings)
    create_url = api_url(f"repos/{settings.repository}/check-runs")
    patch_url = api_url(f"repos/{settings.repository}/check-runs/51")
    initial = check_run_payload(settings)
    final = check_run_payload(settings, status="completed", conclusion="neutral")
    session.enqueue("GET", list_url, FakeResponse(200, check_runs_response()))
    session.enqueue("POST", create_url, FakeResponse(201, initial))
    session.enqueue("GET", list_url, FakeResponse(200, check_runs_response(initial)))
    session.enqueue("GET", list_url, FakeResponse(200, check_runs_response(initial)))
    session.enqueue("PATCH", patch_url, FakeResponse(200, final))

    client = GitHubClient(settings, cast(requests.Session, session))
    assert client.ensure_check_run(
        SHA,
        EXTERNAL_ID,
        status="in_progress",
        conclusion=None,
        title="Provided title",
        summary="Provided summary",
        details_url=DETAILS_URL,
    )["id"] == 51
    assert client.ensure_check_run(
        SHA,
        EXTERNAL_ID,
        status="in_progress",
        conclusion=None,
        title="Provided title",
        summary="Provided summary",
        details_url=DETAILS_URL,
    )["id"] == 51
    assert client.ensure_check_run(
        SHA,
        EXTERNAL_ID,
        status="completed",
        conclusion="neutral",
        title="Provided title",
        summary="Provided summary",
        details_url=DETAILS_URL,
    )["id"] == 51

    write_calls = [
        call
        for call in session.calls
        if call.method in {"POST", "PATCH"} and call.url in {create_url, patch_url}
    ]
    check_calls = [
        call
        for call in session.calls
        if call.url in {list_url, create_url, patch_url}
    ]
    assert {
        call.headers["Authorization"].split(" ", 1)[1]
        for call in check_calls
    } == {"checks-token"}
    assert [call.method for call in write_calls] == ["POST", "PATCH"]
    assert write_calls[0].json_body == {
        "status": "in_progress",
        "details_url": DETAILS_URL,
        "output": {"title": "Provided title", "summary": "Provided summary"},
        "name": settings.check_name,
        "head_sha": SHA,
        "external_id": EXTERNAL_ID,
    }
    assert write_calls[1].json_body == {
        "status": "completed",
        "details_url": DETAILS_URL,
        "output": {"title": "Provided title", "summary": "Provided summary"},
        "conclusion": "neutral",
    }
    access_token_calls = [
        call
        for call in session.calls
        if call.method == "POST"
        and call.url == api_url(f"app/installations/{settings.installation_id}/access_tokens")
    ]
    assert [call.json_body for call in access_token_calls] == [
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


def test_check_token_refreshes_without_replacing_core_token_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file, TLH_CHECK_RUNS="true")
    session = FakeSession()
    bootstrap_app_auth(session, settings, token="core-token")
    bootstrap_check_auth(session, settings, token="soon-checks-token", expires_in=30)
    bootstrap_check_auth(session, settings, token="fresh-checks-token")
    list_url = check_runs_url(settings)
    create_url = api_url(f"repos/{settings.repository}/check-runs")
    created = check_run_payload(settings)
    session.enqueue("GET", list_url, FakeResponse(200, check_runs_response()))
    session.enqueue("POST", create_url, FakeResponse(201, created))
    session.enqueue("GET", list_url, FakeResponse(200, check_runs_response(created)))

    client = GitHubClient(settings, cast(requests.Session, session))
    assert client.ensure_check_run(
        SHA,
        EXTERNAL_ID,
        status="in_progress",
        conclusion=None,
        title="Provided title",
        summary="Provided summary",
        details_url=DETAILS_URL,
    )["id"] == 51
    assert client.ensure_check_run(
        SHA,
        EXTERNAL_ID,
        status="in_progress",
        conclusion=None,
        title="Provided title",
        summary="Provided summary",
        details_url=DETAILS_URL,
    )["id"] == 51

    check_api_calls = [
        call
        for call in session.calls
        if call.url in {list_url, create_url}
    ]
    assert [
        (call.method, call.headers["Authorization"].split(" ", 1)[1])
        for call in check_api_calls
    ] == [
        ("GET", "soon-checks-token"),
        ("POST", "fresh-checks-token"),
        ("GET", "fresh-checks-token"),
    ]
    access_token_bodies = [
        call.json_body
        for call in session.calls
        if call.method == "POST"
        and call.url == api_url(f"app/installations/{settings.installation_id}/access_tokens")
    ]
    assert access_token_bodies == [
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
        {
            "repository_ids": [settings.repository_id],
            "permissions": {"checks": "write"},
        },
    ]


def test_ensure_check_run_rejects_ambiguous_existing_identity_before_editing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file, TLH_CHECK_RUNS="true")
    session = FakeSession()
    bootstrap_app_auth(session, settings)
    bootstrap_check_auth(session, settings)
    create_url = api_url(f"repos/{settings.repository}/check-runs")
    patch_url = api_url(f"repos/{settings.repository}/check-runs/51")
    session.enqueue(
        "GET",
        check_runs_url(settings),
        FakeResponse(
            200,
            check_runs_response(
                check_run_payload(settings, check_run_id=51),
                check_run_payload(settings, check_run_id=52),
            ),
        ),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    with pytest.raises(GitHubError, match="ambiguous"):
        client.ensure_check_run(
            SHA,
            EXTERNAL_ID,
            status="completed",
            conclusion="success",
            title="Provided title",
            summary="Provided summary",
            details_url=DETAILS_URL,
        )
    assert not [
        call
        for call in session.calls
        if call.method in {"POST", "PATCH"} and call.url in {create_url, patch_url}
    ]


def test_check_run_http_errors_propagate_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file, TLH_CHECK_RUNS="true")
    session = FakeSession()
    bootstrap_app_auth(session, settings)
    bootstrap_check_auth(session, settings)
    session.enqueue("GET", check_runs_url(settings), FakeResponse(200, check_runs_response()))
    session.enqueue(
        "POST",
        api_url(f"repos/{settings.repository}/check-runs"),
        FakeResponse(403, {"message": "Resource not accessible by integration"}),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    with pytest.raises(GitHubError, match="HTTP 403"):
        client.ensure_check_run(
            SHA,
            EXTERNAL_ID,
            status="in_progress",
            conclusion=None,
            title="Provided title",
            summary="Provided summary",
            details_url=DETAILS_URL,
        )
    assert len(
        [
            call
            for call in session.calls
            if call.method == "POST"
            and call.url == api_url(f"repos/{settings.repository}/check-runs")
        ]
    ) == 1


@pytest.mark.parametrize(
    ("mutations", "expected"),
    [
        ({"app_id": 999}, "app attribution"),
        ({"sha": "b" * 40}, "sha"),
        ({"name": "Other check"}, "name"),
        ({"external_id": "other-snapshot"}, "external identity"),
    ],
)
def test_check_run_create_validates_returned_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
    mutations: dict[str, object],
    expected: str,
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file, TLH_CHECK_RUNS="true")
    session = FakeSession()
    bootstrap_app_auth(session, settings)
    bootstrap_check_auth(session, settings)
    session.enqueue("GET", check_runs_url(settings), FakeResponse(200, check_runs_response()))
    session.enqueue(
        "POST",
        api_url(f"repos/{settings.repository}/check-runs"),
        FakeResponse(201, check_run_payload(settings, **mutations)),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    with pytest.raises(GitHubError, match=expected):
        client.ensure_check_run(
            SHA,
            EXTERNAL_ID,
            status="in_progress",
            conclusion=None,
            title="Provided title",
            summary="Provided summary",
            details_url=DETAILS_URL,
        )


def test_find_check_run_reads_owned_identity_with_checks_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file, TLH_CHECK_RUNS="true")
    session = FakeSession()
    bootstrap_app_auth(session, settings, token="core-token")
    bootstrap_check_auth(session, settings, token="checks-token")
    owned = check_run_payload(settings, check_run_id=51)
    session.enqueue(
        "GET",
        check_runs_url(settings),
        FakeResponse(
            200,
            check_runs_response(
                check_run_payload(settings, check_run_id=52, app_id=999),
                check_run_payload(settings, check_run_id=53, external_id="other"),
                owned,
            ),
        ),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    run = client.find_check_run(SHA, EXTERNAL_ID)

    assert run is not None
    assert run["id"] == 51
    check_call = next(call for call in session.calls if call.url == check_runs_url(settings))
    assert check_call.headers["Authorization"].split(" ", 1)[1] == "checks-token"


@pytest.mark.parametrize("conclusion", ["success", "neutral", "cancelled", "action_required"])
def test_completed_check_run_allows_only_safe_conclusions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
    conclusion: str,
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file, TLH_CHECK_RUNS="true")
    session = FakeSession()
    bootstrap_app_auth(session, settings)
    bootstrap_check_auth(session, settings)
    created = check_run_payload(settings, status="completed", conclusion=conclusion)
    session.enqueue("GET", check_runs_url(settings), FakeResponse(200, check_runs_response()))
    session.enqueue(
        "POST",
        api_url(f"repos/{settings.repository}/check-runs"),
        FakeResponse(201, created),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    assert client.ensure_check_run(
        SHA,
        EXTERNAL_ID,
        status="completed",
        conclusion=conclusion,  # type: ignore[arg-type]
        title="Provided title",
        summary="Provided summary",
        details_url=DETAILS_URL,
    )["conclusion"] == conclusion


def test_check_run_rejects_failure_conclusion_for_human_hold(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file, TLH_CHECK_RUNS="true")
    client = GitHubClient(settings, cast(requests.Session, FakeSession()))

    with pytest.raises(GitHubError, match="conclusion"):
        client.ensure_check_run(
            SHA,
            EXTERNAL_ID,
            status="completed",
            conclusion="failure",  # type: ignore[arg-type]
            title="Provided title",
            summary="Provided summary",
            details_url=DETAILS_URL,
        )


def test_ensure_check_run_reconciles_successful_create_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file, TLH_CHECK_RUNS="true")
    session = FakeSession()
    bootstrap_app_auth(session, settings)
    bootstrap_check_auth(session, settings)
    created = check_run_payload(settings)
    session.enqueue(
        "GET",
        check_runs_url(settings),
        FakeResponse(200, check_runs_response()),
        FakeResponse(200, check_runs_response(created)),
    )
    session.enqueue(
        "POST",
        api_url(f"repos/{settings.repository}/check-runs"),
        requests.exceptions.Timeout("network timed out"),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    run = client.ensure_check_run(
        SHA,
        EXTERNAL_ID,
        status="in_progress",
        conclusion=None,
        title="Provided title",
        summary="Provided summary",
        details_url=DETAILS_URL,
    )

    assert run["id"] == 51
    assert "network timed out" not in str(run)


def test_ensure_check_run_reconciles_successful_patch_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file, TLH_CHECK_RUNS="true")
    session = FakeSession()
    bootstrap_app_auth(session, settings)
    bootstrap_check_auth(session, settings)
    old = check_run_payload(settings)
    updated = check_run_payload(settings, status="completed", conclusion="success")
    session.enqueue(
        "GET",
        check_runs_url(settings),
        FakeResponse(200, check_runs_response(old)),
        FakeResponse(200, check_runs_response(updated)),
    )
    session.enqueue(
        "PATCH",
        api_url(f"repos/{settings.repository}/check-runs/51"),
        requests.exceptions.Timeout("network timed out"),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    run = client.ensure_check_run(
        SHA,
        EXTERNAL_ID,
        status="completed",
        conclusion="success",
        title="Provided title",
        summary="Provided summary",
        details_url=DETAILS_URL,
    )

    assert run["id"] == 51
    assert run["conclusion"] == "success"


@pytest.mark.parametrize("reconcile_mode", ["missing", "ambiguous"])
def test_uncertain_check_run_create_requires_single_matching_reconciled_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
    reconcile_mode: str,
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file, TLH_CHECK_RUNS="true")
    session = FakeSession()
    bootstrap_app_auth(session, settings)
    bootstrap_check_auth(session, settings)
    reconciled_runs: list[dict[str, object]] = []
    if reconcile_mode == "ambiguous":
        reconciled_runs = [
            check_run_payload(settings, check_run_id=51),
            check_run_payload(settings, check_run_id=52),
        ]
    session.enqueue(
        "GET",
        check_runs_url(settings),
        FakeResponse(200, check_runs_response()),
        FakeResponse(200, check_runs_response(*reconciled_runs)),
    )
    session.enqueue(
        "POST",
        api_url(f"repos/{settings.repository}/check-runs"),
        requests.exceptions.ConnectionError("connection dropped"),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    with pytest.raises(GitHubUncertainResultError, match="uncertain") as error:
        client.ensure_check_run(
            SHA,
            EXTERNAL_ID,
            status="in_progress",
            conclusion=None,
            title="Provided title",
            summary="Provided summary",
            details_url=DETAILS_URL,
        )

    assert "connection dropped" not in str(error.value)


@pytest.mark.parametrize(
    ("mutations", "expected"),
    [
        ({"app_id": 999}, "app attribution"),
        ({"sha": "b" * 40}, "sha"),
        ({"name": "Other check"}, "name"),
        ({"external_id": "other-snapshot"}, "external identity"),
    ],
)
def test_cancel_check_run_validates_identity_before_editing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
    mutations: dict[str, object],
    expected: str,
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file, TLH_CHECK_RUNS="true")
    session = FakeSession()
    bootstrap_app_auth(session, settings)
    bootstrap_check_auth(session, settings)
    session.enqueue(
        "GET",
        api_url(f"repos/{settings.repository}/check-runs/51"),
        FakeResponse(200, check_run_payload(settings, **mutations)),
    )

    client = GitHubClient(settings, cast(requests.Session, session))
    with pytest.raises(GitHubError, match=expected):
        client.cancel_check_run(
            51,
            sha=SHA,
            external_id=EXTERNAL_ID,
            title="Provided title",
            summary="Provided summary",
            details_url=DETAILS_URL,
        )
    assert not [
        call
        for call in session.calls
        if call.method == "PATCH"
        and call.url == api_url(f"repos/{settings.repository}/check-runs/51")
    ]


@pytest.mark.parametrize("check_run_id", [0, True])
def test_cancel_check_run_rejects_invalid_numeric_ids_before_editing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
    check_run_id: object,
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file, TLH_CHECK_RUNS="true")
    session = FakeSession()
    client = GitHubClient(settings, cast(requests.Session, session))

    with pytest.raises(GitHubError, match="positive"):
        client.cancel_check_run(
            check_run_id,  # type: ignore[arg-type]
            sha=SHA,
            external_id=EXTERNAL_ID,
            title="Provided title",
            summary="Provided summary",
            details_url=DETAILS_URL,
        )
    assert session.calls == []


def test_cancel_check_run_updates_owned_identity_to_cancelled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    private_key_pair: tuple[Path, bytes],
) -> None:
    private_key_file, _ = private_key_pair
    settings = make_settings(monkeypatch, tmp_path, private_key_file, TLH_CHECK_RUNS="true")
    session = FakeSession()
    bootstrap_app_auth(session, settings, token="core-token")
    bootstrap_check_auth(session, settings, token="checks-token")
    old = check_run_payload(settings, status="in_progress", conclusion=None)
    cancelled = check_run_payload(settings, status="completed", conclusion="cancelled")
    get_url = api_url(f"repos/{settings.repository}/check-runs/51")
    patch_url = api_url(f"repos/{settings.repository}/check-runs/51")
    session.enqueue("GET", get_url, FakeResponse(200, old))
    session.enqueue("PATCH", patch_url, FakeResponse(200, cancelled))

    client = GitHubClient(settings, cast(requests.Session, session))
    run = client.cancel_check_run(
        51,
        sha=SHA,
        external_id=EXTERNAL_ID,
        title="Provided title",
        summary="Provided summary",
        details_url=DETAILS_URL,
    )

    assert run["conclusion"] == "cancelled"
    patch_call = next(call for call in session.calls if call.method == "PATCH")
    check_run_calls = [call for call in session.calls if call.url in {get_url, patch_url}]
    assert [
        (call.method, call.headers["Authorization"].split(" ", 1)[1])
        for call in check_run_calls
    ] == [
        ("GET", "checks-token"),
        ("PATCH", "checks-token"),
    ]
    assert patch_call.json_body == {
        "status": "completed",
        "details_url": DETAILS_URL,
        "output": {"title": "Provided title", "summary": "Provided summary"},
        "conclusion": "cancelled",
    }
