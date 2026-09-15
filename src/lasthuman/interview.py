"""질문 생성과 판정 — 제품의 심장.

방향이 반대라는 점이 이 파일의 전부다. 다른 AI 리뷰 제품은 모델이 사람에게
말하지만, 여기서는 **사람이 모델에게 답한다**.

바는 코드의 위험도에 고정한다. 사람에 따라 질문을 쉽게 내지 않는다.
다르게 하는 것은 보류됐을 때의 지원 수준뿐이다.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Sequence

from .models import Answer, Hunk, Question, RiskResult

#: GitHub Models. 워크플로에서 `models: read` 권한만 있으면 GITHUB_TOKEN으로 호출된다.
DEFAULT_ENDPOINT = "https://models.github.ai/inference/chat/completions"
DEFAULT_MODEL = "openai/gpt-4o-mini"

QUESTION_PROMPT = """당신은 코드 리뷰 게이트입니다. 아래 변경을 머지하려는 개발자가
이 코드를 실제로 이해했는지 확인하는 질문 {n}개를 만드십시오.

규칙
- 반드시 아래 hunk 안에서만 질문한다. 일반 지식 질문 금지
- 각 질문은 정확히 하나의 anchor(file:Lnnn)를 가리킨다
- 다음 세 유형 중에서만 고른다
  claim       : PR 설명이 주장하는 동작이 코드 어디에 있는지 짚게 한다
  consequence : 특정 입력이나 상황에서 무슨 일이 나는지 묻는다
  rationale   : 취하지 않은 대안을 제시하고 왜 이 방식인지 묻는다
- 사소한 것(변수명, 포매팅, 스타일)은 묻지 않는다
- 답변자가 코드를 열어야만 답할 수 있어야 한다

PR 제목: {title}
PR 본문: {body}
위험 사유: {reasons}
변경 내용:
{hunks}

JSON 배열만 출력. 다른 텍스트 금지.
[{{"type":"claim","anchor":"src/x.py:L88","text":"...",
  "expectedEvidence":"이 답변에 반드시 포함되어야 하는 사실"}}]
"""

GRADE_PROMPT = """아래 질문과 개발자의 답변을 보고 통과 여부만 판정하십시오.

통과 조건
- expectedEvidence에 해당하는 사실이 답변에 있다
- 코드를 보지 않고는 쓸 수 없는 구체성이 있다

보류 조건
- 일반론만 있다
- 코드에 없는 내용을 있다고 답했다
- 모르겠다고 답했다  (정직한 답변이므로 부정적으로 서술하지 말 것)

질문: {question}
기대 근거: {expected}
해당 코드:
{hunk}
답변: {answer}

JSON만 출력.
{{"verdict":"pass"|"hold","hint":"보류일 때 어디를 보면 되는지 한 문장"}}
"""


class ModelError(RuntimeError):
    """모델 호출 실패. 게이트는 이 경우 사람을 막지 않고 안내만 한다."""


def _format_hunks(hunks: Sequence[Hunk], limit_lines: int = 60) -> str:
    out = []
    for h in hunks:
        body = "\n".join(h.body.splitlines()[:limit_lines])
        out.append(f"--- {h.anchor} ({h.file_status})\n{body}")
    return "\n\n".join(out)


def call_model(prompt: str, *, token: str | None = None, timeout: float = 60.0) -> str:
    """GitHub Models 호출. OpenAI 호환 스키마다."""
    token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("LASTHUMAN_TOKEN")
    if not token:
        raise ModelError("토큰이 없습니다. GITHUB_TOKEN을 넘겨주세요.")

    endpoint = os.environ.get("LASTHUMAN_MODEL_ENDPOINT", DEFAULT_ENDPOINT)
    model = os.environ.get("LASTHUMAN_MODEL", DEFAULT_MODEL)
    payload = json.dumps(
        {
            "model": model,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            data = json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        raise ModelError(f"모델 응답 {err.code}: {err.read()[:300]!r}") from err
    except OSError as err:
        raise ModelError(f"모델 호출 실패: {err}") from err

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as err:
        raise ModelError(f"예상과 다른 응답 모양: {str(data)[:300]}") from err


def _extract_json(text: str) -> object:
    """모델이 코드펜스를 붙이는 경우가 잦다. 한 번은 봐준다."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    return json.loads(text)


def generate_questions(
    risk: RiskResult,
    title: str,
    body: str,
    n: int = 2,
    *,
    token: str | None = None,
    dry_run: bool = False,
) -> list[Question]:
    """위험 상위 hunk에 대해서만 묻는다. 전부 넘기면 초점이 흐려진다."""
    if dry_run:
        return _stub_questions(risk, n)

    prompt = QUESTION_PROMPT.format(
        n=n,
        title=title,
        body=(body or "")[:4000],
        reasons="; ".join(risk.reasons),
        hunks=_format_hunks(risk.top_hunks),
    )
    raw = call_model(prompt, token=token)
    try:
        parsed = _extract_json(raw)
    except json.JSONDecodeError:
        # 실패 시 1회 재시도 후 포기.
        raw = call_model(prompt + "\n\nJSON 배열만 출력하십시오.", token=token)
        parsed = _extract_json(raw)

    valid_anchors = {h.anchor for h in risk.top_hunks}
    out: list[Question] = []
    for item in parsed if isinstance(parsed, list) else []:
        anchor = str(item.get("anchor", ""))
        if anchor not in valid_anchors:
            # 모델이 앵커를 지어내면 버린다. 대조가 불가능해지기 때문이다.
            continue
        out.append(
            Question(
                type=item.get("type", "consequence"),
                anchor=anchor,
                text=str(item.get("text", "")).strip(),
                expected_evidence=str(item.get("expectedEvidence", "")).strip(),
            )
        )
    if not out:
        raise ModelError("쓸 수 있는 질문이 생성되지 않았습니다.")
    return out[:n]


def grade(
    question: Question,
    answer_text: str,
    hunk: Hunk | None,
    *,
    token: str | None = None,
    dry_run: bool = False,
) -> Answer:
    """판정 기준이 느슨하면 게이트가 무의미해지고, 지나치게 엄격하면 신뢰를 잃는다."""
    if dry_run:
        return _stub_grade(question, answer_text)

    prompt = GRADE_PROMPT.format(
        question=question.text,
        expected=question.expected_evidence,
        hunk=hunk.body if hunk else "(해당 hunk를 찾지 못했습니다)",
        answer=answer_text,
    )
    raw = call_model(prompt, token=token)
    try:
        parsed = _extract_json(raw)
    except json.JSONDecodeError as err:
        raise ModelError("판정 응답을 읽지 못했습니다.") from err

    verdict = "pass" if str(parsed.get("verdict")) == "pass" else "hold"
    return Answer(
        anchor=question.anchor,
        text=answer_text,
        verdict=verdict,
        hint=str(parsed.get("hint", "")).strip(),
    )


# --- 모델 없이 파이프라인을 돌려보기 위한 결정적 스텁 -------------------------
# 9월 4일 질문 품질 검수 전에도 워크플로 전체를 끝까지 통과시켜 볼 수 있어야 한다.


def _stub_questions(risk: RiskResult, n: int) -> list[Question]:
    types = ("claim", "consequence", "rationale")
    out = []
    for i, h in enumerate(risk.top_hunks[:n]):
        out.append(
            Question(
                type=types[i % len(types)],
                anchor=h.anchor,
                text=f"[dry-run] {h.anchor}의 변경이 어떤 상황에서 무슨 결과를 냅니까?",
                expected_evidence=f"{h.file}의 해당 hunk에 실제로 있는 동작",
            )
        )
    return out


def _stub_grade(question: Question, answer_text: str) -> Answer:
    """코드를 열지 않고는 쓸 수 없는 구체성을 아주 거칠게 흉내 낸다."""
    text = answer_text.strip()
    concrete = len(text) >= 40 and any(ch in text for ch in "()._")
    return Answer(
        anchor=question.anchor,
        text=answer_text,
        verdict="pass" if concrete else "hold",
        hint="" if concrete else f"{question.anchor} 부근을 열어 실제 동작을 확인해 보세요.",
    )
