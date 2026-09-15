"""워크플로 각 단계가 부르는 명령들.

GitHub API 호출은 하지 않는다. 워크플로가 ``gh``로 읽고 쓰고, 여기서는
파일로 주고받는다. 그래야 각 단계를 로컬에서 그대로 재현할 수 있다.

    lasthuman score   위험 점수 계산
    lasthuman page    질문 생성 + 면담 웹 UI 생성
    lasthuman grade   붙여넣은 답변 채점 + 인증 코멘트 본문 생성
    lasthuman gate    인증을 커밋과 대조해 검사 결론을 낸다
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path

from .attest import decode_all, decode_answers, encode, band_for, gate_decision, now_iso
from .config import load_config
from .diff import collect_hunks
from .interview import ModelError, generate_questions, grade as grade_answer
from .structure import build_context
from .models import Answer, Attestation, PrMeta, Question, RiskResult
from .risk import score as score_risk
from .webui import render_page


def _utf8_stdio() -> None:
    """Windows 콘솔은 기본이 cp949라 한글 사유를 찍다가 죽는다."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _write(path: str | None, text: str) -> None:
    if not path:
        sys.stdout.write(text + "\n")
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _read_json(path: str | None) -> dict:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _emit_output(**kv: object) -> None:
    """워크플로 다음 단계가 쓸 수 있도록 GITHUB_OUTPUT에 넘긴다."""
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        return
    with open(target, "a", encoding="utf-8") as fh:
        for k, v in kv.items():
            fh.write(f"{k}={v}\n")


# --- score -------------------------------------------------------------------


def _risk_to_dict(risk: RiskResult) -> dict:
    return {
        "score": risk.score,
        "triggered": risk.triggered,
        "reasons": list(risk.reasons),
        "topHunks": [dataclasses.asdict(h) for h in risk.top_hunks],
    }


def cmd_score(args: argparse.Namespace) -> int:
    config = load_config(args.repo_path)
    diff = collect_hunks(args.repo_path, args.base, args.head)
    meta_raw = _read_json(args.meta)
    meta = PrMeta(
        title=meta_raw.get("title", ""),
        body=meta_raw.get("body", ""),
        author=meta_raw.get("author", ""),
        author_association=meta_raw.get("authorAssociation", "OWNER"),
        agent_hint=bool(meta_raw.get("agentHint", False)),
        labels=tuple(meta_raw.get("labels", ())),
    )
    if args.mode:
        config = dataclasses.replace(config, mode=args.mode)

    risk = score_risk(diff, config, meta)
    _write(args.out, json.dumps(_risk_to_dict(risk), ensure_ascii=False, indent=2))
    _emit_output(
        score=risk.score,
        triggered=str(risk.triggered).lower(),
        threshold=config.threshold,
    )
    print(f"위험 점수 {risk.score} / 임계값 {config.threshold} · 발동 {risk.triggered}")
    for reason in risk.reasons:
        print(f"  - {reason}")
    return 0


# --- page --------------------------------------------------------------------


def cmd_page(args: argparse.Namespace) -> int:
    config = load_config(args.repo_path)
    diff = collect_hunks(args.repo_path, args.base, args.head)
    risk_raw = _read_json(args.risk)
    hunk_by_anchor = {h.anchor: h for h in diff.hunks}
    top = tuple(
        hunk_by_anchor[h["anchor"]] for h in risk_raw.get("topHunks", []) if h["anchor"] in hunk_by_anchor
    )
    risk = RiskResult(
        score=int(risk_raw.get("score", 0)),
        triggered=bool(risk_raw.get("triggered", False)),
        reasons=tuple(risk_raw.get("reasons", ())),
        top_hunks=top,
    )

    body = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else ""
    # 구조 축 질문이 딛고 설 사실. hunk만으로는 "이 변경이 누구에게 전파되는가"를
    # 물을 수 없고, 객관식 오답 보기도 실재하는 경로로 채울 수 없다.
    structure = build_context(args.repo_path, diff.hunks)
    try:
        questions = generate_questions(
            risk, args.title, body, n=args.count, structure=structure, dry_run=args.dry_run
        )
    except ModelError as err:
        # 모델이 죽어도 게이트가 사람을 막지 않는다. 안내만 하고 중립으로 넘긴다.
        print(f"질문 생성 실패: {err}", file=sys.stderr)
        _emit_output(model_failed="true")
        return 3

    html = render_page(
        repo=args.repo,
        pr=args.pr,
        head_sha=args.head_sha,
        risk=risk,
        config=config,
        questions=questions,
        hunks=diff.hunks,
    )
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    _write(
        args.questions_out,
        json.dumps([dataclasses.asdict(q) for q in questions], ensure_ascii=False, indent=2),
    )
    _emit_output(question_count=len(questions))
    print(f"질문 {len(questions)}개 · {out_dir / 'index.html'}")
    return 0


# --- grade -------------------------------------------------------------------


def _question_from(raw: dict) -> Question:
    """JSON으로 오갈 때 choices가 list가 된다. 튜플로 되돌려 dataclass 계약을 지킨다."""
    return Question(
        type=raw.get("type", "consequence"),
        anchor=raw.get("anchor", ""),
        text=raw.get("text", ""),
        expected_evidence=raw.get("expected_evidence", ""),
        choices=tuple(raw.get("choices") or ()),
        answer_index=int(raw.get("answer_index", -1)),
    )


def cmd_grade(args: argparse.Namespace) -> int:
    comment = Path(args.comment_file).read_text(encoding="utf-8")
    submitted = decode_answers(comment)
    if not submitted:
        print("답변 블록이 없습니다. 채점할 것이 없습니다.", file=sys.stderr)
        return 2

    if submitted.get("head_sha") != args.head_sha:
        _write(
            args.out,
            "이 답변은 이전 커밋에 대한 것입니다. 새 커밋이 올라왔으니 면담 페이지를 다시 열어 주세요.",
        )
        _emit_output(graded="stale")
        return 0

    questions = (
        [_question_from(q) for q in _read_json(args.questions) or []] if args.questions else []
    )
    by_anchor = {q.anchor: q for q in questions}
    diff = collect_hunks(args.repo_path, args.base, args.head)
    hunks = {h.anchor: h for h in diff.hunks}

    by_submitted = {a.get("anchor", ""): a for a in submitted.get("answers", [])}
    graded = []
    for question in questions:
        # 답이 없는 질문은 보류다. 빠뜨린 것을 통과로 읽으면 게이트가 열린 채로 고장난다.
        sub = by_submitted.get(question.anchor) or {}
        text = str(sub.get("text", "")).strip()
        raw_choice = sub.get("choice")
        choice = int(raw_choice) if isinstance(raw_choice, int) else None
        # 객관식인데 보기를 안 골랐거나, 서술형인데 근거가 비면 보류다.
        if not text or (question.is_choice and choice is None):
            graded.append(
                Answer(
                    anchor=question.anchor,
                    text=text,
                    choice=choice,
                    verdict="hold",
                    hint="이 질문에 대한 답변이 제출되지 않았습니다.",
                )
            )
            continue
        try:
            graded.append(
                grade_answer(
                    question,
                    text,
                    hunks.get(question.anchor),
                    choice=choice,
                    dry_run=args.dry_run,
                )
            )
        except ModelError as err:
            print(f"판정 실패: {err}", file=sys.stderr)
            _emit_output(model_failed="true")
            return 3

    if not graded:
        # 질문이 하나도 없으면 인증할 근거가 없다. 통과시키지 않는다.
        _write(args.out, "채점할 질문이 없습니다. 면담 페이지를 다시 생성해야 합니다.")
        _emit_output(graded="error")
        return 2

    risk_raw = _read_json(args.risk)
    att = Attestation(
        version=1,
        repo=args.repo,
        pr=args.pr,
        head_sha=args.head_sha,
        actor=args.actor,
        role=submitted.get("role", "author"),
        band=band_for(graded),
        score=int(risk_raw.get("score", 0)),
        reasons=tuple(risk_raw.get("reasons", ())),
        answers=graded,
        created_at=now_iso(),
    )

    held = [a for a in graded if a.verdict != "pass"]
    if held:
        lines = ["### 이해 인증 보류", "", "아래 지점을 확인하고 다시 답해 주세요. **보류는 기록에 남지 않습니다.**", ""]
        for a in held:
            lines.append(f"- `{a.anchor}` — {a.hint or '해당 코드를 열어 실제 동작을 확인해 보세요.'}")
        _write(args.out, "\n".join(lines))
        _emit_output(graded="hold")
        print("보류")
        return 0

    _write(args.out, encode(att))
    _emit_output(graded="pass", band=att.band)
    print(f"통과 · {att.band}")
    return 0


# --- gate --------------------------------------------------------------------


def cmd_gate(args: argparse.Namespace) -> int:
    risk_raw = _read_json(args.risk)
    if not risk_raw.get("triggered", False):
        # 전수 적용하지 않는다. 조용히 지나가되 결과 파일은 반드시 남긴다 —
        # 다음 단계가 이 파일을 읽으므로 빠뜨리면 통과해야 할 PR이 잡 실패로 막힌다.
        summary = "위험도 미달 — 인증 불필요"
        _emit_output(conclusion="neutral", summary=summary)
        _write(args.out, summary)
        print(f"neutral · {summary}")
        return 0

    comments = _read_json(args.comments) if args.comments else []
    if isinstance(comments, dict):
        comments = comments.get("comments", [])
    # `gh api --paginate --slurp`은 페이지 배열의 배열을 준다. 한 겹 편다.
    flat: list[dict] = []
    for item in comments:
        flat.extend(item) if isinstance(item, list) else flat.append(item)
    comments = flat

    found: list[Attestation] = []
    for c in comments:
        # 인증은 GitHub이 작성자를 인증한 코멘트에서만 인정한다.
        for att in decode_all(c.get("body", "")):
            author = (c.get("user") or {}).get("login") or c.get("author", {}).get("login", "")
            if author and att.actor != author:
                continue
            found.append(att)

    conclusion, summary = gate_decision(found, args.head_sha, args.pr_author)
    detail = summary
    if conclusion == "failure":
        detail += "\n\n왜 발동했는가:\n" + "\n".join(f"- {r}" for r in risk_raw.get("reasons", []))
    _emit_output(conclusion=conclusion, summary=summary)
    _write(args.out, detail)
    print(f"{conclusion} · {summary}")
    # 실패를 0으로 끝내면 잡이 초록으로 남아 게이트가 아무것도 막지 못한다.
    return 1 if conclusion == "failure" and not args.soft else 0


def main(argv: list[str] | None = None) -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(prog="lasthuman", description="머지 직전 이해 검증 게이트")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--repo-path", default=".")
        p.add_argument("--base", default="origin/main")
        p.add_argument("--head", default="HEAD")

    p = sub.add_parser("score", help="위험 점수 계산")
    common(p)
    p.add_argument("--meta", help="PR 사실이 담긴 JSON")
    p.add_argument("--mode", choices=["internal", "oss"])
    p.add_argument("--out")
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("page", help="질문 생성과 면담 웹 UI 생성")
    common(p)
    p.add_argument("--risk", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--head-sha", required=True)
    p.add_argument("--title", default="")
    p.add_argument("--body-file")
    p.add_argument("--count", type=int, default=2)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--questions-out")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_page)

    p = sub.add_parser("grade", help="붙여넣은 답변 채점")
    common(p)
    p.add_argument("--comment-file", required=True)
    p.add_argument("--questions")
    p.add_argument("--risk")
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--head-sha", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--out")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_grade)

    p = sub.add_parser("gate", help="인증을 커밋과 대조해 결론을 낸다")
    p.add_argument("--risk", required=True)
    p.add_argument("--comments")
    p.add_argument("--head-sha", required=True)
    p.add_argument("--pr-author", required=True)
    p.add_argument("--out")
    p.add_argument(
        "--soft",
        action="store_true",
        help="실패해도 0으로 끝낸다. 로컬 확인용이며 워크플로에서는 쓰지 않는다.",
    )
    p.set_defaults(func=cmd_gate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
