"""질문 생성과 판정 — 제품의 심장.

방향이 반대라는 점이 이 파일의 전부다. 다른 AI 리뷰 제품은 모델이 사람에게
말하지만, 여기서는 **사람이 모델에게 답한다**.

바는 코드의 위험도에 고정한다. 사람에 따라 질문을 쉽게 내지 않는다.
다르게 하는 것은 보류됐을 때의 지원 수준뿐이다.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import random
import re
import urllib.error
import urllib.request
from collections.abc import Sequence

from . import model_auth
from .models import Answer, Hunk, Question, RiskResult
from .structure import StructureContext

# 모델 공급자는 환경변수로 갈아끼운다.
#
# GitHub Models는 2026년 9월 현재 폐지 브라운아웃 상태라(410
# github_models_retirement_brownout) 기본값으로 쓸 수 없다. 기본은
# Azure OpenAI이고, OpenAI 호환 엔드포인트면 무엇이든 붙는다.
#
#   LASTHUMAN_PROVIDER   azure | openai | none   (기본 azure)
#   LASTHUMAN_ENDPOINT   전체 URL. 지정하면 provider보다 우선한다
#   LASTHUMAN_MODEL      배포 이름 또는 모델 이름
#   LASTHUMAN_API_KEY    Azure/OpenAI 키. 없으면 LASTHUMAN_TOKEN을 Bearer로 쓴다
#   AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_VERSION
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_AZURE_API_VERSION = "2024-10-21"
OPENAI_ENDPOINT = "https://api.openai.com/v1/chat/completions"
QUESTION_TYPES = frozenset({"claim", "consequence", "rationale", "structure"})
_QUESTION_TYPE_ENUM = tuple(sorted(QUESTION_TYPES))
_QUESTION_RESPONSE_NAME = "lasthuman_question_batch_v1"
_QUESTION_KEYS = (
    "type",
    "anchor",
    "text",
    "choices",
    "answerIndex",
    "expectedEvidence",
    "evidencePath",
)
# Keep the schema within the conservative Chat Completions compatibility budget.
_SCHEMA_ENUM_LIMIT = 500
_SCHEMA_STRING_LIMIT = 15_000
_LARGE_ENUM_STRING_LIMIT = 7_500

QUESTION_PROMPT = """당신은 코드 리뷰 게이트입니다. 아래 변경을 머지하려는 개발자가
이 코드를 실제로 이해했는지 확인하는 질문 {n}개를 만드십시오.

질문은 두 축을 **모두** 덮어야 합니다.

  code 축      hunk 안에서 무슨 일이 나는가
    claim       : PR 설명이 주장하는 동작이 코드 어디에 있는지 짚게 한다
    consequence : 특정 입력이나 상황에서 무슨 일이 나는지 묻는다
    rationale   : 취하지 않은 대안을 제시하고 왜 이 방식인지 묻는다

  structure 축  이 변경이 누구에게 전파되는가
    structure   : 호출자, 임포터, 계층 경계, 실패가 번지는 범위를 묻는다
                  아래 "구조 사실"에 실제로 있는 내용만 근거로 삼는다

{n}개 중 최소 1개는 structure 여야 합니다. 구조 사실이 "없음"뿐이면 code로만 채웁니다.

모든 질문은 **객관식 4지선다**입니다.

보기 규칙 — 이걸 어기면 게이트가 무력해집니다
- 정답은 정확히 하나
- 오답 3개는 **저장소에 실재하는 것**으로 만든다. 실제 파일 경로, 실제 함수 이름,
  코드에 실제로 있는 동작. 지어낸 이름을 섞으면 코드를 몰라도 소거법으로 답이 나온다
- 오답도 코드를 읽지 않은 사람에게는 그럴듯해야 한다.
  특히 PR 설명만 읽은 사람이 고를 법한 보기를 반드시 하나 넣는다
- 보기 길이를 비슷하게 맞춘다. 유독 긴 보기가 정답이면 그것만 보고 찍는다

답이 어디에 있는가 — 이 게이트의 난이도 곡선
- {n}개 중 **최소 1개는 답이 아래 hunk 안에** 있어야 한다. diff만 읽으면 답할 수 있는 질문이다.
  이것이 통과해야 게이트가 무조건 막는 도구가 아님이 드러난다
- {n}개 중 **최소 1개는 답이 hunk 밖에** 있어야 한다 — "구조 사실"에 적힌 다른 파일의 동작이나
  상수를 알아야 답할 수 있는 질문. 변경된 코드가 호출하는 것이 무엇을 하는지, 몇 번 하는지,
  누가 이 변경에 영향을 받는지가 그 재료다. 구조 사실이 "없음"이면 전부 hunk 안으로 채운다
- 답이 hunk 밖에 있는 질문의 보기에는 **실제 파일 경로**를 넣는다. 질문을 복사해 모델에 던져도
  그 파일을 열어 붙여넣지 않고는 답이 나오지 않아야 한다
- 배열 순서: 답이 hunk 안에 있는 질문을 먼저, 밖에 있는 질문을 나중에 둔다

공통 규칙
- 반드시 아래 hunk와 구조 사실 안에서만 묻는다. 일반 지식 질문 금지
- 각 질문은 정확히 하나의 anchor(file:Lnnn)를 가리킨다 — 질문이 *무엇에 대한* 것인지
- evidencePath는 답의 **근거가 실제로 있는 파일**이다. 답이 hunk 안이면 hunk의 파일,
  밖이면 구조 사실의 그 파일. 아래 목록에 있는 경로만 쓸 수 있다. 보류됐을 때 사람에게
  이 파일을 열어 준다 — 정답을 알려주는 것이 아니라 어디를 보면 되는지를 알려준다
- 사소한 것(변수명, 포매팅, 스타일)은 묻지 않는다
- expectedEvidence는 **근거 한 줄**에 담겨야 할 사실이다.
  보기를 고른 뒤 "어디를 보고 그렇게 판단했는지"를 따로 쓰게 되어 있다
- 질문·보기·expectedEvidence는 **PR 제목과 본문의 언어**로 쓴다. 영어 PR이면 영어.
  코드 식별자와 파일 경로는 번역하지 않고 원문 그대로 둔다

PR 제목: {title}
PR 본문: {body}
위험 사유: {reasons}

변경 내용:
{hunks}

구조 사실:
{structure}

evidencePath로 쓸 수 있는 파일:
{evidence_files}

JSON 객체만 출력. questions 필드에 질문 배열을 넣으십시오. 다른 텍스트 금지.
{{"questions":[{{"type":"structure","anchor":"src/x.py:L88","text":"...",
  "choices":["...","...","...","..."],"answerIndex":2,
  "expectedEvidence":"근거 한 줄에 반드시 나와야 하는 사실",
  "evidencePath":"src/y.py"}}]}}
"""

GRADE_PROMPT = """개발자가 객관식 보기를 고르고 그렇게 판단한 근거를 한 줄 썼습니다.
**근거 한 줄만** 보고 판정하십시오. 보기 정답 여부는 이미 따로 채점했습니다.

통과 조건
- expectedEvidence에 해당하는 사실이 근거에 있다
- 코드를 열지 않고는 쓸 수 없는 구체성이 있다 (실제 함수명, 줄 위치, 실제 동작)

보류 조건
- 일반론만 있다. 질문을 되풀이하기만 했다
- 코드에 없는 내용을 있다고 했다
- 모르겠다고 답했다  (정직한 답변이므로 부정적으로 서술하지 말 것)

질문: {question}
기대 근거: {expected}
해당 코드:
{hunk}
개발자가 쓴 근거: {answer}

JSON만 출력. hint는 개발자가 쓴 근거와 **같은 언어**로, 어디를 보면 되는지 한 문장.
{{"verdict":"pass"|"hold","hint":"..."}}
"""


class ModelError(RuntimeError):
    """모델 호출 실패. 게이트는 이 경우 사람을 막지 않고 안내만 한다."""


def resolve_endpoint() -> tuple[str, str]:
    """(엔드포인트, 공급자)를 정한다. 공급자를 바꿔도 호출부는 그대로다."""
    explicit = os.environ.get("LASTHUMAN_ENDPOINT")
    provider = os.environ.get("LASTHUMAN_PROVIDER", "azure").lower()
    if explicit:
        return explicit, provider
    if provider == "openai":
        return OPENAI_ENDPOINT, provider
    if provider == "azure":
        base = (os.environ.get("AZURE_OPENAI_ENDPOINT") or "").rstrip("/")
        if not base:
            raise ModelError(
                "AZURE_OPENAI_ENDPOINT가 없습니다. "
                "LASTHUMAN_ENDPOINT로 직접 지정하거나 LASTHUMAN_PROVIDER를 바꾸십시오."
            )
        deployment = os.environ.get("LASTHUMAN_MODEL", DEFAULT_MODEL)
        version = os.environ.get("AZURE_OPENAI_API_VERSION", DEFAULT_AZURE_API_VERSION)
        return (
            f"{base}/openai/deployments/{deployment}/chat/completions?api-version={version}",
            provider,
        )
    raise ModelError(f"알 수 없는 공급자입니다: {provider}")


def evidence_paths(risk: RiskResult, structure: StructureContext | None = None) -> tuple[str, ...]:
    """evidencePath 로 허용하는 실재 경로. hunk 파일이 먼저, 그다음 구조 사실의 파일."""
    out: dict[str, None] = {}
    for hunk in risk.top_hunks:
        out.setdefault(hunk.file, None)
    if structure is not None:
        for path in structure.evidence_files:
            out.setdefault(path, None)
    return tuple(out)


def question_response_format(
    risk: RiskResult, structure: StructureContext | None = None
) -> dict[str, object]:
    """질문 생성용 엄격한 JSON 스키마를 만든다.

    anchor 와 evidencePath 를 enum 으로 묶는다. 모델이 존재하지 않는 줄이나 파일을
    가리키는 것을 스키마 단계에서 막는다.
    """
    anchors: list[str] = []
    seen: set[str] = set()
    for hunk in risk.top_hunks:
        anchor = hunk.anchor
        if not isinstance(anchor, str) or not anchor.strip():
            raise ModelError("질문에 사용할 앵커 형식이 올바르지 않습니다.")
        if anchor not in seen:
            seen.add(anchor)
            anchors.append(anchor)
    if not anchors:
        raise ModelError("질문에 사용할 앵커가 없습니다.")
    paths = list(evidence_paths(risk, structure))
    enum_values = len(anchors) + len(paths)
    enum_chars = sum(map(len, anchors)) + sum(map(len, paths))
    fixed_chars = len("questions") + sum(map(len, _QUESTION_KEYS)) + sum(map(len, _QUESTION_TYPE_ENUM))
    if (
        enum_values + len(_QUESTION_TYPE_ENUM) > _SCHEMA_ENUM_LIMIT
        or enum_chars + fixed_chars > _SCHEMA_STRING_LIMIT
        or (enum_values > 250 and enum_chars > _LARGE_ENUM_STRING_LIMIT)
    ):
        raise ModelError("질문 앵커 목록이 지원하는 스키마 크기를 초과합니다.")

    question_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": list(_QUESTION_TYPE_ENUM)},
            "anchor": {"type": "string", "enum": anchors},
            "text": {"type": "string"},
            "choices": {"type": "array", "items": {"type": "string"}},
            "answerIndex": {"type": "integer"},
            "expectedEvidence": {"type": "string"},
            "evidencePath": {"type": "string", "enum": paths},
        },
        "required": list(_QUESTION_KEYS),
        "additionalProperties": False,
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": _QUESTION_RESPONSE_NAME,
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "questions": {"type": "array", "items": question_schema},
                },
                "required": ["questions"],
                "additionalProperties": False,
            },
        },
    }


def _extract_content(data: object, *, structured: bool) -> str:
    """chat/completions 응답에서 message.content만 안전하게 꺼낸다."""
    if not isinstance(data, dict):
        raise ModelError("예상과 다른 모델 응답 형식입니다.")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ModelError("예상과 다른 모델 응답 형식입니다.")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ModelError("예상과 다른 모델 응답 형식입니다.")

    if structured:
        if choice.get("finish_reason") != "stop":
            raise ModelError("구조화된 모델 응답이 완전하지 않습니다.")
        if message.get("refusal") is not None:
            raise ModelError("구조화된 모델 응답이 거부되었습니다.")
        if message.get("tool_calls") not in (None, []) or message.get("function_call") is not None:
            raise ModelError("구조화된 모델 응답 형식이 올바르지 않습니다.")

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ModelError("모델이 비어 있거나 올바르지 않은 내용을 반환했습니다.")
    return content


def call_model(
    prompt: str,
    *,
    token: str | None = None,
    timeout: float = 60.0,
    response_format: dict[str, object] | None = None,
) -> str:
    """OpenAI 호환 chat/completions 호출."""
    try:
        azure_cli = model_auth.auth_mode() == "azure-cli"
        endpoint, provider = resolve_endpoint()
        if azure_cli:
            token = model_auth.azure_cli_token(endpoint, provider)
            api_key = None
        else:
            api_key = os.environ.get("LASTHUMAN_API_KEY")
            token = token or api_key or os.environ.get("LASTHUMAN_TOKEN") or os.environ.get("GITHUB_TOKEN")
    except model_auth.ModelAuthError as err:
        raise ModelError(str(err)) from None
    if not token:
        raise ModelError("모델 자격 증명이 없습니다. LASTHUMAN_API_KEY를 넘겨주세요.")

    model = os.environ.get("LASTHUMAN_MODEL", DEFAULT_MODEL)
    request_body: dict[str, object] = {
        "model": model,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": prompt}],
    }
    if response_format is not None:
        request_body["response_format"] = response_format
    payload = json.dumps(request_body).encode("utf-8")

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if provider == "azure" and api_key:
        # Azure OpenAI는 api-key 헤더를 쓴다. Entra 토큰이면 Bearer로 보낸다.
        headers["api-key"] = api_key
    else:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(endpoint, data=payload, headers=headers)
    try:
        open_request = (
            urllib.request.build_opener(model_auth.AzureCliNoRedirectHandler()).open
            if azure_cli else urllib.request.urlopen
        )
        with open_request(req, timeout=timeout) as res:
            data = json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        if azure_cli:
            err.close()
            guidance = {
                401: "Check Azure CLI login and the deployment's AZURE_OPENAI_SCOPE.",
                403: "Check Azure resource inference permissions and network access.",
                429: "Retry later and check Azure model quota.",
            }.get(err.code, "Check the deployment endpoint and Azure service availability.")
            if 300 <= err.code < 400:
                guidance = "Redirects are disabled; configure the direct Azure Chat Completions URL."
            raise ModelError(f"Azure model request failed (HTTP {err.code}). {guidance}") from None
        if response_format is not None:
            err.close()
            raise ModelError(f"모델 응답 {err.code}.") from None
        raise ModelError(f"모델 응답 {err.code}: {err.read()[:300]!r}") from err
    except OSError as err:
        if azure_cli or response_format is not None:
            raise ModelError(
                "Azure model connection failed. Check the endpoint, TLS, and network access."
                if azure_cli else "Model connection failed. Check the endpoint, TLS, and network access."
            ) from None
        raise ModelError(f"모델 호출 실패: {err}") from err
    except http.client.HTTPException:
        if azure_cli or response_format is not None:
            raise ModelError(
                "Azure model HTTP transport failed. Check the endpoint and network access."
                if azure_cli else "Model HTTP transport failed. Check the endpoint and network access."
            ) from None
        raise
    except (UnicodeError, json.JSONDecodeError):
        raise ModelError("모델 응답을 읽지 못했습니다.") from None

    return _extract_content(data, structured=response_format is not None)


def shuffle_choices(
    choices: Sequence[str], answer_index: int, *, seed: str
) -> tuple[tuple[str, ...], int]:
    """보기를 결정적으로 섞고 정답 위치를 따라 옮긴다.

    같은 PR을 다시 열어도 순서가 같아야 한다. 열 때마다 바뀌면
    "아까랑 다른데"라는 불신이 생기고, 인증의 재현성도 깨진다.
    """
    items = list(choices)
    if not items:
        return (), -1
    rng = random.Random(hashlib.sha256(seed.encode("utf-8")).hexdigest())
    order = list(range(len(items)))
    rng.shuffle(order)
    return tuple(items[i] for i in order), order.index(answer_index)


def _extract_json(text: str) -> object:
    """모델이 코드펜스를 붙이는 경우가 잦다. 한 번은 봐준다."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    return json.loads(text)


def _format_hunks(hunks: Sequence[Hunk]) -> str:
    return "\n\n".join(f"{h.anchor}\n{h.body}" for h in hunks)


def generate_questions(
    risk: RiskResult,
    title: str,
    body: str,
    n: int = 2,
    *,
    structure: StructureContext | None = None,
    token: str | None = None,
    dry_run: bool = False,
) -> list[Question]:
    """위험 상위 hunk에 대해서만 묻는다. 전부 넘기면 초점이 흐려진다."""
    if dry_run:
        return _stub_questions(risk, n, structure)

    response_format = question_response_format(risk, structure)
    allowed_paths = evidence_paths(risk, structure)
    prompt = QUESTION_PROMPT.format(
        n=n,
        title=title,
        body=(body or "")[:4000],
        reasons="; ".join(risk.reasons),
        hunks=_format_hunks(risk.top_hunks),
        structure=structure.as_prompt() if structure and not structure.is_empty() else "없음",
        evidence_files="\n".join(f"- {path}" for path in allowed_paths),
    )
    valid_anchors = {h.anchor for h in risk.top_hunks}
    for attempt in range(2):
        raw = call_model(prompt, token=token, response_format=response_format)
        try:
            return _parse_questions(_extract_json(raw), valid_anchors, n, allowed_paths=allowed_paths)
        except json.JSONDecodeError:
            if attempt == 1:
                raise ModelError("질문 응답을 읽지 못했습니다.") from None
        except ModelError as error:
            if attempt == 1:
                raise ModelError(str(error)) from None
    raise AssertionError("unreachable")


def _parse_questions(
    parsed: object,
    valid_anchors: set[str],
    n: int,
    *,
    allowed_paths: Sequence[str] = (),
) -> list[Question]:
    if not isinstance(parsed, dict) or set(parsed) != {"questions"}:
        raise ModelError("질문 응답 루트 형식이 올바르지 않습니다.")
    items = parsed.get("questions")
    if not isinstance(items, list):
        raise ModelError("질문 응답은 questions 배열을 포함해야 합니다.")
    if len(items) != n:
        raise ModelError(f"질문 응답 수가 요청과 일치하지 않습니다 (요청 {n}, 응답 {len(items)}).")

    out: list[Question] = []
    for item in items:
        if not isinstance(item, dict) or set(item) != set(_QUESTION_KEYS):
            raise ModelError("질문 항목의 형식이 올바르지 않습니다.")
        anchor = item["anchor"]
        if not isinstance(anchor, str):
            raise ModelError("질문 앵커의 형식이 올바르지 않습니다.")
        if anchor not in valid_anchors:
            raise ModelError("질문 앵커가 제공된 변경 목록에 없습니다.")
        question_type = item["type"]
        if not isinstance(question_type, str) or question_type not in QUESTION_TYPES:
            raise ModelError("질문 유형이 올바르지 않습니다.")
        text = item["text"]
        evidence = item["expectedEvidence"]
        if not isinstance(text, str) or not text.strip():
            raise ModelError("질문 내용은 비어 있지 않은 문자열이어야 합니다.")
        if not isinstance(evidence, str) or not evidence.strip():
            raise ModelError("질문 기대 근거는 비어 있지 않은 문자열이어야 합니다.")
        raw_choices = item["choices"]
        if not isinstance(raw_choices, list) or any(not isinstance(c, str) for c in raw_choices):
            raise ModelError("질문 보기의 형식이 올바르지 않습니다.")
        choices = tuple(c.strip() for c in raw_choices)
        if any(not choice for choice in choices):
            raise ModelError("질문 보기는 비어 있지 않은 문자열이어야 합니다.")
        evidence_path = item["evidencePath"]
        if not isinstance(evidence_path, str) or not evidence_path.strip():
            raise ModelError("질문 근거 파일의 형식이 올바르지 않습니다.")
        if allowed_paths and evidence_path not in allowed_paths:
            raise ModelError("질문 근거 파일이 허용 목록에 없습니다.")
        raw_index = item["answerIndex"]
        if isinstance(raw_index, bool) or not isinstance(raw_index, int):
            raise ModelError("질문 정답 위치의 형식이 올바르지 않습니다.")
        idx = raw_index
        if not choices:
            if idx != -1:
                raise ModelError("서술형 질문의 정답 위치는 -1이어야 합니다.")
        else:
            if len(choices) < 2 or not 0 <= idx < len(choices):
                raise ModelError("질문 보기의 개수 또는 정답 위치가 올바르지 않습니다.")
        out.append(
            Question(
                type=question_type,
                anchor=anchor,
                text=text.strip(),
                expected_evidence=evidence.strip(),
                choices=choices,
                answer_index=idx,
                evidence_path=evidence_path,
            )
        )
    return out


def grade(
    question: Question,
    answer_text: str,
    hunk: Hunk | None,
    *,
    choice: int | None = None,
    token: str | None = None,
    dry_run: bool = False,
) -> Answer:
    """보기는 결정적으로, 근거 한 줄은 모델로 본다.

    보기만 맞으면 통과시키지 않는다. 4지선다는 찍어도 25%가 맞기 때문이다.
    근거 한 줄이 코드를 열지 않고는 쓸 수 없어야 통과다.
    """
    choice_ok: bool | None = None
    if question.is_choice:
        choice_ok = choice == question.answer_index
        if not choice_ok:
            # 보기가 틀렸으면 근거를 볼 것도 없다. 모델 호출을 아낀다.
            return Answer(
                anchor=question.anchor,
                text=answer_text,
                choice=choice,
                verdict="hold",
                choice_correct=False,
                hint=f"Open {question.anchor} and check what the code actually does.",
            )

    if dry_run:
        ans = _stub_grade(question, answer_text)
        ans.choice = choice
        ans.choice_correct = choice_ok
        return ans

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

    if not isinstance(parsed, dict) or parsed.get("verdict") not in ("pass", "hold"):
        raise ModelError("판정 응답에 올바른 결과가 없습니다.")
    verdict = parsed["verdict"]
    return Answer(
        anchor=question.anchor,
        text=answer_text,
        choice=choice,
        verdict=verdict,
        choice_correct=choice_ok,
        hint=str(parsed.get("hint", "")).strip(),
    )


# --- 모델 없이 파이프라인을 돌려보기 위한 결정적 스텁 -------------------------
# 9월 4일 질문 품질 검수 전에도 워크플로 전체를 끝까지 통과시켜 볼 수 있어야 한다.


def _stub_questions(
    risk: RiskResult, n: int, structure: StructureContext | None = None
) -> list[Question]:
    """모델 없이도 두 축과 객관식이 화면에서 보이도록 만든다.

    보기는 구조 사실에서 실재하는 경로로 채운다. 스텁이라도 지어낸 이름을
    쓰면 UI 검수 때 잘못된 인상을 준다.
    """
    out: list[Question] = []

    # structure 축 — 호출자를 실제로 아는 심볼이 있을 때만 만든다.
    if structure and structure.symbols:
        sym = next((x for x in structure.symbols if x.used_in), structure.symbols[0])
        correct = ", ".join(sym.used_in) if sym.used_in else "없음 — 아무도 부르지 않습니다"
        distractors = [f for f in structure.sibling_files if f not in sym.used_in][:3]
        # 정답이 늘 첫 자리면 화면만 보고도 찍힌다. 앵커로 시드를 줘
        # 결정적으로 섞는다 — 같은 PR을 다시 열어도 순서가 바뀌지 않는다.
        choices = [correct, *distractors] or [correct]
        anchor = next(
            (h.anchor for h in risk.top_hunks if h.file == sym.defined_in),
            risk.top_hunks[0].anchor if risk.top_hunks else "",
        )
        if anchor:
            shuffled = shuffle_choices(choices, 0, seed=anchor)
            out.append(
                Question(
                    type="structure",
                    anchor=anchor,
                    text=f"[dry-run] {sym.symbol}()를 이 저장소에서 호출하는 곳은 어디입니까?",
                    expected_evidence=f"{sym.symbol}의 실제 호출 지점",
                    choices=shuffled[0],
                    answer_index=shuffled[1],
                    evidence_path=sym.used_in[0] if sym.used_in else sym.defined_in,
                )
            )

    # code 축 — 남은 자리를 hunk 질문으로 채운다.
    types = ("claim", "consequence", "rationale")
    for i, h in enumerate(risk.top_hunks):
        if len(out) >= n:
            break
        out.append(
            Question(
                type=types[i % len(types)],
                anchor=h.anchor,
                text=f"[dry-run] {h.anchor}의 변경이 어떤 상황에서 무슨 결과를 냅니까?",
                expected_evidence=f"{h.file}의 해당 hunk에 실제로 있는 동작",
                evidence_path=h.file,
            )
        )
    return out[:n]


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
