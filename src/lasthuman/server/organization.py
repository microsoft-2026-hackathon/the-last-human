"""Read-only organization projections and explicitly separated sample data."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from importlib.resources import files
from typing import Literal, cast
from urllib.parse import urlencode

import requests

from .config import Settings
from .coverage import MIN_SAMPLE
from .github import GitHubClient, GitHubError
from .service import BotError, BotService

Source = Literal["actual", "demo"]
Bucket = Literal["all", "zero", "one", "many"]
_BUCKETS = {"all", "zero", "one", "many"}
_REPOSITORY_ID = re.compile(r"[1-9][0-9]{0,18}", re.ASCII)
_SAMPLE_ID = re.compile(r"sample-[a-z0-9]+(?:-[a-z0-9]+)*", re.ASCII)
_LOGIN = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?"
_HANDLE = re.compile(rf"@({_LOGIN})(?:/([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*))?", re.ASCII)
_INCOMPLETE = "Some data is temporarily unavailable."


class OrganizationError(RuntimeError):
    """Non-disclosing organization request failure."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class OrganizationQuery:
    source: Source = "actual"
    repository: str = "all"
    bucket: Bucket = "all"

    @classmethod
    def parse(cls, pairs: Iterable[tuple[str, str]]) -> OrganizationQuery:
        values = _unique_query(pairs, {"source", "repository", "bucket"})
        source = values.get("source", "actual")
        repository = values.get("repository", "all")
        bucket = values.get("bucket", "all")
        if source not in {"actual", "demo"} or bucket not in _BUCKETS:
            raise OrganizationError("Invalid dashboard query")
        # Existence/authorization is deliberately not inferred from syntax.
        if repository != "all" and not (
            _REPOSITORY_ID.fullmatch(repository) or _SAMPLE_ID.fullmatch(repository)
        ):
            raise OrganizationError("Repository not found", status_code=404)
        return cls(cast(Source, source), repository, cast(Bucket, bucket))

    def url(self, path: str) -> str:
        return path + "?" + urlencode({
            "source": self.source, "repository": self.repository, "bucket": self.bucket,
        })


def repository_query(pairs: Iterable[tuple[str, str]]) -> tuple[str, int]:
    """The existing repository period plus an explicit, opt-in source selector."""
    values = _unique_query(pairs, {"data", "days"})
    source = values.get("data", "legacy")
    if source not in {"repo", "demo"} and "data" in values:
        raise OrganizationError("Invalid dashboard query")
    raw_days = values.get("days", "")
    if raw_days == "":
        return source, 30
    if not raw_days.isascii() or not raw_days.isdigit():
        raise OrganizationError("days must be positive")
    try:
        days = int(raw_days)
    except ValueError:
        raise OrganizationError("days must be positive") from None
    if days <= 0:
        raise OrganizationError("days must be positive")
    return source, days


def _unique_query(pairs: Iterable[tuple[str, str]], allowed: set[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for key, value in pairs:
        if key not in allowed or key in values:
            raise OrganizationError("Invalid dashboard query")
        values[key] = value
    return values


@dataclass(frozen=True)
class Contact:
    label: str
    url: str | None = None


@dataclass(frozen=True)
class ModuleView:
    zone: str
    gated: int | None
    attested: int | None
    answerers: int | None
    rate: float | None = None
    contacts: tuple[Contact, ...] = ()
    contact_status: str = "Not declared"
    status: str = "Available"


@dataclass(frozen=True)
class RepositoryView:
    id: str
    name: str
    dashboard_url: str
    modules: tuple[ModuleView, ...] = ()
    status: str = "Available"


@dataclass(frozen=True)
class RepositoryOption:
    id: str
    name: str


@dataclass(frozen=True)
class Summary:
    zero: int = 0
    one: int = 0
    many: int = 0


@dataclass(frozen=True)
class OrganizationView:
    source: Source
    organization: str
    owner_id: int | None
    as_of: datetime
    since: datetime
    demo_enabled: bool
    repository_options: tuple[RepositoryOption, ...]
    selected_repository: str
    selected_bucket: Bucket
    summary: Summary
    repositories: tuple[RepositoryView, ...]
    links: Mapping[str, str]
    notice: str = ""


@dataclass(frozen=True)
class RepositoryRead:
    repositories: tuple[RepositoryView, ...]
    incomplete: bool = False


OrganizationProvider = Callable[[str, datetime], RepositoryRead]


def module_bucket(module: ModuleView) -> Bucket | None:
    if (
        module.status not in {"Available", "Sample too small"}
        or module.gated is None or module.gated <= 0 or module.answerers is None
    ):
        return None
    return "zero" if module.answerers == 0 else "one" if module.answerers == 1 else "many"


def summarize(repositories: Iterable[RepositoryView]) -> Summary:
    counts = {"zero": 0, "one": 0, "many": 0}
    seen: set[tuple[str, str]] = set()
    for repository in repositories:
        for module in repository.modules:
            key = (repository.id, module.zone)
            if key in seen:
                raise ValueError("Duplicate repository module")
            seen.add(key)
            bucket = module_bucket(module)
            if bucket is not None:
                counts[bucket] += 1
    return Summary(**counts)


def organization_view(
    settings: Settings, query: OrganizationQuery, *, user_token: str, provider: OrganizationProvider,
    as_of: datetime | None = None,
) -> OrganizationView:
    end = as_of if as_of is not None else datetime.now(timezone.utc)
    path = settings.path_prefix + "/dashboard/organization"
    if query.source == "demo":
        require_demo(settings)
        repositories = demo_repositories(settings.path_prefix + "/dashboard?data=demo")
        organization, owner_id, notice = "sample-organization", None, ""
    else:
        read = provider(user_token, end)
        repositories = read.repositories
        organization = settings.repository.split("/", 1)[0]
        incomplete = read.incomplete or any(
            repo.status in {"Data unavailable", "Partial measurement"} for repo in repositories
        )
        owner_id, notice = settings.owner_id, _INCOMPLETE if incomplete else ""
    repositories = tuple(
        replace(repo, modules=tuple(sorted(repo.modules, key=lambda module: module.zone.casefold())))
        for repo in sorted(repositories, key=lambda repository: repository.name.casefold())
    )
    options = tuple(RepositoryOption(repo.id, repo.name) for repo in repositories)
    if query.repository != "all":
        repositories = tuple(repo for repo in repositories if repo.id == query.repository)
        if not repositories:
            raise OrganizationError("Repository not found", status_code=404)
    summary = summarize(repositories)
    if query.bucket != "all":
        repositories = tuple(
            replace(repo, modules=tuple(module for module in repo.modules if module_bucket(module) == query.bucket))
            for repo in repositories
        )
    links = {
        "actual": OrganizationQuery(source="actual").url(path),
        "demo": OrganizationQuery(source="demo").url(path),
        **{bucket: replace(query, bucket=cast(Bucket, bucket)).url(path)
           for bucket in ("zero", "one", "many")},
        "reset": replace(query, bucket="all").url(path),
    }
    return OrganizationView(
        query.source, organization, owner_id, end, end - timedelta(days=30), settings.org_demo_enabled,
        options, query.repository, query.bucket, summary, repositories, links, notice,
    )


def require_demo(settings: Settings) -> None:
    if not settings.org_demo_enabled:
        raise OrganizationError("Demo data is unavailable", status_code=404)


def authorized_repository_info(settings: Settings, github: GitHubClient, user_token: str) -> dict[str, object]:
    info = github.repository_info(user_token=user_token)
    owner = info.get("owner")
    repository_id = info.get("id")
    owner_id = owner.get("id") if isinstance(owner, dict) else None
    identities = ((repository_id, settings.repository_id), (owner_id, settings.owner_id))
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value != expected for value, expected in identities
    ):
        raise GitHubError("GitHub repository identity is unavailable")
    if (
        not isinstance(info.get("full_name"), str)
        or str(info["full_name"]).casefold() != settings.repository.casefold()
    ):
        raise GitHubError("GitHub repository identity is unavailable")
    permissions = info.get("permissions")
    if isinstance(permissions, dict) and permissions.get("pull") is False:
        raise GitHubError("GitHub repository is unavailable", status_code=404)
    return info


def read_authorized_repository(
    settings: Settings, service: BotService, github: GitHubClient, *,
    user_token: str, repository_info: dict[str, object], as_of: datetime,
) -> RepositoryView:
    """Caller keeps this tenant admitted and has checked its identity."""
    base = RepositoryView(
        str(settings.repository_id), settings.repository, settings.path_prefix + "/dashboard?data=repo",
    )
    try:
        payload = service.dashboard(days=30, include_seed=False, as_of=as_of)
    except (BotError, sqlite3.Error, OSError, ValueError):
        return replace(base, status="Data unavailable")
    try:
        rules = github.current_codeowners(user_token, repository_info=repository_info)
    except GitHubError as error:
        if error.status_code == 401:
            raise
        rules = None
    except requests.RequestException:
        rules = None
    try:
        modules = project_modules(payload, rules)
    except (ValueError, TypeError, KeyError):
        return replace(base, status="Data unavailable")
    status = "No measured data"
    if payload.get("measured_total", 0):
        status = "Partial measurement" if payload.get("unmeasured_total", 0) else "Available"
    return replace(base, modules=modules, status=status)


def fixed_organization_provider(settings: Settings, service: BotService, github: GitHubClient) -> OrganizationProvider:
    def read(user_token: str, as_of: datetime) -> RepositoryRead:
        try:
            info = authorized_repository_info(settings, github, user_token)
            result = read_authorized_repository(
                settings, service, github, user_token=user_token, repository_info=info, as_of=as_of,
            )
            authorized_repository_info(settings, github, user_token)
        except GitHubError as error:
            if error.status_code == 401:
                raise OrganizationError("Sign in required", status_code=401) from None
            return RepositoryRead((), incomplete=error.status_code != 404)
        except requests.RequestException:
            return RepositoryRead((), incomplete=True)
        return RepositoryRead((result,))
    return read


def project_modules(payload: Mapping[str, object], rules: Mapping[str, str] | None) -> tuple[ModuleView, ...]:
    raw_rows = payload.get("zones")
    if not isinstance(raw_rows, list):
        raise ValueError("Invalid coverage rows")
    result: list[ModuleView] = []
    measured = _count(payload.get("measured_total", 0))
    for raw in raw_rows:
        if not isinstance(raw, dict) or not isinstance(raw.get("zone"), str) or not raw["zone"]:
            raise ValueError("Invalid coverage row")
        zone = raw["zone"]
        contacts, contact_status = declared_contacts(zone, rules)
        if not measured:
            result.append(ModuleView(
                zone, None, None, None, contacts=contacts, contact_status=contact_status, status="No measured data",
            ))
            continue
        gated, attested, answerers = (_count(raw.get(key)) for key in ("gated", "attested", "answerers"))
        if attested > gated:
            raise ValueError("Invalid coverage counters")
        status = "No gated changes" if gated == 0 else "Sample too small" if gated < MIN_SAMPLE else "Available"
        result.append(ModuleView(
            zone, gated, attested, answerers, None if gated < MIN_SAMPLE else attested / gated,
            contacts, contact_status, status,
        ))
    return tuple(result)


def declared_contacts(zone: str, rules: Mapping[str, str] | None) -> tuple[tuple[Contact, ...], str]:
    if rules is None:
        return (), "Contact unavailable"
    declared: str | None = None
    for pattern, owners in rules.items():
        relation = _rule_relation(zone, pattern)
        if relation == "covers":
            declared = owners
        elif relation == "uncertain":
            declared = None
    contacts: list[Contact] = []
    for label in (declared or "").split():
        match = _HANDLE.fullmatch(label)
        if match is None or "--" in match.group(1):
            continue
        user, team = match.groups()
        if team is not None and len(team) > 100:
            continue
        url = f"https://github.com/{user}" if team is None else f"https://github.com/orgs/{user}/teams/{team}"
        contact = Contact(label, url)
        if contact not in contacts:
            contacts.append(contact)
    return tuple(contacts), "" if contacts else "Not declared"


def _rule_relation(zone: str, pattern: str) -> str:
    """Accept whole-module ownership only; narrower or complex rules may override it."""
    module = zone.strip("/")
    if pattern in {"*", "**", "/**", "/**/*"}:
        return "covers"
    if pattern.endswith("/**"):
        pattern = pattern[:-2]
    if any(character in pattern for character in "*?[]!\\"):
        return "uncertain"
    rooted = pattern.startswith("/") or "/" in pattern.rstrip("/")
    target = pattern.strip("/")
    if not target:
        return "uncertain"
    if module == target or module.startswith(target + "/"):
        return "covers"
    if target.startswith(module + "/"):
        return "uncertain"
    # The existing parser drops leading slashes. A single-component rule
    # cannot prove ownership of a nested module, but may override part of it.
    return "disjoint" if rooted else "uncertain"


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Invalid coverage counter")
    return value


def _demo_fixture() -> dict[str, object]:
    payload = json.loads(files("lasthuman").joinpath("data/org_dashboard_demo.json").read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid demo fixture")
    return payload


def demo_repositories(dashboard_url: str) -> tuple[RepositoryView, ...]:
    raw_repositories = _demo_fixture().get("repositories")
    if not isinstance(raw_repositories, list) or len(raw_repositories) != 3:
        raise ValueError("Invalid demo repositories")
    repositories: list[RepositoryView] = []
    for raw in raw_repositories:
        if not isinstance(raw, dict):
            raise ValueError("Invalid demo repository")
        repo_id, name, rows = raw.get("id"), raw.get("name"), raw.get("modules")
        if (
            not isinstance(repo_id, str) or not _SAMPLE_ID.fullmatch(repo_id)
            or not isinstance(name, str) or name != repo_id or not isinstance(rows, list)
        ):
            raise ValueError("Invalid demo repository")
        modules: list[ModuleView] = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("zone"), str) or not row["zone"]:
                raise ValueError("Invalid demo module")
            contact = row.get("owner")
            if not isinstance(contact, str):
                raise ValueError("Invalid demo contact")
            if row.get("gated") is None:
                if row.get("attested") is not None or row.get("answerers") is not None:
                    raise ValueError("Invalid unknown demo module")
                module = ModuleView(
                    row["zone"], None, None, None, contacts=(Contact(contact),),
                    contact_status="", status="Collection delayed",
                )
            else:
                gated, attested, answerers = (_count(row.get(key)) for key in ("gated", "attested", "answerers"))
                if attested > gated or bool(attested) != bool(answerers):
                    raise ValueError("Invalid demo counters")
                module = ModuleView(
                    row["zone"], gated, attested, answerers, None if gated < MIN_SAMPLE else attested / gated,
                    (Contact(contact),), "", "Sample too small" if gated < MIN_SAMPLE else "Available",
                )
            modules.append(module)
        repositories.append(RepositoryView(repo_id, name, dashboard_url, tuple(modules)))
    if len({repo.id for repo in repositories}) != 3 or sum(len(repo.modules) for repo in repositories) != 5:
        raise ValueError("Invalid demo identities")
    if summarize(repositories) != Summary(zero=1, one=1, many=2):
        raise ValueError("Invalid demo distribution")
    return tuple(repositories)


def repository_demo(settings: Settings, *, as_of: datetime | None = None) -> dict[str, object]:
    require_demo(settings)
    raw = _demo_fixture().get("repository_dashboard")
    if not isinstance(raw, dict) or not isinstance(raw.get("zones"), list):
        raise ValueError("Invalid repository demo")
    zones: list[dict[str, object]] = []
    for row in raw["zones"]:
        if not isinstance(row, dict):
            raise ValueError("Invalid repository demo row")
        gated, attested = _count(row.get("gated")), _count(row.get("attested"))
        if attested > gated:
            raise ValueError("Invalid repository demo counters")
        zones.append({
            **row, "prs": [], "owner": "", "rate": None if gated < MIN_SAMPLE else attested / gated,
            "low_sample": 0 < gated < MIN_SAMPLE,
            "sample_state": "no_data" if not gated else "small_sample" if gated < MIN_SAMPLE else "measured",
        })
    gated = _count(raw.get("gated_total"))
    attested = _count(raw.get("attested_total"))
    return {
        **raw, "zones": zones, "actions": [], "repo": settings.repository, "source": "demo",
        "generated_at": (as_of or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_days": 30, "min_sample": MIN_SAMPLE, "demo_seeded": False,
        "attested_rate": None if gated < MIN_SAMPLE else attested / gated,
        "zone_count": len(zones),
        "zero_answerer_zones": sum(1 for row in zones if row["gated"] and row["answerers"] == 0),
    }
