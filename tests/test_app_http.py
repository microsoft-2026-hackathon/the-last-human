from __future__ import annotations

import json
import re
import shutil
import subprocess
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from flask.testing import FlaskClient

from lasthuman.config import Config
from lasthuman.models import Answer, DiffResult, FileChange, Hunk, Question, RiskResult
from lasthuman.server import __main__ as server_main
from lasthuman.server.app import AppRuntime, AppRuntimeError, create_app
from lasthuman.server.config import Settings
from lasthuman.server.events import ActionsIdentity, OIDCError
from lasthuman.server.github import GitHubError
from lasthuman.server.service import BotError, BotService
from lasthuman.server.snapshot import Snapshot
from lasthuman.server.store import Store
from lasthuman.structure import StructureContext, SymbolUse

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
OTHER_HEAD_SHA = "c" * 40


def test_real_submission_script_retries_terminal_errors_and_preserves_network_retry_id(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is only needed to exercise the existing browser script")
    client, app, service, _github, _settings, _clock = make_app(tmp_path)
    login(client)
    service.sync(7)
    html = client.get("/prs/7").get_data(as_text=True)
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", html, flags=re.S)
    script = next(value for value in scripts if 'var form = document.getElementById("submission-form")' in value)
    harness = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const script = fs.readFileSync(0, "utf8");
const handlers = {};
const texts = [{value:"same answer"}, {value:"structure evidence"}, {value:"intent evidence"}];
const posts = [];
let failNextPost = false;
let counter = 0;
function element() {
  return {textContent:"",className:"",disabled:false,children:[],
    appendChild(child){this.children.push(child);},
    removeChild(child){this.children.splice(this.children.indexOf(child),1);},
    get firstChild(){return this.children[0] || null;}};
}
const form = {...element(),dataset:{requestId:"initial"},
  addEventListener(name,handler){handlers[name]=handler;},
  querySelector(selector) {
    if(selector.startsWith("textarea")) return texts[Number(selector.match(/data-question-id="(\d+)"/)[1])];
    return {value:"0"};
  }};
const nodes = {"submission-form":form,"snapshot-id":{value:"snapshot"},
  "submission-status":element(),"submission-hints":element(),
  "receipt-link":element(),"submit-button":element()};
const context = {console, window:{crypto:{randomUUID(){return "request-"+(++counter);}},
  setTimeout}, document:{
  getElementById(id){return nodes[id];},
  querySelector(){return {content:"csrf"};},
  querySelectorAll(){return texts.map((_,id)=>({dataset:{questionId:String(id)}}));},
  createElement(){return element();}},
  async fetch(url, options) {
    if(options.method==="POST") {
      posts.push(JSON.parse(options.body));
      if(failNextPost){failNextPost=false;throw new Error("connection lost");}
      return {ok:true,status:202,json:async()=>({url:"/result"})};
    }
    return {ok:true,status:200,json:async()=>({state:"error",message:"Model unavailable"})};
  }};
vm.runInNewContext(script,context);
(async()=>{
  const submit=()=>handlers.submit({preventDefault(){}});
  await submit();
  await submit();
  assert.notEqual(posts[0].request_id,posts[1].request_id,
    "An intentional retry after a terminal error must not return the old job forever");
  failNextPost=true;
  await submit();
  await submit();
  assert.equal(posts[2].request_id,posts[3].request_id,
    "An uncertain network retry with unchanged answers must reuse its ID");
  failNextPost=true;
  await submit();
  texts[0].value="edited answer";
  if(handlers.input)handlers.input();
  await submit();
  assert.notEqual(posts[4].request_id,posts[5].request_id,
    "Edited answers after a network error must not conflict with the earlier payload");
})().catch(error=>{console.error(error.message);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", harness], input=script, text=True, capture_output=True, timeout=10, check=False
    )
    assert result.returncode == 0, result.stderr
    app.extensions["runtime"].shutdown()


def test_web_templates_escape_untrusted_pr_title(tmp_path: Path) -> None:
    client, app, service, github, _settings, _clock = make_app(tmp_path)
    login(client)
    service.sync(7)
    github.current_pull["title"] = '<img src=x onerror="alert(1)">'
    response = client.get("/prs/7")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<img src=x onerror="alert(1)">' not in html
    assert "&lt;img src=x onerror=" in html
    app.extensions["runtime"].shutdown()


class FakeClock:
    def __init__(self, start: float = 1_725_798_000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeOAuth:
    def __init__(self) -> None:
        self.tokens_by_code = {
            "author-code": {
                "access_token": "author-token",
                "expires_at": 4_000_000_000,
            },
            "other-code": {
                "access_token": "other-token",
                "expires_at": 4_000_000_000,
            },
        }

    def create_authorization_url(self, *, state: str, code_verifier: str) -> str:
        return (
            "https://github.com/login/oauth/authorize"
            f"?state={state}&code_challenge={code_verifier[:12]}&code_challenge_method=S256"
        )

    def fetch_token(self, *, code: str, code_verifier: str) -> dict[str, object]:
        assert code_verifier
        return dict(self.tokens_by_code[code])


@dataclass
class FakeReader:
    snapshot: Snapshot

    def read(self, pr: int) -> Snapshot:
        if pr != self.snapshot.pr:
            raise AssertionError("wrong PR requested")
        return self.snapshot


class FakeGitHub:
    def __init__(self, settings: Settings, snapshot: Snapshot) -> None:
        self.settings = settings
        self.current_pull = make_pull(snapshot)
        self.users_by_token = {
            "author-token": {"id": snapshot.author_id, "login": snapshot.author_login},
            "other-token": {"id": 99, "login": "someone-else"},
        }
        self.comment_calls: list[dict[str, object]] = []
        self.status_calls: list[dict[str, object]] = []
        self.dispatch_calls: list[str] = []
        self.fail_comment = 0
        self.fail_status = 0

    def repository_info(self, user_token: str | None = None) -> dict[str, object]:
        self._require_token(user_token)
        return {
            "id": self.settings.repository_id,
            "full_name": self.settings.repository,
            "owner": {"id": self.settings.owner_id},
        }

    def user(self, access_token: str) -> dict[str, object]:
        self._require_token(access_token)
        return dict(self.users_by_token[access_token])

    def pull(self, pr: int, user_token: str | None = None) -> dict[str, object]:
        self._require_token(user_token)
        if pr != self.current_pull["number"]:
            raise AssertionError("wrong PR requested")
        return dict(self.current_pull)

    def ensure_comment(self, pr: int, body: str, publication_id: str) -> dict[str, object]:
        if self.fail_comment > 0:
            self.fail_comment -= 1
            raise GitHubError("comment RAW_SECRET failed", status_code=502)
        self.comment_calls.append(
            {"pr": pr, "body": body, "publication_id": publication_id}
        )
        return {
            "id": len(self.comment_calls),
            "html_url": f"https://example.com/comments/{len(self.comment_calls)}",
        }

    def set_status(
        self,
        sha: str,
        state: str,
        description: str,
        target_url: str,
    ) -> dict[str, object]:
        if self.fail_status > 0:
            self.fail_status -= 1
            raise GitHubError("status RAW_SECRET failed", status_code=502)
        self.status_calls.append(
            {
                "sha": sha,
                "state": state,
                "description": description,
                "target_url": target_url,
            }
        )
        return {"sha": sha, "state": state}

    def dispatch_verification(self, receipt_id: str) -> None:
        self.dispatch_calls.append(receipt_id)

    def request(self, method: str, relative_api_path: str) -> dict[str, object]:
        assert method == "GET"
        assert relative_api_path == f"repos/{self.settings.repository}/commits/{'d' * 40}"
        return {"sha": "d" * 40, "parents": [{"sha": BASE_SHA}, {"sha": HEAD_SHA}]}

    def _require_token(self, token: str | None) -> None:
        if token is None:
            return
        if token not in self.users_by_token:
            raise GitHubError("bad RAW_SECRET token", status_code=401)


class FakeVerifier:
    def __init__(self, settings: Settings) -> None:
        workflow_ref = (
            f"{settings.repository}/.github/workflows/{settings.workflow}@"
            f"{settings.workflow_ref}"
        )
        self.identities = {
            "pr-token": ActionsIdentity(
                event_name="pull_request_target",
                run_id="101",
                run_attempt="1",
                jti="jti-pr",
                workflow_ref=workflow_ref,
            ),
            "pr-rerun-token": ActionsIdentity(
                event_name="pull_request_target",
                run_id="102",
                run_attempt="1",
                jti="jti-pr-rerun",
                workflow_ref=workflow_ref,
            ),
            "workflow-token": ActionsIdentity(
                event_name="workflow_dispatch",
                run_id="202",
                run_attempt="1",
                jti="jti-workflow",
                workflow_ref=workflow_ref,
            ),
        }

    def verify(self, token: str) -> ActionsIdentity:
        try:
            return self.identities[token]
        except KeyError:
            raise OIDCError("GitHub Actions OIDC token is invalid") from None


def make_settings(tmp_path: Path) -> Settings:
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
        base_url="https://app.example",
        secret_key="k" * 32,
        database=tmp_path / "state" / "lasthuman.sqlite3",
        mode="live",
        status_context="comprehension-gate",
        workflow="lasthuman-app.yml",
        workflow_ref="refs/heads/main",
        oidc_audience="hunhoon21/the-last-human",
    )


def make_snapshot() -> Snapshot:
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
        pr=7,
        head_sha=HEAD_SHA,
        base_sha=BASE_SHA,
        author_id=7,
        author_login="octocat",
        title="Handle refresh",
        body="Explains refresh handling.",
        risk=RiskResult(
            score=80,
            triggered=True,
            reasons=("critical path changed",),
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
) -> dict[str, object]:
    return {
        "number": snapshot.pr,
        "state": state,
        "merged": merged,
        "merged_at": merged_at,
        "merge_commit_sha": ("d" * 40) if merged else None,
        "user": {"id": snapshot.author_id, "login": snapshot.author_login},
        "head": {"sha": snapshot.head_sha, "ref": "feat/runtime"},
        "base": {"sha": snapshot.base_sha, "ref": "main"},
        "title": snapshot.title,
        "body": snapshot.body,
    }


def make_app(
    tmp_path: Path,
) -> tuple[FlaskClient, Any, BotService, FakeGitHub, Settings, FakeClock]:
    clock = FakeClock()
    settings = make_settings(tmp_path)
    snapshot = make_snapshot()
    github = FakeGitHub(settings, snapshot)
    store = Store(settings.database)

    def generate(risk: Any, title: Any, body: Any, count: Any, *, structure: Any) -> list[Question]:
        assert risk == snapshot.risk
        assert title == snapshot.title
        assert body == snapshot.body
        assert count == settings.question_count
        assert structure == snapshot.structure
        return list(make_questions())

    def grade(question: Question, answer_text: str, hunk: Hunk, *, choice: int | None = None) -> Answer:
        del hunk
        verdict = "hold" if question.anchor == "app/auth/token.py:L10" and answer_text == "first try" else "pass"
        hint = "inspect app/auth/token.py:L10" if verdict == "hold" else ""
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
        FakeReader(snapshot),
        store,
        generate=generate,
        grade=grade,
        clock=clock,
    )
    app = create_app(
        settings,
        service=service,
        github=github,
        oauth=FakeOAuth(),
        verifier=FakeVerifier(settings),
        start_worker=False,
    )
    client = app.test_client()
    return client, app, service, github, settings, clock


def login(
    client: FlaskClient,
    *,
    code: str = "author-code",
    next_path: str = "/prs/7",
) -> None:
    begin = client.get(f"/auth/github?next={next_path}")
    state = parse_qs(urlsplit(begin.headers["Location"]).query)["state"][0]
    callback = client.get(f"/auth/github/callback?state={state}&code={code}")
    assert callback.status_code == 302


def extract_csrf(response_text: str) -> str:
    match = re.search(r'<meta name="csrf-token" content="([^"]+)"', response_text)
    assert match is not None
    return match.group(1)


def test_full_http_runtime_flow_from_sync_to_verify_publish_merge_and_dashboard(
    tmp_path: Path,
) -> None:
    client, app, _service, github, settings, _clock = make_app(tmp_path)

    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.get_json()["status"] == "alive"
    assert settings.client_secret not in health.get_data(as_text=True)
    assert app.config["MAX_CONTENT_LENGTH"] == 64 * 1024

    root = client.get("/")
    assert root.status_code == 302
    assert root.headers["Location"].endswith("/dashboard")

    login(client)

    not_prepared = client.get("/prs/7")
    assert not_prepared.status_code == 200
    assert "not prepared" in not_prepared.get_data(as_text=True).lower()
    assert 'id="sync-status"' in not_prepared.get_data(as_text=True)
    assert "Preparing questions..." in not_prepared.get_data(as_text=True)
    assert github.comment_calls == []
    assert github.status_calls == []

    csrf_token = extract_csrf(not_prepared.get_data(as_text=True))
    sync = client.post(
        "/prs/7/sync",
        data={"csrf_token": csrf_token},
        headers={"Accept": "application/json"},
    )
    assert sync.status_code == 202
    sync_payload = sync.get_json()
    assert sync_payload["url"] == f"/api/prs/7/sync/{sync_payload['job_id']}"
    app.extensions["drain_events"](timeout=1.0)

    sync_done = client.get(
        sync_payload["url"],
        headers={"X-CSRF-Token": csrf_token},
    )
    assert sync_done.status_code == 200
    assert sync_done.get_json()["state"] == "completed"
    assert sync_done.get_json()["result"]["state"] == "pending"

    assert len(github.comment_calls) == 1
    assert [call["state"] for call in github.status_calls] == ["pending"]

    pr_page = client.get("/prs/7")
    assert pr_page.status_code == 200
    pr_html = pr_page.get_data(as_text=True)
    assert "What does refresh return now?" in pr_html
    assert "expected_evidence" not in pr_html
    assert "answer_index" not in pr_html
    assert 'name="choice-0"' in pr_html
    assert 'name="choice-1"' in pr_html
    assert pr_page.headers["Cache-Control"] == "no-store"
    assert pr_page.headers["Referrer-Policy"] == "no-referrer"
    assert pr_page.headers["X-Content-Type-Options"] == "nosniff"
    csrf_token = extract_csrf(pr_html)

    hold = client.post(
        "/api/prs/7/submissions",
        json={
            "snapshot_id": make_snapshot().snapshot_id,
            "request_id": "request-1",
            "answers": [
                {"id": "0", "text": "first try", "choice": 1},
                {"id": "1", "text": "called from app/main.py", "choice": 0},
                {"id": "2", "text": "Updated guide"},
            ],
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert hold.status_code == 202
    hold_job_id = hold.get_json()["job_id"]
    app.extensions["drain_events"](timeout=1.0)

    hold_result = client.get(
        f"/api/submissions/{hold_job_id}",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert hold_result.status_code == 200
    assert hold_result.get_json()["state"] == "needs_followup"
    assert hold_result.get_json()["feedback"] == [
        {"id": "0", "hint": "inspect app/auth/token.py:L10"}
    ]

    passed = client.post(
        "/api/prs/7/submissions",
        json={
            "snapshot_id": make_snapshot().snapshot_id,
            "request_id": "request-2",
            "answers": [
                {"id": "0", "text": "returns refresh(token)", "choice": 1},
                {"id": "1", "text": "called from app/main.py", "choice": 0},
                {"id": "2", "text": "Updated guide"},
            ],
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert passed.status_code == 202
    pass_job_id = passed.get_json()["job_id"]
    app.extensions["drain_events"](timeout=1.0)

    awaiting = client.get(
        f"/api/submissions/{pass_job_id}",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert awaiting.status_code == 200
    awaiting_payload = awaiting.get_json()
    assert awaiting_payload["state"] == "awaiting_verification"
    receipt_id = awaiting_payload["receipt_id"]
    assert github.dispatch_calls == [receipt_id]

    receipt_binding = client.get(
        f"/api/actions/receipts/{receipt_id}",
        headers={"Authorization": "Bearer workflow-token"},
    )
    assert receipt_binding.status_code == 200
    assert "successful_answers" not in json.dumps(receipt_binding.get_json(), sort_keys=True)

    verify = client.post(
        f"/api/actions/receipts/{receipt_id}/verify",
        json={"binding": receipt_binding.get_json()["binding"]},
        headers={"Authorization": "Bearer workflow-token"},
    )
    assert verify.status_code == 202
    verify_job_id = verify.get_json()["job_id"]

    wrong_identity = client.get(
        f"/api/actions/jobs/{verify_job_id}",
        headers={"Authorization": "Bearer pr-token"},
    )
    assert wrong_identity.status_code == 403

    app.extensions["drain_events"](timeout=1.0)
    verify_done = client.get(
        f"/api/actions/jobs/{verify_job_id}",
        headers={"Authorization": "Bearer workflow-token"},
    )
    assert verify_done.status_code == 200
    assert verify_done.get_json()["state"] == "completed"
    assert verify_done.get_json()["result"]["state"] == "verified"
    assert [call["state"] for call in github.status_calls] == ["pending", "success"]
    assert len(github.comment_calls) == 2

    verified = client.get(
        f"/api/submissions/{pass_job_id}",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert verified.status_code == 200
    verified_payload = verified.get_json()
    assert verified_payload["state"] == "verified"
    assert verified_payload["publication"]["published"] is True

    receipt_page = client.get(f"/receipts/{receipt_id}")
    assert receipt_page.status_code == 200
    assert "returns refresh(token)" in receipt_page.get_data(as_text=True)
    assert receipt_page.headers["Cache-Control"] == "no-store"

    github.current_pull = make_pull(
        make_snapshot(),
        state="closed",
        merged=True,
        merged_at="2026-09-08T12:05:00Z",
    )

    conflict = client.post(
        "/api/actions/events",
        json={
            "repository_id": settings.repository_id,
            "pr": 7,
            "action": "closed",
            "head_sha": OTHER_HEAD_SHA,
        },
        headers={"Authorization": "Bearer pr-token"},
    )
    assert conflict.status_code == 202
    conflict_job_id = conflict.get_json()["job_id"]
    app.extensions["drain_events"](timeout=1.0)
    conflict_done = client.get(
        f"/api/actions/jobs/{conflict_job_id}",
        headers={"Authorization": "Bearer pr-token"},
    )
    assert conflict_done.status_code == 200
    assert conflict_done.get_json()["state"] == "conflict"

    merged = client.post(
        "/api/actions/events",
        json={
            "repository_id": settings.repository_id,
            "pr": 7,
            "action": "closed",
            "head_sha": HEAD_SHA,
        },
        headers={"Authorization": "Bearer pr-token"},
    )
    assert merged.status_code == 202
    merged_job_id = merged.get_json()["job_id"]
    app.extensions["drain_events"](timeout=1.0)
    merged_done = client.get(
        f"/api/actions/jobs/{merged_job_id}",
        headers={"Authorization": "Bearer pr-token"},
    )
    assert merged_done.status_code == 200
    assert merged_done.get_json()["state"] == "completed"
    assert merged_done.get_json()["result"]["state"] == "merged"

    dashboard_api = client.get(
        "/api/dashboard",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert dashboard_api.status_code == 200
    payload = dashboard_api.get_json()
    assert payload["attested_total"] == 1
    assert payload["gated_total"] == 1
    assert "actor_login" not in json.dumps(payload, sort_keys=True)
    assert "successful_answers" not in json.dumps(payload, sort_keys=True)

    dashboard_page = client.get("/dashboard")
    assert dashboard_page.status_code == 200
    assert "app/auth/" in dashboard_page.get_data(as_text=True)


def test_http_actions_event_returns_poll_url_and_equivalent_rerun_can_poll(
    tmp_path: Path,
) -> None:
    client, _app, _service, _github, settings, _clock = make_app(tmp_path)
    payload = {
        "repository_id": settings.repository_id,
        "pr": 7,
        "action": "opened",
        "binding": make_snapshot().binding(),
    }

    first = client.post(
        "/api/actions/events",
        json=payload,
        headers={"Authorization": "Bearer pr-token"},
    )
    assert first.status_code == 202
    first_payload = first.get_json()
    assert first_payload["url"] == f"/api/actions/jobs/{first_payload['job_id']}"

    second = client.post(
        "/api/actions/events",
        json=payload,
        headers={"Authorization": "Bearer pr-rerun-token"},
    )
    assert second.status_code == 202
    second_payload = second.get_json()
    assert second_payload == first_payload

    rerun_read = client.get(
        first_payload["url"],
        headers={"Authorization": "Bearer pr-rerun-token"},
    )
    assert rerun_read.status_code == 200

    wrong_identity = client.get(
        first_payload["url"],
        headers={"Authorization": "Bearer workflow-token"},
    )
    assert wrong_identity.status_code == 403


def test_http_actions_verify_returns_poll_url(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    snapshot = make_snapshot()
    github = FakeGitHub(settings, snapshot)
    binding = snapshot.binding()
    clock = FakeClock()

    class VerifyUrlService:
        def __init__(self) -> None:
            self.clock = clock

        def purge_expired(self) -> int:
            return 0

        def flush_publications(self) -> int:
            return 0

        def receipt_binding(self, receipt_id: str) -> dict[str, object]:
            assert receipt_id == "receipt-5"
            return {
                "receipt_id": receipt_id,
                "binding": binding,
                "actor_id": snapshot.author_id,
                "question_version": "v1",
                "issued_at": "2026-09-08T00:00:00Z",
                "app_id": settings.app_id,
                "installation_id": settings.installation_id,
            }

        def verify(
            self,
            receipt_id: str,
            provided_binding: dict[str, object],
        ) -> dict[str, object]:
            assert receipt_id == "receipt-5"
            assert provided_binding == binding
            return {
                "state": "verified",
                "pr": snapshot.pr,
                "receipt_id": receipt_id,
                "verified_at": "2026-09-08T12:00:00Z",
            }

        def shutdown(self) -> None:
            return None

    app = create_app(
        settings,
        service=VerifyUrlService(),
        github=github,
        oauth=FakeOAuth(),
        verifier=FakeVerifier(settings),
        start_worker=False,
    )
    client = app.test_client()

    receipt_binding = client.get(
        "/api/actions/receipts/receipt-5",
        headers={"Authorization": "Bearer workflow-token"},
    )
    assert receipt_binding.status_code == 200

    verify = client.post(
        "/api/actions/receipts/receipt-5/verify",
        json={"binding": receipt_binding.get_json()["binding"]},
        headers={"Authorization": "Bearer workflow-token"},
    )
    assert verify.status_code == 202
    verify_payload = verify.get_json()
    assert verify_payload["url"] == f"/api/actions/jobs/{verify_payload['job_id']}"


def test_http_rejects_nonauthor_and_missing_csrf(tmp_path: Path) -> None:
    client, app, _service, _github, _settings, _clock = make_app(tmp_path)

    login(client, code="other-code")
    dashboard = client.get("/dashboard")
    csrf_token = extract_csrf(dashboard.get_data(as_text=True))

    denied = client.post(
        "/prs/7/sync",
        data={"csrf_token": csrf_token},
        headers={"Accept": "application/json"},
    )
    assert denied.status_code == 403
    assert denied.get_json()["error"] == "Only the pull request author can continue"

    author = app.test_client()
    login(author)
    missing_csrf = author.post(
        "/api/prs/7/submissions",
        json={},
        headers={"Accept": "application/json"},
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.get_json()["error"] == "CSRF token is invalid"


def test_http_rejects_invalid_json_oversize_bad_oidc_and_bad_host(tmp_path: Path) -> None:
    client, _app, _service, _github, settings, _clock = make_app(tmp_path)

    login(client)
    dashboard = client.get("/dashboard")
    csrf_token = extract_csrf(dashboard.get_data(as_text=True))

    wrong_mimetype = client.post(
        "/api/prs/7/submissions",
        data="{}",
        content_type="text/plain",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert wrong_mimetype.status_code == 415
    assert wrong_mimetype.get_json()["error"] == "request body must be application/json"

    malformed = client.post(
        "/api/prs/7/submissions",
        data="{",
        content_type="application/json",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert malformed.status_code == 400
    assert malformed.get_json()["error"] == "request body must be a JSON object"

    too_large = client.post(
        "/api/prs/7/submissions",
        data='{"pad":"' + ("x" * 70000) + '"}',
        content_type="application/json",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert too_large.status_code == 413
    assert too_large.get_json()["error"] == "request body is too large"

    bad_oidc = client.post(
        "/api/actions/events",
        json={
            "repository_id": settings.repository_id,
            "pr": 7,
            "action": "opened",
            "binding": make_snapshot().binding(),
        },
        headers={"Authorization": "Bearer invalid-token"},
    )
    assert bad_oidc.status_code == 401
    assert bad_oidc.get_json()["error"] == "GitHub Actions OIDC token is invalid"

    bad_host = client.get("/", headers={"Host": "evil.example"})
    assert bad_host.status_code == 400


def test_http_sanitizes_model_and_storage_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _app, service, _github, _settings, _clock = make_app(tmp_path)

    login(client)
    dashboard = client.get("/dashboard")
    csrf_token = extract_csrf(dashboard.get_data(as_text=True))

    def fail_submit(*args: object, **kwargs: object) -> str:
        del args, kwargs
        raise BotError("RAW_SECRET model detail", code="model_error", status_code=502)

    monkeypatch.setattr(service, "submit", fail_submit)
    model_failure = client.post(
        "/api/prs/7/submissions",
        json={
            "snapshot_id": "snapshot-id",
            "request_id": "request-id",
            "answers": [],
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert model_failure.status_code == 502
    assert model_failure.get_json()["error"] == "Model processing failed; retry later"
    assert "RAW_SECRET" not in model_failure.get_data(as_text=True)

    def fail_dashboard(*, days: int) -> dict[str, object]:
        del days
        raise sqlite3.OperationalError("RAW_SECRET sqlite detail")

    monkeypatch.setattr(service, "dashboard", fail_dashboard)
    storage_failure = client.get(
        "/api/dashboard",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert storage_failure.status_code == 503
    assert storage_failure.get_json()["error"] == "Storage temporarily unavailable"
    assert "RAW_SECRET" not in storage_failure.get_data(as_text=True)


def test_http_submission_result_ttl_and_restart_return_410_without_extending_reads(
    tmp_path: Path,
) -> None:
    client, app, service, github, settings, clock = make_app(tmp_path)

    login(client)
    not_prepared = client.get("/prs/7")
    csrf_token = extract_csrf(not_prepared.get_data(as_text=True))

    sync = client.post(
        "/prs/7/sync",
        data={"csrf_token": csrf_token},
        headers={"Accept": "application/json"},
    )
    assert sync.status_code == 202
    app.extensions["drain_events"](timeout=1.0)

    pr_page = client.get("/prs/7")
    csrf_token = extract_csrf(pr_page.get_data(as_text=True))
    submitted = client.post(
        "/api/prs/7/submissions",
        json={
            "snapshot_id": make_snapshot().snapshot_id,
            "request_id": "request-ttl",
            "answers": [
                {"id": "0", "text": "returns refresh(token)", "choice": 1},
                {"id": "1", "text": "called from app/main.py", "choice": 0},
                {"id": "2", "text": "Updated guide"},
            ],
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert submitted.status_code == 202
    job_id = submitted.get_json()["job_id"]
    app.extensions["drain_events"](timeout=1.0)

    first = client.get(
        f"/api/submissions/{job_id}",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert first.status_code == 200

    clock.advance(settings.session_ttl.total_seconds() + 1)
    expired = client.get(
        f"/api/submissions/{job_id}",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert expired.status_code == 410
    assert expired.get_json()["error"] == "Result expired; resubmit your answers"

    restarted_app = create_app(
        settings,
        service=service,
        github=github,
        oauth=FakeOAuth(),
        verifier=FakeVerifier(settings),
        start_worker=False,
    )
    restarted_client = restarted_app.test_client()
    restarted = restarted_client.get(f"/api/submissions/{job_id}")
    assert restarted.status_code == 410
    assert restarted.get_json()["error"] == "Result expired; resubmit your answers"


def test_healthz_degrades_while_publication_retries_and_pr_page_stays_pending(
    tmp_path: Path,
) -> None:
    client, app, _service, github, _settings, clock = make_app(tmp_path)

    login(client)
    not_prepared = client.get("/prs/7")
    csrf_token = extract_csrf(not_prepared.get_data(as_text=True))
    sync = client.post(
        "/prs/7/sync",
        data={"csrf_token": csrf_token},
        headers={"Accept": "application/json"},
    )
    assert sync.status_code == 202
    app.extensions["drain_events"](timeout=1.0)

    pr_page = client.get("/prs/7")
    csrf_token = extract_csrf(pr_page.get_data(as_text=True))
    passed = client.post(
        "/api/prs/7/submissions",
        json={
            "snapshot_id": make_snapshot().snapshot_id,
            "request_id": "request-publication-retry",
            "answers": [
                {"id": "0", "text": "returns refresh(token)", "choice": 1},
                {"id": "1", "text": "called from app/main.py", "choice": 0},
                {"id": "2", "text": "Updated guide"},
            ],
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert passed.status_code == 202
    pass_job_id = passed.get_json()["job_id"]
    app.extensions["drain_events"](timeout=1.0)

    awaiting = client.get(
        f"/api/submissions/{pass_job_id}",
        headers={"X-CSRF-Token": csrf_token},
    )
    receipt_id = awaiting.get_json()["receipt_id"]

    github.fail_status = 1
    receipt_binding = client.get(
        f"/api/actions/receipts/{receipt_id}",
        headers={"Authorization": "Bearer workflow-token"},
    )
    verify = client.post(
        f"/api/actions/receipts/{receipt_id}/verify",
        json={"binding": receipt_binding.get_json()["binding"]},
        headers={"Authorization": "Bearer workflow-token"},
    )
    assert verify.status_code == 202
    app.extensions["drain_events"](timeout=1.0)

    verified = client.get(
        f"/api/submissions/{pass_job_id}",
        headers={"X-CSRF-Token": csrf_token},
    )
    verified_payload = verified.get_json()
    assert verified_payload["state"] == "verified"
    assert verified_payload["publication"]["published"] is False
    assert verified_payload["publication"]["publication_failed"] == 1
    assert verified_payload["publication"]["publication_pending"] == 1

    health = client.get("/healthz")
    assert health.status_code == 503
    assert health.get_json()["status"] == "degraded"

    published_page = client.get("/prs/7")
    published_html = published_page.get_data(as_text=True)
    assert "Verified, awaiting GitHub publication." in published_html
    assert "Completed and published to GitHub." not in published_html

    clock.advance(5)
    app.extensions["drain_events"](timeout=1.0)

    recovered = client.get("/healthz")
    assert recovered.status_code == 200
    assert recovered.get_json()["status"] == "alive"

    final = client.get(
        f"/api/submissions/{pass_job_id}",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert final.get_json()["publication"]["published"] is True


class NoopAuth:
    def purge_expired(self) -> int:
        return 0


class LockProbeService:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.runtime: AppRuntime | None = None
        self.lock_released = False
        self.shutdown_calls = 0

    def purge_expired(self) -> int:
        return 0

    def flush_publications(self) -> int:
        assert self.runtime is not None
        # This probe must check availability without blocking on a context manager.
        self.lock_released = self.runtime._lock.acquire(  # pylint: disable=consider-using-with
            blocking=False
        )
        if self.lock_released:
            self.runtime._lock.release()
        return 0

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class DedupeService:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.sync_entered = Event()
        self.release_sync = Event()
        self.action_sync_entered = Event()
        self.release_action_sync = Event()
        self.fail_action_syncs = 1

    def purge_expired(self) -> int:
        return 0

    def flush_publications(self) -> int:
        return 0

    def sync(
        self,
        pr: int,
        *,
        expected_binding: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if expected_binding is None:
            self.sync_entered.set()
            assert self.release_sync.wait(timeout=1.0)
            return {
                "state": "pending",
                "pr": pr,
                "snapshot_id": "snapshot-id",
                "question_count": 3,
            }
        self.action_sync_entered.set()
        assert self.release_action_sync.wait(timeout=1.0)
        if self.fail_action_syncs > 0:
            self.fail_action_syncs -= 1
            raise BotError("RAW_SECRET github detail", code="github_error", status_code=502)
        return {
            "state": "pending",
            "pr": pr,
            "snapshot_id": "snapshot-id",
            "question_count": 3,
        }

    def shutdown(self) -> None:
        return None


def test_runtime_scheduler_is_daemon_lock_free_and_dedupes_retries(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    snapshot = make_snapshot()
    github = FakeGitHub(settings, snapshot)

    clock = FakeClock()
    probe_service = LockProbeService(clock)
    runtime = AppRuntime(settings, probe_service, github, NoopAuth(), start_scheduler=True)
    probe_service.runtime = runtime

    try:
        assert runtime.scheduler_running is True
        assert runtime._scheduler_thread is not None
        assert runtime._scheduler_thread.daemon is True
        assert runtime.tick_once() == 0
        assert probe_service.lock_released is True
    finally:
        runtime.shutdown()

    assert probe_service.shutdown_calls == 1

    dedupe_service = DedupeService(clock)
    runtime = AppRuntime(settings, dedupe_service, github, NoopAuth(), start_scheduler=False)
    try:
        manual_first = runtime.enqueue_manual_sync(snapshot.pr)
        assert dedupe_service.sync_entered.wait(timeout=1.0)

        manual_second = runtime.enqueue_manual_sync(snapshot.pr)
        assert manual_second.job_id == manual_first.job_id

        dedupe_service.release_sync.set()
        assert manual_first.future is not None
        manual_first.future.result(timeout=1.0)

        manual_retry = runtime.enqueue_manual_sync(snapshot.pr)
        assert manual_retry.job_id != manual_first.job_id
        assert manual_retry.future is not None
        manual_retry.future.result(timeout=1.0)

        identity = ActionsIdentity(
            event_name="pull_request_target",
            run_id="101",
            run_attempt="1",
            jti="jti-pr",
            workflow_ref="workflow@refs/heads/main",
        )
        payload = {
            "repository_id": settings.repository_id,
            "pr": snapshot.pr,
            "action": "opened",
            "binding": snapshot.binding(),
        }

        failed = runtime.enqueue_actions_event(identity, payload)
        assert dedupe_service.action_sync_entered.wait(timeout=1.0)

        rerun_identity = ActionsIdentity(
            event_name="pull_request_target",
            run_id="102",
            run_attempt="1",
            jti="jti-pr-rerun",
            workflow_ref="workflow@refs/heads/main",
        )
        deduped = runtime.enqueue_actions_event(rerun_identity, payload)
        assert deduped.job_id == failed.job_id
        assert runtime.action_job_payload(rerun_identity, failed.job_id)["job_id"] == failed.job_id

        wrong_identity = ActionsIdentity(
            event_name="workflow_dispatch",
            run_id="202",
            run_attempt="1",
            jti="jti-workflow",
            workflow_ref="workflow@refs/heads/main",
        )
        with pytest.raises(AppRuntimeError, match="forbidden"):
            runtime.action_job_payload(wrong_identity, failed.job_id)

        dedupe_service.release_action_sync.set()
        assert failed.future is not None
        failed.future.result(timeout=1.0)
        assert runtime.action_job_payload(identity, failed.job_id)["state"] == "error"

        retried = runtime.enqueue_actions_event(identity, payload)
        assert retried.job_id != failed.job_id
    finally:
        runtime.shutdown()


def test_sanitized_request_handler_strips_oauth_query_strings() -> None:
    records: list[tuple[object, ...]] = []
    handler = object.__new__(server_main.SanitizedRequestHandler)
    handler.path = "/auth/github/callback?code=RAW_SECRET&state=secret-state"
    handler.command = "GET"
    handler.request_version = "HTTP/1.1"
    handler.log = lambda *args: records.append(args)

    handler.log_request(302, 123)

    assert records
    assert records[0][3] == "/auth/github/callback"
    assert "RAW_SECRET" not in " ".join(str(item) for item in records[0])
    assert "secret-state" not in " ".join(str(item) for item in records[0])


def test_cli_serve_uses_sanitized_handler_and_shutdowns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(tmp_path)
    captured: dict[str, object] = {}

    class DummyRuntime:
        def __init__(self) -> None:
            self.shutdown_calls = 0

        def shutdown(self) -> None:
            self.shutdown_calls += 1

    class DummyApp:
        def __init__(self, runtime: DummyRuntime) -> None:
            self.extensions = {"runtime": runtime}

    runtime = DummyRuntime()
    dummy_app = DummyApp(runtime)

    def fake_from_env(cls: type[Settings]) -> Settings:
        del cls
        return settings

    def fake_create_app(
        passed_settings: Settings,
        *,
        start_worker: bool = True,
    ) -> DummyApp:
        captured["settings"] = passed_settings
        captured["start_worker"] = start_worker
        return dummy_app

    def fake_run_simple(**kwargs: object) -> None:
        captured["run_simple"] = dict(kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(server_main.Settings, "from_env", classmethod(fake_from_env))
    monkeypatch.setattr(server_main, "create_app", fake_create_app)
    monkeypatch.setattr(server_main, "run_simple", fake_run_simple)

    assert server_main.main(["serve", "--host", "0.0.0.0", "--port", "9000"]) == 0
    assert captured["settings"] == settings
    assert captured["start_worker"] is True
    assert captured["run_simple"] == {
        "hostname": "0.0.0.0",
        "port": 9000,
        "application": dummy_app,
        "use_reloader": False,
        "use_debugger": False,
        "threaded": True,
        "processes": 1,
        "request_handler": server_main.SanitizedRequestHandler,
    }
    assert runtime.shutdown_calls == 1
