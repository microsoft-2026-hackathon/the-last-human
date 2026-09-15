"""구조 질문이 딛고 설 사실을 저장소에서 뽑는다.

hunk만 보면 "이 줄이 무엇을 하는가"까지밖에 못 묻는다. 정작 에이전트 코드에서
사고가 나는 지점은 **이 변경이 누구에게 전파되는가**, 그리고 **이 변경이 무엇 위에
서 있는가**다. 두 방향을 실제로 찾아 두면 세 가지가 가능해진다.

1. 구조를 묻는 질문을 만들 수 있다 — 호출자(들어오는 방향)
2. 답이 diff 밖에 있는 질문을 만들 수 있다 — 피호출자와 그 파일의 상수(나가는 방향).
   "이 함수가 부르는 전송 계층이 이미 3번 재시도한다"는 사실은 hunk 어디에도 없다
3. 객관식 오답 보기를 실재하는 파일 이름으로 채울 수 있다

오답이 실재하지 않는 이름이면 답을 몰라도 소거법으로 걸러진다. 그래서
보기는 반드시 이 모듈이 찾아낸 실제 경로에서 나와야 한다.

파이썬만 본다. 시연 워크로드가 파이썬이고, 다른 언어는 후속 과제다.
"""

from __future__ import annotations

import ast
import functools
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .models import Hunk

SKIP_DIRS = {".git", ".github", ".work", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
MAX_FILES = 400
#: 나가는 방향은 상한을 둔다. 전부 넘기면 프롬프트가 저장소 요약이 된다.
MAX_CALLEES = 6
MAX_CONSTANTS = 6
_CONSTANT_VALUE_CHARS = 60


@dataclass(frozen=True)
class SymbolUse:
    """변경된 심볼 하나와 그것을 부르는 곳."""

    symbol: str
    defined_in: str
    used_in: tuple[str, ...]


@dataclass(frozen=True)
class Callee:
    """변경된 코드가 부르는, 다른 파일에 정의된 심볼 하나."""

    symbol: str
    defined_in: str
    line: int
    #: 정의 파일의 대문자 모듈 상수. ``MAX_ATTEMPTS = 3`` 형태의 원문.
    constants: tuple[str, ...]
    #: 그 심볼이 실제로 무엇을 하는지 — docstring 첫 줄과, 본문에서 모듈 상수를 쓰는 줄.
    #: "post_json 이 MAX_ATTEMPTS 까지 다시 보낸다"는 사실은 상수 목록이 아니라 여기에 있다.
    excerpt: tuple[str, ...] = ()

    @property
    def anchor(self) -> str:
        return f"{self.defined_in}:L{self.line}"


@dataclass(frozen=True)
class StructureContext:
    changed_files: tuple[str, ...]
    #: 변경 파일 -> 그 파일을 import 하는 파일들
    importers: dict[str, tuple[str, ...]]
    symbols: tuple[SymbolUse, ...]
    #: 저장소에 실재하는 다른 경로. 오답 보기 재료로 쓴다.
    sibling_files: tuple[str, ...]
    #: 변경된 코드가 부르는 다른 파일의 심볼. 답이 diff 밖에 있는 질문의 재료.
    callees: tuple[Callee, ...] = ()

    def is_empty(self) -> bool:
        return not self.symbols and not self.callees and not any(self.importers.values())

    @property
    def evidence_files(self) -> tuple[str, ...]:
        """질문이 근거로 삼을 수 있는 실재 경로. 변경 파일, 피호출자 파일, 형제 파일."""
        seen: dict[str, None] = {}
        for path in (*self.changed_files, *(c.defined_in for c in self.callees), *self.sibling_files):
            seen.setdefault(path, None)
        return tuple(seen)

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
        for c in self.callees:
            lines.append(f"- 변경된 코드가 호출하는 다른 파일의 심볼 {c.symbol} (정의: {c.anchor})")
            for line in c.excerpt:
                lines.append(f"  {c.symbol}: {line}")
            if c.constants:
                lines.append(f"  그 파일의 상수: {', '.join(c.constants)}")
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


def _module_constants(tree: ast.AST) -> tuple[str, ...]:
    """대문자 모듈 상수를 ``NAME = 값`` 원문으로. 값은 unparse 해서 자른다."""
    out: list[str] = []
    for node in getattr(tree, "body", ()):
        target: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target = node.target
        if not isinstance(target, ast.Name) or not target.id.isupper():
            continue
        value = getattr(node, "value", None)
        if value is None:
            continue
        rendered = ast.unparse(value)
        if len(rendered) > _CONSTANT_VALUE_CHARS:
            rendered = rendered[: _CONSTANT_VALUE_CHARS - 1] + "…"
        out.append(f"{target.id} = {rendered}")
        if len(out) >= MAX_CONSTANTS:
            break
    return tuple(out)


MAX_EXCERPT_LINES = 4


def _top_level_defs(
    tree: ast.AST, source_lines: Sequence[str] = (), constant_names: frozenset[str] = frozenset()
) -> dict[str, tuple[int, set[str], tuple[str, ...]]]:
    """최상위 함수/클래스 이름 -> (정의 줄, 본문이 호출하는 이름들, 동작 발췌).

    발췌는 docstring 첫 문장과, 본문에서 모듈 상수를 쓰는 줄이다. 호출된 것이
    "무엇을 몇 번 하는지"를 질문 생성기가 알 수 있는 최소한의 근거.
    """
    out: dict[str, tuple[int, set[str], tuple[str, ...]]] = {}
    for node in getattr(tree, "body", ()):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        excerpt: list[str] = []
        doc = ast.get_docstring(node)
        if doc:
            first = doc.strip().split("\n\n", 1)[0].replace("\n", " ").strip()
            excerpt.append(first[:160])
        if source_lines and constant_names:
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            for lineno in range(node.lineno, min(end, len(source_lines)) + 1):
                text = source_lines[lineno - 1].strip()
                if any(name in text for name in constant_names) and not text.startswith(("#", '"""')):
                    excerpt.append(text[:160])
                if len(excerpt) >= MAX_EXCERPT_LINES:
                    break
        out[node.name] = (node.lineno, _called_names(node), tuple(excerpt[:MAX_EXCERPT_LINES]))
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
    defs_by_file: dict[str, dict[str, tuple[int, set[str], tuple[str, ...]]]] = {}
    constants_by_file: dict[str, tuple[str, ...]] = {}
    for p in _iter_py(root):
        rel = p.relative_to(root).as_posix()
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            continue
        imports_by_file[rel] = _imported_modules(tree)
        calls_by_file[rel] = _called_names(tree)
        constants_by_file[rel] = _module_constants(tree)
        names = frozenset(c.split(" = ", 1)[0] for c in constants_by_file[rel])
        defs_by_file[rel] = _top_level_defs(tree, text.splitlines(), names)

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

    callees = _resolve_callees(changed, symbols, imports_by_file, defs_by_file, constants_by_file)

    return StructureContext(
        changed_files=tuple(sorted(changed)),
        importers=importers,
        symbols=tuple(symbols[:max_symbols]),
        sibling_files=siblings,
        callees=callees,
    )


def _is_test_path(rel: str) -> bool:
    parts = rel.split("/")
    return any(part in ("tests", "test") for part in parts[:-1]) or parts[-1].startswith("test_")


def _shared_prefix(a: str, b: str) -> int:
    """두 경로가 앞에서부터 공유하는 디렉터리 수."""
    pa, pb = a.split("/")[:-1], b.split("/")[:-1]
    n = 0
    for x, y in zip(pa, pb):
        if x != y:
            break
        n += 1
    return n


def _shared_prefix_desc(rel: str, candidate: str) -> int:
    return -_shared_prefix(candidate, rel)


def _resolve_callees(
    changed: dict[str, set[int]],
    symbols: Sequence[SymbolUse],
    imports_by_file: dict[str, set[str]],
    defs_by_file: dict[str, dict[str, tuple[int, set[str], tuple[str, ...]]]],
    constants_by_file: dict[str, tuple[str, ...]],
) -> tuple[Callee, ...]:
    """변경된 심볼의 본문이 부르는 이름을 다른 파일의 최상위 정의에 맞춘다.

    같은 파일의 정의를 거치는 한 홉은 따라간다 — ``ensure_fresh``가 같은 파일의
    ``refresh``를 부르고 ``refresh``가 다른 파일의 ``post_json``을 부르면, 질문이
    물어야 할 사실은 ``post_json`` 쪽에 있다. 이름이 여러 파일에 정의돼 있으면
    변경 파일이 import 하는 모듈의 것을 고르고, 그래도 갈리면 지어내지 않고 건너뛴다.
    """
    out: list[Callee] = []
    seen: set[tuple[str, str]] = set()
    for rel in sorted(changed):
        # 테스트가 부르는 것은 "변경이 무엇 위에 서 있는가"가 아니다. 테스트 파일은 출처에서 뺀다.
        if _is_test_path(rel):
            continue
        local_defs = defs_by_file.get(rel, {})
        changed_names = [s.symbol for s in symbols if s.defined_in == rel]
        wanted: dict[str, None] = {}
        for name in changed_names:
            for called in sorted(local_defs.get(name, (0, set(), ()))[1]):
                wanted.setdefault(called, None)
                # 같은 파일 안 한 홉.
                if called in local_defs and called not in changed_names:
                    for inner in sorted(local_defs[called][1]):
                        wanted.setdefault(inner, None)
        imported = imports_by_file.get(rel, set())
        for called in wanted:
            if called in local_defs:
                continue
            # 변경 파일이 import 한 모듈의 정의만 본다. 그렇지 않으면 ``asyncio.run`` 의
            # ``run`` 이 저장소 어딘가의 다른 ``run`` 에 붙는다.
            candidates = [
                f for f, defs in defs_by_file.items()
                if f != rel and called in defs and "test" not in f and (_module_tails(f) & imported)
            ]
            if not candidates:
                continue
            if len(candidates) > 1:
                # 같은 이름이 여러 곳에 있으면 변경 파일과 경로를 가장 길게 공유하는 쪽.
                ranked = sorted(candidates, key=functools.partial(_shared_prefix_desc, rel))
                if _shared_prefix(ranked[0], rel) == _shared_prefix(ranked[1], rel):
                    continue
                candidates = ranked[:1]
            target = candidates[0]
            key = (called, target)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                Callee(
                    symbol=called,
                    defined_in=target,
                    line=defs_by_file[target][called][0],
                    constants=constants_by_file.get(target, ()),
                    excerpt=defs_by_file[target][called][2],
                )
            )
            if len(out) >= MAX_CALLEES:
                return tuple(out)
    return tuple(out)
