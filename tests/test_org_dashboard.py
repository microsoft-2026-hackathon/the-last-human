"""Pure distribution, provenance, contact and navigation contracts."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from test_app_service import make_settings
from lasthuman.server.organization import (
    ModuleView, OrganizationError, OrganizationQuery, RepositoryRead, RepositoryView, Summary,
    declared_contacts, demo_repositories, module_bucket, organization_view, project_modules,
    repository_demo, repository_query, summarize,
)
AS_OF = datetime(2026, 9, 17, 3, tzinfo=timezone.utc)


def test_bundled_fixture_has_exact_distribution_and_only_anchor_demo_navigation() -> None:
    repos = demo_repositories("/repos/101/dashboard?data=demo")
    assert len(repos) == 3
    assert sum(len(repo.modules) for repo in repos) == 5
    assert summarize(repos) == Summary(zero=1, one=1, many=2)
    assert [module.answerers for repo in repos for module in repo.modules] == [1, 3, 2, 0, None]
    assert [module.rate for repo in repos for module in repo.modules] == [0.2, 0.8, None, 0.0, None]
    assert {repo.dashboard_url for repo in repos} == {"/repos/101/dashboard?data=demo"}
    assert all(contact.url is None for repo in repos for module in repo.modules for contact in module.contacts)
    encoded = json.dumps([asdict(repo) for repo in repos])
    assert "github.com" not in encoded and "/pull/" not in encoded and "/receipts/" not in encoded
    assert files("lasthuman").joinpath("data/org_dashboard_demo.json").is_file()


@pytest.mark.parametrize(
    ("module", "bucket"),
    [
        (ModuleView("auth/", 5, 0, 0), "zero"),
        (ModuleView("auth/", 5, 1, 1), "one"),
        (ModuleView("auth/", 10, 8, 3), "many"),
        (ModuleView("auth/", 3, 3, 2, status="Sample too small"), "many"),
        (ModuleView("auth/", 0, 0, 0, status="No gated changes"), None),
        (ModuleView("auth/", None, None, None, status="No measured data"), None),
        (ModuleView("auth/", None, None, None, status="Collection delayed"), None),
        (ModuleView("auth/", 5, 0, 0, status="Data unavailable"), None),
    ],
)
def test_bucket_contains_only_known_active_modules(module: ModuleView, bucket: str | None) -> None:
    assert module_bucket(module) == bucket


def test_equal_module_names_across_repositories_are_distinct_not_global_people() -> None:
    module = ModuleView("auth/", 5, 1, 1)
    repos = (RepositoryView("101", "acme/one", "/one", (module,)),
             RepositoryView("102", "acme/two", "/two", (module,)))
    assert summarize(repos) == Summary(one=2)
    with pytest.raises(ValueError, match="Duplicate"):
        summarize((repos[0], repos[0]))


def test_repository_filter_precedes_summary_but_bucket_does_not_change_cards(tmp_path: Path) -> None:
    settings = replace(make_settings(tmp_path), org_demo_enabled=True)

    def never_read(_token: str, _end: datetime) -> RepositoryRead:
        pytest.fail("Demo enumerated or read actual repositories")

    all_view = organization_view(
        settings, OrganizationQuery("demo", bucket="zero"), user_token="unused", provider=never_read, as_of=AS_OF,
    )
    assert all_view.summary == Summary(zero=1, one=1, many=2)
    assert sum(len(repo.modules) for repo in all_view.repositories) == 1
    selected = organization_view(
        settings, OrganizationQuery("demo", "sample-payments", "one"),
        user_token="unused", provider=never_read, as_of=AS_OF,
    )
    assert selected.summary == Summary(one=1, many=1)
    assert len(selected.repository_options) == 3
    assert [module.zone for module in selected.repositories[0].modules] == ["auth/"]
    assert selected.owner_id is None and selected.organization == "sample-organization"
    assert (selected.as_of - selected.since).days == 30
    assert parse_qs(urlsplit(selected.links["actual"]).query) == {
        "source": ["actual"], "repository": ["all"], "bucket": ["all"],
    }
    assert parse_qs(urlsplit(selected.links["demo"]).query) == {
        "source": ["demo"], "repository": ["all"], "bucket": ["all"],
    }
    assert parse_qs(urlsplit(selected.links["many"]).query)["repository"] == ["sample-payments"]
    assert parse_qs(urlsplit(selected.links["reset"]).query)["bucket"] == ["all"]


@pytest.mark.parametrize("repository", ["999", "sample-other", "101"])
def test_demo_selection_is_sample_scoped(tmp_path: Path, repository: str) -> None:
    settings = replace(make_settings(tmp_path), org_demo_enabled=True)
    with pytest.raises(OrganizationError) as failure:
        organization_view(
            settings, OrganizationQuery("demo", repository), user_token="unused",
            provider=lambda _token, _end: pytest.fail("Actual provider called"),
        )
    assert failure.value.status_code == 404


def test_actual_view_carries_owner_and_one_window_without_loading_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(tmp_path)
    calls = []

    def provider(token: str, end: datetime) -> RepositoryRead:
        calls.append((token, end))
        return RepositoryRead((RepositoryView("101", "acme/one", "/repos/101/dashboard?data=repo"),), True)

    monkeypatch.setattr(
        "lasthuman.server.organization._demo_fixture", lambda: pytest.fail("Actual loaded a sample fixture"),
    )
    view = organization_view(settings, OrganizationQuery(), user_token="user-token", provider=provider, as_of=AS_OF)
    assert calls == [("user-token", AS_OF)]
    assert view.owner_id == settings.owner_id and view.organization == "hunhoon21"
    assert view.source == "actual" and view.notice == "Some data is temporarily unavailable."
    assert view.repositories[0].dashboard_url == "/repos/101/dashboard?data=repo"


def test_disabled_demo_is_explicit_and_never_calls_actual_provider(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    with pytest.raises(OrganizationError) as failure:
        organization_view(
            settings, OrganizationQuery("demo"), user_token="unused",
            provider=lambda _token, _end: pytest.fail("Disabled Demo fell back to Actual"),
        )
    assert failure.value.status_code == 404
    with pytest.raises(OrganizationError, match="Demo data is unavailable"):
        repository_demo(settings)


def test_repository_demo_keeps_real_destination_identity_but_has_no_business_links(tmp_path: Path) -> None:
    settings = replace(make_settings(tmp_path), org_demo_enabled=True, demo_seed=tmp_path / "must-not-read.json")
    demo = repository_demo(settings, as_of=AS_OF)
    assert demo["repo"] == settings.repository and demo["source"] == "demo"
    assert demo["window_days"] == 30 and demo["demo_seeded"] is False
    assert demo["generated_at"] == "2026-09-17T03:00:00Z"
    assert demo["zones"] and all(row["prs"] == [] for row in demo["zones"])
    assert "receipt" not in json.dumps(demo)


@pytest.mark.parametrize(
    "pairs",
    [
        [("source", "seed")], [("source", "actual"), ("source", "demo")],
        [("bucket", "all"), ("bucket", "one")], [("repository", "all"), ("repository", "101")],
        [("data", "repo")], [("owner_id", "77")], [("bucket", "unknown")], [("days", "30")],
    ],
)
def test_organization_query_rejects_unknown_and_duplicate_keys(pairs: list[tuple[str, str]]) -> None:
    with pytest.raises(OrganizationError) as failure:
        OrganizationQuery.parse(pairs)
    assert failure.value.status_code == 400


@pytest.mark.parametrize(
    "pairs",
    [[("data", "actual")], [("data", "demo"), ("data", "repo")],
     [("source", "demo")], [("repository", "101")], [("bucket", "one")], [("days", "-1")]],
)
def test_repository_source_queries_never_accept_organization_filters(pairs: list[tuple[str, str]]) -> None:
    with pytest.raises(OrganizationError) as failure:
        repository_query(pairs)
    assert failure.value.status_code == 400
    assert repository_query([]) == ("legacy", 30)
    assert repository_query([("data", "repo"), ("days", "7")]) == ("repo", 7)


def test_projected_states_do_not_conflate_unmeasured_or_no_changes_with_zero_authors() -> None:
    payload = {"measured_total": 2, "unmeasured_total": 1, "zones": [
        {"zone": "auth/", "gated": 5, "attested": 0, "answerers": 0, "owner": "@historical-owner"},
        {"zone": "docs/", "gated": 0, "attested": 0, "answerers": 0},
        {"zone": "api/", "gated": 3, "attested": 3, "answerers": 2},
    ]}
    rows = project_modules(payload, None)
    assert [row.status for row in rows] == [
        "Available", "No gated changes", "Sample too small",
    ]
    assert [row.zone for row in rows] == ["auth/", "docs/", "api/"]
    assert rows[0].rate == 0.0 and rows[2].rate is None
    assert all(row.contact_status == "Contact unavailable" for row in rows)
    assert "@historical-owner" not in repr(rows)
    assert summarize((RepositoryView("101", "acme/one", "/dashboard", rows),)) == Summary(zero=1, many=1)
    unknown = project_modules({**payload, "measured_total": 0}, {})
    assert all(row.answerers is None for row in unknown)
    assert all(module_bucket(row) is None for row in unknown)


@pytest.mark.parametrize(
    ("rules", "labels"),
    [
        ({"/app/auth/": "@acme/security @maintainer"}, ["@acme/security", "@maintainer"]),
        ({"*": "@global", "/app/": "@acme/platform"}, ["@acme/platform"]),
        ({"/app/auth/**": "@acme/security"}, ["@acme/security"]),
        ({"auth/": "@acme/root-auth"}, []),
        ({"/unrelated/": "@acme/other"}, []),
        ({"/app/auth/": "@old", "/app/auth/secret.py": "@different"}, []),
        ({"*": "@global", "*.py": "@python"}, []),
        ({"/app/auth/": "https://evil.example @bad--name @acme/../secret <script> me@example.com"}, []),
        ({"/app/auth/": ""}, []),
        ({}, []),
    ],
)
def test_current_contact_mapping_is_conservative_and_validated(rules: dict[str, str], labels: list[str]) -> None:
    contacts, status = declared_contacts("app/auth/", rules)
    assert [contact.label for contact in contacts] == labels
    assert status == ("" if labels else "Not declared")
    assert all(contact.url.startswith("https://github.com/") for contact in contacts)
    if labels == ["@acme/security", "@maintainer"]:
        assert [contact.url for contact in contacts] == [
            "https://github.com/orgs/acme/teams/security", "https://github.com/maintainer",
        ]
    assert declared_contacts("app/auth/", None) == ((), "Contact unavailable")
