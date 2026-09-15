"""위험 점수는 순수 함수다. 같은 입력이면 항상 같은 출력이어야 한다."""

from lasthuman.config import parse_config
from lasthuman.diff import parse_hunks
from lasthuman.models import PrMeta
from lasthuman.risk import score

CONFIG = parse_config(
    r"""
mode: internal
threshold: 40
signals:
  criticalPaths:
    - 'app/auth/**': 30
  linesChanged: { per100: 10, max: 30 }
  patterns:
    - 'except\s': 10
  testsRemoved: 25
  agentHint: 10
  externalContributor: 40
"""
)

AUTH_DIFF = """diff --git app/auth/token.py app/auth/token.py
index 1111111..2222222 100644
--- app/auth/token.py
+++ app/auth/token.py
@@ -30,3 +30,5 @@ async def refresh(transport, token):
     body = await post_json(transport, TOKEN_ENDPOINT, payload)
+    except HttpError as err:
+        return token
"""

README_DIFF = """diff --git README.md README.md
index 1111111..2222222 100644
--- README.md
+++ README.md
@@ -8,1 +8,1 @@
-old line
+new line
"""


def test_같은_입력이면_같은_출력이다():
    diff = parse_hunks(AUTH_DIFF)
    a = score(diff, CONFIG, PrMeta())
    b = score(diff, CONFIG, PrMeta())
    assert (a.score, a.triggered, a.reasons) == (b.score, b.triggered, b.reasons)


def test_중요_경로와_패턴이_함께_걸리면_발동한다():
    result = score(parse_hunks(AUTH_DIFF), CONFIG, PrMeta())
    assert result.triggered
    assert result.score >= CONFIG.threshold


def test_사유를_반드시_반환한다():
    result = score(parse_hunks(AUTH_DIFF), CONFIG, PrMeta())
    assert result.reasons, "점수만 던지면 개발자가 반발한다"


def test_한_줄짜리_문서_변경은_발동하지_않는다():
    result = score(parse_hunks(README_DIFF), CONFIG, PrMeta())
    assert not result.triggered
    assert any("임계값" in r for r in result.reasons)


def test_에이전트_흔적은_가점일_뿐_트리거_근거가_아니다():
    diff = parse_hunks(README_DIFF)
    assert not score(diff, CONFIG, PrMeta(agent_hint=True)).triggered


def test_internal_모드에서는_외부_기여자_신호가_꺼진다():
    diff = parse_hunks(README_DIFF)
    result = score(diff, CONFIG, PrMeta(author_association="NONE"))
    assert not any("외부 기여자" in r for r in result.reasons)


def test_oss_모드_라벨이_양쪽에서_같은_점수를_낸다():
    """확장/Action 어느 쪽에서 채점해도 같아야 한다.

    모드 판단이 워크플로 셸 한쪽에만 있으면 gate의 재계산이 다른 점수를 내고,
    신뢰 경계가 자기 자신을 불일치로 막는다. 실제로 그렇게 한 번 막혔다.
    """
    diff = parse_hunks(AUTH_DIFF)
    plain = score(diff, CONFIG, PrMeta(author_association="OWNER", labels=()))
    labeled = score(
        diff,
        CONFIG,
        PrMeta(author_association="OWNER", labels=("oss-mode", "external-contributor")),
    )

    assert labeled.score > plain.score, "라벨이 붙으면 외부 기여자 가점이 붙어야 한다"
    assert any("외부 기여자" in r for r in labeled.reasons)
    assert not any("외부 기여자" in r for r in plain.reasons)
    # 같은 입력이면 항상 같은 출력. 두 번 불러도 동일해야 한다.
    again = score(
        diff,
        CONFIG,
        PrMeta(author_association="OWNER", labels=("oss-mode", "external-contributor")),
    )
    assert again.score == labeled.score
