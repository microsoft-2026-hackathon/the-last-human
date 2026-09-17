"""Pure presentation renderer for PR cards and supplemental Check Runs."""

from __future__ import annotations

import html
import hashlib
import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import quote, urlsplit

from ..config import glob_to_regex
from ..diff import parse_anchor
from ..risk import is_test_path
from .presentation_copy import (
    PresentationCopyCatalog,
    PresentationLocale,
    catalog_for_locale,
    format_copy,
)
from .snapshot import Snapshot

PresentationPhase = Literal[
    "preparing",
    "awaiting_author",
    "verifying",
    "verified",
    "neutral",
    "closed",
    "superseded",
    "error",
]
CheckStatus = Literal["in_progress", "completed"]
CheckConclusion = Literal["success", "neutral", "cancelled", "action_required"]
_ReasonKind = Literal[
    "critical",
    "pattern",
    "lines",
    "tests",
    "compile",
    "threshold",
    "other",
]

_CHECK_MAP: dict[PresentationPhase, tuple[CheckStatus, CheckConclusion | None]] = {
    "preparing": ("in_progress", None),
    "awaiting_author": ("in_progress", None),
    "verifying": ("in_progress", None),
    "verified": ("completed", "success"),
    "neutral": ("completed", "neutral"),
    "closed": ("completed", "cancelled"),
    "superseded": ("completed", "cancelled"),
    "error": ("completed", "action_required"),
}

_STAGE_KEYS = (
    "stage_selected",
    "stage_questions",
    "stage_explanation",
    "stage_verification",
)
_STAGE_MAP: dict[PresentationPhase, tuple[str, str, str, str]] = {
    "preparing": ("state_complete", "state_current", "state_pending", "state_pending"),
    "awaiting_author": (
        "state_complete",
        "state_complete",
        "state_current",
        "state_pending",
    ),
    "verifying": (
        "state_complete",
        "state_complete",
        "state_complete",
        "state_current",
    ),
    "verified": (
        "state_complete",
        "state_complete",
        "state_complete",
        "state_complete",
    ),
    "neutral": (
        "state_below_threshold",
        "state_not_required",
        "state_not_required",
        "state_not_required",
    ),
    "closed": ("state_closed", "state_closed", "state_closed", "state_closed"),
    "superseded": (
        "state_superseded",
        "state_superseded",
        "state_superseded",
        "state_superseded",
    ),
    "error": (
        "state_attention",
        "state_attention",
        "state_attention",
        "state_attention",
    ),
}

_CRITICAL_REASON_RE = re.compile(r"^중요 경로 `(?P<pattern>.*)` 변경")
_PATTERN_REASON_RE = re.compile(r"^위험 패턴 `(?P<pattern>.*)` ")
_LINE_REASON_RE = re.compile(r"^변경 \d+줄 ")
_COMPILE_FAILURE_REASON_RE = re.compile(r"^패턴 `(?P<pattern>.*)` 컴파일 실패")

_MARKER_RESERVE = 128
_MIN_CONFIGURED_BUDGET = 1000
_SHORT_TEXT_LIMIT = 220
_LONG_TEXT_LIMIT = 520
_PATH_PREFIX_RE = re.compile(r"^/repos/[1-9][0-9]*$")


class PresentationSettings(Protocol):
    repository: str
    base_url: str
    public_base_url: str
    path_prefix: str
    presentation_name: str
    presentation_locale: PresentationLocale
    presentation_max_chars: int
    presentation_reason_limit: int
    presentation_detail_limit: int
    presentation_paths_per_group: int


@dataclass(frozen=True)
class PresentationView:
    snapshot: Snapshot
    phase: PresentationPhase
    question_anchors: tuple[str, ...] = ()
    receipt_id: str | None = None


@dataclass(frozen=True)
class RenderedPresentation:
    body: str
    title: str
    summary: str
    details_url: str
    check_status: CheckStatus
    check_conclusion: CheckConclusion | None


@dataclass(frozen=True)
class _ReasonGroup:
    reason: str
    kind: _ReasonKind
    pattern: str | None
    matched_paths: tuple[str, ...] | None


@dataclass(frozen=True)
class _RenderCaps:
    reason_limit: int
    detail_limit: int
    paths_per_group: int
    text_limit: int
    include_phase_summary: bool
    include_repo: bool
    locale: PresentationLocale


@cache
def _renderer_revision() -> str:
    source = Path(__file__)
    return hashlib.sha256(
        source.read_bytes() + source.with_name("presentation_copy.py").read_bytes()
    ).hexdigest()


def presentation_revision(settings: PresentationSettings) -> str:
    payload = {
        "renderer": _renderer_revision(),
        "repository": settings.repository,
        "base_url": settings.base_url,
        "path_prefix": settings.path_prefix,
        "name": settings.presentation_name,
        "locale": settings.presentation_locale,
        "max_chars": settings.presentation_max_chars,
        "reason_limit": settings.presentation_reason_limit,
        "detail_limit": settings.presentation_detail_limit,
        "paths_per_group": settings.presentation_paths_per_group,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def render_presentation(
    view: PresentationView,
    settings: PresentationSettings,
) -> RenderedPresentation:
    _validate_presentation_settings(settings)
    catalog = catalog_for_locale(settings.presentation_locale)
    phase_copy = catalog.phase[view.phase]
    status, conclusion = _CHECK_MAP[view.phase]
    details_url = _action_url(settings, view)
    content_budget = settings.presentation_max_chars - _MARKER_RESERVE

    body = ""
    for caps in _render_caps(settings):
        candidate = _body(view, settings, caps, details_url)
        if len(candidate) <= content_budget:
            body = candidate
            break
    if not body:
        raise ValueError("configured presentation budget cannot contain the required card")

    title = f"{_plain_text(settings.presentation_name, 100)} · {phase_copy.label}"
    return RenderedPresentation(
        body=body,
        title=title,
        summary=body,
        details_url=details_url,
        check_status=status,
        check_conclusion=conclusion,
    )


def _validate_presentation_settings(settings: PresentationSettings) -> None:
    if settings.presentation_max_chars < _MIN_CONFIGURED_BUDGET:
        raise ValueError("presentation_max_chars must be at least 1000")
    for name, value in (
        ("presentation_reason_limit", settings.presentation_reason_limit),
        ("presentation_detail_limit", settings.presentation_detail_limit),
        ("presentation_paths_per_group", settings.presentation_paths_per_group),
    ):
        if value < 1:
            raise ValueError(f"{name} must be positive")


def _render_caps(settings: PresentationSettings) -> tuple[_RenderCaps, ...]:
    locale = settings.presentation_locale
    reasons = settings.presentation_reason_limit
    details = settings.presentation_detail_limit
    paths = settings.presentation_paths_per_group
    candidates = (
        _RenderCaps(reasons, details, paths, _SHORT_TEXT_LIMIT, True, True, locale),
        _RenderCaps(reasons, max(1, details // 2), paths, 160, True, True, locale),
        _RenderCaps(reasons, 1, min(paths, 1), 120, True, True, locale),
        _RenderCaps(1, 1, 0, 80, True, True, locale),
        _RenderCaps(1, 1, 0, 48, False, False, locale),
        _RenderCaps(0, 1, 0, 24, False, False, locale),
        _RenderCaps(0, 0, 0, 24, False, False, locale),
    )
    return tuple(dict.fromkeys(candidates))


def _body(
    view: PresentationView,
    settings: PresentationSettings,
    caps: _RenderCaps,
    action_url: str,
) -> str:
    catalog = catalog_for_locale(caps.locale)
    snapshot = view.snapshot
    phase_copy = catalog.phase[view.phase]
    question_count, region_count, question_file_count = _question_counts(view)
    sections = [
        format_copy(
            catalog,
            "page_heading",
            name=_safe_inline(settings.presentation_name, caps.text_limit),
            phase_label=_safe_inline(phase_copy.label, caps.text_limit),
        ),
    ]
    if caps.include_phase_summary:
        sections.append(
            format_copy(
                catalog,
                "phase_block",
                heading=_safe_inline(phase_copy.heading, caps.text_limit),
                summary=_safe_inline(phase_copy.summary, caps.text_limit),
            )
        )
    sections.extend(
        (
            _stages(view.phase, catalog),
            format_copy(
                catalog,
                "cta_line",
                label=_safe_inline(phase_copy.cta, caps.text_limit),
                url=action_url,
            ),
        )
    )
    if caps.include_repo:
        sections.append(
            format_copy(
                catalog,
                "repo_pr_line",
                repo=_safe_inline(snapshot.repo, caps.text_limit),
                pr=snapshot.pr,
            )
        )
    sections.extend(
        (
            format_copy(
                catalog,
                "facts_line",
                head=_safe_code(_short_head(snapshot.head_sha), caps.text_limit),
                files=len(snapshot.diff.files),
                questions=question_count,
                regions=region_count,
                question_files=question_file_count,
            ),
            format_copy(
                catalog,
                "files_link",
                count=len(snapshot.diff.files),
                url=_github_files_url(snapshot),
            ),
        )
    )
    if caps.reason_limit:
        sections.extend((format_copy(catalog, "reason_heading"), _risk_summary(snapshot, caps)))
    sections.append(_risk_details(snapshot, caps))
    return "\n\n".join(section for section in sections if section)


def _stages(phase: PresentationPhase, catalog: PresentationCopyCatalog) -> str:
    lines = [format_copy(catalog, "stage_heading")]
    for index, (label_key, state_key) in enumerate(
        zip(_STAGE_KEYS, _STAGE_MAP[phase], strict=True),
        start=1,
    ):
        lines.append(
            format_copy(
                catalog,
                "stage_line",
                index=index,
                label=format_copy(catalog, label_key),
                state=format_copy(catalog, state_key),
            )
        )
    return "\n".join(lines)


def _question_counts(view: PresentationView) -> tuple[int, int, int]:
    unique_anchors = tuple(dict.fromkeys(view.question_anchors))
    changed_paths = {item.file for item in view.snapshot.diff.files}
    referenced_regions: set[str] = set()
    referenced_files: set[str] = set()
    for anchor in unique_anchors:
        parsed = parse_anchor(anchor)
        if parsed is not None and parsed[0] in changed_paths:
            referenced_regions.add(anchor)
            referenced_files.add(parsed[0])
    return len(view.question_anchors), len(referenced_regions), len(referenced_files)


def reason_lines(snapshot: Snapshot, locale: PresentationLocale) -> tuple[str, ...]:
    """면담 화면의 "왜 게이트됐나" 목록. 카드의 근거 요약과 같은 문구를, 불릿 없이 한 줄씩.

    risk.reasons 원문은 채점기 언어(한국어)로 남고, 사람에게 보이는 줄은 locale 을 따른다.
    HTML 이스케이프가 끝난 문자열이므로 템플릿에서 그대로 출력한다.
    """
    catalog = catalog_for_locale(locale)
    caps = _RenderCaps(8, 8, 3, _SHORT_TEXT_LIMIT, True, True, locale)
    return tuple(
        _summary_reason_line(catalog, group, caps).removeprefix("- ")
        for group in _reason_groups(snapshot)[: caps.reason_limit]
    )


def _risk_summary(snapshot: Snapshot, caps: _RenderCaps) -> str:
    catalog = catalog_for_locale(caps.locale)
    groups = _reason_groups(snapshot)
    selected = groups[: caps.reason_limit]
    lines = [_summary_reason_line(catalog, group, caps) for group in selected]
    omitted = len(groups) - len(selected)
    if omitted > 0:
        lines.append(format_copy(catalog, "summary_omitted", count=omitted))
    if not lines:
        lines.append(format_copy(catalog, "no_summary"))
    return "\n".join(lines)


def _risk_details(snapshot: Snapshot, caps: _RenderCaps) -> str:
    catalog = catalog_for_locale(caps.locale)
    groups = _reason_groups(snapshot)
    selected = groups[: caps.detail_limit]
    lines = [
        format_copy(
            catalog,
            "score_line",
            score=snapshot.risk.score,
            threshold=snapshot.config.threshold,
        ),
    ]
    if caps.include_phase_summary:
        lines.append(format_copy(catalog, "evidence_note"))
    lines.extend(_detail_reason_line(catalog, group, caps) for group in selected)
    if not groups:
        lines.append(format_copy(catalog, "no_evidence"))
    omitted = len(groups) - len(selected)
    if omitted > 0:
        lines.append(format_copy(catalog, "detail_omitted", count=omitted))
    return _details(format_copy(catalog, "details_summary"), "\n".join(lines))


def _summary_reason_line(
    catalog: PresentationCopyCatalog,
    group: _ReasonGroup,
    caps: _RenderCaps,
) -> str:
    pattern = ""
    if group.pattern is not None:
        pattern = _safe_code(_without_weight_fragments(group.pattern), caps.text_limit)
    label = format_copy(catalog, f"reason_{group.kind}", pattern=pattern)
    return _matched_reason_line(catalog, label, group.matched_paths, caps, "reason_group")


def _detail_reason_line(
    catalog: PresentationCopyCatalog,
    group: _ReasonGroup,
    caps: _RenderCaps,
) -> str:
    reason = _safe_inline(group.reason, min(_LONG_TEXT_LIMIT, caps.text_limit * 2))
    matches = _matched_paths_suffix(catalog, group.matched_paths, caps, "detail_matches")
    return format_copy(catalog, "detail_reason", reason=reason, matches=matches)


def _matched_reason_line(
    catalog: PresentationCopyCatalog,
    label: str,
    paths: tuple[str, ...] | None,
    caps: _RenderCaps,
    key: str,
) -> str:
    if paths is None:
        return format_copy(catalog, f"{key}_no_count", label=label)
    return format_copy(
        catalog,
        key,
        label=label,
        count=len(paths),
        paths=_path_suffix(catalog, paths, caps),
    )


def _matched_paths_suffix(
    catalog: PresentationCopyCatalog,
    paths: tuple[str, ...] | None,
    caps: _RenderCaps,
    key: str,
) -> str:
    if paths is None:
        return ""
    return format_copy(
        catalog,
        key,
        count=len(paths),
        paths=_path_suffix(catalog, paths, caps),
    )


def _path_suffix(
    catalog: PresentationCopyCatalog,
    paths: tuple[str, ...],
    caps: _RenderCaps,
) -> str:
    if not paths:
        return ""
    examples = paths[: caps.paths_per_group]
    if not examples:
        return format_copy(catalog, "paths_hidden", count=len(paths))
    examples_text = ", ".join(_safe_code(path, caps.text_limit) for path in examples)
    omitted = len(paths) - len(examples)
    if omitted > 0:
        examples_text += format_copy(catalog, "path_omitted", count=omitted)
    return format_copy(catalog, "path_examples", paths=examples_text)


def _reason_groups(snapshot: Snapshot) -> tuple[_ReasonGroup, ...]:
    groups: list[_ReasonGroup] = []
    for reason in snapshot.risk.reasons:
        kind, pattern = _reason_kind(reason)
        groups.append(
            _ReasonGroup(
                reason=reason,
                kind=kind,
                pattern=pattern,
                matched_paths=_matched_paths(snapshot, reason),
            )
        )
    return tuple(groups)


def _reason_kind(reason: str) -> tuple[_ReasonKind, str | None]:
    critical = _CRITICAL_REASON_RE.match(reason)
    if critical is not None:
        return "critical", critical.group("pattern")
    pattern_reason = _PATTERN_REASON_RE.match(reason)
    if pattern_reason is not None:
        return "pattern", pattern_reason.group("pattern")
    if _LINE_REASON_RE.match(reason):
        return "lines", None
    if reason.startswith("테스트 삭제/축소 "):
        return "tests", None
    compile_failure = _COMPILE_FAILURE_REASON_RE.match(reason)
    if compile_failure is not None:
        return "compile", compile_failure.group("pattern")
    if reason.startswith("임계값 "):
        return "threshold", None
    return "other", None


def _matched_paths(snapshot: Snapshot, reason: str) -> tuple[str, ...] | None:
    if _LINE_REASON_RE.match(reason):
        return tuple(item.file for item in snapshot.diff.files)

    critical = _CRITICAL_REASON_RE.match(reason)
    if critical is not None:
        return _paths_matching_critical_pattern(snapshot, critical.group("pattern"))

    pattern_reason = _PATTERN_REASON_RE.match(reason)
    if pattern_reason is not None:
        return _paths_matching_risk_pattern(snapshot, pattern_reason.group("pattern"))

    if reason.startswith("테스트 삭제/축소 "):
        return tuple(
            item.file
            for item in snapshot.diff.files
            if is_test_path(item.file) and item.deletions > 0
        )

    if _COMPILE_FAILURE_REASON_RE.match(reason):
        return ()

    if reason.startswith("임계값 "):
        return tuple(item.file for item in snapshot.diff.files)

    return None


def _paths_matching_critical_pattern(snapshot: Snapshot, pattern: str) -> tuple[str, ...]:
    configured = {item[0] for item in snapshot.config.critical_paths}
    if pattern not in configured:
        return ()
    rx = glob_to_regex(pattern)
    return tuple(item.file for item in snapshot.diff.files if rx.match(item.file))


def _paths_matching_risk_pattern(snapshot: Snapshot, pattern: str) -> tuple[str, ...]:
    configured = {item[0] for item in snapshot.config.patterns}
    if pattern not in configured:
        return ()
    try:
        rx = re.compile(pattern)
    except re.error:
        return ()
    matched: list[str] = []
    seen: set[str] = set()
    for hunk in snapshot.diff.hunks:
        if hunk.file in seen:
            continue
        if rx.search("\n".join(hunk.added)):
            matched.append(hunk.file)
            seen.add(hunk.file)
    return tuple(matched)


def _details(summary: str, body: str) -> str:
    return f"<details>\n<summary>{summary}</summary>\n\n{body}\n\n</details>"


def _action_url(settings: PresentationSettings, view: PresentationView) -> str:
    base_url = _validated_public_base_url(settings)
    if view.phase == "verified" and view.receipt_id is not None:
        return (
            f"{base_url}/receipts/"
            f"{quote(view.receipt_id, safe='')}"
        )
    return f"{base_url}/prs/{quote(str(view.snapshot.pr), safe='')}"


def _validated_public_base_url(settings: PresentationSettings) -> str:
    base = settings.base_url.rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url must be an HTTP origin")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("base_url must be an origin")
    prefix = settings.path_prefix
    if prefix and not _PATH_PREFIX_RE.fullmatch(prefix):
        raise ValueError("path_prefix must be empty or /repos/<repository_id>")
    if settings.public_base_url.rstrip("/") != base + prefix:
        raise ValueError("public_base_url must match base_url plus path_prefix")
    return base + prefix


def _github_files_url(snapshot: Snapshot) -> str:
    owner, repo = snapshot.repo.split("/", 1)
    return (
        f"https://github.com/{quote(owner, safe='')}/{quote(repo, safe='')}"
        f"/pull/{snapshot.pr}/files"
    )


def _short_head(head_sha: str) -> str:
    return head_sha[:12]


def _without_weight_fragments(value: str) -> str:
    return re.sub(r"\+\s*\d+", "", value)


def _plain_text(value: str, limit: int) -> str:
    normalized = value.replace("\r", " ").replace("\n", " ").strip()
    normalized = re.sub(r"@(?=[A-Za-z0-9_-])", "@\u200b", normalized)
    normalized = normalized.replace("://", ":\u200b//")
    normalized = re.sub(r"(?i)\bwww\.", "www.\u200b", normalized)
    if len(normalized) > limit:
        return f"{normalized[:limit]}…"
    return normalized


def _safe_code(value: str, limit: int) -> str:
    return f"<code>{_safe_inline(value, limit)}</code>"


def _safe_inline(value: str, limit: int) -> str:
    normalized = value.replace("\r", "\\r").replace("\n", "\\n")
    if len(normalized) > limit:
        normalized = f"{normalized[:limit]}…"
    neutralized = re.sub(r"@(?=[A-Za-z0-9_-])", "@\u200b", normalized)
    neutralized = re.sub(r"(?i)\bwww\.", "www.\u200b", neutralized)
    escaped = html.escape(neutralized, quote=False)
    return escaped.translate(
        str.maketrans(
            {
                "\\": "&#92;",
                "`": "&#96;",
                "*": "&#42;",
                "_": "&#95;",
                "{": "&#123;",
                "}": "&#125;",
                "[": "&#91;",
                "]": "&#93;",
                "#": "&#35;",
                "-": "&#45;",
                "!": "&#33;",
                "|": "&#124;",
                ":": "&#58;",
            }
        )
    )
