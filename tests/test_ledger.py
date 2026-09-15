"""원장 집계의 계약.

여기서 지켜야 할 것은 숫자의 정확성만이 아니다. **무엇을 집계하지 않는가**가
같은 무게로 중요하다. 개인 단위 필드가 산출물에 새어 들어가는 순간
일주일 안에 인사 지표가 되고, 그때부터 사람들은 배지를 위해 최적화한다.
"""

from __future__ import annotations

import json

from lasthuman.ledger import MIN_SAMPLE, MergedPr, aggregate, parse_codeowners, zone_of

ZONES = ["app/auth/", "app/db/", "app/orders/", "docs/"]


def pr(n, files, *, triggered=True, attested=False, forced=False, count=0, lines=10):
    return MergedPr(
        number=n, merged_at="2026-08-20T00:00:00Z", files=tuple(files),
        triggered=triggered, attested=attested, forced=forced,
        attester_count=count, changed_lines=lines,
    )


def test_가장_구체적인_구역이_이긴다():
    zones = ["app/", "app/auth/"]
    assert zone_of("app/auth/token.py", zones) == "app/auth/"


def test_표본이_적으면_비율_대신_None을_준다():
    # 3건 중 2건을 67%로 적으면 없는 신호를 읽게 된다.
    prs = [pr(i, ["app/auth/token.py"], attested=True) for i in range(3)]
    s = aggregate("o/r", prs, ZONES)
    assert s.gated_total == 3 < MIN_SAMPLE
    assert s.attested_rate is None
    assert s.zones[0].rate is None


def test_표본이_충분하면_비율이_나온다():
    prs = [pr(i, ["app/auth/token.py"], attested=i < 4) for i in range(MIN_SAMPLE)]
    s = aggregate("o/r", prs, ZONES)
    assert s.attested_rate == 4 / MIN_SAMPLE


def test_강제_머지가_구역에_귀속된다():
    s = aggregate("o/r", [pr(1, ["app/db/client.py"], forced=True)], ZONES)
    db = next(z for z in s.zones if z.zone == "app/db/")
    assert db.forced == 1 and s.forced_total == 1


def test_발동하지_않은_PR은_게이트_통계에_들어가지_않는다():
    # 전수 적용하지 않는다. 임계값 미달 PR을 미인증으로 세면 숫자가 거짓이 된다.
    s = aggregate("o/r", [pr(1, ["docs/x.md"], triggered=False)], ZONES)
    assert s.merged_total == 1
    assert s.gated_total == 0
    assert s.attested_rate is None


def test_답할_사람이_2명_이상이어야_커버로_센다():
    # 버스 팩터가 1이면 커버된 것이 아니다.
    prs = [pr(1, ["app/auth/token.py"], attested=True, count=1)]
    s = aggregate("o/r", prs, ZONES, answerers={"app/auth/": 1})
    assert s.covered_zones == 0
    assert s.thin_zones == 1


def test_산출물에_개인_식별_필드가_없다():
    """이 테스트가 이 파일의 이유다. 깨지면 필드를 지우지 말고 설계를 다시 볼 것."""
    prs = [pr(1, ["app/auth/token.py"], attested=True, count=2)]
    s = aggregate("o/r", prs, ZONES, owners={"app/auth/": "@security-team"}, answerers={"app/auth/": 2})
    blob = json.dumps(s.to_json(), ensure_ascii=False)

    금지 = ("score", "rank", "ranking", "leaderboard", "attesters", "logins", "userName")
    for 필드 in 금지:
        assert f'"{필드}"' not in blob, f"개인 단위 필드 {필드}가 산출물에 들어갔다"
    # 담당자는 CODEOWNERS에 이미 공개된 사실이라 그대로 나간다.
    assert "@security-team" in blob
    # 인증한 사람은 이름이 아니라 수로만 나간다.
    assert '"answerers": 2' in blob


def test_codeowners는_주석과_빈_줄을_무시한다():
    rules = parse_codeowners("# 주석\n\n/app/auth/  @sec  @lead\n잘못된줄\n")
    assert rules == [("app/auth/", "@sec @lead")]
