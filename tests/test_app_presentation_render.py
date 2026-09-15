from __future__ import annotations

from dataclasses import dataclass, replace
from typing import get_args
from urllib.parse import quote

import pytest

from lasthuman.config import Config
from lasthuman.models import DiffResult, FileChange, Hunk, RiskResult
from lasthuman.server.github import _append_marker_with_budget, _pr_card_marker
from lasthuman.server.presentation import (
    PresentationPhase,
    PresentationView,
    reason_lines,
    render_presentation,
)
from lasthuman.server.presentation_copy import catalog_for_locale, format_copy
from lasthuman.server.snapshot import Snapshot
from lasthuman.structure import StructureContext

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
POLICY_VERSION = "c" * 64
STAGE_LABELS = ("변경 선별", "질문 준비", "작성자 설명", "현재 변경 인증")


@dataclass(frozen=True)
class RenderSettings:
    repository: str = "octo-org/the-last-human"
    base_url: str = "https://stamp.example.test"
    presentation_name: str = "The Last Human"
    presentation_locale: str = "ko"
    presentation_max_chars: int = 6000
    presentation_reason_limit: int = 3
    presentation_detail_limit: int = 10
    presentation_paths_per_group: int = 2


def _path(index: int, *, auth_count: int, extra: tuple[str, ...] = ()) -> str:
    if index < len(extra):
        return extra[index]
    if index < auth_count:
        return f"app/auth/file_{index:03}.py"
    return f"app/service/file_{index:03}.py"


def _snapshot(
    *,
    file_count: int = 3,
    auth_count: int | None = None,
    repo: str = "octo-org/the-last-human",
    pr: int = 42,
    title: str = "Refresh token safety",
    reasons: tuple[str, ...] | None = None,
    extra_paths: tuple[str, ...] = (),
) -> Snapshot:
    auth_total = file_count if auth_count is None else auth_count
    files: list[FileChange] = []
    hunks: list[Hunk] = []
    for index in range(file_count):
        path = _path(index, auth_count=auth_total, extra=extra_paths)
        files.append(
            FileChange(
                file=path,
                status="modified",
                binary=False,
                additions=2,
                deletions=1,
                hunk_count=1,
            )
        )
        hunks.append(
            Hunk(
                file=path,
                new_start=index + 10,
                old_start=index + 9,
                anchor=f"{path}:L{index + 10}",
                added=("+    raise RuntimeError('stop')", "+    value = 1"),
                removed=("-    value = 0",),
                body="-    value = 0\n+    raise RuntimeError('stop')\n+    value = 1",
                file_status="modified",
            )
        )
    diff = DiffResult(hunks=tuple(hunks), files=tuple(files))
    config = Config(
        threshold=40,
        critical_paths=(("app/**", 10), ("app/auth/**", 20)),
        patterns=(("raise RuntimeError", 5),),
    )
    risk_reasons = reasons or (
        "중요 경로 `app/**` 변경 (+10) — app/auth/file_000.py, app/service/file_010.py",
        "중요 경로 `app/auth/**` 변경 (+20) — app/auth/file_000.py",
        "위험 패턴 `raise RuntimeError` (+5)",
        f"변경 {diff.total_additions + diff.total_deletions}줄 (+3, 상한 30)",
    )
    return Snapshot.create(
        repo=repo,
        repo_id=101,
        pr=pr,
        head_sha=HEAD_SHA,
        base_sha=BASE_SHA,
        author_id=202,
        author_login="author",
        title=title,
        body="Body is not rendered as presentation copy.",
        risk=RiskResult(score=55, triggered=True, reasons=risk_reasons, top_hunks=tuple(hunks[:5])),
        config=config,
        diff=diff,
        structure=StructureContext(changed_files=(), importers={}, symbols=(), sibling_files=()),
        zones=(),
        policy_version=POLICY_VERSION,
    )


def _render(
    snapshot: Snapshot | None = None,
    *,
    phase: PresentationPhase = "awaiting_author",
    settings: RenderSettings = RenderSettings(),
    anchors: tuple[str, ...] = (),
    receipt_id: str | None = None,
):
    return render_presentation(
        PresentationView(
            snapshot=snapshot or _snapshot(),
            phase=phase,
            question_anchors=anchors,
            receipt_id=receipt_id,
        ),
        settings,
    )


def _stage_lines(body: str) -> list[str]:
    return [
        line
        for line in body.splitlines()
        if any(f"**{label}**" in line for label in STAGE_LABELS)
    ]


def test_phase_contract_has_four_truthful_stages_and_exact_check_map() -> None:
    assert set(get_args(PresentationPhase)) == {
        "preparing",
        "awaiting_author",
        "verifying",
        "verified",
        "neutral",
        "closed",
        "superseded",
        "error",
    }
    expected = {
        "preparing": ("in_progress", None),
        "awaiting_author": ("in_progress", None),
        "verifying": ("in_progress", None),
        "verified": ("completed", "success"),
        "neutral": ("completed", "neutral"),
        "closed": ("completed", "cancelled"),
        "superseded": ("completed", "cancelled"),
        "error": ("completed", "action_required"),
    }
    catalog = catalog_for_locale("ko")
    for phase, (status, conclusion) in expected.items():
        rendered = _render(phase=phase)
        assert rendered.title == f"The Last Human · {catalog.phase[phase].label}"
        assert f"## The Last Human · {catalog.phase[phase].label}" in rendered.body
        assert rendered.check_status == status
        assert rendered.check_conclusion == conclusion
        stage_lines = _stage_lines(rendered.body)
        assert len(stage_lines) == 4
        assert all(label in "\n".join(stage_lines) for label in STAGE_LABELS)
        explanation_complete = "3. **작성자 설명** — 완료" in stage_lines
        assert explanation_complete is (phase in {"verifying", "verified"})
        assert sum(line.endswith("완료") for line in stage_lines) == (
            4 if phase == "verified" else {"preparing": 1, "awaiting_author": 2, "verifying": 3}.get(phase, 0)
        )
        if phase != "verified":
            assert "Human Verified" not in rendered.body
            assert "Human Verified" not in rendered.title

    verified = _render(phase="verified", receipt_id="receipt-42")
    assert "이 변경에 대한 이해 확인을 완료했습니다." in verified.body
    error = _render(phase="error")
    assert "확인 준비 또는 상태 갱신" in error.body
    assert "표시 동기화" not in error.body
    assert "답변 실패" not in error.body


def test_score_and_original_rule_evidence_are_only_in_one_details_block() -> None:
    settings = replace(RenderSettings(), presentation_detail_limit=2)
    snapshot = _snapshot()
    rendered = _render(snapshot, settings=settings)
    before, separator, details = rendered.body.partition("<details>")
    assert separator
    assert rendered.body.count("<details>") == rendered.body.count("</details>") == 1
    assert "위험 점수" not in before
    assert "적용 기준" not in before
    assert "+10" not in before
    assert "+20" not in before
    assert "snapshot_id" not in before
    assert "expected_evidence" not in before
    assert "answer_index" not in before
    summary_groups = [
        line
        for line in before.splitlines()
        if line.startswith("- ") and "요약 그룹 생략" not in line
    ]
    assert len(summary_groups) <= 3
    assert "위험 점수 55 / 적용 기준 40" in details
    assert "+10" in details
    assert "+20" in details
    assert "외 2개 상세 근거" in details
    assert sum(line.startswith("- ") for line in details.splitlines()) == 2
    assert "모든 PR" not in rendered.body
    assert "CI를 대체" not in rendered.body
    assert "개인 점수" not in rendered.body
    assert "순위" not in rendered.body


@pytest.mark.parametrize("file_count", [1, 10, 30, 200])
def test_bounded_file_summaries_use_native_diff_without_inventory(file_count: int) -> None:
    snapshot = _snapshot(file_count=file_count, auth_count=file_count)
    rendered = _render(snapshot, anchors=tuple(h.anchor for h in snapshot.diff.hunks[:12]))
    assert len(rendered.body) <= 6000 - 128
    assert f"변경 파일 {file_count}개" in rendered.body
    assert f"일치 파일 {file_count}개" in rendered.body
    assert "| 파일 |" not in rendered.body
    assert "| <code>" not in rendered.body
    assert "[Files changed에서 전체 " in rendered.body
    assert "https://github.com/octo-org/the-last-human/pull/42/files" in rendered.body
    if file_count > 2:
        assert f"외 {file_count - 2}개" in rendered.body


def test_overlapping_patterns_report_complete_matched_counts_and_omissions() -> None:
    snapshot = _snapshot(file_count=30, auth_count=7)
    rendered = _render(snapshot)
    before_details = rendered.body.split("<details>", 1)[0]
    assert "일치 파일 30개" in before_details
    assert "일치 파일 7개" in before_details
    assert "외 28개" in before_details
    assert "외 5개" in before_details


def test_actual_question_region_and_file_counts_are_visible_without_anchor_dump() -> None:
    snapshot = _snapshot(file_count=2, auth_count=2)
    anchor_one = snapshot.diff.hunks[0].anchor
    anchor_two = f"{snapshot.diff.files[0].file}:L99"
    anchors = (anchor_one, anchor_one, anchor_two, "not-changed.py:L1")
    rendered = _render(snapshot, anchors=anchors)
    assert "질문 4개" in rendered.body
    assert "변경 구간 2곳" in rendered.body
    assert "참조 파일 1개" in rendered.body
    assert anchor_one not in rendered.body
    assert snapshot.snapshot_id not in rendered.body
    assert f"Head <code>{HEAD_SHA[:12]}</code>" in rendered.body


def test_cta_and_details_url_use_actual_pr_or_quoted_receipt() -> None:
    pending = _render()
    pr_url = "https://stamp.example.test/prs/42"
    assert f"[이 변경 확인하기]({pr_url})" in pending.body
    assert pending.details_url == pr_url

    receipt_id = "receipt/id ?#42"
    verified = _render(phase="verified", receipt_id=receipt_id)
    receipt_url = f"https://stamp.example.test/receipts/{quote(receipt_id, safe='')}"
    assert f"[내 확인 내역]({receipt_url})" in verified.body
    assert verified.details_url == receipt_url


def test_check_summary_reuses_the_complete_card_rendering() -> None:
    rendered = _render(phase="verifying")
    assert rendered.summary == rendered.body
    assert all(label in rendered.summary for label in STAGE_LABELS)
    assert "<details>" in rendered.summary
    assert "위험 점수 55 / 적용 기준 40" in rendered.summary


@pytest.mark.parametrize("locale", ["ko", "en"])
def test_minimum_budget_is_bounded_before_the_adapter_adds_its_marker(
    locale: str,
) -> None:
    dangerous_path = (
        "app/auth/@team/<script>|[x](javascript:alert(1))/" + ("deep-" * 120) + ".py"
    )
    dangerous_reason = (
        "중요 경로 `app/auth/**` 변경 (+20) — @team <script>alert(1)</script> "
        "[x](javascript:alert(1)) "
        + ("detail " * 300)
    )
    reasons = tuple(dangerous_reason for _ in range(30))
    snapshot = _snapshot(
        file_count=200,
        auth_count=200,
        title=("unsafe title " * 300),
        reasons=reasons,
        extra_paths=(dangerous_path,),
    )
    settings = replace(
        RenderSettings(),
        presentation_name=("Unsafe [name](https://example.invalid) @team " * 20),
        presentation_locale=locale,
        presentation_max_chars=1000,
        presentation_reason_limit=1000,
        presentation_detail_limit=1000,
        presentation_paths_per_group=1000,
    )
    rendered = _render(snapshot, settings=settings)
    marker = _pr_card_marker(snapshot.pr)
    complete_comment = _append_marker_with_budget(
        rendered.body,
        marker,
        settings.presentation_max_chars,
    )
    assert len(rendered.body) <= settings.presentation_max_chars - 128
    assert len(complete_comment) <= settings.presentation_max_chars
    assert "<!--" not in rendered.body
    assert complete_comment.endswith("<!-- lasthuman:pr-card:42 -->")
    assert rendered.body.count("<details>") == rendered.body.count("</details>") == 1
    assert f"]({rendered.details_url})" in rendered.body


def test_untrusted_text_cannot_create_html_links_headings_or_mentions() -> None:
    dangerous_path = "app/@team/<script>/[path](https://evil.invalid)/file.py"
    dangerous_reason = (
        "중요 경로 `app/**` 변경 (+20) — @team <script>alert(1)</script> "
        "[reason](https://evil.invalid) www.evil.invalid\n## injected"
    )
    settings = replace(
        RenderSettings(),
        presentation_name="STAMP [name](https://evil.invalid) @team\n## injected",
    )
    snapshot = _snapshot(
        file_count=3,
        auth_count=3,
        reasons=(dangerous_reason,),
        extra_paths=(dangerous_path,),
    )
    rendered = _render(snapshot, settings=settings)
    assert "<script>" not in rendered.body
    assert "&lt;script&gt;" in rendered.body
    assert "[name](" not in rendered.body
    assert "[path](" not in rendered.body
    assert "[reason](" not in rendered.body
    assert "https://evil.invalid" not in rendered.body
    assert "www.evil.invalid" not in rendered.body
    assert "@team" not in rendered.body
    assert "@\u200bteam" in rendered.body
    assert "\n## injected" not in rendered.body
    assert rendered.body.count("## ") == 1
    assert "https://evil.invalid" not in rendered.title
    assert "@team" not in rendered.title


@pytest.mark.parametrize(
    ("base_url", "repo", "pr", "name"),
    [
        ("https://stamp.example.test", "octo-org/the-last-human", 42, "The Last Human"),
        ("https://verify.example.org:8443", "Example-Org/repo.name", 77, "STAMP"),
        ("http://127.0.0.1:8000", "local-owner/demo_repo", 5, "Human Check"),
    ],
)
def test_runtime_origin_repository_pr_and_name_are_not_example_specific(
    base_url: str,
    repo: str,
    pr: int,
    name: str,
) -> None:
    settings = replace(
        RenderSettings(),
        repository=repo,
        base_url=base_url,
        presentation_name=name,
    )
    snapshot = _snapshot(repo=repo, pr=pr)
    rendered = _render(snapshot, settings=settings)
    assert rendered.details_url == f"{base_url}/prs/{pr}"
    assert f"## {name} · 작성자 설명 대기" in rendered.body
    owner, repository = repo.split("/", 1)
    assert f"https://github.com/{owner}/{repository}/pull/{pr}/files" in rendered.body
    assert "hunhoon21" not in rendered.body


def test_limits_are_configurable_and_invalid_limits_are_not_silently_clamped() -> None:
    settings = replace(
        RenderSettings(),
        presentation_reason_limit=1,
        presentation_detail_limit=1,
        presentation_paths_per_group=1,
    )
    snapshot = _snapshot(file_count=30, auth_count=30)
    rendered = _render(snapshot, settings=settings)
    before, _, details = rendered.body.partition("<details>")
    assert "외 3개 요약 그룹" in before
    assert "외 29개" in before
    assert "외 3개 상세 근거" in details
    assert sum(line.startswith("- ") for line in details.splitlines()) == 1

    for field in (
        "presentation_reason_limit",
        "presentation_detail_limit",
        "presentation_paths_per_group",
    ):
        with pytest.raises(ValueError):
            _render(settings=replace(RenderSettings(), **{field: 0}))
    with pytest.raises(ValueError):
        _render(settings=replace(RenderSettings(), presentation_max_chars=999))


def test_summary_group_limit_can_be_increased_without_renderer_changes() -> None:
    settings = replace(RenderSettings(), presentation_reason_limit=4)
    rendered = _render(_snapshot(file_count=30, auth_count=30), settings=settings)
    before_details = rendered.body.split("<details>", 1)[0]
    assert sum(line.startswith("- ") for line in before_details.splitlines()) == 4
    assert "개 요약 그룹" not in before_details


def test_english_copy_seam_and_stable_repeated_rendering() -> None:
    settings = replace(RenderSettings(), presentation_locale="en", presentation_name="STAMP")
    first = _render(settings=settings)
    second = _render(settings=settings)
    assert first == second
    assert "## STAMP · Awaiting author explanation" in first.body
    assert "**Stages**" in first.body
    assert "Risk score 55 / threshold 40" in first.body
    assert "중요 경로" in first.body
    assert "Human Verified" not in first.body

    catalog = catalog_for_locale("en")
    replacement = replace(
        catalog,
        messages={**catalog.messages, "score_line": "score={score}; threshold={threshold}"},
    )
    assert format_copy(replacement, "score_line", score=12, threshold=40) == (
        "score=12; threshold=40"
    )


def test_interview_reason_lines_follow_the_locale_without_bullets_or_raw_scorer_text() -> None:
    snapshot = _snapshot(file_count=2)

    english = reason_lines(snapshot, "en")
    assert english
    assert all(not line.startswith("- ") for line in english)
    assert english[0].startswith("Critical path <code>")
    assert "matched file(s)" in english[0]
    assert not any("중요 경로" in line for line in english)

    korean = reason_lines(snapshot, "ko")
    assert korean[0].startswith("중요 경로 <code>")
    # 카드의 요약과 같은 묶음 순서를 따른다.
    assert len(korean) == len(english)
