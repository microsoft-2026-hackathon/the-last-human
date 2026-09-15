from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from flask import Flask, render_template

from lasthuman.config import Config
from lasthuman.interview import ModelError, generate_questions
from lasthuman.models import Answer, DiffResult, FileChange, Hunk, Question, RiskResult
from lasthuman.server.config import Settings
from lasthuman.server.github import GitHubError
from lasthuman.server.service import BotError, BotService
from lasthuman.server.snapshot import Snapshot
from lasthuman.server.store import PublicationRequest, ReceiptAnswer, Store
from lasthuman.structure import StructureContext, SymbolUse

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
OTHER_HEAD_SHA = "c" * 40
TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "src" / "lasthuman" / "templates"


class FakeClock:
    def __init__(self, start: float = 1_725_798_000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeReader:
    def __init__(self, *snapshots: Snapshot) -> None:
        self._snapshots = list(snapshots)
        self.calls = 0

    def read(self, pr: int) -> Snapshot:
        snapshot = self._snapshots[min(self.calls, len(self._snapshots) - 1)]
        self.calls += 1
        if snapshot.pr != pr:
            raise AssertionError("wrong PR requested")
        return snapshot


class FakeGitHub:
    def __init__(
        self,
        pulls: list[dict[str, object]],
        *,
        commits: dict[str, dict[str, object]] | None = None,
    ) -> None:
        self._pulls = list(pulls)
        self._commits = dict(commits or {})
        self.pull_calls = 0
        self.request_calls: list[tuple[str, str]] = []
        self.status_calls: list[dict[str, object]] = []
        self.comment_calls: list[dict[str, object]] = []
        self.check_calls: list[dict[str, object]] = []
        self.cancel_check_calls: list[dict[str, object]] = []
        self.check_runs: dict[str, dict[str, object]] = {}
        self.pr_card: dict[str, object] | None = None
        self.dispatch_calls: list[str] = []
        self.fail_status = 0
        self.fail_comment = 0
        self.fail_check = 0
        self.fail_dispatch = 0

    def pull(self, pr: int, user_token: str | None = None) -> dict[str, object]:
        del user_token
        payload = self._pulls[min(self.pull_calls, len(self._pulls) - 1)]
        self.pull_calls += 1
        if payload["number"] != pr:
            raise AssertionError("wrong PR requested")
        return payload

    def set_status(
        self,
        sha: str,
        state: str,
        description: str,
        target_url: str,
    ) -> dict[str, object]:
        if self.fail_status > 0:
            self.fail_status -= 1
            raise GitHubError("status failed RAW_SECRET", status_code=502)
        self.status_calls.append(
            {
                "sha": sha,
                "state": state,
                "description": description,
                "target_url": target_url,
            }
        )
        return {
            "id": len(self.status_calls),
            "sha": sha,
            "state": state,
            "context": "comprehension-gate",
            "target_url": target_url,
        }

    def ensure_comment(self, pr: int, body: str, publication_id: str) -> dict[str, object]:
        if self.fail_comment > 0:
            self.fail_comment -= 1
            raise GitHubError("comment failed RAW_SECRET", status_code=502)
        call = {"pr": pr, "body": body, "publication_id": publication_id}
        self.comment_calls.append(call)
        return {"id": len(self.comment_calls), "html_url": f"https://example.com/comments/{len(self.comment_calls)}"}

    def find_pr_card(self, pr: int) -> dict[str, object] | None:
        if self.pr_card is None or self.pr_card["pr"] != pr:
            return None
        return dict(self.pr_card)

    def ensure_pr_card(self, pr: int, body: str) -> dict[str, object]:
        if self.fail_comment > 0:
            self.fail_comment -= 1
            raise GitHubError("card failed RAW_SECRET", status_code=502)
        existing = self.find_pr_card(pr)
        if existing is not None and existing["body"] == body:
            return existing
        card_id = 1 if self.pr_card is None else int(self.pr_card["id"])
        self.pr_card = {
            "id": card_id,
            "pr": pr,
            "body": body,
            "html_url": f"https://example.com/comments/{card_id}",
        }
        self.comment_calls.append({"pr": pr, "body": body, "publication_id": "pr-card"})
        return dict(self.pr_card)

    def ensure_check_run(
        self,
        sha: str,
        external_id: str,
        *,
        status: str,
        conclusion: str | None,
        title: str,
        summary: str,
        details_url: str,
    ) -> dict[str, object]:
        if self.fail_check > 0:
            self.fail_check -= 1
            raise GitHubError("check failed RAW_SECRET", status_code=403)
        call = {
            "sha": sha,
            "external_id": external_id,
            "status": status,
            "conclusion": conclusion,
            "title": title,
            "summary": summary,
            "details_url": details_url,
        }
        existing = self.check_runs.get(external_id)
        if existing is not None and all(existing[key] == value for key, value in call.items()):
            return dict(existing)
        check_id = len(self.check_runs) + 1 if existing is None else existing["id"]
        result = {"id": check_id, "html_url": f"https://example.com/checks/{check_id}", **call}
        self.check_runs[external_id] = result
        self.check_calls.append(call)
        return dict(result)

    def find_check_run(self, sha: str, external_id: str) -> dict[str, object] | None:
        existing = self.check_runs.get(external_id)
        if existing is None or existing["sha"] != sha:
            return None
        return dict(existing)

    def cancel_check_run(
        self,
        check_run_id: int,
        *,
        sha: str,
        external_id: str,
        title: str,
        summary: str,
        details_url: str,
    ) -> dict[str, object]:
        existing = self.find_check_run(sha, external_id)
        if existing is None or existing["id"] != check_run_id:
            raise GitHubError("check run not found", status_code=404)
        call = {
            "id": check_run_id,
            "html_url": existing.get("html_url"),
            "sha": sha,
            "external_id": external_id,
            "title": title,
            "summary": summary,
            "details_url": details_url,
            "status": "completed",
            "conclusion": "cancelled",
        }
        self.cancel_check_calls.append(call)
        self.check_runs[external_id] = call
        return call

    def dispatch_verification(self, receipt_id: str) -> None:
        self.dispatch_calls.append(receipt_id)
        if self.fail_dispatch > 0:
            self.fail_dispatch -= 1
            raise GitHubError("dispatch failed", status_code=502)

    def request(
        self,
        method: str,
        relative_api_path: str,
        token_override: str | None = None,
        json: dict[str, object] | None = None,
    ) -> dict[str, object]:
        del token_override, json
        self.request_calls.append((method, relative_api_path))
        if method != "GET":
            raise AssertionError("only GET requests are expected")
        commit_sha = relative_api_path.rsplit("/", 1)[-1]
        try:
            return dict(self._commits[commit_sha])
        except KeyError:
            raise GitHubError("commit not found", status_code=404) from None


def make_settings(tmp_path: Path, *, live: bool = False, checks_enabled: bool = False) -> Settings:
    key_file = tmp_path / "app.pem"
    key_file.write_text("not used", encoding="utf-8")
    return Settings(
        app_id=101,
        client_id="Iv1.test",
        client_secret="s" * 32,
        private_key_file=key_file,
        installation_id=202,
        repository="hunhoon21/the-last-human",
        repository_id=1361123778,
        owner_id=36983960,
        base_url="https://example.com" if live else "http://localhost:8000",
        secret_key="k" * 32,
        database=tmp_path / "state" / "lasthuman.sqlite3",
        mode="live" if live else "development",
        status_context="comprehension-gate" if live else "comprehension-gate-dev",
        workflow="lasthuman-app.yml",
        workflow_ref="refs/heads/main",
        oidc_audience="hunhoon21/the-last-human",
        checks_enabled=checks_enabled,
    )


def make_snapshot(
    *,
    pr: int = 7,
    head_sha: str = HEAD_SHA,
    base_sha: str = BASE_SHA,
    triggered: bool = True,
    author_id: int = 7,
    author_login: str = "octocat",
) -> Snapshot:
    auth_hunk = Hunk(
        file="app/auth/token.py",
        new_start=10,
        old_start=10,
        anchor="app/auth/token.py:L10",
        added=("+    return refresh(token)",),
        removed=("-    return token",),
        body=(
            "@@ -10,1 +10,1 @@\n"
            "-    return token\n"
            "+    return refresh(token)\n"
        ),
        file_status="modified",
    )
    docs_hunk = Hunk(
        file="docs/guide.md",
        new_start=3,
        old_start=3,
        anchor="docs/guide.md:L3",
        added=("+Updated guide",),
        removed=("-Old guide",),
        body=(
            "@@ -3,1 +3,1 @@\n"
            "-Old guide\n"
            "+Updated guide\n"
        ),
        file_status="modified",
    )
    diff = DiffResult(
        hunks=(auth_hunk, docs_hunk),
        files=(
            FileChange(
                file="app/auth/token.py",
                status="modified",
                binary=False,
                additions=1,
                deletions=1,
                hunk_count=1,
            ),
            FileChange(
                file="docs/guide.md",
                status="modified",
                binary=False,
                additions=1,
                deletions=1,
                hunk_count=1,
            ),
        ),
    )
    return Snapshot.create(
        repo="hunhoon21/the-last-human",
        repo_id=1361123778,
        pr=pr,
        head_sha=head_sha,
        base_sha=base_sha,
        author_id=author_id,
        author_login=author_login,
        title="Handle refresh",
        body="Explains refresh handling.",
        risk=RiskResult(
            score=80 if triggered else 10,
            triggered=triggered,
            reasons=("critical path changed",) if triggered else ("below threshold",),
            top_hunks=(auth_hunk, docs_hunk),
        ),
        config=Config(),
        diff=diff,
        structure=StructureContext(
            changed_files=("app/auth/token.py",),
            importers={"app/auth/token.py": ("app/main.py",)},
            symbols=(SymbolUse("refresh", "app/auth/token.py", ("app/main.py",)),),
            sibling_files=("app/main.py", "README.md"),
        ),
        zones=("app/auth/", "docs/"),
        policy_version="1" * 64,
    )


def make_questions() -> list[Question]:
    return [
        Question(
            type="claim",
            anchor="app/auth/token.py:L10",
            text="What does refresh return now?",
            expected_evidence="refresh(token)",
            choices=("token", "refresh(token)", "None", "exception"),
            answer_index=1,
        ),
        Question(
            type="structure",
            anchor="app/auth/token.py:L10",
            text="Who calls refresh?",
            expected_evidence="app/main.py",
            choices=("app/main.py", "docs/guide.md", "README.md", "tests/test_app.py"),
            answer_index=0,
        ),
        Question(
            type="rationale",
            anchor="docs/guide.md:L3",
            text="What changed in the guide?",
            expected_evidence="Updated guide",
            choices=(),
            answer_index=-1,
        ),
    ]


def make_pull(
    snapshot: Snapshot,
    *,
    state: str = "open",
    merged: bool = False,
    merged_at: str | None = None,
    merge_commit_sha: str | None = None,
    commits: int | None = None,
) -> dict[str, object]:
    return {
        "number": snapshot.pr,
        "state": state,
        "merged": merged,
        "merged_at": merged_at,
        "merge_commit_sha": (merge_commit_sha if merge_commit_sha is not None else ("d" * 40)) if merged else None,
        "user": {"id": snapshot.author_id, "login": snapshot.author_login},
        "head": {"sha": snapshot.head_sha, "ref": "feat/runtime"},
        "base": {"sha": snapshot.base_sha, "ref": "main"},
        "title": snapshot.title,
        "body": snapshot.body,
        "commits": commits,
    }


def make_service(
    tmp_path: Path,
    *,
    snapshot: Snapshot,
    pulls: list[dict[str, object]],
    clock: FakeClock,
    live: bool = False,
    checks_enabled: bool = False,
    questions: list[Question] | None = None,
    verdicts: dict[str, str] | None = None,
    generate_func: Callable[..., list[Question]] | None = None,
    grade_func: Callable[..., Answer] | None = None,
    commits: dict[str, dict[str, object]] | None = None,
) -> tuple[BotService, FakeGitHub]:
    settings = make_settings(tmp_path, live=live, checks_enabled=checks_enabled)
    github = FakeGitHub(pulls, commits=commits)
    reader = FakeReader(snapshot)
    store = Store(settings.database)
    question_list = make_questions() if questions is None else questions
    verdict_map = verdicts or {}

    def generate(risk, title, body, n, *, structure):
        if generate_func is not None:
            return list(generate_func(risk, title, body, n, structure=structure))
        assert risk == snapshot.risk
        assert title == snapshot.title
        assert body == snapshot.body
        assert n == settings.question_count
        assert structure == snapshot.structure
        return list(question_list)

    def grade(question, answer_text, hunk, *, choice=None):
        if grade_func is not None:
            return grade_func(question, answer_text, hunk, choice=choice)
        verdict = verdict_map.get(question.anchor + "|" + question.text, "pass")
        hint = f"inspect {question.anchor}" if verdict == "hold" else ""
        return Answer(
            anchor=question.anchor,
            text=answer_text,
            choice=choice,
            verdict=verdict,
            hint=hint,
            choice_correct=True if choice is not None else None,
        )

    service = BotService(
        settings,
        github,
        reader,
        store,
        generate=generate,
        grade=grade,
        clock=clock,
    )
    return service, github


def render_pr_template(
    snapshot: Snapshot,
    interview: dict[str, object],
    *,
    pull_state: str = "open",
    receipt: dict[str, object] | None = None,
) -> str:
    app = Flask(__name__, template_folder=str(TEMPLATE_DIR))

    @app.route("/prs/<int:pr>/sync", methods=["POST"])
    def pr_sync(pr: int) -> str:
        return str(pr)

    @app.route("/api/prs/<int:pr>/submissions", methods=["POST"])
    def submit_answers(pr: int) -> str:
        return str(pr)

    @app.route("/receipts/<receipt_id>")
    def receipt_page(receipt_id: str) -> str:
        return receipt_id

    with app.test_request_context(f"/prs/{snapshot.pr}"):
        return render_template(
            "app_pr.html.j2",
            repo=snapshot.repo,
            pr=snapshot.pr,
            title=snapshot.title,
            head_sha=snapshot.head_sha,
            pull_state=pull_state,
            interview=interview,
            receipt=receipt,
            csrf_token="csrf-token",
            csp_nonce="nonce",
            mode_label="Development",
            initial_request_id="req-0",
        )


def test_sync_and_interview_preserve_same_anchor_questions_with_stable_ids(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    service, _github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot)],
        clock=clock,
    )

    sync = service.sync(snapshot.pr)
    interview = service.interview(snapshot.pr, snapshot.author_id)

    assert sync["state"] == "pending"
    assert sync["question_count"] == 3
    assert interview["state"] == "pending"
    assert interview["id"] == snapshot.snapshot_id
    assert interview["question_count"] == 3
    assert [question["id"] for question in interview["questions"]] == ["0", "1", "2"]
    assert interview["questions"][0]["anchor"] == interview["questions"][1]["anchor"]
    assert "answer_index" not in interview["questions"][0]
    assert "expected_evidence" not in interview["questions"][0]
    assert interview["questions"][0]["code"].startswith("@@ -10,1 +10,1 @@")


@pytest.mark.parametrize("recovers", [True, False])
def test_sync_requires_a_complete_regenerated_batch(tmp_path: Path, monkeypatch, recovers: bool):
    snapshot = make_snapshot()
    anchor = snapshot.risk.top_hunks[0].anchor
    complete = [
        {
            "type": "consequence",
            "anchor": anchor,
            "text": f"Complete question {index}",
            "choices": ["first", "second"],
            "answerIndex": 0,
            "expectedEvidence": "The changed return path.",
        }
        for index in range(3)
    ]
    partial = [dict(item) for item in complete]
    partial[0]["anchor"] = "not-in-the-diff.py:L999"
    response = Mock(side_effect=[
        json.dumps({"questions": partial}),
        json.dumps({"questions": complete if recovers else partial}),
    ])
    monkeypatch.setattr("lasthuman.interview.call_model", response)
    service, _github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot)],
        clock=FakeClock(),
        generate_func=generate_questions,
    )
    try:
        if recovers:
            result = service.sync(snapshot.pr)
            assert result["state"] == "pending"
            assert result["question_count"] == 3
        else:
            with pytest.raises(BotError) as failure:
                service.sync(snapshot.pr)
            assert failure.value.code == "model_error"
        stored = service.store.load_current_snapshot(snapshot.pr)
        assert stored is not None
        assert stored.state == "pending"
        assert stored.question_count == (3 if recovers else 0)
        assert response.call_count == 2
    finally:
        service.shutdown()


def test_sync_queues_canonical_card_with_real_renderer_and_no_localhost_status(tmp_path: Path):
    live_path = tmp_path / "live"
    dev_path = tmp_path / "dev"
    live_path.mkdir()
    dev_path.mkdir()

    live_clock = FakeClock()
    live_snapshot = make_snapshot()
    live_service, _live_github = make_service(
        live_path,
        snapshot=live_snapshot,
        pulls=[make_pull(live_snapshot)],
        clock=live_clock,
        live=True,
    )
    live_service.sync(live_snapshot.pr)
    live_service.flush_publications()
    live_body = str(_live_github.pr_card["body"])
    assert f"https://example.com/prs/{live_snapshot.pr}" in live_body
    assert live_snapshot.head_sha[:7] in live_body
    assert "localhost" not in live_body
    assert "Human Verified" not in live_body

    dev_clock = FakeClock()
    dev_snapshot = make_snapshot(pr=8)
    dev_service, _dev_github = make_service(
        dev_path,
        snapshot=dev_snapshot,
        pulls=[make_pull(dev_snapshot)],
        clock=dev_clock,
    )
    dev_service.sync(dev_snapshot.pr)
    dev_service.flush_publications()
    dev_events = {
        event.kind: event
        for event in dev_service.store.load_due_publications(
            now="9999-12-31T23:59:59Z",
            limit=10,
        )
    }
    assert _dev_github.pr_card is not None
    dev_body = str(_dev_github.pr_card["body"]).lower()
    assert f"{dev_service.settings.base_url}/prs/{dev_snapshot.pr}" in dev_body
    assert _dev_github.status_calls == []
    assert _dev_github.check_calls == []
    assert "pending_status" not in dev_events


def test_sync_persists_pending_publications_before_model_failure(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    attempts = {"count": 0}

    def fail_generate(risk, title, body, n, *, structure):
        del risk, title, body, n, structure
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ModelError("HTTP 500 RAW_SECRET prompt body")
        return make_questions()

    service, _github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[
            make_pull(snapshot),
            make_pull(snapshot),
            make_pull(snapshot),
            make_pull(snapshot),
        ],
        clock=clock,
        live=True,
        generate_func=fail_generate,
    )

    with pytest.raises(BotError) as err:
        service.sync(snapshot.pr)

    assert err.value.code == "model_error"
    assert err.value.status_code == 502
    assert "RAW_SECRET" not in str(err.value)
    stored = service.store.load_current_snapshot(snapshot.pr)
    assert stored is not None
    assert stored.state == "pending"
    assert stored.question_count == 0
    interview = service.interview(snapshot.pr, snapshot.author_id)
    assert interview["state"] == "preparation_error"
    assert interview["question_count"] == 0
    assert "RAW_SECRET" not in str(interview["message"])
    rendered = render_pr_template(snapshot, interview)
    assert 'id="submission-form"' not in rendered
    assert 'id="sync-form"' in rendered
    assert "Re-sync" in rendered
    with pytest.raises(BotError) as submit_err:
        service.submit(
            snapshot.pr,
            snapshot.author_id,
            stored.snapshot.snapshot_id,
            "req-preparation-error",
            [],
        )
    assert submit_err.value.status_code == 409
    assert "RAW_SECRET" not in str(submit_err.value)
    due = {
        event.kind: event
        for event in service.store.load_due_publications(
            now="9999-12-31T23:59:59Z",
            limit=10,
        )
    }
    assert "start_comment" in due
    assert "pending_status" in due
    assert "success_status" not in due

    synced = service.sync(snapshot.pr)
    assert synced["state"] == "pending"
    assert synced["question_count"] == service.settings.question_count
    recovered = service.interview(snapshot.pr, snapshot.author_id)
    assert recovered["state"] == "pending"
    assert recovered["question_count"] == service.settings.question_count
    assert 'id="submission-form"' in render_pr_template(snapshot, recovered)


def test_interview_reports_partial_question_sets_as_preparing_and_blocks_submit(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    settings = make_settings(tmp_path)
    store = Store(settings.database)
    store.save_snapshot(
        snapshot,
        make_questions()[:1],
        "pending",
        now="2026-09-08T12:00:00Z",
    )
    service = BotService(
        settings,
        FakeGitHub([make_pull(snapshot)]),
        FakeReader(snapshot),
        store,
        generate=lambda *args, **kwargs: make_questions(),
        grade=lambda *args, **kwargs: Answer(anchor="x", text="", verdict="pass"),
        clock=clock,
    )

    interview = service.interview(snapshot.pr, snapshot.author_id)

    assert interview["state"] == "preparing"
    assert interview["question_count"] == 1
    rendered = render_pr_template(snapshot, interview)
    assert 'id="submission-form"' not in rendered
    assert 'id="sync-form"' in rendered

    record = service.store.load_current_snapshot(snapshot.pr)
    assert record is not None
    with pytest.raises(BotError) as err:
        service.submit(
            snapshot.pr,
            snapshot.author_id,
            record.snapshot.snapshot_id,
            "req-preparing",
            [{"id": "0", "text": "returns refresh(token)", "choice": 1}],
        )
    assert err.value.status_code == 409


def test_submit_hold_keeps_raw_only_in_memory_and_result_expires_or_restarts(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    service, _github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot), make_pull(snapshot)],
        clock=clock,
        verdicts={f"{make_questions()[0].anchor}|{make_questions()[0].text}": "hold"},
    )
    service.sync(snapshot.pr)

    job_id = service.submit(
        snapshot.pr,
        snapshot.author_id,
        snapshot.snapshot_id,
        "req-1",
        [
            {"id": "0", "text": "NEVER_PERSIST_THIS", "choice": 1},
            {"id": "1", "text": "called from app/main.py", "choice": 0},
            {"id": "2", "text": "Updated guide"},
        ],
    )
    service.drain()

    result = service.result(job_id, snapshot.author_id)
    assert result["state"] == "needs_followup"
    assert result["feedback"] == [{"id": "0", "hint": "inspect app/auth/token.py:L10"}]

    db_bytes = make_settings(tmp_path).database.read_bytes()
    assert b"NEVER_PERSIST_THIS" not in db_bytes
    assert b"req-1" not in db_bytes
    assert b"inspect app/auth/token.py:L10" not in db_bytes

    restarted = BotService(
        make_settings(tmp_path),
        FakeGitHub([make_pull(snapshot)]),
        FakeReader(snapshot),
        Store(make_settings(tmp_path).database),
        generate=lambda *args, **kwargs: make_questions(),
        grade=lambda *args, **kwargs: Answer(anchor="x", text="", verdict="pass"),
        clock=clock,
    )
    with pytest.raises(BotError) as restarted_err:
        restarted.result(job_id, snapshot.author_id)
    assert restarted_err.value.status_code == 410

    clock.advance(make_settings(tmp_path).session_ttl.total_seconds() + 1)
    with pytest.raises(BotError) as expired_err:
        service.result(job_id, snapshot.author_id)
    assert expired_err.value.status_code == 410


def test_submit_model_errors_are_sanitized_and_not_persisted(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    settings = make_settings(tmp_path)

    def fail_grade(question, answer_text, hunk, *, choice=None):
        del question, answer_text, hunk, choice
        raise ModelError("grader leaked RAW_SECRET request body")

    service, _github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot), make_pull(snapshot)],
        clock=clock,
        grade_func=fail_grade,
    )
    service.sync(snapshot.pr)

    job_id = service.submit(
        snapshot.pr,
        snapshot.author_id,
        snapshot.snapshot_id,
        "req-model-error",
        [
            {"id": "0", "text": "returns refresh(token)", "choice": 1},
            {"id": "1", "text": "called from app/main.py", "choice": 0},
            {"id": "2", "text": "Updated guide"},
        ],
    )
    service.drain()

    result = service.result(job_id, snapshot.author_id)
    assert result["state"] == "error"
    assert "RAW_SECRET" not in str(result["message"])
    current = service.store.load_current_snapshot(snapshot.pr)
    assert current is not None
    assert service.store.load_receipts_for_snapshot(current.snapshot.snapshot_id) == ()
    assert b"raw_secret" not in settings.database.read_bytes().lower()


def test_submit_expires_before_completion_without_persisting_receipt(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    ttl_seconds = int(make_settings(tmp_path).session_ttl.total_seconds())

    def slow_grade(question, answer_text, hunk, *, choice=None):
        del question, answer_text, hunk, choice
        clock.advance(ttl_seconds + 1)
        return Answer(anchor="x", text="", verdict="pass")

    service, _github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot), make_pull(snapshot)],
        clock=clock,
        grade_func=slow_grade,
    )
    service.sync(snapshot.pr)

    job_id = service.submit(
        snapshot.pr,
        snapshot.author_id,
        snapshot.snapshot_id,
        "req-expire",
        [
            {"id": "0", "text": "returns refresh(token)", "choice": 1},
            {"id": "1", "text": "called from app/main.py", "choice": 0},
            {"id": "2", "text": "Updated guide"},
        ],
    )
    service.drain()

    assert service.purge_expired() == 1
    current = service.store.load_current_snapshot(snapshot.pr)
    assert current is not None
    assert service.store.load_receipts_for_snapshot(current.snapshot.snapshot_id) == ()
    with pytest.raises(BotError) as err:
        service.result(job_id, snapshot.author_id)
    assert err.value.status_code == 410


def test_submit_dedups_same_request_and_conflicts_on_body_change(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    service, _github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot), make_pull(snapshot)],
        clock=clock,
    )
    service.sync(snapshot.pr)

    answers = [
        {"id": "0", "text": "returns refresh(token)", "choice": 1},
        {"id": "1", "text": "called from app/main.py", "choice": 0},
        {"id": "2", "text": "Updated guide"},
    ]
    job_id = service.submit(snapshot.pr, snapshot.author_id, snapshot.snapshot_id, "same-id", answers)
    same = service.submit(snapshot.pr, snapshot.author_id, snapshot.snapshot_id, "same-id", answers)

    assert same == job_id

    with pytest.raises(BotError) as err:
        service.submit(
            snapshot.pr,
            snapshot.author_id,
            snapshot.snapshot_id,
            "same-id",
            [
                {"id": "0", "text": "changed body", "choice": 1},
                {"id": "1", "text": "called from app/main.py", "choice": 0},
                {"id": "2", "text": "Updated guide"},
            ],
        )
    assert err.value.status_code == 409


def test_pass_stays_pending_until_verify_and_rejects_foreign_or_missing_answers(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    service, _github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot), make_pull(snapshot), make_pull(snapshot)],
        clock=clock,
        live=True,
    )
    service.sync(snapshot.pr)

    with pytest.raises(BotError) as foreign_err:
        service.interview(snapshot.pr, 999)
    assert foreign_err.value.status_code == 403

    with pytest.raises(BotError) as missing_err:
        service.submit(
            snapshot.pr,
            snapshot.author_id,
            snapshot.snapshot_id,
            "missing",
            [
                {"id": "0", "text": "returns refresh(token)", "choice": 1},
                {"id": "1", "text": "called from app/main.py", "choice": 0},
            ],
        )
    assert missing_err.value.status_code == 400

    job_id = service.submit(
        snapshot.pr,
        snapshot.author_id,
        snapshot.snapshot_id,
        "req-pass",
        [
            {"id": "0", "text": "returns refresh(token)", "choice": 1},
            {"id": "1", "text": "called from app/main.py", "choice": 0},
            {"id": "2", "text": "Updated guide"},
        ],
    )
    service.drain()

    result = service.result(job_id, snapshot.author_id)
    assert result["state"] == "awaiting_verification"
    receipt_id = result["receipt_id"]
    assert service.interview(snapshot.pr, snapshot.author_id)["state"] == "pending"

    binding = service.receipt_binding(receipt_id)
    assert binding["binding"] == snapshot.binding()
    with pytest.raises(BotError) as binding_err:
        service.verify(receipt_id, {**snapshot.binding(), "head_sha": OTHER_HEAD_SHA})
    assert binding_err.value.status_code == 409
    verified = service.verify(receipt_id, snapshot.binding())

    assert verified["state"] == "verified"
    assert service.interview(snapshot.pr, snapshot.author_id)["state"] == "confirmed"
    detail = service.receipt_detail(receipt_id, snapshot.author_id)
    assert detail["receipt_id"] == receipt_id
    assert detail["successful_answers"][0]["question_id"] == "0"


def test_submit_marks_stale_when_head_changes_after_model_call(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    stale_pull = make_snapshot(head_sha=OTHER_HEAD_SHA)
    changed = make_pull(stale_pull)
    service, _github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot), make_pull(snapshot), make_pull(snapshot), changed],
        clock=clock,
    )
    service.sync(snapshot.pr)

    job_id = service.submit(
        snapshot.pr,
        snapshot.author_id,
        snapshot.snapshot_id,
        "stale",
        [
            {"id": "0", "text": "returns refresh(token)", "choice": 1},
            {"id": "1", "text": "called from app/main.py", "choice": 0},
            {"id": "2", "text": "Updated guide"},
        ],
    )
    service.drain()

    result = service.result(job_id, snapshot.author_id)
    assert result["state"] == "stale"
    assert "changed" in result["message"]


def test_outbox_retries_after_failure_across_restart(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    service, github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot), make_pull(snapshot), make_pull(snapshot)],
        clock=clock,
        live=True,
    )
    service.sync(snapshot.pr)
    job_id = service.submit(
        snapshot.pr,
        snapshot.author_id,
        snapshot.snapshot_id,
        "req-pass",
        [
            {"id": "0", "text": "returns refresh(token)", "choice": 1},
            {"id": "1", "text": "called from app/main.py", "choice": 0},
            {"id": "2", "text": "Updated guide"},
        ],
    )
    service.drain()
    receipt_id = service.result(job_id, snapshot.author_id)["receipt_id"]

    github.fail_dispatch = 1
    first = service.flush_publications()
    assert first >= 1
    assert github.dispatch_calls == [receipt_id]

    clock.advance(10)
    restarted = BotService(
        make_settings(tmp_path, live=True),
        github,
        FakeReader(snapshot),
        Store(make_settings(tmp_path, live=True).database),
        generate=lambda *args, **kwargs: make_questions(),
        grade=lambda *args, **kwargs: Answer(anchor="x", text="", verdict="pass"),
        clock=clock,
    )
    second = restarted.flush_publications()
    assert second >= 1
    assert github.dispatch_calls == [receipt_id, receipt_id]
    verified = restarted.verify(receipt_id, snapshot.binding())
    assert verified["state"] == "verified"
    restarted.flush_publications()
    assert github.dispatch_calls == [receipt_id, receipt_id]
    assert restarted.store.load_due_publications(now="2026-09-08T12:10:00Z") == ()


def test_sync_merged_and_dashboard_count_only_premerge_verified_module_coverage(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    merge_commit_sha = "d" * 40
    auth_only_questions = [
        Question(
            type="claim",
            anchor="app/auth/token.py:L10",
            text="What does refresh return now?",
            expected_evidence="refresh(token)",
            choices=("token", "refresh(token)", "None", "exception"),
            answer_index=1,
        ),
        Question(
            type="structure",
            anchor="app/auth/token.py:L10",
            text="Who calls refresh?",
            expected_evidence="app/main.py",
            choices=("app/main.py", "docs/guide.md", "README.md", "tests/test_app.py"),
            answer_index=0,
        ),
        Question(
            type="rationale",
            anchor="app/auth/token.py:L10",
            text="Why call refresh here?",
            expected_evidence="refresh(token)",
            choices=(),
            answer_index=-1,
        ),
    ]
    service, _github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot), make_pull(snapshot), make_pull(snapshot)],
        clock=clock,
        live=True,
        questions=auth_only_questions,
        commits={
            merge_commit_sha: {
                "sha": merge_commit_sha,
                "parents": [
                    {"sha": BASE_SHA},
                    {"sha": HEAD_SHA},
                ],
            }
        },
    )
    service.sync(snapshot.pr)
    job_id = service.submit(
        snapshot.pr,
        snapshot.author_id,
        snapshot.snapshot_id,
        "req-pass",
        [
            {"id": "0", "text": "returns refresh(token)", "choice": 1},
            {"id": "1", "text": "called from app/main.py", "choice": 0},
            {"id": "2", "text": "Updated guide"},
        ],
    )
    service.drain()
    receipt_id = service.result(job_id, snapshot.author_id)["receipt_id"]
    service.verify(receipt_id, snapshot.binding())

    merged_snapshot = make_snapshot(base_sha="e" * 40)
    closed_pull = make_pull(
        merged_snapshot,
        state="closed",
        merged=True,
        merged_at="2026-09-08T12:05:00Z",
        merge_commit_sha=merge_commit_sha,
    )
    service.github._pulls = [closed_pull]
    merged = service.sync_merged(snapshot.pr)
    current = service.store.load_current_snapshot(snapshot.pr)
    assert current is not None
    late = service.store.save_receipt(
        current,
        actor_id=99,
        actor_login="reviewer",
        answers=(
            ReceiptAnswer(question_id="late", anchor="docs/guide.md:L3", text="late docs answer"),
        ),
        app_id=service.settings.app_id,
        installation_id=service.settings.installation_id,
        now="2026-09-08T12:04:30Z",
    )
    service.store.mark_receipt_verified(
        late.receipt_id,
        publications=(),
        now="2026-09-08T12:06:30Z",
    )
    service.store.save_snapshot(
        make_snapshot(head_sha=OTHER_HEAD_SHA),
        auth_only_questions,
        "pending",
        now="2026-09-08T12:07:00Z",
    )
    merged_again = service.sync_merged(snapshot.pr)
    dashboard = service.dashboard(days=30)

    assert merged["state"] == "merged"
    assert merged["measured"] is True
    assert merged_again["measured"] is True
    stored_merge = service.store.load_merge(snapshot.pr)
    assert stored_merge is not None
    assert stored_merge.snapshot_id == snapshot.snapshot_id
    auth_zone = next(item for item in dashboard["zones"] if item["zone"] == "app/auth/")
    docs_zone = next(item for item in dashboard["zones"] if item["zone"] == "docs/")
    assert auth_zone["gated"] == 1
    assert auth_zone["attested"] == 1
    assert docs_zone["gated"] == 1
    assert docs_zone["attested"] == 0
    assert "actor_login" not in json.dumps(dashboard, sort_keys=True)
    assert "successful_answers" not in json.dumps(dashboard, sort_keys=True)
    assert service.github.request_calls == [
        ("GET", f"repos/{service.settings.repository}/commits/{merge_commit_sha}"),
    ]


def test_sync_merged_supports_single_parent_squash_commit(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    merge_commit_sha = "d" * 40
    service, github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot), make_pull(snapshot)],
        clock=clock,
        commits={
            merge_commit_sha: {
                "sha": merge_commit_sha,
                "parents": [{"sha": BASE_SHA}],
            }
        },
    )
    service.sync(snapshot.pr)

    github._pulls = [
        make_pull(
            make_snapshot(base_sha="e" * 40),
            state="closed",
            merged=True,
            merged_at="2026-09-08T12:05:00Z",
            merge_commit_sha=merge_commit_sha,
            commits=1,
        )
    ]
    merged = service.sync_merged(snapshot.pr)

    assert merged["state"] == "merged"
    assert merged["measured"] is True
    assert merged["snapshot_id"] == snapshot.snapshot_id


def test_sync_merged_without_authoritative_commit_parents_is_unmeasured(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    merge_commit_sha = "d" * 40
    service, github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot), make_pull(snapshot)],
        clock=clock,
    )
    service.sync(snapshot.pr)

    github._pulls = [
        make_pull(
            make_snapshot(base_sha="e" * 40),
            state="closed",
            merged=True,
            merged_at="2026-09-08T12:05:00Z",
            merge_commit_sha=merge_commit_sha,
        )
    ]
    merged = service.sync_merged(snapshot.pr)

    assert merged["state"] == "merged"
    assert merged["measured"] is False
    assert merged["snapshot_id"] is None


def test_sync_merged_leaves_multi_commit_single_parent_unmeasured(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    merge_commit_sha = "d" * 40
    service, github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot), make_pull(snapshot)],
        clock=clock,
        commits={
            merge_commit_sha: {
                "sha": merge_commit_sha,
                "parents": [{"sha": BASE_SHA}],
            }
        },
    )
    service.sync(snapshot.pr)

    github._pulls = [
        make_pull(
            make_snapshot(base_sha="e" * 40),
            state="closed",
            merged=True,
            merged_at="2026-09-08T12:05:00Z",
            merge_commit_sha=merge_commit_sha,
            commits=2,
        )
    ]
    merged = service.sync_merged(snapshot.pr)

    assert merged["state"] == "merged"
    assert merged["measured"] is False
    assert merged["snapshot_id"] is None


def test_sync_merged_marks_drifted_first_parent_unmeasured(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    merge_commit_sha = "d" * 40
    drift_base_sha = "e" * 40
    service, github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot), make_pull(snapshot)],
        clock=clock,
        commits={
            merge_commit_sha: {
                "sha": merge_commit_sha,
                "parents": [
                    {"sha": drift_base_sha},
                    {"sha": HEAD_SHA},
                ],
            }
        },
    )
    service.sync(snapshot.pr)

    github._pulls = [
        make_pull(
            make_snapshot(base_sha="f" * 40),
            state="closed",
            merged=True,
            merged_at="2026-09-08T12:05:00Z",
            merge_commit_sha=merge_commit_sha,
        )
    ]
    merged = service.sync_merged(snapshot.pr)
    service.store.save_snapshot(
        make_snapshot(base_sha=drift_base_sha),
        make_questions(),
        "pending",
        now="2026-09-08T12:06:00Z",
    )
    merged_again = service.sync_merged(snapshot.pr)
    stored_merge = service.store.load_merge(snapshot.pr)

    assert merged["state"] == "merged"
    assert merged["measured"] is False
    assert merged_again["measured"] is False
    assert stored_merge is not None
    assert stored_merge.snapshot_id is None


def test_flush_skips_stale_pending_publications_when_pull_changes(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    service, github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot)],
        clock=clock,
        live=True,
    )
    service.sync(snapshot.pr)

    github._pulls = [make_pull(make_snapshot(head_sha=OTHER_HEAD_SHA))]
    queued = service.store.load_due_publications(now=service._now_iso())
    processed = service.flush_publications()

    assert processed == len(queued)
    assert github.comment_calls == []
    assert github.status_calls == []
    assert service.store.load_due_publications(now="9999-12-31T23:59:59Z", limit=10) == ()


def test_publication_status_distinguishes_verified_from_published_and_skips_stale_success(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    pulls = [make_pull(snapshot), make_pull(snapshot), make_pull(snapshot), make_pull(snapshot), make_pull(snapshot)]
    service, github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=pulls,
        clock=clock,
        live=True,
    )
    service.sync(snapshot.pr)
    service.flush_publications()
    job_id = service.submit(
        snapshot.pr,
        snapshot.author_id,
        snapshot.snapshot_id,
        "req-publication-status",
        [
            {"id": "0", "text": "returns refresh(token)", "choice": 1},
            {"id": "1", "text": "called from app/main.py", "choice": 0},
            {"id": "2", "text": "Updated guide"},
        ],
    )
    service.drain()
    receipt_id = service.result(job_id, snapshot.author_id)["receipt_id"]
    service.verify(receipt_id, snapshot.binding())

    with pytest.raises(BotError) as err:
        service.publication_status(receipt_id, 999)
    assert err.value.status_code == 403

    pending = service.publication_status(receipt_id, snapshot.author_id)
    result = service.result(job_id, snapshot.author_id)
    assert pending["verified"] is True
    assert pending["published"] is False
    assert pending["publication_pending"] == 2
    assert pending["publication_applied"] == 0
    assert result["publication"]["published"] is False

    service.reader = FakeReader(make_snapshot(author_login="renamed-author"))
    service.flush_publications()

    published = service.publication_status(receipt_id, snapshot.author_id)
    assert published["published"] is False
    assert published["publication_pending"] == 0
    assert published["publication_applied"] == 0
    assert published["publication_skipped"] == 2
    assert [call["state"] for call in github.status_calls] == ["pending"]
    assert len(github.comment_calls) == 1


def test_receipt_publication_selects_only_current_exact_status_event(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    snapshot = make_snapshot()
    service, _github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot)] * 12,
        clock=clock,
        live=True,
    )
    service.sync(snapshot.pr)
    service.flush_publications()
    job_id = service.submit(
        snapshot.pr,
        snapshot.author_id,
        snapshot.snapshot_id,
        "req-exact-publication",
        [
            {"id": "0", "text": "returns refresh(token)", "choice": 1},
            {"id": "1", "text": "called from app/main.py", "choice": 0},
            {"id": "2", "text": "Updated guide"},
        ],
    )
    service.drain()
    receipt_id = service.result(job_id, snapshot.author_id)["receipt_id"]
    dispatch = service.store.load_verifier_dispatch(receipt_id)
    assert dispatch is not None
    service.store.mark_publication_sent(
        dispatch.event_id,
        now=service._now_iso(),
        remote={"receipt_id": receipt_id},
    )
    clock.advance(301)

    waiting = service.receipt_publication(receipt_id)
    assert set(waiting) == {"receipt", "verified_at", "gate"}
    assert waiting["verified_at"] is None
    assert waiting["gate"] == {
        "context": "comprehension-gate",
        "target_url": f"https://example.com/receipts/{receipt_id}",
        "state": "waiting_verification",
        "status_id": None,
        "error_code": None,
    }
    unchanged_dispatch = service.store.load_verifier_dispatch(receipt_id)
    assert unchanged_dispatch is not None
    assert unchanged_dispatch.status == "sent"

    service.verify(receipt_id, snapshot.binding())
    old_target = f"https://example.com/receipts/{receipt_id}?old-context=1"
    old_event_id = service._status_event_id(
        "success-status",
        receipt_id,
        old_target,
    )
    service.store.queue_publication(
        PublicationRequest(
            event_id=old_event_id,
            kind="success_status",
            pr=snapshot.pr,
            snapshot_id=snapshot.snapshot_id,
            receipt_id=receipt_id,
            payload={
                "description": "Old configured context",
                "target_url": old_target,
            },
        ),
        now=service._now_iso(),
    )
    service.store.mark_publication_sent(
        old_event_id,
        now=service._now_iso(),
        remote={
            "id": 999,
            "context": "old-comprehension-gate",
            "target_url": old_target,
            "state": "success",
        },
    )

    pending = service.receipt_publication(receipt_id)
    assert pending["gate"]["state"] == "waiting_publication"
    assert pending["gate"]["status_id"] is None

    expected_target = f"https://example.com/receipts/{receipt_id}"
    expected_event_id = service._status_event_id(
        "success-status",
        receipt_id,
        expected_target,
    )
    service.store.mark_publication_sent(
        expected_event_id,
        now=service._now_iso(),
        remote={
            "id": 55,
            "context": "wrong-context",
            "target_url": expected_target,
            "state": "success",
        },
    )
    invalid = service.receipt_publication(receipt_id)
    assert invalid["gate"]["state"] == "invalid"
    service.store.mark_publication_retry(
        expected_event_id,
        now=service._now_iso(),
        due_at=service._now_iso(),
        error_code="github-502",
        error_message="GitHub publication failed",
    )

    service.flush_publications()
    published = service.receipt_publication(receipt_id)
    assert published["gate"]["state"] == "published"
    assert published["gate"]["status_id"] != 999
    assert published["gate"]["target_url"] == (
        f"https://example.com/receipts/{receipt_id}"
    )
    assert "successful_answers" not in json.dumps(published, sort_keys=True)


def test_publication_status_reports_sanitized_outbox_failures(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    pulls = [make_pull(snapshot), make_pull(snapshot), make_pull(snapshot), make_pull(snapshot)]
    service, github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=pulls,
        clock=clock,
        live=True,
    )
    service.sync(snapshot.pr)
    service.flush_publications()
    job_id = service.submit(
        snapshot.pr,
        snapshot.author_id,
        snapshot.snapshot_id,
        "req-publication-retry",
        [
            {"id": "0", "text": "returns refresh(token)", "choice": 1},
            {"id": "1", "text": "called from app/main.py", "choice": 0},
            {"id": "2", "text": "Updated guide"},
        ],
    )
    service.drain()
    receipt_id = service.result(job_id, snapshot.author_id)["receipt_id"]
    service.verify(receipt_id, snapshot.binding())

    github.fail_status = 1
    service.flush_publications()

    status = service.publication_status(receipt_id, snapshot.author_id)
    result = service.result(job_id, snapshot.author_id)
    due = {
        event.kind: event
        for event in service.store.load_due_publications(
            now="9999-12-31T23:59:59Z",
            limit=10,
        )
    }

    assert status["published"] is False
    assert status["publication_applied"] == 1
    assert status["publication_pending"] == 1
    assert status["publication_failed"] == 1
    assert status["publication_error_codes"] == ["github-502"]
    assert result["publication"]["publication_failed"] == 1
    assert due["success_status"].last_error == "GitHub publication failed"
    assert "RAW_SECRET" not in due["success_status"].last_error

    clock.advance(10)
    service.flush_publications()
    assert service.publication_status(receipt_id, snapshot.author_id)["published"] is True


def test_publication_status_retries_unverified_dispatch_after_restart(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    service, github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot), make_pull(snapshot), make_pull(snapshot)],
        clock=clock,
        live=True,
    )
    service.sync(snapshot.pr)
    job_id = service.submit(
        snapshot.pr,
        snapshot.author_id,
        snapshot.snapshot_id,
        "req-dispatch-timeout",
        [
            {"id": "0", "text": "returns refresh(token)", "choice": 1},
            {"id": "1", "text": "called from app/main.py", "choice": 0},
            {"id": "2", "text": "Updated guide"},
        ],
    )
    service.drain()
    receipt_id = service.result(job_id, snapshot.author_id)["receipt_id"]

    assert service.flush_publications() >= 1
    waiting = service.publication_status(receipt_id, snapshot.author_id)
    assert waiting["verified"] is False
    assert waiting["verification_state"] == "waiting"
    assert waiting["verification_dispatch_attempts"] == 1
    assert github.dispatch_calls == [receipt_id]

    clock.advance(299)
    restarted = BotService(
        make_settings(tmp_path, live=True),
        github,
        FakeReader(snapshot),
        Store(make_settings(tmp_path, live=True).database),
        generate=lambda *args, **kwargs: make_questions(),
        grade=lambda *args, **kwargs: Answer(anchor="x", text="", verdict="pass"),
        clock=clock,
    )
    assert restarted.flush_publications() == 0
    assert github.dispatch_calls == [receipt_id]

    clock.advance(2)
    polled = restarted.publication_status(receipt_id, snapshot.author_id)
    dispatch = restarted.store.load_verifier_dispatch(receipt_id)
    assert polled["verification_state"] == "retrying"
    assert dispatch is not None
    assert dispatch.status == "pending"
    assert restarted.flush_publications() >= 1
    retrying = restarted.publication_status(receipt_id, snapshot.author_id)
    assert retrying["verification_state"] == "retrying"
    assert retrying["verification_dispatch_attempts"] == 2
    assert github.dispatch_calls == [receipt_id, receipt_id]

    clock.advance(301)
    assert restarted.flush_publications() >= 1
    assert github.dispatch_calls == [receipt_id, receipt_id, receipt_id]

    clock.advance(301)
    assert restarted.flush_publications() == 0
    exhausted = restarted.publication_status(receipt_id, snapshot.author_id)
    assert exhausted["verification_state"] == "error"
    assert exhausted["verification_dispatch_attempts"] == 3


def test_publication_status_caps_dispatch_failures_without_flooding(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    service, github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot), make_pull(snapshot), make_pull(snapshot)],
        clock=clock,
        live=True,
    )
    service.sync(snapshot.pr)
    job_id = service.submit(
        snapshot.pr,
        snapshot.author_id,
        snapshot.snapshot_id,
        "req-dispatch-failures",
        [
            {"id": "0", "text": "returns refresh(token)", "choice": 1},
            {"id": "1", "text": "called from app/main.py", "choice": 0},
            {"id": "2", "text": "Updated guide"},
        ],
    )
    service.drain()
    receipt_id = service.result(job_id, snapshot.author_id)["receipt_id"]

    github.fail_dispatch = 10
    service.flush_publications()
    clock.advance(5)
    service.flush_publications()
    clock.advance(10)
    service.flush_publications()

    status = service.publication_status(receipt_id, snapshot.author_id)

    assert status["verified"] is False
    assert status["verification_state"] == "error"
    assert status["verification_dispatch_attempts"] == 3
    assert status["verification_error_codes"] == ["github-502"]
    assert github.dispatch_calls == [receipt_id, receipt_id, receipt_id]

    clock.advance(20)
    assert service.flush_publications() == 0
    assert github.dispatch_calls == [receipt_id, receipt_id, receipt_id]
