"""User-facing copy for pull request presentation rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

PresentationLocale = Literal["ko", "en"]


@dataclass(frozen=True)
class PhaseCopy:
    label: str
    heading: str
    summary: str
    cta: str


@dataclass(frozen=True)
class PresentationCopyCatalog:
    locale: PresentationLocale
    phase: Mapping[str, PhaseCopy]
    messages: Mapping[str, str]


def format_copy(
    catalog: PresentationCopyCatalog,
    key: str,
    **values: str | int,
) -> str:
    return catalog.messages[key].format(**values)


KO_CATALOG = PresentationCopyCatalog(
    locale="ko",
    phase={
        "preparing": PhaseCopy(
            label="준비 중",
            heading="변경 이해 확인을 준비하고 있습니다.",
            summary="위험 기준과 질문 범위를 현재 변경에 맞춰 준비하는 중입니다.",
            cta="현재 상태 보기",
        ),
        "awaiting_author": PhaseCopy(
            label="작성자 설명 대기",
            heading="작성자의 변경 이해 설명을 기다리고 있습니다.",
            summary="질문이 준비되었습니다. 현재 변경을 설명해 주세요.",
            cta="이 변경 확인하기",
        ),
        "verifying": PhaseCopy(
            label="현재 변경 인증 중",
            heading="제출된 인증을 현재 커밋과 대조하고 있습니다.",
            summary="인증이 현재 변경에 유효한지 독립적으로 확인하는 중입니다.",
            cta="인증 상태 보기",
        ),
        "verified": PhaseCopy(
            label="Human-verified",
            heading="이 변경에 대한 이해 확인을 완료했습니다.",
            summary="작성자의 설명과 코드 근거를 확인하고 인증을 현재 커밋에 연결했습니다.",
            cta="내 확인 내역",
        ),
        "neutral": PhaseCopy(
            label="확인 대상 아님",
            heading="이번 변경은 이해 확인 적용 기준에 도달하지 않았습니다.",
            summary="추가 작성자 설명 없이 현재 평가를 마쳤습니다.",
            cta="현재 평가 보기",
        ),
        "closed": PhaseCopy(
            label="종료됨",
            heading="Pull request가 종료되어 이해 확인을 멈췄습니다.",
            summary="닫힌 Pull request의 현재 평가 상태입니다.",
            cta="현재 상태 보기",
        ),
        "superseded": PhaseCopy(
            label="새 평가로 대체됨",
            heading="이 평가는 더 최신인 변경 또는 기준으로 대체되었습니다.",
            summary="최신 평가가 현재 Pull request 상태를 이어받습니다.",
            cta="최신 상태 보기",
        ),
        "error": PhaseCopy(
            label="운영 확인 필요",
            heading="확인 준비 또는 상태 갱신에 운영자 확인이 필요합니다.",
            summary="운영 과정에서 문제가 발생했습니다. 작성자 설명 결과를 뜻하지 않습니다.",
            cta="현재 상태 및 재시도 보기",
        ),
    },
    messages={
        "page_heading": "## {name} · {phase_label}",
        "phase_block": "**{heading}**\n\n{summary}",
        "phase_heading": "**{heading}**",
        "stage_heading": "**진행 단계**",
        "stage_line": "{index}. **{label}** — {state}",
        "stage_selected": "변경 선별",
        "stage_questions": "질문 준비",
        "stage_explanation": "작성자 설명",
        "stage_verification": "현재 변경 인증",
        "state_complete": "완료",
        "state_current": "진행 중",
        "state_pending": "대기",
        "state_not_required": "해당 없음",
        "state_below_threshold": "적용 기준 미달",
        "state_closed": "종료",
        "state_superseded": "대체됨",
        "state_attention": "운영 확인 필요",
        "cta_line": "[{label}]({url})",
        "repo_pr_line": "{repo} · PR #{pr}",
        "facts_line": "Head {head} · 변경 파일 {files}개 · 질문 {questions}개 · 변경 구간 {regions}곳 · 참조 파일 {question_files}개",
        "files_link": "[Files changed에서 전체 {count}개 파일 보기]({url})",
        "reason_heading": "**현재 변경의 위험 근거 요약**",
        "reason_critical": "중요 경로 {pattern} 변경",
        "reason_pattern": "위험 패턴 {pattern} 감지",
        "reason_lines": "변경 규모가 위험 기준에 포함됨",
        "reason_tests": "테스트 삭제 또는 축소 감지",
        "reason_compile": "위험 패턴 {pattern} 규칙 평가 필요",
        "reason_threshold": "현재 평가 기준 조건 적용",
        "reason_other": "추가 위험 근거",
        "reason_group": "- {label} — 일치 파일 {count}개{paths}",
        "reason_group_no_count": "- {label}",
        "path_examples": " (예: {paths})",
        "path_omitted": ", 외 {count}개",
        "paths_hidden": " (경로 예시 {count}개 생략)",
        "summary_omitted": "- 외 {count}개 요약 그룹 생략",
        "no_summary": "- 표시할 위험 근거 요약이 없습니다.",
        "details_summary": "위험 점수와 상세 적용 근거",
        "score_line": "**위험 점수 {score} / 적용 기준 {threshold}**",
        "evidence_note": "아래 문장은 점수 계산기가 기록한 원문 근거입니다.",
        "detail_reason": "- {reason}{matches}",
        "detail_matches": " — 일치 파일 {count}개{paths}",
        "detail_omitted": "외 {count}개 상세 근거는 표시 한도 때문에 생략했습니다.",
        "no_evidence": "기록된 상세 근거가 없습니다.",
    },
)

EN_CATALOG = PresentationCopyCatalog(
    locale="en",
    phase={
        "preparing": PhaseCopy(
            label="Preparing",
            heading="Preparing the change-understanding check.",
            summary="Risk policy and question scope are being prepared for this change.",
            cta="View current status",
        ),
        "awaiting_author": PhaseCopy(
            label="Awaiting author explanation",
            heading="Waiting for the author to explain this change.",
            summary="Questions are ready. Explain the current change to continue.",
            cta="Check this change",
        ),
        "verifying": PhaseCopy(
            label="Verifying current change",
            heading="Checking the submitted receipt against the current commit.",
            summary="Independent verification is confirming that the receipt applies to this change.",
            cta="View verification status",
        ),
        "verified": PhaseCopy(
            label="Human-verified",
            heading="Understanding verification for this change is complete.",
            summary="The explanation and code evidence were verified and bound to the current commit.",
            cta="View my verification",
        ),
        "neutral": PhaseCopy(
            label="Check not required",
            heading="This change did not reach the understanding-check threshold.",
            summary="The current evaluation completed without an author explanation.",
            cta="View current evaluation",
        ),
        "closed": PhaseCopy(
            label="Closed",
            heading="The pull request is closed, so the understanding check has stopped.",
            summary="This is the current evaluation state for the closed pull request.",
            cta="View current status",
        ),
        "superseded": PhaseCopy(
            label="Superseded",
            heading="A newer change or policy evaluation superseded this one.",
            summary="The latest evaluation now owns the pull request state.",
            cta="View latest status",
        ),
        "error": PhaseCopy(
            label="Operator attention required",
            heading="Check preparation or state update needs operator attention.",
            summary="An operational issue occurred. This is not an author explanation result.",
            cta="View status and retry",
        ),
    },
    messages={
        "page_heading": "## {name} · {phase_label}",
        "phase_block": "**{heading}**\n\n{summary}",
        "phase_heading": "**{heading}**",
        "stage_heading": "**Stages**",
        "stage_line": "{index}. **{label}** — {state}",
        "stage_selected": "Selected",
        "stage_questions": "Questions",
        "stage_explanation": "Explanation",
        "stage_verification": "Verification",
        "state_complete": "Complete",
        "state_current": "In progress",
        "state_pending": "Waiting",
        "state_not_required": "Not required",
        "state_below_threshold": "Below threshold",
        "state_closed": "Closed",
        "state_superseded": "Superseded",
        "state_attention": "Operator attention required",
        "cta_line": "[{label}]({url})",
        "repo_pr_line": "{repo} · PR #{pr}",
        "facts_line": (
            "Head {head} · Files {files} · Questions {questions} · "
            "Regions {regions} · Referenced files {question_files}"
        ),
        "files_link": "[View all {count} file(s) in Files changed]({url})",
        "reason_heading": "**Risk evidence summary**",
        "reason_critical": "Critical path {pattern} changed",
        "reason_pattern": "Risk pattern {pattern} detected",
        "reason_lines": "Change size contributed to risk",
        "reason_tests": "Test removal or reduction detected",
        "reason_compile": "Risk-pattern rule {pattern} needs evaluation",
        "reason_threshold": "Current evaluation policy applied",
        "reason_other": "Additional risk evidence",
        "reason_group": "- {label} — {count} matched file(s){paths}",
        "reason_group_no_count": "- {label}",
        "path_examples": " (for example: {paths})",
        "path_omitted": ", plus {count} more",
        "paths_hidden": " ({count} path example(s) omitted)",
        "summary_omitted": "- {count} more summary group(s) omitted",
        "no_summary": "- No risk summary is available.",
        "details_summary": "Risk score and detailed rule evidence",
        "score_line": "**Risk score {score} / threshold {threshold}**",
        "evidence_note": "The following text is the scorer's original evidence.",
        "detail_reason": "- {reason}{matches}",
        "detail_matches": " — {count} matched file(s){paths}",
        "detail_omitted": "{count} detailed evidence item(s) omitted by the display limit.",
        "no_evidence": "No detailed evidence was recorded.",
    },
)

PRESENTATION_COPY_CATALOGS: Mapping[PresentationLocale, PresentationCopyCatalog] = {
    "ko": KO_CATALOG,
    "en": EN_CATALOG,
}


def catalog_for_locale(locale: PresentationLocale) -> PresentationCopyCatalog:
    return PRESENTATION_COPY_CATALOGS[locale]
