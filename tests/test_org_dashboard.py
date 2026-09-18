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


def repository_demo_zone(**overrides: object) -> dict[str, object]:
    return {
        "zone": "auth/", "owner": "@sample-organization/payments", "prs": [901, 907, 903],
        "merged": 5, "gated": 5, "attested": 1, "forced": 0, "answerers": 1,
        **overrides,
    }


def set_repository_demo_zones(monkeypatch: pytest.MonkeyPatch, zones: list[dict[str, object]]) -> None:
    fixture = json.loads(files("lasthuman").joinpath("data/org_dashboard_demo.json").read_text(encoding="utf-8"))
    fixture["repository_dashboard"]["zones"] = zones
    monkeypatch.setattr("lasthuman.server.organization._demo_fixture", lambda: fixture)


def test_bundled_fixture_has_exact_distribution_and_only_anchor_demo_navigation() -> None:
    repos = demo_repositories("/repos/101/dashboard?data=demo")
    assert [repo.id for repo in repos] == [
        "sample-payments", "sample-orders", "sample-identity", "sample-platform",
    ]
    assert len(repos) == 4
    assert sum(len(repo.modules) for repo in repos) == 8
    assert summarize(repos) == Summary(zero=1, one=1, many=5)
    assert [
        (repo.id, module.zone, module.gated, module.attested, module.answerers, module.contacts[0].label)
        for repo in repos for module in repo.modules
    ] == [
        ("sample-payments", "ledger/", 10, 8, 3, "@sample-organization/payments"),
        ("sample-payments", "billing/", 8, 6, 3, "@sample-organization/payments"),
        ("sample-orders", "checkout/", 9, 7, 2, "@sample-organization/orders"),
        ("sample-orders", "shipping/", 5, 0, 0, "@sample-organization/orders"),
        ("sample-identity", "session/", 6, 4, 2, "@sample-organization/identity"),
        ("sample-identity", "auth/", 5, 1, 1, "@sample-organization/identity"),
        ("sample-platform", "jobs/", 4, 4, 2, "@sample-organization/platform"),
        ("sample-platform", "events/", None, None, None, "@sample-organization/platform"),
    ]
    assert [module.rate for repo in repos for module in repo.modules] == [
        0.8, 0.75, 7 / 9, 0.0, 4 / 6, 0.2, None, None,
    ]
    assert [module.status for module in repos[-1].modules] == ["Sample too small", "Collection delayed"]
    assert {repo.dashboard_url for repo in repos} == {"/repos/101/dashboard?data=demo"}
    assert all(contact.url is None for repo in repos for module in repo.modules for contact in module.contacts)
    encoded = json.dumps([asdict(repo) for repo in repos])
    assert "github.com" not in encoded and "/pull/" not in encoded and "/receipts/" not in encoded
    assert files("lasthuman").joinpath("data/org_dashboard_demo.json").is_file()


@pytest.fixture
def demo_repository_rows() -> list[dict[str, object]]:
    return json.loads(
        files("lasthuman").joinpath("data/org_dashboard_demo.json").read_text(encoding="utf-8"),
    )["repositories"]


def set_demo_repositories(monkeypatch: pytest.MonkeyPatch, repositories: list[dict[str, object]]) -> None:
    monkeypatch.setattr("lasthuman.server.organization._demo_fixture", lambda: {"repositories": repositories})


@pytest.mark.parametrize("extra_repositories", [0, 3])
def test_demo_shape_accepts_lower_bounds_and_larger_valid_distributions(
    monkeypatch: pytest.MonkeyPatch, demo_repository_rows: list[dict[str, object]], extra_repositories: int,
) -> None:
    rows = demo_repository_rows[1:3]
    rows.extend({
        **demo_repository_rows[0], "id": f"sample-extra-{index}", "name": f"sample-extra-{index}",
    } for index in range(extra_repositories))
    set_demo_repositories(monkeypatch, rows)
    repos = demo_repositories("/dashboard?data=demo")
    assert len(repos) == 2 + extra_repositories
    assert sum(len(repo.modules) for repo in repos) == 4 + 2 * extra_repositories
    assert summarize(repos) == Summary(zero=1, one=1, many=2 + 2 * extra_repositories)


def test_demo_distribution_has_no_many_bucket_minimum(
    monkeypatch: pytest.MonkeyPatch, demo_repository_rows: list[dict[str, object]],
) -> None:
    rows = demo_repository_rows[1:3]
    for repo in rows:
        for module in repo["modules"]:
            if module["answerers"] == 2:
                module.update(attested=0, answerers=0)
    set_demo_repositories(monkeypatch, rows)
    assert summarize(demo_repositories("/dashboard?data=demo")) == Summary(zero=3, one=1)


@pytest.mark.parametrize("shape", ["empty", "one-repository", "three-modules", "duplicate-id"])
def test_demo_shape_rejects_below_bounds_and_duplicate_repository_ids(
    monkeypatch: pytest.MonkeyPatch, demo_repository_rows: list[dict[str, object]], shape: str,
) -> None:
    rows = demo_repository_rows[1:3]
    if shape == "empty":
        rows = []
    elif shape == "one-repository":
        rows = [{"id": "sample-only", "name": "sample-only",
                 "modules": [module for repo in rows for module in repo["modules"]]}]
    elif shape == "three-modules":
        rows[0]["modules"] = rows[0]["modules"][:1]
    else:
        rows.append(rows[0])
    set_demo_repositories(monkeypatch, rows)
    with pytest.raises(ValueError, match="Invalid demo"):
        demo_repositories("/dashboard?data=demo")


@pytest.mark.parametrize("repo_id", [
    "acme/payments", "payments", "sample-", "sample--orders", "sample-orders!", "sample-Orders",
    "sample_orders", "sample-orders\n", 101, True, None,
])
def test_demo_repositories_require_unmistakably_sample_ids(
    monkeypatch: pytest.MonkeyPatch, demo_repository_rows: list[dict[str, object]], repo_id: object,
) -> None:
    demo_repository_rows[0].update(id=repo_id, name=repo_id)
    set_demo_repositories(monkeypatch, demo_repository_rows)
    with pytest.raises(ValueError, match="Invalid demo repository"):
        demo_repositories("/dashboard?data=demo")


@pytest.mark.parametrize("name", ["acme/payments", "sample-other", None, 1])
def test_demo_repository_name_must_equal_sample_id(
    monkeypatch: pytest.MonkeyPatch, demo_repository_rows: list[dict[str, object]], name: object,
) -> None:
    demo_repository_rows[0]["name"] = name
    set_demo_repositories(monkeypatch, demo_repository_rows)
    with pytest.raises(ValueError, match="Invalid demo repository"):
        demo_repositories("/dashboard?data=demo")


@pytest.mark.parametrize("field", ["gated", "attested", "answerers"])
@pytest.mark.parametrize("value", [True, False, -1, 1.0, 0.5, "1", None])
def test_demo_org_validates_counter_types(
    monkeypatch: pytest.MonkeyPatch, demo_repository_rows: list[dict[str, object]], field: str, value: object,
) -> None:
    demo_repository_rows[0]["modules"][0][field] = value
    set_demo_repositories(monkeypatch, demo_repository_rows)
    with pytest.raises(ValueError, match="Invalid"):
        demo_repositories("/dashboard?data=demo")


@pytest.mark.parametrize("counters", [
    {"gated": 5, "attested": 6, "answerers": 2},
    {"gated": 5, "attested": 1, "answerers": 0},
    {"gated": 5, "attested": 0, "answerers": 1},
    {"gated": None, "attested": 0, "answerers": None},
    {"gated": None, "attested": None, "answerers": 0},
])
def test_demo_org_rejects_inconsistent_known_and_unknown_counters(
    monkeypatch: pytest.MonkeyPatch, demo_repository_rows: list[dict[str, object]], counters: dict[str, object],
) -> None:
    demo_repository_rows[0]["modules"][0].update(counters)
    set_demo_repositories(monkeypatch, demo_repository_rows)
    with pytest.raises(ValueError, match="Invalid"):
        demo_repositories("/dashboard?data=demo")


@pytest.mark.parametrize("missing_bucket", ["zero", "one"])
def test_demo_distribution_requires_both_explanatory_buckets(
    monkeypatch: pytest.MonkeyPatch, demo_repository_rows: list[dict[str, object]], missing_bucket: str,
) -> None:
    for repo in demo_repository_rows:
        for module in repo["modules"]:
            if module["answerers"] == (0 if missing_bucket == "zero" else 1):
                module.update(attested=2, answerers=2)
    set_demo_repositories(monkeypatch, demo_repository_rows)
    with pytest.raises(ValueError, match="Invalid demo distribution"):
        demo_repositories("/dashboard?data=demo")


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
    assert all_view.summary == Summary(zero=1, one=1, many=5)
    assert sum(len(repo.modules) for repo in all_view.repositories) == 1
    assert [(repo.id, module.zone) for repo in all_view.repositories for module in repo.modules] == [
        ("sample-orders", "shipping/"),
    ]
    selected = organization_view(
        settings, OrganizationQuery("demo", "sample-payments", "one"),
        user_token="unused", provider=never_read, as_of=AS_OF,
    )
    assert selected.summary == Summary(many=2)
    assert len(selected.repository_options) == 4
    assert selected.repositories[0].modules == ()
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
    identity = organization_view(
        settings, OrganizationQuery("demo", "sample-identity", "one"),
        user_token="unused", provider=never_read, as_of=AS_OF,
    )
    assert identity.summary == Summary(one=1, many=1)
    assert [module.zone for module in identity.repositories[0].modules] == ["auth/"]


@pytest.mark.parametrize(
    ("bucket", "expected"),
    [
        ("zero", [("sample-orders", "shipping/")]),
        ("one", [("sample-identity", "auth/")]),
        ("many", [
            ("sample-identity", "session/"), ("sample-orders", "checkout/"),
            ("sample-payments", "billing/"), ("sample-payments", "ledger/"), ("sample-platform", "jobs/"),
        ]),
    ],
)
def test_demo_bucket_filters_keep_exact_modules_and_unfiltered_summary(
    tmp_path: Path, bucket: str, expected: list[tuple[str, str]],
) -> None:
    view = organization_view(
        replace(make_settings(tmp_path), org_demo_enabled=True),
        OrganizationQuery.parse([("source", "demo"), ("bucket", bucket)]),
        user_token="unused", provider=lambda _token, _end: pytest.fail("Demo called Actual"), as_of=AS_OF,
    )
    assert [(repo.id, module.zone) for repo in view.repositories for module in repo.modules] == expected
    assert view.summary == Summary(zero=1, one=1, many=5)
    assert len(view.repositories) == 4


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
    assert demo["zone_count"] == 6
    assert all(row["prs"] for row in demo["zones"] if row["gated"])
    assert all(row["prs"] == sorted(row["prs"], reverse=True) for row in demo["zones"])
    assert all(isinstance(pr, int) and not isinstance(pr, bool) and pr > 0
               for row in demo["zones"] for pr in row["prs"])
    assert all(row["owner"].startswith("@sample-organization/") for row in demo["zones"])
    encoded = json.dumps(demo)
    assert all(text not in encoded for text in ("receipt", "github.com", "/pull/", "actor_login", "answers"))


def test_repository_demo_totals_and_six_zone_states_match_the_table(tmp_path: Path) -> None:
    demo = repository_demo(replace(make_settings(tmp_path), org_demo_enabled=True), as_of=AS_OF)
    assert {key: demo[key] for key in (
        "merged_total", "gated_total", "attested_total", "forced_total", "waiting_total",
        "measured_total", "unmeasured_total",
    )} == {
        "merged_total": 34, "gated_total": 28, "attested_total": 21, "forced_total": 3,
        "waiting_total": 1, "measured_total": 34, "unmeasured_total": 0,
    }
    for key in ("merged", "gated", "attested", "forced"):
        assert demo[f"{key}_total"] == sum(row[key] or 0 for row in demo["zones"])
    assert demo["attested_rate"] == 0.75 and demo["zero_answerer_zones"] == 0
    assert [
        (row["zone"], row["owner"], row["answerers"], row["gated"], row["attested"], row["forced"])
        for row in demo["zones"]
    ] == [
        ("sample-app/app/auth/", "@sample-organization/payments", 1, 5, 1, 2),
        ("sample-app/migrations/", "@sample-organization/platform", 2, 4, 4, 0),
        ("sample-app/app/orders/", "@sample-organization/orders", 3, 7, 6, 0),
        ("sample-app/app/ledger/", "@sample-organization/payments", 4, 12, 10, 1),
        ("docs/", "@sample-organization/platform", None, None, None, 0),
        (".github/workflows/", "@sample-organization/platform", None, None, None, 0),
    ]
    assert [row["rate"] for row in demo["zones"]] == [0.2, None, 6 / 7, 10 / 12, None, None]
    assert [row["low_sample"] for row in demo["zones"]] == [False, True, False, False, False, False]
    assert [row["sample_state"] for row in demo["zones"]] == [
        "measured", "small_sample", "measured", "measured", "no_data", "no_data",
    ]
    assert demo["zones"][3]["prs"] == [48, 45, 41, 38, 34, 30]
    assert all(row["prs"] == [] for row in demo["zones"][-2:])
    assert demo["actions"] == [
        {"zone": "sample-app/app/auth/", "owner": "@sample-organization/payments",
         "action": "Verify 2 exceptions after the fact", "from": 1, "to": 2},
        {"zone": "sample-app/app/ledger/", "owner": "@sample-organization/payments",
         "action": "Verify 1 exception after the fact", "from": 4, "to": 5},
    ]


@pytest.mark.parametrize("metadata", [True, False])
def test_repository_demo_preserves_optional_metadata_without_mutating_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, metadata: bool,
) -> None:
    row = repository_demo_zone(owner=" @sample-organization/payments ")
    if not metadata:
        row.pop("owner")
        row.pop("prs")
    before = json.dumps(row)
    set_repository_demo_zones(monkeypatch, [row])
    demo = repository_demo(replace(make_settings(tmp_path), org_demo_enabled=True), as_of=AS_OF)
    assert demo["zones"] == [{
        **row, "owner": " @sample-organization/payments " if metadata else "",
        "prs": [907, 903, 901] if metadata else [],
        "rate": 0.2, "low_sample": False, "sample_state": "measured",
    }]
    assert demo["actions"] == [{
        "zone": "auth/", "owner": row.get("owner", ""),
        "action": "One more verified change in this zone", "from": 1, "to": 2,
    }]
    assert json.dumps(row) == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner", None), ("owner", True), ("owner", 1), ("owner", []), ("owner", {}),
        ("prs", None), ("prs", "901"), ("prs", (901,)), ("prs", {}),
        ("prs", [True]), ("prs", [False]), ("prs", [0]), ("prs", [-1]),
        ("prs", [1.5]), ("prs", ["901"]), ("prs", [None]),
        ("zone", None), ("zone", True), ("zone", 1), ("zone", ""),
    ],
)
def test_repository_demo_rejects_invalid_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object,
) -> None:
    set_repository_demo_zones(monkeypatch, [repository_demo_zone(**{field: value})])
    with pytest.raises(ValueError, match="Invalid repository demo"):
        repository_demo(replace(make_settings(tmp_path), org_demo_enabled=True))


@pytest.mark.parametrize("field", ["merged", "gated", "attested", "forced", "answerers"])
@pytest.mark.parametrize("value", [True, False, -1, "1", 1.0, 1.5])
def test_repository_demo_validates_helper_counters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object,
) -> None:
    set_repository_demo_zones(monkeypatch, [repository_demo_zone(**{field: value})])
    with pytest.raises(ValueError, match="Invalid coverage counter"):
        repository_demo(replace(make_settings(tmp_path), org_demo_enabled=True))


@pytest.mark.parametrize("counters", [
    {"gated": 5, "attested": 6, "answerers": 2},
    {"gated": 5, "attested": 1, "answerers": 0},
    {"gated": 5, "attested": 0, "answerers": 1},
    {"gated": None, "attested": 0, "answerers": None},
    {"gated": None, "attested": None, "answerers": 0},
    {"gated": 0, "attested": None, "answerers": None},
])
def test_repository_demo_rejects_inconsistent_known_and_unknown_counters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, counters: dict[str, object],
) -> None:
    set_repository_demo_zones(monkeypatch, [repository_demo_zone(**counters)])
    with pytest.raises(ValueError, match="Invalid"):
        repository_demo(replace(make_settings(tmp_path), org_demo_enabled=True))


def test_repository_demo_rejects_more_attested_than_gated_in_card_totals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = json.loads(
        files("lasthuman").joinpath("data/org_dashboard_demo.json").read_text(encoding="utf-8"),
    )
    fixture["repository_dashboard"]["attested_total"] = fixture["repository_dashboard"]["gated_total"] + 1
    monkeypatch.setattr("lasthuman.server.organization._demo_fixture", lambda: fixture)
    with pytest.raises(ValueError, match="Invalid repository demo counters"):
        repository_demo(replace(make_settings(tmp_path), org_demo_enabled=True))


def test_repository_demo_sorts_table_and_limited_actions_by_actual_risk_rank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        repository_demo_zone(zone="idle/", merged=0, gated=0, attested=0, answerers=0),
        repository_demo_zone(zone="quiet/", gated=0, attested=0, answerers=0),
        repository_demo_zone(zone="covered/", answerers=2),
        repository_demo_zone(zone="one/"),
        repository_demo_zone(zone="zero/", answerers=0, attested=0),
        repository_demo_zone(zone="a-tie/", answerers=0, attested=0),
        repository_demo_zone(zone="more-merges/", merged=10, answerers=0, attested=0),
        repository_demo_zone(zone="exceptions/", forced=2, answerers=0, attested=0),
        repository_demo_zone(zone="one-exception/", forced=1),
    ]
    before = json.dumps(rows)
    set_repository_demo_zones(monkeypatch, rows)
    demo = repository_demo(replace(make_settings(tmp_path), org_demo_enabled=True))
    assert [row["zone"] for row in demo["zones"]] == [
        "exceptions/", "more-merges/", "a-tie/", "zero/", "one-exception/",
        "one/", "covered/", "quiet/", "idle/",
    ]
    assert [action["zone"] for action in demo["actions"]] == [
        row["zone"] for row in demo["zones"][:5]
    ]
    assert [action["action"] for action in demo["actions"]] == [
        "Verify 2 exceptions after the fact",
        "One more verified change in this zone",
        "One more verified change in this zone",
        "One more verified change in this zone",
        "Verify 1 exception after the fact",
    ]
    assert json.dumps(rows) == before


@pytest.mark.parametrize(
    ("gated", "answerers", "forced", "action"),
    [
        (5, 0, 0, "One more verified change in this zone"),
        (3, 1, 0, "One more verified change in this zone"),
        (5, 1, 1, "Verify 1 exception after the fact"),
        (5, 2, 2, "Verify 2 exceptions after the fact"),
        (5, 2, 0, None),
        (0, 0, 0, None),
        (0, 0, 1, None),
        (None, None, 0, None),
    ],
)
def test_repository_demo_actions_preserve_sparse_and_ungated_display_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    gated: int | None, answerers: int | None, forced: int, action: str | None,
) -> None:
    row = repository_demo_zone(
        gated=gated, attested=None if gated is None else int(bool(answerers)),
        answerers=answerers, forced=forced,
    )
    set_repository_demo_zones(monkeypatch, [row])
    demo = repository_demo(replace(make_settings(tmp_path), org_demo_enabled=True))
    result = demo["zones"][0]
    assert result["gated"] == gated and result["answerers"] == answerers
    assert result["attested"] == row["attested"]
    assert result["rate"] == (row["attested"] / gated if gated == 5 else None)
    assert result["low_sample"] is (gated == 3)
    assert result["sample_state"] == (
        "no_data" if not gated else "small_sample" if gated < 5 else "measured"
    )
    if action is None:
        assert demo["actions"] == []
    else:
        assert demo["actions"] == [{
            "zone": row["zone"], "owner": row["owner"], "action": action,
            "from": answerers, "to": answerers + 1,
        }]


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
