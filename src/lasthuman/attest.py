"""인증 기록.

인증은 PR이 아니라 **커밋 하나**에 묶인다. 새 커밋이 올라오면 자동으로 무효가
되고 다시 면담해야 한다. 리뷰 승인이 새 커밋에 의해 무효화되는 기존 동작과
같은 원리라 개발자에게 따로 설명할 필요가 없다.

한계 — 답변 블록 자체는 서명되지 않는다. 공개 페이지에 비밀키를 둘 수 없기
때문이다. 대신 답변이 GitHub 코멘트로 올라오므로 **누가 냈는지는 GitHub가
인증**한다. 블록 위조 방지는 후속 과제다.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timezone

from .models import Answer, Attestation, Band

#: PR 코멘트에 인증을 숨겨 넣는 표식.
ATTEST_MARKER = "lasthuman:attest:v1"
#: 웹 UI가 만들어 사람이 붙여넣는 답변 블록의 표식.
ANSWERS_MARKER = "lasthuman:answers:v1"

_ATTEST_RE = re.compile(rf"<!--\s*{re.escape(ATTEST_MARKER)}\s+(\{{.*?\}})\s*-->", re.S)
_ANSWERS_RE = re.compile(rf"<!--\s*{re.escape(ANSWERS_MARKER)}\s+(\{{.*?\}})\s*-->", re.S)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def band_for(answers: list[Answer], human_edited_hunks: bool = False) -> Band:
    """PR 단위 참여도 밴드.

    그 변경 하나에 대한 사실이지 사람에 대한 평가가 아니다.
    **사람 단위로 합산하지 않는다.**
    """
    if not answers:
        return "agent-led"
    passed = sum(1 for a in answers if a.verdict == "pass")
    if passed == len(answers) and human_edited_hunks:
        return "human-led"
    if passed == len(answers):
        return "co-authored"
    return "agent-led"


def encode(att: Attestation) -> str:
    """인증을 PR 코멘트 본문으로 만든다. 표식은 숨고 배지는 보인다."""
    payload = asdict(att)
    payload["reasons"] = list(att.reasons)
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"<!-- {ATTEST_MARKER} {blob} -->\n{render_badge(att)}"


def decode_all(body: str) -> list[Attestation]:
    """코멘트 본문에서 인증을 전부 꺼낸다."""
    out: list[Attestation] = []
    for m in _ATTEST_RE.finditer(body or ""):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        answers = [Answer(**a) for a in data.pop("answers", [])]
        data["reasons"] = tuple(data.get("reasons", ()))
        out.append(Attestation(**data, answers=answers))
    return out


def decode_answers(body: str) -> dict | None:
    """사람이 붙여넣은 답변 블록을 읽는다."""
    m = _ANSWERS_RE.search(body or "")
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def render_badge(att: Attestation) -> str:
    role = {"author": "생성자", "reviewer": "리뷰어"}.get(att.role, att.role)
    lines = [
        f"### 이해 인증 · {att.band}",
        "",
        f"**{att.actor}** ({role})가 `{att.head_sha[:7]}`의 변경을 설명했습니다. "
        f"위험 점수 {att.score}.",
        "",
    ]
    for a in att.answers:
        mark = "✅" if a.verdict == "pass" else "⏸️"
        lines.append(f"- {mark} `{a.anchor}`")
    lines += [
        "",
        "> 이 인증은 위 커밋에만 유효합니다. 새 커밋이 올라오면 무효가 되고 다시 면담해야 합니다.",
    ]
    return "\n".join(lines)


def valid_for(att: Attestation, head_sha: str) -> bool:
    """이 한 줄이 인증을 재사용 불가능하게 만든다."""
    return att.head_sha == head_sha


def gate_decision(
    attestations: list[Attestation],
    head_sha: str,
    pr_author: str,
    required_roles: tuple[str, ...] = ("author", "reviewer"),
) -> tuple[str, str]:
    """(conclusion, summary)를 낸다.

    conclusion은 GitHub 검사 결론이다. success / failure / neutral.
    """
    live = [a for a in attestations if valid_for(a, head_sha)]
    stale = [a for a in attestations if not valid_for(a, head_sha)]

    have_author = any(a.role == "author" and a.actor == pr_author for a in live)
    have_reviewer = any(a.role == "reviewer" and a.actor != pr_author for a in live)

    missing = []
    if "author" in required_roles and not have_author:
        missing.append("생성자")
    if "reviewer" in required_roles and not have_reviewer:
        missing.append("리뷰어")

    if not missing:
        bands = ", ".join(sorted({a.band for a in live}))
        return "success", f"이해 인증 완료 · {bands}"

    if stale and not live:
        return "failure", "새 커밋으로 인증 무효 — 다시 면담이 필요합니다"
    return "failure", f"이해 인증 없음 — {', '.join(missing)}의 인증이 필요합니다"
