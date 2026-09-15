"""객관식과 구조 축의 계약.

객관식은 편의 기능이 아니라 채점을 결정적으로 만드는 장치다.
동시에 찍기로 통과하면 게이트가 무의미해지므로, 보기만으로는 절대
통과할 수 없어야 한다. 아래 테스트가 그 선을 지킨다.
"""

from __future__ import annotations

from lasthuman.interview import grade, shuffle_choices
from lasthuman.models import Hunk, Question

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
