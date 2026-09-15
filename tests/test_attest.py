"""인증의 수명과 게이트 판단.

여기서 막지 못하면 게이트가 열린 채로 고장난다.
"""

from lasthuman.attest import (
    ANSWERS_MARKER,
    band_for,
    decode_all,
    decode_answers,
    encode,
    gate_decision,
    valid_for,
)
from lasthuman.models import Answer, Attestation

SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER = "ffffffffffffffffffffffffffffffffffffffff"


def att(actor="daeungo1", role="author", head_sha=SHA, verdicts=("pass", "pass")):
    return Attestation(
        version=1,
        repo="daeungo1/the-last-human",
        pr=1,
        head_sha=head_sha,
        actor=actor,
        role=role,
        band="co-authored",
        score=69,
        reasons=("중요 경로 변경",),
        answers=[Answer(anchor=f"x.py:L{i}", text="...", verdict=v) for i, v in enumerate(verdicts)],
        created_at="2026-09-02T00:00:00+00:00",
    )


def test_인증은_코멘트_본문을_왕복한다():
    back = decode_all(encode(att()))
    assert len(back) == 1
    assert back[0].head_sha == SHA
    assert [a.verdict for a in back[0].answers] == ["pass", "pass"]


def test_인증은_커밋_하나에만_유효하다():
    assert valid_for(att(), SHA)
    assert not valid_for(att(), OTHER)


def test_새_커밋이_오면_인증이_무효가_된다():
    conclusion, summary = gate_decision(
        [att(role="author"), att(actor="reviewer2", role="reviewer")], OTHER, "daeungo1"
    )
    assert conclusion == "failure"
    assert "무효" in summary


def test_생성자만_인증하면_통과하지_않는다():
    conclusion, summary = gate_decision([att(role="author")], SHA, "daeungo1")
    assert conclusion == "failure"
    assert "리뷰어" in summary


def test_생성자와_리뷰어가_모두_인증하면_통과한다():
    conclusion, _ = gate_decision(
        [att(role="author"), att(actor="reviewer2", role="reviewer")], SHA, "daeungo1"
    )
    assert conclusion == "success"


def test_PR_작성자가_리뷰어를_겸할_수_없다():
    """자기 PR에 자기가 리뷰어 인증을 남겨도 리뷰어 몫으로 세지 않는다."""
    conclusion, summary = gate_decision(
        [att(role="author"), att(actor="daeungo1", role="reviewer")], SHA, "daeungo1"
    )
    assert conclusion == "failure"
    assert "리뷰어" in summary


def test_답변이_하나라도_보류면_밴드가_agent_led다():
    answers = [Answer("x:L1", "a", verdict="pass"), Answer("x:L2", "b", verdict="hold")]
    assert band_for(answers) == "agent-led"


def test_답변이_없으면_밴드가_agent_led다():
    assert band_for([]) == "agent-led"


def test_답변_블록을_읽는다():
    body = f'<!-- {ANSWERS_MARKER} {{"v":1,"role":"author","answers":[]}} -->\n제출'
    assert decode_answers(body) == {"v": 1, "role": "author", "answers": []}


def test_답변_블록이_없으면_None이다():
    assert decode_answers("그냥 코멘트입니다") is None


def test_깨진_JSON은_조용히_무시한다():
    assert decode_answers(f"<!-- {ANSWERS_MARKER} {{not json}} -->") is None
