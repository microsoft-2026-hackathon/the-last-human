"""diff 파싱 — base와의 차이를 hunk로 쪼개고 고정 앵커를 만든다.

앵커가 흔들리면 질문과 답의 대조가 불가능해진다.
앵커를 만드는 곳은 :func:`make_anchor` 하나뿐이다.

unified diff를 직접 읽는다. 외부 파서를 쓰지 않는 이유는 앵커 규칙이
제품의 계약이라 파서 동작에 끌려다니면 안 되기 때문이다.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .models import DiffResult, FileChange, FileStatus, Hunk

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

_C_ESCAPES = {
    "a": "\a", "b": "\b", "f": "\f", "n": "\n",
    "r": "\r", "t": "\t", "v": "\v", "\\": "\\", '"': '"',
}


def make_anchor(file: str, new_start: int) -> str:
    """앵커 문자열을 만드는 단 하나의 지점."""
    return f"{file}:L{new_start}"


def parse_anchor(anchor: str) -> tuple[str, int] | None:
    """앵커를 되돌려 읽는다. 모델이 지어낸 앵커를 검증할 때 쓴다."""
    m = re.fullmatch(r"(.+):L(\d+)", anchor)
    if not m:
        return None
    return m.group(1), int(m.group(2))


def _unquote(path: str) -> str:
    """git이 C 스타일로 따옴표 친 경로를 되돌린다. 한글 경로에서 실제로 나온다."""
    if not (path.startswith('"') and path.endswith('"')):
        return path
    raw = path[1:-1]
    out = bytearray()
    i = 0
    while i < len(raw):
        c = raw[i]
        if c != "\\":
            out.extend(c.encode("utf-8"))
            i += 1
            continue
        nxt = raw[i + 1]
        if nxt in _C_ESCAPES:
            out.extend(_C_ESCAPES[nxt].encode("utf-8"))
            i += 2
        elif nxt.isdigit():  # \ooo 8진 바이트
            out.append(int(raw[i + 1 : i + 4], 8))
            i += 4
        else:
            out.extend(nxt.encode("utf-8"))
            i += 2
    return out.decode("utf-8", errors="replace")


def read_raw_diff(repo: str | Path, base_ref: str, head_ref: str, context: int = 3) -> str:
    """``base...head`` 3점 표기를 쓴다.

    머지 베이스 기준이라 base가 앞서 나가도 이 PR이 실제로 건드린 것만 잡힌다.
    ``--no-prefix``로 ``a/``·``b/`` 접두사를 없애 경로 파싱의 모호함을 줄인다.
    """
    return subprocess.run(
        [
            "git", "diff", f"--unified={context}", "--no-color", "--no-prefix",
            "--find-renames", f"{base_ref}...{head_ref}",
        ],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def parse_hunks(raw: str) -> DiffResult:
    """원문 diff를 hunk 배열로 쪼갠다. git 실행 없이 테스트할 수 있도록 분리해 둔다."""
    files: list[FileChange] = []
    hunks: list[Hunk] = []

    cur: dict | None = None
    body: list[str] | None = None
    added: list[str] | None = None
    removed: list[str] | None = None
    new_start = old_start = 0

    def close_hunk() -> None:
        nonlocal body, added, removed
        if cur is None or body is None:
            return
        # 순수 문맥 hunk는 질문 대상이 아니다.
        if added or removed:
            hunks.append(
                Hunk(
                    file=cur["file"],
                    new_start=new_start,
                    old_start=old_start,
                    anchor=make_anchor(cur["file"], new_start),
                    added=tuple(added or ()),
                    removed=tuple(removed or ()),
                    body="\n".join(body),
                    file_status=cur["status"],
                )
            )
            cur["hunk_count"] += 1
        cur["additions"] += len(added or ())
        cur["deletions"] += len(removed or ())
        body = added = removed = None

    def close_file() -> None:
        close_hunk()
        if cur is None:
            return
        files.append(
            FileChange(
                file=cur["file"],
                status=cur["status"],
                binary=cur["binary"],
                additions=cur["additions"],
                deletions=cur["deletions"],
                hunk_count=cur["hunk_count"],
                previous_file=cur["previous_file"],
            )
        )

    for line in raw.splitlines():
        if line.startswith("diff --git "):
            close_file()
            cur = {
                "file": "", "status": "modified", "binary": False,
                "additions": 0, "deletions": 0, "hunk_count": 0, "previous_file": None,
            }
            body = added = removed = None
            continue

        if cur is None:
            continue

        if line.startswith("new file mode"):
            cur["status"] = "added"
        elif line.startswith("deleted file mode"):
            cur["status"] = "deleted"
        elif line.startswith("rename from "):
            cur["status"] = "renamed"
            cur["previous_file"] = _unquote(line[len("rename from ") :])
        elif line.startswith("rename to "):
            cur["status"] = "renamed"
            cur["file"] = _unquote(line[len("rename to ") :])
        elif line.startswith("Binary files "):
            cur["binary"] = True
        elif line.startswith("--- "):
            path = _unquote(line[4:])
            if path != "/dev/null" and not cur["file"]:
                cur["file"] = path
        elif line.startswith("+++ "):
            path = _unquote(line[4:])
            if path != "/dev/null":
                cur["file"] = path
        elif line.startswith("@@"):
            m = _HUNK_RE.match(line)
            if not m:
                continue
            close_hunk()
            old_start = int(m.group(1))
            new_start = int(m.group(3))
            body, added, removed = [], [], []
        elif body is not None:
            if line.startswith("+"):
                added.append(line)
                body.append(line)
            elif line.startswith("-"):
                removed.append(line)
                body.append(line)
            elif line.startswith(" ") or line == "":
                body.append(line)
            # "\ No newline at end of file" 같은 줄은 버린다.

    close_file()
    return DiffResult(hunks=tuple(hunks), files=tuple(files))


def collect_hunks(repo: str | Path, base_ref: str, head_ref: str, context: int = 3) -> DiffResult:
    """git 실행 + 파싱. 모든 단계의 공통 진입점."""
    return parse_hunks(read_raw_diff(repo, base_ref, head_ref, context))
