from __future__ import annotations

import json
import inspect
from pathlib import Path
from unittest.mock import Mock

import pytest

from lasthuman import interview
from lasthuman.interview import (
    ModelError,
    generate_questions,
    question_response_format,
)
from lasthuman.models import Hunk, RiskResult


def make_hunk(anchor: str, *, file: str = "app/service.py", line: int = 10) -> Hunk:
    return Hunk(
        file=file,
        new_start=line,
        old_start=line,
        anchor=anchor,
        added=("+    return result",),
        removed=(),
        body="@@ -10,1 +10,1 @@\n+    return result\n",
        file_status="modified",
    )


def make_risk(*hunks: Hunk) -> RiskResult:
    return RiskResult(80, True, ("critical path changed",), hunks)


def valid_question(
    anchor: str,
    *,
    question_type: str = "claim",
    choices: list[str] | None = None,
    answer_index: int = 0,
) -> dict[str, object]:
    return {
        "type": question_type,
        "anchor": anchor,
        "text": f"Question for {anchor}",
        "choices": ["first", "second"] if choices is None else choices,
        "answerIndex": answer_index,
        "expectedEvidence": "return result",
        "evidencePath": anchor.rsplit(":L", 1)[0],
    }


def wrapped_response(*questions: dict[str, object], **extra_root: object) -> str:
    payload: dict[str, object] = {"questions": list(questions)}
    payload.update(extra_root)
    return json.dumps(payload)


def test_question_response_format_shape_and_anchor_enum_are_stable() -> None:
    risk = make_risk(
        make_hunk("app/service.py:L10"),
        make_hunk("docs/guide.md:L3", file="docs/guide.md", line=3),
        make_hunk("app/service.py:L10"),
    )

    response_format = question_response_format(risk)

    assert question_response_format.__module__ == "lasthuman.interview"
    assert interview.question_response_format is question_response_format
    assert Path(inspect.getfile(question_response_format)).resolve() == Path(interview.__file__).resolve()
    assert response_format["type"] == "json_schema"

    wrapper = response_format["json_schema"]
    assert wrapper["name"] == "lasthuman_question_batch_v1"
    assert wrapper["strict"] is True
    schema = wrapper["schema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["questions"]
    assert schema["additionalProperties"] is False

    questions = schema["properties"]["questions"]
    assert questions["type"] == "array"
    item = questions["items"]
    assert item["type"] == "object"
    assert item["required"] == [
        "type",
        "anchor",
        "text",
        "choices",
        "answerIndex",
        "expectedEvidence",
        "evidencePath",
    ]
    assert item["additionalProperties"] is False
    assert item["properties"]["type"] == {
        "type": "string",
        "enum": ["claim", "consequence", "rationale", "structure"],
    }
    assert item["properties"]["anchor"] == {
        "type": "string",
        "enum": ["app/service.py:L10", "docs/guide.md:L3"],
    }
    assert item["properties"]["text"] == {"type": "string"}
    assert item["properties"]["choices"] == {"type": "array", "items": {"type": "string"}}
    assert item["properties"]["answerIndex"] == {"type": "integer"}
    assert item["properties"]["expectedEvidence"] == {"type": "string"}
    # evidencePath 도 enum 이다 — hunk 파일이 먼저. 모델이 없는 파일을 가리키지 못한다.
    assert item["properties"]["evidencePath"] == {
        "type": "string",
        "enum": ["app/service.py", "docs/guide.md"],
    }


@pytest.mark.parametrize("hunks", [
    (),
    (make_hunk(" "),),
])
def test_question_response_format_requires_nonempty_anchor_candidates(
    hunks: tuple[Hunk, ...],
) -> None:
    with pytest.raises(ModelError):
        question_response_format(make_risk(*hunks))


def test_generate_questions_fails_before_model_call_without_valid_anchors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = Mock(side_effect=AssertionError("model call should not happen"))
    monkeypatch.setattr("lasthuman.interview.call_model", call)

    with pytest.raises(ModelError):
        generate_questions(make_risk(), "PR", "", n=1)

    call.assert_not_called()


# 앵커 enum + 질문 유형 4 + evidencePath enum(여기서는 파일 "a" 하나) 이 500 을 넘으면 안 된다.
@pytest.mark.parametrize(("count", "accepted"), [(495, True), (496, False)])
def test_schema_enum_budget_counts_question_types(
    monkeypatch: pytest.MonkeyPatch, count: int, accepted: bool,
) -> None:
    risk = make_risk(*(make_hunk(f"a:L{index}") for index in range(count)))
    call = Mock(side_effect=AssertionError("model call should not happen"))
    monkeypatch.setattr("lasthuman.interview.call_model", call)
    if accepted:
        anchors = question_response_format(risk)["json_schema"]["schema"]["properties"]["questions"][
            "items"
        ]["properties"]["anchor"]["enum"]
        assert len(anchors) == count
    else:
        with pytest.raises(ModelError, match="스키마 크기"):
            generate_questions(risk, "PR", "", n=1)
    call.assert_not_called()


@pytest.mark.parametrize(("large_enum", "extra"), [(False, 0), (False, 1), (True, 0), (True, 1)])
def test_schema_string_budgets_fail_without_truncation(
    monkeypatch: pytest.MonkeyPatch, large_enum: bool, extra: int,
) -> None:
    keys = ["questions", "type", "anchor", "text", "choices", "answerIndex", "expectedEvidence", "evidencePath"]
    types = ["claim", "consequence", "rationale", "structure"]
    # make_hunk 는 모든 앵커를 같은 파일(app/service.py)에 둔다. 그 경로 하나가
    # evidencePath enum 으로 예산에 한 번 더 들어간다.
    shared_file = "app/service.py"
    if large_enum:
        anchors = [f"a:L{index}" for index in range(250)]
        remaining = 7_500 - sum(map(len, anchors)) - len(shared_file)
    else:
        anchors = []
        remaining = 15_000 - sum(map(len, keys + types)) - len(shared_file)
    anchors.append("x" * (remaining - len(":L1") + extra) + ":L1")
    risk = make_risk(*(make_hunk(anchor) for anchor in anchors))
    call = Mock(side_effect=AssertionError("model call should not happen"))
    monkeypatch.setattr("lasthuman.interview.call_model", call)
    if extra:
        with pytest.raises(ModelError, match="스키마 크기"):
            generate_questions(risk, "PR", "", n=1)
    else:
        actual = question_response_format(risk)["json_schema"]["schema"]["properties"]["questions"][
            "items"
        ]["properties"]["anchor"]["enum"]
        assert actual == anchors
    call.assert_not_called()


def test_generate_questions_accepts_wrapped_multiple_choice_and_free_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = make_hunk("app/service.py:L10")
    second = make_hunk("docs/guide.md:L3", file="docs/guide.md", line=3)

    def reply(prompt: str, *, token=None, response_format=None):
        assert first.anchor in prompt
        assert second.anchor in prompt
        assert token == "unit-token"
        assert response_format == question_response_format(make_risk(first, second))
        return wrapped_response(
            valid_question(first.anchor, question_type="structure"),
            valid_question(
                second.anchor,
                question_type="rationale",
                choices=[],
                answer_index=-1,
            ),
        )

    monkeypatch.setattr("lasthuman.interview.call_model", reply)

    questions = generate_questions(
        make_risk(first, second),
        "PR",
        "",
        n=2,
        token="unit-token",
    )

    assert [(question.anchor, question.type) for question in questions] == [
        (first.anchor, "structure"),
        (second.anchor, "rationale"),
    ]
    assert questions[0].choices == ("first", "second")
    assert questions[0].answer_index == 0
    assert questions[1].choices == ()
    assert questions[1].answer_index == -1


@pytest.mark.parametrize("raw", [
    json.dumps([valid_question("app/service.py:L10")]),
    wrapped_response(valid_question("app/service.py:L10"), meta="unexpected"),
    json.dumps({"items": [valid_question("app/service.py:L10")]}),
    wrapped_response({
        "type": "claim",
        "anchor": "app/service.py:L10",
        "text": "Question",
        "choices": ["first", "second"],
        "answerIndex": 0,
    }),
    wrapped_response({
        **valid_question("app/service.py:L10"),
        "extra": "unexpected",
    }),
    wrapped_response(valid_question("outside.py:L999")),
    wrapped_response(valid_question("app/service.py:L10", question_type="unknown")),
    wrapped_response(valid_question("app/service.py:L10", choices=["only"], answer_index=0)),
    wrapped_response(valid_question("app/service.py:L10", choices=[" ", "second"], answer_index=1)),
    wrapped_response(valid_question("app/service.py:L10", choices=[], answer_index=0)),
    wrapped_response(
        valid_question(
            "app/service.py:L10",
            choices=["first", "second"],
            answer_index=-1,
        )
    ),
])
def test_generate_questions_rejects_invalid_wrapper_shapes_and_choice_contracts(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    call = Mock(side_effect=[raw, raw])
    monkeypatch.setattr("lasthuman.interview.call_model", call)

    with pytest.raises(ModelError):
        generate_questions(make_risk(make_hunk("app/service.py:L10")), "PR", "", n=1)

    assert call.call_count == 2


def test_generate_questions_retries_once_with_identical_prompt_and_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    risk = make_risk(
        make_hunk("app/service.py:L10"),
        make_hunk("docs/guide.md:L3", file="docs/guide.md", line=3),
    )
    valid = wrapped_response(
        valid_question("app/service.py:L10", question_type="claim"),
        valid_question("docs/guide.md:L3", question_type="structure"),
    )
    call = Mock(side_effect=["not-json", valid])
    monkeypatch.setattr("lasthuman.interview.call_model", call)

    questions = generate_questions(risk, "PR", "", n=2, token="schema-token")

    assert len(questions) == 2
    assert call.call_count == 2
    assert call.call_args_list[0].args[0] == call.call_args_list[1].args[0]
    assert call.call_args_list[0].kwargs["token"] == "schema-token"
    assert call.call_args_list[1].kwargs["token"] == "schema-token"
    assert (
        call.call_args_list[0].kwargs["response_format"]
        is call.call_args_list[1].kwargs["response_format"]
    )


def test_generate_questions_does_not_retry_refusals_or_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = ModelError("structured refusal")
    call = Mock(side_effect=failure)
    monkeypatch.setattr("lasthuman.interview.call_model", call)

    with pytest.raises(ModelError) as error:
        generate_questions(make_risk(make_hunk("app/service.py:L10")), "PR", "", n=1)

    assert error.value is failure
    assert call.call_count == 1
