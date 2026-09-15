"""이해 게이트 대시보드 렌더링.

집계는 :mod:`lasthuman.ledger`가 하고, 여기서는 화면에 필요한 모양으로만 접는다.
숫자를 보여주는 것으로 끝내지 않고 "무엇을 하면 올라가는가"를 함께 낸다 —
지표만 걸어두면 아무도 움직이지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from .ledger import MIN_SAMPLE, Summary, actions_for

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"], default_for_string=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _bars(s: Summary) -> list[dict]:
    """게이트가 발동한 뒤 무슨 일이 있었는지. 네 갈래로만 나눈다."""
    total = max(
        s.attested_total + s.forced_total + s.waiting_total + s.closed_unattested, 1
    )

    def pct(n: int) -> int:
        return round(n / total * 100)

    return [
        {"label": "인증 후 머지", "kind": "pass", "count": s.attested_total,
         "pct": pct(s.attested_total), "note": "통과"},
        {"label": "강제 머지", "kind": "force", "count": s.forced_total,
         "pct": pct(s.forced_total), "note": "우회"},
        {"label": "인증 대기", "kind": "wait", "count": s.waiting_total,
         "pct": pct(s.waiting_total), "note": "머지 버튼이 잠긴 상태"},
        {"label": "인증 없이 닫힘", "kind": "drop", "count": s.closed_unattested,
         "pct": pct(s.closed_unattested), "note": "포기"},
    ]


def render_dashboard(
    summary: Summary,
    *,
    since: str,
    target: int = 80,
    sample_path: str = "src/auth/token.py",
) -> str:
    actions = actions_for(summary)
    zone_actions = {a["zone"]: a["action"] for a in actions}

    zones_json = json.dumps(
        [
            {
                "zone": z.zone,
                "owner": z.owner,
                "merged": z.merged,
                "gated": z.gated,
                "attested": z.attested,
                "forced": z.forced,
                "answerers": z.answerers,
                "rate": z.rate,
            }
            for z in summary.zones
        ],
        ensure_ascii=False,
    )

    forced_rows = [
        # 사후 인증은 아직 추적하지 않는다. 0으로 두고 화면에서 미인증으로 센다.
        {"zone": z.zone, "count": z.forced, "after": 0}
        for z in summary.zones
        if z.forced
    ]

    gated_zones = [z for z in summary.zones if z.gated]
    template = _env().get_template("dashboard.html.j2")
    return template.render(
        s=summary,
        since=since,
        target=target,
        min_sample=MIN_SAMPLE,
        one_zone=sum(1 for z in gated_zones if z.answerers == 1),
        zero_zone=sum(1 for z in gated_zones if z.answerers == 0),
        # 답할 사람이 1명 이하인 구역에 그동안 몇 건이 들어갔는가.
        # 구역 수보다 이 숫자가 위험의 크기를 더 정직하게 말한다.
        thin_merges=sum(z.merged for z in gated_zones if z.answerers <= 1),
        bars=_bars(summary),
        actions=actions,
        zone_actions=zone_actions,
        forced_rows=forced_rows,
        sample_path=sample_path,
        zones_json=_script_json(zones_json),
    )


def _script_json(raw: str) -> Markup:
    """`<script>` 안에 넣어도 안전한 JSON.

    autoescape를 그대로 두면 따옴표가 엔티티가 되어 JS가 깨지고, 그냥 통과시키면
    구역 이름이나 CODEOWNERS 담당자 문자열에 `</script>`가 섞였을 때 페이지를
    벗어난다. 셋만 유니코드 이스케이프하면 두 문제가 같이 없어진다.
    """
    safe = raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return Markup(safe)
