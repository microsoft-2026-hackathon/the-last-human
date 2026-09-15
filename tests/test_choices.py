"""객관식과 구조 축의 계약.

객관식은 편의 기능이 아니라 채점을 결정적으로 만드는 장치다.
동시에 찍기로 통과하면 게이트가 무의미해지므로, 보기만으로는 절대
통과할 수 없어야 한다. 아래 테스트가 그 선을 지킨다.
"""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from lasthuman.interview import ModelError, generate_questions, grade, shuffle_choices
from lasthuman.models import Hunk, Question, RiskResult

CHOICE_Q = Question(
    type="structure",
    anchor="app/auth/token.py:L32",
    text="ensure_fresh()를 호출하는 곳은?",
    expected_evidence="session.py가 ensure_fresh를 호출한다",
    choices=("app/auth/session.py", "app/db/client.py", "app/orders/service.py", "없음"),
    answer_index=0,
)

HUNK = Hunk(
    file="app/auth/token.py",
    new_start=32,
    old_start=30,
    anchor="app/auth/token.py:L32",
    added=("+    return token",),
    removed=(),
    body="+    return token",
    file_status="modified",
)


def test_틀린_보기는_근거가_좋아도_보류다():
    ans = grade(CHOICE_Q, "session.py L18에서 ensure_fresh를 부릅니다", HUNK, choice=1, dry_run=True)
    assert ans.verdict == "hold"
    assert ans.choice_correct is False


def test_맞은_보기라도_근거가_비면_통과하지_못한다():
    # 4지선다는 찍어도 25%가 맞는다. 보기만으로 통과시키면 게이트가 무너진다.
    ans = grade(CHOICE_Q, "잘 모르겠음", HUNK, choice=0, dry_run=True)
    assert ans.verdict == "hold"
    assert ans.choice_correct is True


def test_보기와_근거가_모두_맞으면_통과한다():
    ans = grade(
        CHOICE_Q,
        "app/auth/session.py의 current_token()이 ensure_fresh()를 호출합니다",
        HUNK,
        choice=0,
        dry_run=True,
    )
    assert ans.verdict == "pass"
    assert ans.choice_correct is True


def test_보기를_고르지_않으면_보류다():
    ans = grade(CHOICE_Q, "근거는 씀", HUNK, choice=None, dry_run=True)
    assert ans.verdict == "hold"


def test_셔플은_같은_시드에_같은_결과를_준다():
    # 같은 PR을 다시 열었는데 순서가 바뀌면 인증의 재현성이 깨진다.
    a = shuffle_choices(["가", "나", "다", "라"], 2, seed="app/x.py:L10")
    b = shuffle_choices(["가", "나", "다", "라"], 2, seed="app/x.py:L10")
    assert a == b
    assert a[0][a[1]] == "다"


def test_셔플은_시드가_다르면_정답_위치를_옮긴다():
    seeds = [f"app/x.py:L{i}" for i in range(12)]
    positions = {shuffle_choices(["가", "나", "다", "라"], 0, seed=s)[1] for s in seeds}
    assert len(positions) > 1, "정답이 늘 같은 자리면 화면만 보고 찍힌다"


def test_generated_first_choice_remains_the_answer(monkeypatch):
    def response(prompt, *, token=None, response_format=None):
        assert HUNK.anchor in prompt
        assert HUNK.body in prompt
        assert response_format is not None
        return json.dumps({"questions": [{
            "type": "structure", "anchor": HUNK.anchor,
            "text": CHOICE_Q.text, "choices": list(CHOICE_Q.choices),
            "answerIndex": 0, "expectedEvidence": CHOICE_Q.expected_evidence,
        }]})

    monkeypatch.setattr("lasthuman.interview.call_model", response)
    questions = generate_questions(RiskResult(50, True, ("critical path",), (HUNK,)), "PR", "", n=1)
    assert questions[0].is_choice
    assert questions[0].answer_index == 0


@pytest.mark.parametrize("raw", [
    "not-json",
    "{}",
    "[null]",
    '{"questions":[{"choices":1,"anchor":"app/auth/token.py:L32"}]}',
])
def test_invalid_question_response_is_a_model_error(monkeypatch, raw):
    monkeypatch.setattr("lasthuman.interview.call_model", lambda prompt, **kwargs: raw)
    with pytest.raises(ModelError):
        generate_questions(RiskResult(50, True, (), (HUNK,)), "PR", "")


@pytest.mark.parametrize("raw", ["[]", '{"verdict":"unknown"}', '{"error":"unavailable"}'])
def test_invalid_grade_response_is_not_a_human_hold(monkeypatch, raw):
    monkeypatch.setattr("lasthuman.interview.call_model", lambda *args, **kwargs: raw)
    with pytest.raises(ModelError):
        grade(CHOICE_Q, "code evidence", HUNK, choice=0)


def question_batch(prefix="question"):
    return [
        {
            "type": kind,
            "anchor": HUNK.anchor,
            "text": f"{prefix} {index}",
            "choices": list(CHOICE_Q.choices),
            "answerIndex": 0,
            "expectedEvidence": CHOICE_Q.expected_evidence,
        }
        for index, kind in enumerate(("claim", "consequence", "rationale"))
    ]


def wrapped_question_batch(prefix="question"):
    return json.dumps({"questions": question_batch(prefix)})


def test_complete_question_batch_needs_one_model_call(monkeypatch):
    call = Mock(return_value=wrapped_question_batch())
    monkeypatch.setattr("lasthuman.interview.call_model", call)
    questions = generate_questions(RiskResult(50, True, (), (HUNK,)), "PR", "", n=3)
    assert len(questions) == 3
    call.assert_called_once()


@pytest.mark.parametrize("problem", [
    "short", "anchor", "type", "null-type", "text", "evidence", "index", "root-extra",
    "item", "extra-field", "missing-field", "extra-valid", "extra-invalid",
])
def test_invalid_question_batch_is_replaced_in_full(monkeypatch, problem):
    first = {"questions": question_batch("discarded")}
    if problem == "short":
        first["questions"].pop()
    elif problem == "anchor":
        first["questions"][0]["anchor"] = "outside.py:L999"
    elif problem == "type":
        first["questions"][0]["type"] = "code"
    elif problem == "null-type":
        first["questions"][0]["type"] = None
    elif problem == "text":
        first["questions"][0]["text"] = " "
    elif problem == "evidence":
        first["questions"][0]["expectedEvidence"] = None
    elif problem == "index":
        first["questions"][0]["answerIndex"] = True
    elif problem == "root-extra":
        first["meta"] = "unexpected"
    elif problem == "extra-field":
        first["questions"][0]["extra"] = "unexpected"
    elif problem == "missing-field":
        del first["questions"][0]["expectedEvidence"]
    elif problem in {"extra-valid", "extra-invalid"}:
        first["questions"].append(dict(first["questions"][0]))
        if problem == "extra-invalid":
            first["questions"][-1]["anchor"] = "outside.py:L999"
    else:
        first["questions"][0] = None
    call = Mock(side_effect=[json.dumps(first), wrapped_question_batch("accepted")])
    monkeypatch.setattr("lasthuman.interview.call_model", call)
    questions = generate_questions(RiskResult(50, True, (), (HUNK,)), "PR", "", n=3, token="unit-token")
    assert [question.text for question in questions] == ["accepted 0", "accepted 1", "accepted 2"]
    assert all(question.anchor == HUNK.anchor for question in questions)
    assert call.call_count == 2
    assert call.call_args_list[0].args[0] == call.call_args_list[1].args[0]
    assert call.call_args_list[0].kwargs["response_format"] is call.call_args_list[1].kwargs["response_format"]
    assert all(item.kwargs["token"] == "unit-token" for item in call.call_args_list)


def test_json_repair_retries_with_the_same_prompt_and_schema(monkeypatch):
    call = Mock(side_effect=["not-json", wrapped_question_batch()])
    monkeypatch.setattr("lasthuman.interview.call_model", call)
    assert len(generate_questions(RiskResult(50, True, (), (HUNK,)), "PR", "", n=3)) == 3
    assert call.call_count == 2
    assert call.call_args_list[1].args[0] == call.call_args_list[0].args[0]
    assert call.call_args_list[1].kwargs["response_format"] is call.call_args_list[0].kwargs["response_format"]


@pytest.mark.parametrize(("first", "second"), [
    ("not-json", "[]"),
    ("[]", "not-json"),
    ("[]", "[]"),
    ("not-json", "not-json"),
])
def test_json_and_batch_errors_share_one_retry_budget(monkeypatch, first, second):
    call = Mock(side_effect=[first, second, wrapped_question_batch()])
    monkeypatch.setattr("lasthuman.interview.call_model", call)
    with pytest.raises(ModelError):
        generate_questions(RiskResult(50, True, (), (HUNK,)), "PR", "", n=3)
    assert call.call_count == 2


def test_partial_batches_are_never_combined(monkeypatch):
    batch = question_batch()
    call = Mock(side_effect=[
        json.dumps({"questions": batch[:2]}),
        json.dumps({"questions": batch[2:]}),
    ])
    monkeypatch.setattr("lasthuman.interview.call_model", call)
    with pytest.raises(ModelError):
        generate_questions(RiskResult(50, True, (), (HUNK,)), "PR", "", n=3)
    assert call.call_count == 2


@pytest.mark.parametrize("after_invalid_batch", [False, True])
def test_model_request_errors_are_not_retried(monkeypatch, after_invalid_batch):
    failure = ModelError("Model request unavailable")
    replies = ["[]", failure] if after_invalid_batch else [failure]
    call = Mock(side_effect=replies)
    monkeypatch.setattr("lasthuman.interview.call_model", call)
    with pytest.raises(ModelError) as result:
        generate_questions(RiskResult(50, True, (), (HUNK,)), "PR", "", n=3)
    assert result.value is failure
    assert call.call_count == len(replies)


def test_dry_run_keeps_its_existing_behavior_without_model_calls(monkeypatch):
    call = Mock(side_effect=AssertionError("Unexpected model request"))
    monkeypatch.setattr("lasthuman.interview.call_model", call)
    questions = generate_questions(RiskResult(50, True, (), (HUNK,)), "PR", "", n=3, dry_run=True)
    assert len(questions) == 1
    call.assert_not_called()
