"""면담 웹 UI 생성.

Action이 PR마다 정적 페이지를 만들어 GitHub Pages에 올린다.
페이지에는 비밀이 들어가지 않는다. 공개 저장소이므로 토큰을 심을 수 없고,
심을 필요도 없다 — 답변은 사람이 GitHub 코멘트로 올리고 GitHub이 작성자를
인증한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import Config
from .diff import parse_anchor
from .models import Hunk, Question, RiskResult

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"], default_for_string=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_page(
    *,
    repo: str,
    pr: int,
    head_sha: str,
    risk: RiskResult,
    config: Config,
    questions: Sequence[Question],
    hunks: Sequence[Hunk] = (),
) -> str:
    by_anchor = {h.anchor: h for h in hunks}
    rows = []
    for q in questions:
        parsed = parse_anchor(q.anchor)
        hunk = by_anchor.get(q.anchor)
        rows.append(
            {
                "type": q.type,
                "axis": q.axis,
                # 정답 위치는 페이지에 실어 보내지 않는다. 공개 저장소의 정적
                # 페이지라 소스 보기로 그대로 읽힌다. 채점은 Action에서만 한다.
                "choices": list(q.choices),
                "anchor": q.anchor,
                "file": parsed[0] if parsed else q.anchor,
                "line": parsed[1] if parsed else 1,
                "text": q.text,
                # 막혔을 때만 펼치는 지원. 처음부터 보여주면 게이트의 의미가 사라진다.
                "body": hunk.body if hunk else "",
            }
        )

    template = _env().get_template("interview.html.j2")
    return template.render(
        repo=repo,
        pr=pr,
        head_sha=head_sha,
        short_sha=head_sha[:7],
        score=risk.score,
        threshold=config.threshold,
        reasons=list(risk.reasons),
        questions=rows,
    )
