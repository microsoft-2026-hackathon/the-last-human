"""구조 질문이 딛고 설 사실을 저장소에서 뽑는다.

hunk만 보면 "이 줄이 무엇을 하는가"까지밖에 못 묻는다. 정작 에이전트 코드에서
사고가 나는 지점은 **이 변경이 누구에게 전파되는가**다. 호출자와 임포터를
실제로 찾아 두면 두 가지가 가능해진다.

1. 구조를 묻는 질문을 만들 수 있다
2. 객관식 오답 보기를 실재하는 파일 이름으로 채울 수 있다

오답이 실재하지 않는 이름이면 답을 몰라도 소거법으로 걸러진다. 그래서
보기는 반드시 이 모듈이 찾아낸 실제 경로에서 나와야 한다.

파이썬만 본다. 시연 워크로드가 파이썬이고, 다른 언어는 후속 과제다.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .models import Hunk

SKIP_DIRS = {".git", ".github", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
MAX_FILES = 400


@dataclass(frozen=True)
class SymbolUse:
    """변경된 심볼 하나와 그것을 부르는 곳."""

    symbol: str
    defined_in: str
    used_in: tuple[str, ...]


@dataclass(frozen=True)
class StructureContext:
    changed_files: tuple[str, ...]
    #: 변경 파일 -> 그 파일을 import 하는 파일들
    importers: dict[str, tuple[str, ...]]
    symbols: tuple[SymbolUse, ...]
    #: 저장소에 실재하는 다른 경로. 오답 보기 재료로 쓴다.
    sibling_files: tuple[str, ...]

    def is_empty(self) -> bool:
        return not self.symbols and not any(self.importers.values())

    def as_prompt(self) -> str:
        lines: list[str] = []
        for f in self.changed_files:
            imps = self.importers.get(f, ())
            lines.append(f"- 변경 파일 {f}")
            lines.append(f"  이 파일을 import 하는 곳: {', '.join(imps) if imps else '없음'}")
        for s in self.symbols:
            used = ", ".join(s.used_in) if s.used_in else "없음"
            lines.append(f"- 변경된 심볼 {s.symbol} (정의: {s.defined_in})")
            lines.append(f"  호출하는 곳: {used}")
        if self.sibling_files:
            lines.append(f"- 저장소의 다른 실제 경로: {', '.join(self.sibling_files)}")
        return "\n".join(lines)


def _iter_py(root: Path) -> Iterable[Path]:
    count = 0
    for p in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        count += 1
        if count > MAX_FILES:
            return
        yield p


def _module_tails(rel: str) -> set[str]:
    """`sample-app/app/auth/token.py` -> {app.auth.token, auth.token, token, ...}"""
    parts = rel[:-3].split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return {".".join(parts[i:]) for i in range(len(parts))} | set(parts[-1:])


def _imported_modules(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            # `from app.auth import token` 도 token 모듈 사용이다.
            out.update(f"{node.module}.{a.name}" for a in node.names)
    return out


def _called_names(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                out.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                out.add(fn.attr)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
    return out


def _changed_symbols(path: Path, rel: str, lines: set[int]) -> list[tuple[str, str]]:
    """hunk 줄 범위와 겹치는 최상위 함수/클래스 이름을 고른다."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return []
    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        end = getattr(node, "end_lineno", node.lineno) or node.lineno
        if any(node.lineno <= ln <= end for ln in lines):
            found.append((node.name, rel))
    return found


def build_context(
    repo: str | Path, hunks: Sequence[Hunk], *, max_symbols: int = 6
) -> StructureContext:
    """변경된 파이썬 심볼의 호출자와 임포터를 찾는다."""
    root = Path(repo)
    changed: dict[str, set[int]] = {}
    for h in hunks:
        if not h.file.endswith(".py"):
            continue
        # hunk 첫 줄만 보면 그 안에서 실제로 바뀐 함수를 놓친다.
        # 변경 후 파일 기준 span 전체를 잡는다 — 삭제된 줄은 새 파일에 없다.
        span = sum(1 for line in h.body.splitlines() if not line.startswith("-")) or 1
        changed.setdefault(h.file, set()).update(range(h.new_start, h.new_start + span))

    if not changed:
        return StructureContext((), {}, (), ())

    # 저장소 전체를 한 번만 훑는다.
    imports_by_file: dict[str, set[str]] = {}
    calls_by_file: dict[str, set[str]] = {}
    for p in _iter_py(root):
        rel = p.relative_to(root).as_posix()
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        imports_by_file[rel] = _imported_modules(tree)
        calls_by_file[rel] = _called_names(tree)

    importers: dict[str, tuple[str, ...]] = {}
    symbols: list[SymbolUse] = []
    for rel, lines in sorted(changed.items()):
        tails = _module_tails(rel)
        importers[rel] = tuple(
            f for f, mods in sorted(imports_by_file.items()) if f != rel and (mods & tails)
        )
        for name, defined_in in _changed_symbols(root / rel, rel, lines):
            used = tuple(
                f for f, names in sorted(calls_by_file.items()) if f != rel and name in names
            )
            symbols.append(SymbolUse(symbol=name, defined_in=defined_in, used_in=used))

    # 오답 보기 재료. 변경되지 않았지만 실재하는 경로여야 소거법이 통하지 않는다.
    siblings = tuple(
        f for f in sorted(imports_by_file) if f not in changed and "test" not in f
    )[:8]

    return StructureContext(
        changed_files=tuple(sorted(changed)),
        importers=importers,
        symbols=tuple(symbols[:max_symbols]),
        sibling_files=siblings,
    )
