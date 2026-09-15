"""대시보드 집계의 마무리 — 구역 행, 요약 수치, 액션, 시드 병합.

service.dashboard()가 머지 기록을 훑어 모은 구역별 원시 카운트를 받아
화면이 바로 쓰는 모양으로 접는다. 순수 함수만 둔다.

여기서도 원장의 선은 같다. 구역 단위 수만 내보내고, 인증한 사람은 "몇 명"으로만
센다. 사람 이름이 붙는 필드를 여기에 추가하지 말 것 (담당은 CODEOWNERS에 이미
공개된 사실이라 예외).

시드 파일(TLH_DEMO_SEED)은 데모용 30일치 이력을 **집계 단계에서** 더한다.
DB에 가짜 snapshot·receipt를 만들지 않으므로 기록 위조가 아니고, 화면은
`demo_seeded` 로 표기한다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ..ledger import MIN_SAMPLE

#: 시드 파일의 구역 항목이 가질 수 있는 정수 필드.
_SEED_COUNT_KEYS = ("merged", "gated", "attested", "forced", "answerers")
_SEED_TOTAL_KEYS = ("merged", "gated", "attested", "forced", "waiting")
_MAX_SEED_ZONES = 200
_MAX_SEED_PRS = 50


class SeedError(ValueError):
    """시드 파일 형식 오류. 서버는 시드를 무시하지 않고 기동 시 실패해야 한다."""


@dataclass
class ZoneCounts:
    """한 구역의 원시 카운트. 서비스가 채우고 이 모듈이 접는다."""

    zone: str
    owner: str = ""
    merged: int = 0
    gated: int = 0
    attested: int = 0
    forced: int = 0
    answerers: int = 0
    prs: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class SeedZone:
    zone: str
    owner: str
    merged: int
    gated: int
    attested: int
    forced: int
    answerers: int
    prs: tuple[int, ...]


@dataclass(frozen=True)
class Seed:
    zones: tuple[SeedZone, ...]
    merged: int
    gated: int
    attested: int
    forced: int
    waiting: int

    @property
    def owners(self) -> dict[str, str]:
        return {z.zone: z.owner for z in self.zones if z.owner}


def _int(value: object, where: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SeedError(f"{where} must be an integer >= {minimum}")
    return value


def load_seed(path: str | Path) -> Seed:
    """시드 JSON을 읽고 검증한다. 모양이 틀리면 SeedError."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise SeedError(f"seed file could not be read: {err}") from None
    if not isinstance(data, dict):
        raise SeedError("seed root must be an object")
    raw_zones = data.get("zones", [])
    if not isinstance(raw_zones, list) or len(raw_zones) > _MAX_SEED_ZONES:
        raise SeedError("seed zones must be a list")
    zones: list[SeedZone] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_zones):
        where = f"seed zones[{index}]"
        if not isinstance(item, dict):
            raise SeedError(f"{where} must be an object")
        zone = item.get("zone")
        if not isinstance(zone, str) or not zone.strip() or zone in seen:
            raise SeedError(f"{where}.zone must be a unique non-empty string")
        seen.add(zone)
        owner = item.get("owner", "")
        if not isinstance(owner, str):
            raise SeedError(f"{where}.owner must be a string")
        counts = {key: _int(item.get(key, 0), f"{where}.{key}") for key in _SEED_COUNT_KEYS}
        if counts["attested"] + counts["forced"] > counts["gated"]:
            raise SeedError(f"{where}: attested + forced must not exceed gated")
        if counts["gated"] > counts["merged"]:
            raise SeedError(f"{where}: gated must not exceed merged")
        raw_prs = item.get("prs", [])
        if not isinstance(raw_prs, list) or len(raw_prs) > _MAX_SEED_PRS:
            raise SeedError(f"{where}.prs must be a short list")
        prs = tuple(_int(pr, f"{where}.prs[]", minimum=1) for pr in raw_prs)
        zones.append(SeedZone(zone=zone, owner=owner.strip(), prs=prs, **counts))
    raw_totals = data.get("totals", {})
    if not isinstance(raw_totals, dict):
        raise SeedError("seed totals must be an object")
    totals = {key: _int(raw_totals.get(key, 0), f"seed totals.{key}") for key in _SEED_TOTAL_KEYS}
    if totals["attested"] + totals["forced"] > totals["gated"] or totals["gated"] > totals["merged"]:
        raise SeedError("seed totals are inconsistent")
    return Seed(zones=tuple(zones), **totals)


def risk_rank(row: ZoneCounts) -> tuple[int, int, int, str]:
    """위험순 정렬 키. 게이트가 걸린 구역 중 답할 사람이 적고 예외가 많은 순.

    게이트가 걸리지 않은 구역은 머지가 있었던 것(50)을 없었던 것(99)보다 앞에 둔다.
    """
    tier = row.answerers if row.gated else (50 if row.merged else 99)
    return (tier, -row.forced, -row.merged, row.zone)


def actions_for(rows: Iterable[ZoneCounts], limit: int = 5) -> list[dict[str, object]]:
    """무엇을 하면 숫자가 올라가는지. 게이트가 걸린 구역에 대해서만 말한다."""
    out: list[dict[str, object]] = []
    for row in rows:
        if not row.gated:
            continue
        if row.forced:
            action = f"Verify {row.forced} exception{'s' if row.forced > 1 else ''} after the fact"
        elif row.answerers <= 1:
            action = "One more verified change in this zone"
        else:
            continue
        out.append(
            {
                "zone": row.zone,
                "action": action,
                "owner": row.owner,
                "from": row.answerers,
                "to": row.answerers + 1,
            }
        )
    return out[:limit]


def finalize(
    live: Mapping[str, ZoneCounts],
    *,
    all_zones: Iterable[str],
    owners: Mapping[str, str],
    totals: Mapping[str, int],
    seed: Seed | None,
    min_sample: int = MIN_SAMPLE,
) -> dict[str, object]:
    """구역 행과 요약을 화면 모양으로 접는다.

    ``all_zones`` (CODEOWNERS 전체)로 행을 시드해서, 머지가 없는 구역도
    "답할 사람 0명"으로 보이게 한다 — 그게 이 화면이 드러내려는 것이다.
    """
    rows: dict[str, ZoneCounts] = {}
    for zone in all_zones:
        rows[zone] = ZoneCounts(zone=zone)
    for zone, counts in live.items():
        rows[zone] = ZoneCounts(
            zone=zone,
            merged=counts.merged,
            gated=counts.gated,
            attested=counts.attested,
            forced=counts.forced,
            answerers=counts.answerers,
            prs=list(counts.prs),
        )
    summary = {key: int(totals.get(key, 0)) for key in _SEED_TOTAL_KEYS}

    if seed is not None:
        for sz in seed.zones:
            row = rows.setdefault(sz.zone, ZoneCounts(zone=sz.zone))
            row.merged += sz.merged
            row.gated += sz.gated
            row.attested += sz.attested
            row.forced += sz.forced
            # 시드의 "답할 사람"과 실측의 "답할 사람"은 서로 다른 사람으로 본다.
            row.answerers += sz.answerers
            row.prs = list(row.prs) + [pr for pr in sz.prs if pr not in row.prs]
        for key in _SEED_TOTAL_KEYS:
            summary[key] += getattr(seed, key)

    merged_owners = dict(seed.owners) if seed is not None else {}
    merged_owners.update({zone: owner for zone, owner in owners.items() if owner})
    for row in rows.values():
        row.owner = merged_owners.get(row.zone, "")

    ordered = sorted(rows.values(), key=risk_rank)
    zones_out = []
    for row in ordered:
        rate = None if row.gated < min_sample else row.attested / row.gated
        zones_out.append(
            {
                "zone": row.zone,
                "owner": row.owner,
                "merged": row.merged,
                "gated": row.gated,
                "attested": row.attested,
                "forced": row.forced,
                "answerers": row.answerers,
                "prs": sorted(row.prs, reverse=True),
                "rate": rate,
                "low_sample": 0 < row.gated < min_sample,
                "sample_state": (
                    "no_data" if row.gated == 0 else ("small_sample" if row.gated < min_sample else "measured")
                ),
            }
        )

    gated_total = summary["gated"]
    return {
        "merged_total": summary["merged"],
        "gated_total": gated_total,
        "attested_total": summary["attested"],
        "forced_total": summary["forced"],
        "waiting_total": summary["waiting"],
        "attested_rate": None if gated_total < min_sample else summary["attested"] / gated_total,
        "zero_answerer_zones": sum(1 for row in ordered if row.gated and row.answerers == 0),
        "zone_count": len(ordered),
        "zones": zones_out,
        "actions": actions_for(ordered),
        "demo_seeded": seed is not None,
    }
