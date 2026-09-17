from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "src" / "lasthuman" / "templates"
ORG_PATH = "/repos/41/dashboard/organization"
REPO_PATH = "/repos/41/dashboard"


@dataclass
class Element:
    tag: str
    attrs: dict[str, str | None]
    text: str = ""
    ancestors: tuple[Element, ...] = ()

    @property
    def words(self) -> str:
        return " ".join(self.text.split())


class Page(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__()
        self.elements: list[Element] = []
        self.stack: list[Element] = []
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = Element(tag, dict(attrs), ancestors=tuple(self.stack))
        self.elements.append(element)
        if tag not in {"meta", "input", "br", "hr", "link", "img"}:
            self.stack.append(element)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        for element in self.stack:
            element.text += data

    def select(self, tag: str, **attrs: str) -> list[Element]:
        return [
            element
            for element in self.elements
            if element.tag == tag and all(element.attrs.get(key) == value for key, value in attrs.items())
        ]

    def by_class(self, tag: str, class_name: str) -> list[Element]:
        return [
            element
            for element in self.select(tag)
            if class_name in (element.attrs.get("class") or "").split()
        ]


def _render(template: str, **context: object) -> str:
    # .html.j2 is not autoescaped by Flask's extension detection.
    environment = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=False,
        undefined=StrictUndefined,
    )
    environment.globals["url_for"] = lambda endpoint: {"logout": "/auth/logout"}[endpoint]
    return environment.get_template(template).render(**context)


def _module(**overrides: object) -> dict[str, object]:
    return {
        "zone": "auth/",
        "gated": 5,
        "attested": 1,
        "answerers": 1,
        "rate": 0.2,
        "contacts": [{"label": "@sample-org/payments", "url": None}],
        "contact_status": "",
        "status": "Available",
        **overrides,
    }


def _repository(**overrides: object) -> dict[str, object]:
    return {
        "id": "sample-payments",
        "name": "sample-payments",
        "dashboard_url": f"{REPO_PATH}?data=demo",
        "status": "Available",
        "modules": [_module()],
        **overrides,
    }


def _view(**overrides: object) -> dict[str, object]:
    view: dict[str, object] = {
        "source": "demo",
        "organization": "sample-organization",
        "demo_enabled": True,
        "repository_options": [
            {"id": "sample-payments", "name": "sample-payments"},
            {"id": "sample-platform", "name": "sample-platform"},
            {"id": "sample-commerce", "name": "sample-commerce"},
        ],
        "selected_repository": "all",
        "selected_bucket": "all",
        "summary": {"zero": 1, "one": 1, "many": 2},
        "repositories": [_repository()],
        "notice": "",
        **overrides,
    }
    view["links"] = {
        "actual": f"{ORG_PATH}?source=actual",
        "demo": f"{ORG_PATH}?source=demo",
        **{
            key: f"{ORG_PATH}?"
            + urlencode(
                {
                    "source": view["source"],
                    "repository": view["selected_repository"],
                    "bucket": bucket,
                }
            )
            for key, bucket in (("reset", "all"), ("zero", "zero"), ("one", "one"), ("many", "many"))
        },
    }
    return view


def _render_org(view: dict[str, object] | None = None, **context: object) -> str:
    return _render(
        "app_organization_dashboard.html.j2",
        **{
            "view": _view() if view is None else view,
            "csp_nonce": "org-render-nonce",
            "organization_path": ORG_PATH,
            "repo_dashboard_url": f"{REPO_PATH}?data=demo",
            **context,
        },
    )


def _render_repo(**context: object) -> str:
    return _render(
        "app_dashboard.html.j2",
        **{
            "repo": "acme/the-last-human",
            "actor_login": "reader",
            "csrf_token": "csrf-value",
            "csp_nonce": "repo-render-nonce",
            "dashboard": {
                "demo_seeded": True,
                "window_days": 30,
                "generated_at": "2026-09-17T00:00:00Z",
                "attested_total": 1,
                "gated_total": 5,
                "attested_rate": 0.2,
                "min_sample": 5,
                "merged_total": 5,
                "waiting_total": 0,
                "forced_total": 0,
                "zero_answerer_zones": 0,
                "zones": [
                    {
                        "zone": "auth/",
                        "owner": "@sample-org/payments",
                        "answerers": 1,
                        "gated": 5,
                        "attested": 1,
                        "rate": 0.2,
                        "forced": 0,
                        "prs": [901],
                    }
                ],
                "actions": [],
            },
            **context,
        },
    )


def test_organization_product_labels_and_module_count_cards() -> None:
    page = Page(_render_org())
    assert page.select("h1")[0].words == "sample-organization"
    assert page.by_class("span", "period")[0].words == "Last 30 days"
    assert page.by_class("span", "source-badge")[0].words == "Demo data"
    assert page.by_class("span", "selection-name")[0].words == "3 sample repositories"
    assert [card.words for card in page.by_class("a", "summary-card")] == [
        "0 confirmed authors 1 module",
        "1 confirmed author 1 module",
        "2+ confirmed authors 2 modules",
    ]
    assert [heading.words for heading in page.select("th", scope="col")] == [
        "Module", "Verified before merge", "Can answer", "Declared owner", "Data status",
    ]


@pytest.mark.parametrize("source", ["actual", "demo"])
@pytest.mark.parametrize("count", [0, 1, 3])
def test_organization_header_permission_scope_depends_on_source(source: str, count: int) -> None:
    options = [{"id": str(index), "name": f"acme/repo-{index}"} for index in range(count)]
    page = Page(_render_org(_view(source=source, repository_options=options)))
    selection = page.by_class("span", "selection-name")[0]
    noun = "repository" if count == 1 else "repositories"
    if source == "actual":
        assert selection.words == f"{count} {noun} you can open"
    else:
        assert selection.words == f"{count} sample {noun}"
        header = page.by_class("header", "topbar")[0].words + page.by_class("div", "selection")[0].words
        assert not any(word in header.lower() for word in ("permission", "authorized", "you can open"))


@pytest.mark.parametrize("source", ["actual", "demo"])
def test_selected_repository_header_keeps_only_its_name(source: str) -> None:
    page = Page(
        _render_org(
            _view(
                source=source,
                selected_repository="41",
                repository_options=[{"id": "41", "name": "acme/the-last-human"}],
            )
        )
    )
    assert page.by_class("span", "selection-name")[0].words == "acme/the-last-human"


def test_bucket_links_preserve_repository_without_recalculating_summary() -> None:
    page = Page(_render_org(_view(selected_repository="sample-payments", selected_bucket="one")))
    cards = page.by_class("a", "summary-card")
    assert [card.words for card in cards] == [
        "0 confirmed authors 1 module", "1 confirmed author 1 module", "2+ confirmed authors 2 modules",
    ]
    assert [card.attrs.get("aria-current") for card in cards] == [None, "true", None]
    for card, bucket in zip(cards, ("zero", "one", "many"), strict=True):
        assert parse_qs(urlsplit(card.attrs["href"]).query) == {
            "source": ["demo"], "repository": ["sample-payments"], "bucket": [bucket],
        }
    reset = page.by_class("a", "reset")[0]
    assert reset.words == "All modules"
    assert parse_qs(urlsplit(reset.attrs["href"]).query)["bucket"] == ["all"]


def test_source_switch_links_reset_source_specific_filters() -> None:
    page = Page(_render_org(_view(selected_repository="sample-payments", selected_bucket="many")))
    for source, label in (("actual", "Actual data"), ("demo", "Demo")):
        link = next(link for link in page.select("a") if link.words == label)
        assert parse_qs(urlsplit(link.attrs["href"]).query) == {"source": [source]}
        assert link.attrs.get("aria-current") == ("page" if source == "demo" else None)


def test_repository_get_form_retains_source_and_bucket_with_authorized_options() -> None:
    page = Page(
        _render_org(
            _view(
                source="actual",
                selected_repository="41",
                selected_bucket="zero",
                repository_options=[{"id": "41", "name": "acme/the-last-human"}],
            )
        )
    )
    assert page.select("form")[0].attrs["action"] == ORG_PATH
    assert page.select("form")[0].attrs["method"] == "get"
    assert {field.attrs["name"]: field.attrs["value"] for field in page.select("input")} == {
        "source": "actual", "bucket": "zero",
    }
    assert page.select("select")[0].attrs == {"id": "repository", "name": "repository"}
    assert page.select("label")[0].attrs["for"] == "repository"
    assert [option.words for option in page.select("option")] == ["All connected", "acme/the-last-human"]
    assert "selected" in page.select("option", value="41")[0].attrs
    assert "selected" not in page.select("option", value="all")[0].attrs
    assert page.select("button", type="submit")[0].words == "Apply"
    assert page.by_class("span", "selection-name")[0].words == "acme/the-last-human"
    assert page.by_class("span", "source-badge")[0].words == "Actual data"


def test_disabled_demo_is_visible_but_not_linked() -> None:
    page = Page(_render_org(_view(source="actual", demo_enabled=False)))
    assert page.select("span", **{"aria-disabled": "true"})[0].words == "Demo"
    assert not any(link.words == "Demo" for link in page.select("a"))
    assert page.select("a", **{"aria-current": "page"})[0].words == "Actual data"


@pytest.mark.parametrize(
    ("module", "fraction", "answerers", "status"),
    [
        (_module(), "1 / 5 20%", "1", "Available"),
        (_module(attested=0, answerers=0, rate=0.0), "0 / 5 0%", "0", "Available"),
        (
            _module(gated=3, attested=3, answerers=2, rate=None, status="Sample too small"),
            "3 / 3", "2", "Sample too small",
        ),
        (
            _module(gated=0, attested=0, answerers=0, rate=None, status="No gated changes"),
            "0 / 0", "0", "No gated changes",
        ),
        (
            _module(gated=None, attested=None, answerers=None, rate=None, status="Collection delayed"),
            "—", "—", "Collection delayed",
        ),
        (
            _module(gated=None, attested=None, answerers=None, rate=None, status="No measured data"),
            "—", "—", "No measured data",
        ),
        (
            _module(gated=None, attested=None, answerers=None, rate=None, status="Data unavailable"),
            "—", "—", "Data unavailable",
        ),
    ],
)
def test_module_fractions_small_samples_and_unknown_states(
    module: dict[str, object], fraction: str, answerers: str, status: str,
) -> None:
    page = Page(_render_org(_view(repositories=[_repository(modules=[module])])))
    assert page.by_class("td", "verified")[0].words == fraction
    assert page.by_class("td", "answerers")[0].words == answerers
    assert page.select("td")[-1].words == status
    if module["rate"] is None:
        assert not page.by_class("span", "rate")


def test_actual_declared_contacts_link_to_supplied_current_github_destinations() -> None:
    contacts = [
        {"label": "@acme/platform", "url": "https://github.com/orgs/acme/teams/platform"},
        {"label": "@maintainer", "url": "https://github.com/maintainer"},
    ]
    page = Page(
        _render_org(_view(source="actual", repositories=[_repository(modules=[_module(contacts=contacts)])]))
    )
    links = page.by_class("a", "contact")
    assert [link.attrs["href"] for link in links] == [contact["url"] for contact in contacts]
    assert [link.words for link in links] == [contact["label"] for contact in contacts]
    assert all(set((link.attrs["rel"] or "").split()) == {"noopener", "noreferrer"} for link in links)


@pytest.mark.parametrize("url", [None, "https://github.com/orgs/acme/teams/platform"])
def test_demo_contacts_never_link_to_real_people(url: str | None) -> None:
    module = _module(contacts=[{"label": "@sample-org/platform", "url": url}])
    page = Page(_render_org(_view(repositories=[_repository(modules=[module])])))
    assert page.by_class("span", "contact")[0].words == "@sample-org/platform"
    assert not page.by_class("a", "contact")
    assert not any("github.com" in (link.attrs["href"] or "") for link in page.select("a"))


@pytest.mark.parametrize("status", ["", "Not declared", "Contact unavailable"])
def test_missing_contacts_preserve_independently_valid_metrics(status: str) -> None:
    module = _module(contacts=[], contact_status=status)
    page = Page(_render_org(_view(source="actual", repositories=[_repository(modules=[module])])))
    assert page.by_class("td", "owner")[0].words == (status or "Not declared")
    assert page.by_class("td", "verified")[0].words == "1 / 5 20%"
    assert not page.by_class("a", "contact")


def test_fake_repository_groups_all_open_actual_target_in_sample_mode() -> None:
    page = Page(
        _render_org(
            _view(
                repositories=[
                    _repository(id=name, name=name)
                    for name in ("sample-payments", "sample-platform", "sample-commerce")
                ]
            )
        )
    )
    links = page.by_class("a", "repository-link")
    assert len(links) == 3
    assert {link.attrs["href"] for link in links} == {f"{REPO_PATH}?data=demo"}
    assert all(link.attrs["title"] == "Open repository dashboard" for link in links)
    destination = Page(_render_repo(dashboard_source="demo"))
    assert destination.by_class("div", "repo")[0].words == "acme/the-last-human Demo data"
    assert "sample-payments" not in destination.select("title")[0].words
    assert not any("/pull/" in (link.attrs["href"] or "") for link in destination.select("a"))


def test_actual_repository_links_keep_their_own_scoped_dashboard_urls() -> None:
    urls = ["/repos/41/dashboard?data=repo", "/repos/52/dashboard?data=repo"]
    page = Page(
        _render_org(
            _view(
                source="actual",
                repositories=[
                    _repository(id=str(index), name=f"acme/repo-{index}", dashboard_url=url)
                    for index, url in enumerate(urls)
                ],
            ),
            repo_dashboard_url=urls[0],
        )
    )
    assert [link.attrs["href"] for link in page.by_class("a", "repository-link")] == urls
    assert page.by_class("a", "back")[0].attrs["href"] == urls[0]


@pytest.mark.parametrize("status", ["Available", "No measured data", "Data unavailable"])
def test_empty_repository_groups_remain_visible_with_status(status: str) -> None:
    page = Page(_render_org(_view(repositories=[_repository(modules=[], status=status)])))
    assert page.by_class("a", "repository-link")[0].words == "sample-payments→"
    assert not page.select("table")
    assert page.by_class("p", "empty")[0].words == (
        "No measured data" if status == "Available" else status
    )


@pytest.mark.parametrize("repositories", [[], [_repository(modules=[])]])
def test_empty_bucket_has_a_reset_and_does_not_imply_zero_measurements(
    repositories: list[dict[str, object]],
) -> None:
    page = Page(_render_org(_view(repositories=repositories, selected_bucket="many")))
    assert page.by_class("p", "empty")[0].words == "No modules match this filter."
    assert page.by_class("a", "reset")[0].words == "All modules"
    assert len(page.by_class("a", "summary-card")) == 3


def test_no_measured_repositories_and_optional_notice() -> None:
    view = _view(repositories=[], repository_options=[], summary={"zero": 0, "one": 0, "many": 0})
    del view["notice"]
    page = Page(_render_org(view))
    assert page.by_class("p", "empty")[0].words == "No measured data"
    assert not page.by_class("p", "notice")
    assert all(card.words.endswith("0 modules") for card in page.by_class("a", "summary-card"))
    notice = Page(_render_org(_view(notice="Some repository data is unavailable.")))
    assert notice.select("p", role="status")[0].words == "Some repository data is unavailable."


def test_organization_autoescapes_all_displayed_data_and_nonce() -> None:
    text = '<script>alert("sample")</script> & owner'
    module = _module(zone=text, contacts=[{"label": text, "url": None}], contact_status=text, status=text)
    html = _render_org(
        _view(
            organization=text,
            repository_options=[{"id": 'sample" value="injected', "name": text}],
            repositories=[_repository(name=text, modules=[module], status=text)],
            notice=text,
        ),
        csp_nonce='nonce" onload="bad',
    )
    page = Page(html)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert not page.select("script")
    assert page.select("h1")[0].words == text
    assert page.select("style")[0].attrs == {"nonce": 'nonce" onload="bad'}
    assert page.select("option")[1].attrs == {"value": 'sample" value="injected'}
    assert page.select("th", scope="row")[0].words == text
    assert page.by_class("span", "contact")[0].words == text


def test_organization_is_keyboard_accessible_and_has_no_frontend_dependencies() -> None:
    html = _render_org()
    page = Page(html)
    assert page.select("html")[0].attrs["lang"] == "en"
    assert page.select("style")[0].attrs["nonce"] == "org-render-nonce"
    assert ":focus-visible" in html
    assert "overflow-x: auto" in html
    assert "min-width: 0" in html
    assert "@media (max-width:" in html
    assert page.by_class("div", "tablewrap")[0].attrs["tabindex"] == "0"
    assert page.by_class("div", "tablewrap")[0].attrs["role"] == "region"
    assert page.select("th", scope="row")
    assert not page.select("script")
    assert not page.select("link")
    assert all(not name.startswith("on") for element in page.elements for name in element.attrs)


@pytest.mark.parametrize("source", ["legacy", "repo", "demo"])
def test_repository_trust_headings_are_visible_between_cards_and_table_outside_footer(source: str) -> None:
    page = Page(_render_repo(dashboard_source=source))
    region = page.by_class("div", "trust-notes")[0]
    notes = page.by_class("details", "trust-note")
    headings = page.select("h3")
    assert [heading.words for heading in headings] == [
        "No people metrics", "Small samples stay small", "No retroactive credit",
    ]
    assert len(notes) == 3
    for heading, note in zip(headings, notes, strict=True):
        assert heading.ancestors[-1].tag == "summary"
        assert note in heading.ancestors
        assert region in heading.ancestors
        assert "open" not in note.attrs
        assert not any(
            ancestor.tag == "footer" or "foot" in (ancestor.attrs.get("class") or "").split()
            for ancestor in heading.ancestors
        )
        assert not any("hidden" in ancestor.attrs for ancestor in (*heading.ancestors, heading))
    kpis = page.by_class("div", "kpis")[0]
    table_panel = page.select("section", **{"aria-labelledby": "zones-h"})[0]
    assert page.elements.index(kpis) < page.elements.index(region) < page.elements.index(table_panel)


def test_repository_trust_disclosures_preserve_the_original_copy() -> None:
    page = Page(_render_repo())
    notes = page.by_class("details", "trust-note")
    bodies = [
        paragraph.words for paragraph in page.select("p")
        if paragraph.ancestors[-1] in notes
    ]
    assert bodies == [
        "Owners come from CODEOWNERS as written. People who verified are counted, never named. "
        "No rankings, no hold history, no raw answers.",
        "A zone with fewer than 5 gated PRs shows counts, not a rate. "
        "A percentage of three is a signal that isn't there.",
        "A verification after merge never raises the before-merge rate. Exceptions stay visible.",
    ]


def test_repository_answer_count_explanation_belongs_to_its_column() -> None:
    page = Page(_render_repo())
    note = page.by_class("span", "column-note")[0]
    assert note.words == "counts, never names"
    column = note.ancestors[-1]
    assert column.tag == "th"
    assert column.attrs["scope"] == "col"
    assert column.words == "Can answer counts, never names"
    assert "counts, never names" not in page.by_class("span", "sub")[0].words


def test_repository_template_retains_legacy_behavior_when_new_context_is_absent() -> None:
    page = Page(_render_repo())
    assert not page.by_class("a", "org-link")
    assert not page.by_class("nav", "source-switch")
    assert page.by_class("span", "demo")[0].attrs["title"] == (
        "The history in this window includes seeded demo data; live merges are added on top."
    )
    assert page.select("h1")[0].words == "Where trust is thin"
    assert any(link.attrs["href"] == "https://github.com/acme/the-last-human/pull/901" for link in page.select("a"))
    assert page.select("form")[0].attrs == {"method": "post", "action": "/auth/logout"}
    assert page.select("input", name="csrf_token")[0].attrs["value"] == "csrf-value"


@pytest.mark.parametrize(
    ("source", "active_label", "badge"),
    [("repo", "Actual data", "Actual data"), ("demo", "Demo", "Demo data"), ("legacy", None, "Demo data")],
)
def test_repository_source_controls_preserve_target_name_and_legacy_provenance(
    source: str, active_label: str | None, badge: str,
) -> None:
    page = Page(
        _render_repo(
            dashboard_source=source,
            dashboard_source_links={"actual": f"{REPO_PATH}?data=repo", "demo": f"{REPO_PATH}?data=demo"},
            organization_url=f"{ORG_PATH}?source=actual",
        )
    )
    assert page.by_class("div", "repo")[0].words == f"acme/the-last-human {badge}"
    assert page.by_class("a", "org-link")[0].attrs["href"] == f"{ORG_PATH}?source=actual"
    assert {link.words: link.attrs["href"] for link in page.select("a") if link.words in {"Actual data", "Demo"}} == {
        "Actual data": f"{REPO_PATH}?data=repo", "Demo": f"{REPO_PATH}?data=demo",
    }
    assert [link.words for link in page.select("a", **{"aria-current": "page"})] == (
        [active_label] if active_label else []
    )
    if source == "demo":
        assert page.by_class("span", "demo")[0].attrs["title"] == "Sample data only"
        assert not any("/pull/" in (link.attrs["href"] or "") for link in page.select("a"))
    elif source == "repo":
        assert not page.by_class("span", "demo")


def test_repository_optional_empty_controls_and_autoescaping() -> None:
    text = '<script>alert("reader")</script> & repository'
    html = _render_repo(
        repo=text,
        actor_login=text,
        dashboard_source="repo",
        dashboard_source_links={},
        organization_url="",
    )
    page = Page(html)
    assert "<script>" not in html
    assert not page.select("script")
    assert not page.by_class("nav", "source-switch")
    assert not page.by_class("a", "org-link")
    assert page.select("title")[0].words == f"Last Human dashboard · {text}"
    assert page.select("style")[0].attrs["nonce"] == "repo-render-nonce"
