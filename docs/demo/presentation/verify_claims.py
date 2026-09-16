"""발표 슬라이드의 수치를 이 저장소에서 재현한다.

발표 중 "그 점수 진짜냐"는 질문이 들어오면 그 자리에서 돌린다.

    pip install -e .
    PYTHONIOENCODING=utf-8 python docs/demo/presentation/verify_claims.py

모델을 부르지 않는다. risk.score()가 순수 함수라 네트워크 없이 같은 값이 나온다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CASES = Path(__file__).resolve().parent / "cases"

# `pip install -e .` 을 하지 않은 발표용 노트북에서도 그대로 돌게 한다.
sys.path.insert(0, str(REPO / "src"))

# pylint: disable=wrong-import-position
from lasthuman import risk                  # noqa: E402
from lasthuman.config import load_config    # noqa: E402
from lasthuman.diff import parse_hunks      # noqa: E402

#: (파일명, 슬라이드에 적은 주장, 발동해야 하는가)
EXPECTED = [
    ("demo-pr.diff", "데모 PR — 게이트가 켜진다", True),
    ("silent-high-risk.diff", "조용한 고위험 — 못 잡는다 (슬라이드 10)", False),
    ("low-risk-doc.diff", "저위험 문서 PR — 그냥 지나간다", False),
]


def run_case(config, path: Path) -> tuple[int, bool, tuple[str, ...]]:
    raw = path.read_text(encoding="utf-8")
    result = risk.score(parse_hunks(raw), config)
    return result.score, result.triggered, result.reasons


def repo_facts() -> list[tuple[str, str]]:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=str(REPO), check=True,
            capture_output=True, text=True, encoding="utf-8",
        ).stdout.strip()

    workflows = sorted((REPO / ".github" / "workflows").glob("*.yml"))
    return [
        ("커밋", git("rev-list", "--count", "HEAD")),
        ("머지된 PR", str(len(git("log", "--oneline", "--merges").splitlines()))),
        ("동작 중인 워크플로", str(len(workflows))),
    ]


def main() -> int:
    config = load_config(REPO)
    print(f"임계값 {config.threshold} · 중요 경로 {len(config.critical_paths)}개 · 패턴 {len(config.patterns)}개\n")

    failures = 0
    for name, claim, should_trigger in EXPECTED:
        path = CASES / name
        if not path.exists():
            print(f"  [없음] {path}")
            failures += 1
            continue
        score, triggered, reasons = run_case(config, path)
        ok = triggered is should_trigger
        failures += 0 if ok else 1
        mark = "OK " if ok else "불일치"
        print(f"[{mark}] {claim}")
        print(f"        {score} / {config.threshold} -> 발동 {triggered} (기대 {should_trigger})")
        for reason in reasons:
            print(f"        - {reason}")
        print()

    print("저장소 규모")
    for label, value in repo_facts():
        print(f"        {label}: {value}")

    if failures:
        print(f"\n{failures}건이 슬라이드의 주장과 다릅니다. 슬라이드를 고치거나 규칙을 확인하세요.")
    else:
        print("\n슬라이드의 주장이 현재 규칙과 모두 일치합니다.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
