"""``.lasthuman.yml`` 로더.

설정은 저장소 루트 한 곳에서만 읽는다. 경로별로 다른 설정을 허용하면
"어느 규칙으로 막혔는지"를 개발자가 추적할 수 없게 된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

Mode = Literal["internal", "oss"]

DEFAULT_CONFIG_NAME = ".lasthuman.yml"


@dataclass(frozen=True)
class LinesChanged:
    per100: int = 10
    max: int = 30


@dataclass(frozen=True)
class Config:
    mode: Mode = "internal"
    threshold: int = 40
    critical_paths: tuple[tuple[str, int], ...] = ()
    lines_changed: LinesChanged = field(default_factory=LinesChanged)
    patterns: tuple[tuple[str, int], ...] = ()
    tests_removed: int = 25
    agent_hint: int = 10
    external_contributor: int = 40
    zero_coverage: int = 999
    #: 질문 생성에 넘길 hunk 상한. 넘기면 컨텍스트가 넘치고 초점이 흐려진다.
    max_hunks: int = 5
    #: 시연 편의용. 이 라벨이 붙으면 외부 기여자로 간주한다.
    external_contributor_label: str = "external-contributor"


def _pairs(raw: Any) -> tuple[tuple[str, int], ...]:
    """``- "src/auth/**": 30`` 형태의 단일 키 맵 목록을 (키, 가중치)로 편다."""
    out: list[tuple[str, int]] = []
    if isinstance(raw, dict):
        for k, v in raw.items():
            out.append((str(k), int(v)))
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                for k, v in item.items():
                    out.append((str(k), int(v)))
    return tuple(out)


def load_config(repo: str | Path, name: str = DEFAULT_CONFIG_NAME) -> Config:
    path = Path(repo) / name
    if not path.exists():
        return Config()
    return parse_config(path.read_text(encoding="utf-8"))


def parse_config(text: str) -> Config:
    data = yaml.safe_load(text) or {}
    signals = data.get("signals") or {}
    lc = signals.get("linesChanged") or {}
    return Config(
        mode=data.get("mode", "internal"),
        threshold=int(data.get("threshold", 40)),
        critical_paths=_pairs(signals.get("criticalPaths")),
        lines_changed=LinesChanged(
            per100=int(lc.get("per100", 10)), max=int(lc.get("max", 30))
        ),
        patterns=_pairs(signals.get("patterns")),
        tests_removed=int(signals.get("testsRemoved", 25)),
        agent_hint=int(signals.get("agentHint", 10)),
        external_contributor=int(signals.get("externalContributor", 40)),
        zero_coverage=int(signals.get("zeroCoverage", 999)),
        max_hunks=int(data.get("maxHunks", 5)),
    )


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """gitignore에 가까운 glob 의미로 옮긴다.

    ``*``는 경로 구분자를 넘지 않고 ``**``만 넘는다.
    ``fnmatch``는 ``*``도 ``/``를 넘어서 ``src/auth/*``가 하위 전체를 잡아버린다.
    """
    i, out = 0, ["(?s)"]
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
                continue
            if pattern[i : i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matches(pattern: str, path: str) -> bool:
    return glob_to_regex(pattern).match(path) is not None
