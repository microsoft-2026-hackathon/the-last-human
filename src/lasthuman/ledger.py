"""이해 커버리지 원장 — 저장소 단위 집계.

무엇을 집계하고 무엇을 집계하지 않는지가 이 파일의 전부다.

집계한다
  구역(모듈 경로) 단위 인증 비율, 강제 머지 건수, 답할 수 있는 사람의 **수**

집계하지 않는다
  개인 점수, 등급, 순위, 사람 이름이 붙은 성과 지표.
  만드는 순간 일주일 안에 인사 지표가 되고, 그때부터 사람들은
  배지를 위해 최적화한다. 이 선을 넘는 필드를 여기에 추가하지 말 것.

담당자 이름은 CODEOWNERS에 이미 공개된 사실이라 그대로 보여준다.
인증한 사람의 이름은 "몇 명"으로만 환원해 내보낸다.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path as Path_

#: 표본이 이보다 적으면 비율을 숫자로 내세우지 않는다.
#: 3건 중 2건을 67%로 적으면 없는 신호를 읽게 된다.
MIN_SAMPLE = 5


@dataclass(frozen=True)
class MergedPr:
    """집계 입력. GitHub에서 뽑아 온 사실만 담는다."""

    number: int
    merged_at: str
    #: 이 PR이 건드린 경로들
    files: tuple[str, ...]
    #: 게이트가 발동했는가
    triggered: bool
    #: 인증을 거쳐 머지됐는가
    attested: bool
    #: 인증한 사람 수 (이름이 아니라 수)
    attester_count: int = 0
    #: 게이트가 발동했는데 인증 없이 머지됐는가
    forced: bool = False
    changed_lines: int = 0


@dataclass
class ZoneStat:
    zone: str
    owner: str = "—"
    merged: int = 0
    gated: int = 0
    attested: int = 0
    forced: int = 0
    #: 이 구역에 답할 수 있는 사람 수. 이름이 아니라 수다.
    answerers: int = 0
    #: 표본이 적어 비율을 신뢰할 수 없다
    low_sample: bool = False

    @property
    def rate(self) -> float | None:
        if self.gated < MIN_SAMPLE:
            return None
        return self.attested / self.gated if self.gated else None

    @property
    def status(self) -> str:
        """구역의 위험 상태. 사람이 아니라 구역을 평가한다."""
        if self.gated == 0:
            return "위험도 낮음"
        if self.answerers == 0:
            return "0명"
        if self.answerers == 1:
            return "1명"
        return f"{self.answerers}명"

    @property
    def risk_rank(self) -> tuple[int, int, int]:
        """위험순 정렬 키. 답할 사람이 없고 강제 머지가 많을수록 앞."""
        return (self.answerers if self.gated else 99, -self.forced, -self.merged)


@dataclass
class Summary:
    repo: str
    generated_at: str
    window_days: int
    merged_total: int = 0
    gated_total: int = 0
    attested_total: int = 0
    forced_total: int = 0
    waiting_total: int = 0
    closed_unattested: int = 0
    attested_lines: int = 0
    gated_lines: int = 0
    zones: list[ZoneStat] = field(default_factory=list)

    @property
    def attested_rate(self) -> float | None:
        if self.gated_total < MIN_SAMPLE:
            return None
        return self.attested_total / self.gated_total if self.gated_total else None

    @property
    def covered_zones(self) -> int:
        """답할 사람이 2명 이상인 구역 수. 버스 팩터가 1이면 커버된 게 아니다."""
        return sum(1 for z in self.zones if z.gated and z.answerers >= 2)

    @property
    def gated_zones(self) -> int:
        return sum(1 for z in self.zones if z.gated)

    @property
    def thin_zones(self) -> int:
        return sum(1 for z in self.zones if z.gated and z.answerers <= 1)

    def to_json(self) -> dict:
        d = asdict(self)
        d["attestedRate"] = self.attested_rate
        d["coveredZones"] = self.covered_zones
        d["gatedZones"] = self.gated_zones
        d["thinZones"] = self.thin_zones
        d["minSample"] = MIN_SAMPLE
        for z, raw in zip(self.zones, d["zones"], strict=True):
            raw["rate"] = z.rate
            raw["status"] = z.status
        return d


def zone_of(path: str, zones: Sequence[str]) -> str | None:
    """경로가 속한 구역을 고른다. 가장 구체적인 구역이 이긴다."""
    best: str | None = None
    for z in zones:
        pattern = z if z.endswith("/") else z + "/"
        if path.startswith(pattern.lstrip("/")) or fnmatch.fnmatch(path, z.strip("/") + "/*"):
            if best is None or len(z) > len(best):
                best = z
    return best


def aggregate(
    repo: str,
    prs: Iterable[MergedPr],
    zones: Sequence[str],
    *,
    owners: dict[str, str] | None = None,
    answerers: dict[str, int] | None = None,
    window_days: int = 30,
    waiting: int = 0,
) -> Summary:
    """머지된 PR 목록을 구역 단위로 접는다."""
    owners = owners or {}
    answerers = answerers or {}
    stats = {
        z: ZoneStat(zone=z, owner=owners.get(z, "—"), answerers=answerers.get(z, 0))
        for z in zones
    }
    summary = Summary(
        repo=repo,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        window_days=window_days,
        waiting_total=waiting,
    )

    for pr in prs:
        summary.merged_total += 1
        if pr.triggered:
            summary.gated_total += 1
            summary.gated_lines += pr.changed_lines
            if pr.attested:
                summary.attested_total += 1
                summary.attested_lines += pr.changed_lines
            elif pr.forced:
                summary.forced_total += 1

        touched = {z for f in pr.files if (z := zone_of(f, zones))}
        for z in touched:
            st = stats[z]
            st.merged += 1
            if pr.triggered:
                st.gated += 1
                if pr.attested:
                    st.attested += 1
                elif pr.forced:
                    st.forced += 1

    for st in stats.values():
        st.low_sample = 0 < st.gated < MIN_SAMPLE

    summary.zones = sorted(stats.values(), key=lambda z: z.risk_rank)
    return summary


def actions_for(summary: Summary, limit: int = 5) -> list[dict]:
    """무엇을 하면 숫자가 올라가는지. 지표만 보여주면 아무도 움직이지 않는다."""
    out: list[dict] = []
    for z in summary.zones:
        if not z.gated:
            continue
        if z.forced:
            out.append(
                {
                    "zone": z.zone,
                    "action": f"강제 머지 {z.forced}건 사후 인증",
                    "target": z.owner,
                    "effect": f"{z.answerers}명 → {z.answerers + 1}명",
                }
            )
        elif z.answerers <= 1:
            out.append(
                {
                    "zone": z.zone,
                    "action": "이 구역 변경에 인증 1건 추가",
                    "target": z.owner,
                    "effect": f"{z.answerers}명 → {z.answerers + 1}명",
                }
            )
    return out[:limit]


# --- 구역 정의 ---------------------------------------------------------------
# 구역을 새로 발명하지 않는다. 조직에 이미 있는 선언을 읽는다.
# CODEOWNERS는 "이 경로는 누구 것인가"가 이미 합의되어 적혀 있는 유일한 파일이고,
# 대시보드가 묻는 것은 "그 선언된 담당 중 실제로 답할 수 있는 사람이 있는가"다.
# 둘을 나란히 놓는 순간 격차가 그대로 보인다.

CODEOWNERS_PATHS = (
    ".github/CODEOWNERS",
    "CODEOWNERS",
    "docs/CODEOWNERS",
)


def parse_codeowners(text: str) -> list[tuple[str, str]]:
    """(경로 패턴, 담당) 목록. 마지막에 일치하는 규칙이 이기는 것이 CODEOWNERS 규칙이다."""
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        pattern, owners = parts[0], parts[1:]
        out.append((pattern.lstrip("/"), " ".join(owners)))
    return out


def zones_from_repo(repo_root: str | Path_, fallback: Sequence[str] = ()) -> tuple[list[str], dict[str, str]]:
    """CODEOWNERS에서 구역과 담당을 읽는다. 없으면 fallback을 쓴다."""
    root = Path_(repo_root)
    for candidate in CODEOWNERS_PATHS:
        f = root / candidate
        if not f.exists():
            continue
        rules = parse_codeowners(f.read_text(encoding="utf-8", errors="replace"))
        if not rules:
            continue
        zones = [p for p, _ in rules]
        owners = {p: o for p, o in rules}
        return zones, owners
    return list(fallback), {}
