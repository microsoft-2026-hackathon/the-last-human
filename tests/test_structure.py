from pathlib import Path

from lasthuman.models import Hunk
from lasthuman.structure import MAX_CALLEES, build_context


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _hunk(file: str, start: int, added: tuple[str, ...]) -> Hunk:
    body = "\n".join(added)
    return Hunk(
        file=file, new_start=start, old_start=start, anchor=f"{file}:L{start}",
        added=added, removed=(), body=body, file_status="modified",
    )


def _repo(tmp_path: Path) -> Path:
    _write(tmp_path, "app/__init__.py", "")
    _write(tmp_path, "app/auth/__init__.py", "")
    _write(
        tmp_path,
        "app/http_client.py",
        "import json\n"
        "TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})\n"
        "MAX_ATTEMPTS = 3\n"
        "RETRY_BACKOFF_SEC: float = 0.2\n"
        "_private = 1\n"
        "lower = 2\n"
        "class HttpError(Exception):\n    pass\n"
        "async def post_json(transport, url, payload):\n    return json.loads('{}')\n",
    )
    _write(
        tmp_path,
        "app/auth/token.py",
        "import time\n"
        "from ..http_client import post_json\n"
        "CLOCK_SKEW_SEC = 60.0\n"
        "def is_expired(token, now=None):\n    return True\n"
        "async def refresh(transport, token):\n    return await post_json(transport, 'u', {})\n"
        "async def ensure_fresh(transport, token):\n"
        "    if not is_expired(token):\n        return token\n"
        "    for attempt in range(3):\n        return await refresh(transport, token)\n",
    )
    _write(
        tmp_path,
        "app/auth/session.py",
        "from .token import ensure_fresh\nasync def current(t, k):\n    return await ensure_fresh(t, k)\n",
    )
    _write(tmp_path, "tests/test_token.py", "from app.auth.token import ensure_fresh\ndef post_json():\n    pass\n")
    return tmp_path


def test_callees_follow_one_same_file_hop_and_carry_module_constants(tmp_path: Path):
    root = _repo(tmp_path)
    ctx = build_context(root, [_hunk("app/auth/token.py", 9, ("+    for attempt in range(3):",))])

    assert [c.symbol for c in ctx.callees] == ["post_json"]
    callee = ctx.callees[0]
    assert callee.defined_in == "app/http_client.py"
    assert callee.line == 9
    assert callee.anchor == "app/http_client.py:L9"
    # 대문자 모듈 상수만, 선언 순서대로. 값은 unparse 원문.
    assert callee.constants == (
        "TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})",
        "MAX_ATTEMPTS = 3",
        "RETRY_BACKOFF_SEC = 0.2",
    )
    # 같은 파일 심볼(is_expired, refresh)과 표준 라이브러리(time)는 피호출자가 아니다.
    assert ctx.is_empty() is False
    prompt = ctx.as_prompt()
    assert "호출하는 다른 파일의 심볼 post_json (정의: app/http_client.py:L9)" in prompt
    assert "MAX_ATTEMPTS = 3" in prompt
    # 근거 파일 후보: 변경 파일 → 피호출자 파일 → 형제 파일, 중복 없음, 테스트 제외.
    assert ctx.evidence_files[:2] == ("app/auth/token.py", "app/http_client.py")
    assert "tests/test_token.py" not in ctx.evidence_files
    assert len(ctx.evidence_files) == len(set(ctx.evidence_files))


def test_callees_skip_ambiguous_definitions_instead_of_guessing(tmp_path: Path):
    root = _repo(tmp_path)
    # 같은 이름이 import 되지 않은 두 파일에 있고 경로 공유 길이도 같으면 지어내지 않는다.
    _write(root, "app/db/client.py", "def connect():\n    pass\n")
    _write(root, "app/orders/repository.py", "def connect():\n    pass\n")
    _write(root, "app/auth/link.py", "def use():\n    return connect()\n")
    ctx = build_context(root, [_hunk("app/auth/link.py", 1, ("+def use():",))])
    assert ctx.callees == ()


def test_callees_are_capped_and_deduplicated(tmp_path: Path):
    root = tmp_path
    _write(root, "pkg/__init__.py", "")
    helpers = "\n".join(f"def h{i}():\n    pass" for i in range(10))
    _write(root, "pkg/helpers.py", helpers + "\n")
    body = "\n".join(f"    h{i}()" for i in range(10))
    _write(root, "pkg/main.py", "from .helpers import *\ndef run():\n" + body + "\n    h0()\n")
    ctx = build_context(root, [_hunk("pkg/main.py", 2, ("+def run():",))])
    assert len(ctx.callees) == MAX_CALLEES
    assert len({c.symbol for c in ctx.callees}) == MAX_CALLEES


def test_non_python_changes_yield_empty_context(tmp_path: Path):
    ctx = build_context(tmp_path, [_hunk("README.md", 1, ("+hello",))])
    assert ctx.is_empty() and ctx.callees == () and ctx.evidence_files == ()
