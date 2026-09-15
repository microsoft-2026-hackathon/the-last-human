"""위험 점수 — 순수 함수.

같은 입력이면 항상 같은 출력이 나와야 하고 모델을 부르지 않는다.
재현되지 않으면 게이트로서 신뢰를 잃는다.

트리거는 **AI 작성 여부가 아니라 위험도**다. AI 탐지에 의존하지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .config import Config, glob_to_regex
from .models import DiffResult, Hunk, PrMeta, RiskResult

#: 테스트로 간주하는 경로. testsRemoved 신호가 쓴다.
TEST_PATH_RE = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]+$|_test\.[a-z]+$")

_TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


def is_test_path(path: str) -> bool:
    return TEST_PATH_RE.search(path) is not None


def _added_text(hunks: Iterable[Hunk]) -> str:
    """패턴 검사는 추가된 줄에만 건다. 지워진 코드를 근거로 막으면 납득되지 않는다."""
    return "\n".join(line for h in hunks for line in h.added)


def score(
    diff: DiffResult,
    config: Config,
    meta: PrMeta | None = None,
    zero_coverage_paths: Iterable[str] = (),
) -> RiskResult:
    """위험 점수와 **사유**를 함께 낸다.

    사유 없이 점수만 던지면 개발자가 반발한다. reasons는 비워두지 않는다.
    """
    meta = meta or PrMeta()
    total = 0
    reasons: list[str] = []

    changed = [f.file for f in diff.files]

    # 1. 중요 경로 — 패턴 하나당 한 번만 더한다.
    path_weight: dict[str, int] = {}
    for pattern, weight in config.critical_paths:
        rx = glob_to_regex(pattern)
        hit = [p for p in changed if rx.match(p)]
        if hit:
            total += weight
            reasons.append(f"중요 경로 `{pattern}` 변경 (+{weight}) — {', '.join(hit[:3])}")
            for p in hit:
                path_weight[p] = max(path_weight.get(p, 0), weight)

    # 2. 변경량
    lines = diff.total_additions + diff.total_deletions
    if lines:
        raw = (lines / 100) * config.lines_changed.per100
        capped = int(min(raw, config.lines_changed.max))
        if capped:
            total += capped
            reasons.append(f"변경 {lines}줄 (+{capped}, 상한 {config.lines_changed.max})")

    # 3. 위험 패턴 — 추가된 줄에만 건다.
    added = _added_text(diff.hunks)
    pattern_weight: dict[str, int] = {}
    for pattern, weight in config.patterns:
        try:
            rx = re.compile(pattern)
        except re.error:
            reasons.append(f"패턴 `{pattern}` 컴파일 실패 — 무시함")
            continue
        if rx.search(added):
            total += weight
            reasons.append(f"위험 패턴 `{pattern}` (+{weight})")
            pattern_weight[pattern] = weight

    # 4. 테스트 삭제
    removed_tests = [f.file for f in diff.files if is_test_path(f.file) and f.deletions > 0]
    if removed_tests:
        total += config.tests_removed
        reasons.append(
            f"테스트 삭제/축소 (+{config.tests_removed}) — {', '.join(removed_tests[:3])}"
        )

    # 5. 에이전트 흔적 — 가점일 뿐 트리거의 근거가 아니다.
    if meta.agent_hint:
        total += config.agent_hint
        reasons.append(f"에이전트 흔적 (+{config.agent_hint})")

    # 6. 외부 기여자 — OSS 모드에서만.
    if config.mode == "oss":
        external = meta.author_association.upper() not in _TRUSTED_ASSOCIATIONS
        # 계정이 하나뿐인 시연 환경에서는 라벨로 외부 기여를 흉내 낸다.
        if config.external_contributor_label in meta.labels:
            external = True
        if external:
            total += config.external_contributor
            reasons.append(f"외부 기여자 (+{config.external_contributor})")

    # 7. 이해 보유자 0명 모듈 — 무조건 발동시킨다.
    zero = [p for p in changed if any(p.startswith(z) for z in zero_coverage_paths)]
    if zero:
        total += config.zero_coverage
        reasons.append(f"이해 보유자 0명 모듈 (+{config.zero_coverage}) — {', '.join(zero[:3])}")

    triggered = total >= config.threshold
    if not triggered:
        reasons.append(f"임계값 {config.threshold} 미만 — 게이트 발동 안 함")

    return RiskResult(
        score=total,
        triggered=triggered,
        reasons=tuple(reasons),
        top_hunks=_rank(diff.hunks, path_weight, pattern_weight, config.max_hunks),
    )


def _rank(
    hunks: tuple[Hunk, ...],
    path_weight: dict[str, int],
    pattern_weight: dict[str, int],
    limit: int,
) -> tuple[Hunk, ...]:
    """질문이 향할 hunk를 고른다.

    대용량 PR에서 hunk가 수백 개 나온다. 전부 넘기면 컨텍스트 한도를 넘고
    질문의 초점이 흐려지므로 위험한 순서로 상한만큼만 남긴다.
    """

    def weight(h: Hunk) -> tuple[int, int]:
        w = path_weight.get(h.file, 0)
        text = "\n".join(h.added)
        for pattern, pw in pattern_weight.items():
            if re.search(pattern, text):
                w += pw
        # 동점이면 변경량이 큰 쪽을 앞세운다.
        return (w, len(h.added) + len(h.removed))

    return tuple(sorted(hunks, key=weight, reverse=True)[:limit])
