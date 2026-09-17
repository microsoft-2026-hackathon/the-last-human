"""Offline HTTP peers; real OAuth, JWT, GitHub, Git, model parsing and SQLite.

Git fetch is the sole filesystem transport seam. Objects are manufactured without
staging or committing; every tenant deliberately receives identical commit SHAs.
Chat responses are scripted protocol data, not evidence of live model quality.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.request
import zlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import unified_diff
from io import BytesIO
from pathlib import Path
from threading import Event, Lock
from typing import cast
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import Flask
from flask.testing import FlaskClient
from werkzeug.test import TestResponse

from lasthuman.server import relay, snapshot as snapshot_module
from lasthuman.server.auth import AuthManager, AuthlibGitHubOAuth
from lasthuman.server.config import GatewaySettings, load_settings
from lasthuman.server.events import DynamicOIDCVerifier
from lasthuman.server.gateway import TenantManager, create_gateway
from lasthuman.server.github import GitHubInstallationDiscovery
from lasthuman.server.registry import RepositoryRegistry
from lasthuman.server.snapshot import Snapshot, SnapshotReader

Json = dict[str, object]
ORIGIN = "https://bot.example"
ISSUER = "https://token.actions.githubusercontent.com"
MODEL_URL = "https://offline-model.example/chat/completions"
WORKFLOW = "lasthuman-app.yml"
HOLD = "PRIVATE_HOLD: I do not know yet."
PASS = "PRIVATE_PASS: refresh catches RuntimeError and returns the original token."
CORE = {"contents": "read", "pull_requests": "write", "statuses": "write", "actions": "write"}
POLICY = "mode: internal\nthreshold: 40\nsignals:\n  criticalPaths:\n    - 'app/auth/**': 60\n"
WORKFLOW_TEXT = """name: Last Human
on:
  pull_request_target:
  workflow_dispatch:
    inputs:
      receipt_id:
        required: true
permissions:
  contents: read
  id-token: write
jobs:
  relay:
    runs-on: ubuntu-latest
    steps:
      - run: python -m lasthuman.server.relay
"""
BEFORE = "from app.transport import post_json\n\n\ndef refresh(token):\n    return post_json(token)\n"
AFTER = """from app.transport import post_json


def refresh(token):
    try:
        return post_json(token)
    except RuntimeError:
        return token
"""


def obj(value: object) -> Json:
    assert isinstance(value, dict), value
    assert all(isinstance(key, str) for key in value)
    return cast(Json, value)


def response(request: requests.PreparedRequest, status: int, payload: object = None,
             headers: Mapping[str, str] | None = None) -> requests.Response:
    result = requests.Response()
    result.status_code, result.request, result.url = status, request, request.url
    result.encoding = "utf-8"
    result._content = b"" if payload is None else json.dumps(payload).encode()
    result.headers.update({"Content-Type": "application/json", "Content-Length": str(len(result.content))})
    result.headers.update(headers or {})
    return result


class Clock:
    def __init__(self) -> None:
        self.value = time.time()

    def __call__(self) -> float:
        return self.value


class GitCorpus:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.objects: dict[str, bytes] = {}
        self.fetches: list[Path] = []
        self.lock = Lock()
        files = {
            ".lasthuman.yml": POLICY, ".github/workflows/" + WORKFLOW: WORKFLOW_TEXT,
            "CODEOWNERS": "/app/auth/ @security-team\n", "app/auth/token.py": BEFORE,
            "app/transport.py": (
                'def post_json(token):\n    if not token:\n        raise RuntimeError("missing token")\n'
                '    return token + "-renewed"\n'
            ),
        }
        self.base_sha = self.commit(self.tree(files), None)
        files["app/auth/token.py"] = AFTER
        self.head_sha = self.commit(self.tree(files), self.base_sha)
        self.runner = snapshot_module._default_runner

    def git_object(self, kind: str, content: bytes) -> str:
        raw = f"{kind} {len(content)}\0".encode() + content
        digest = hashlib.sha1(raw, usedforsecurity=False).hexdigest()
        self.objects[digest] = zlib.compress(raw)
        return digest

    def tree(self, files: Mapping[str, str]) -> str:
        entries = []
        for name, content in files.items():
            if "/" not in name:
                digest = self.git_object("blob", content.encode())
                entries.append((name, f"100644 {name}\0".encode() + bytes.fromhex(digest)))
        for name in sorted({name.split("/", 1)[0] for name in files if "/" in name}):
            digest = self.tree({path[len(name) + 1:]: text for path, text in files.items()
                                if path.startswith(name + "/")})
            entries.append((name + "/", f"40000 {name}\0".encode() + bytes.fromhex(digest)))
        return self.git_object("tree", b"".join(entry for _, entry in sorted(entries)))

    def commit(self, tree: str, parent: str | None) -> str:
        lines = [f"tree {tree}"] + ([] if parent is None else [f"parent {parent}"])
        lines += ["author Fixture <fixture@example.invalid> 1700000000 +0000",
                  "committer Fixture <fixture@example.invalid> 1700000000 +0000", "", "Offline corpus", ""]
        return self.git_object("commit", "\n".join(lines).encode())

    def run(self, args: tuple[str, ...], *, cwd: Path | None, env: Mapping[str, str],
            timeout: int) -> snapshot_module._GitResult:
        assert timeout > 0
        if cwd is not None:
            assert cwd.resolve().is_relative_to(self.root.resolve())
        if args[:2] != ("git", "fetch"):
            assert args[1] in {"init", "remote", "rev-parse", "diff", "show", "ls-tree", "cat-file"}
            if args[1] == "init":
                assert Path(args[-1]).resolve().is_relative_to(self.root.resolve())
            return self.runner(args, cwd=self.root if cwd is None else cwd, env=env, timeout=timeout)
        assert cwd is not None
        assert args[-2:] == (self.base_sha, "+refs/pull/1/head:refs/tlh/pr/1")
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        with self.lock:
            self.fetches.append(cwd)
            for digest, compressed in self.objects.items():
                path = cwd / "objects" / digest[:2] / digest[2:]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(compressed)
            ref = cwd / "refs/tlh/pr/1"
            ref.parent.mkdir(parents=True, exist_ok=True)
            ref.write_text(self.head_sha + "\n", encoding="ascii")
        return snapshot_module._GitResult(0)


class ScriptedChat:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.anchors: set[str] = set()
        self.lock = Lock()

    def open(self, request: urllib.request.Request, *, timeout: float) -> BytesIO:
        assert request.full_url == MODEL_URL, "Unscripted external model/network request"
        assert timeout > 0
        payload = obj(json.loads(request.data or b""))
        messages = payload["messages"]
        assert isinstance(messages, list) and len(messages) == 1
        prompt = obj(messages[0])["content"]
        assert isinstance(prompt, str)
        if payload.get("response_format") is not None:
            schema = obj(obj(obj(payload["response_format"])["json_schema"])["schema"])
            questions = obj(obj(schema["properties"])["questions"])
            properties = obj(obj(questions["items"])["properties"])
            anchors = obj(properties["anchor"])["enum"]
            paths = obj(properties["evidencePath"])["enum"]
            assert isinstance(anchors, list) and anchors
            assert isinstance(paths, list) and "app/transport.py" in paths
            anchor = anchors[0]
            assert isinstance(anchor, str) and anchor in prompt
            with self.lock:
                self.anchors.add(anchor)
                self.calls.append("questions")
            result = {"questions": [
                {"type": "consequence", "anchor": anchor, "text": "What happens after RuntimeError?",
                 "choices": ["The original token", "None", "Empty token", "Renewed token"], "answerIndex": 0,
                 "expectedEvidence": "refresh catches RuntimeError and returns the original token.",
                 "evidencePath": "app/auth/token.py"},
                {"type": "structure", "anchor": anchor, "text": "Which app/transport.py behavior reaches fallback?",
                 "choices": ["Raises RuntimeError for missing token", "Returns None", "Retries", "Caches"],
                 "answerIndex": 0, "expectedEvidence": "post_json raises RuntimeError when token is empty.",
                 "evidencePath": "app/transport.py"},
            ]}
        else:
            assert (HOLD in prompt) != (PASS in prompt)
            verdict = "hold" if HOLD in prompt else "pass"
            with self.lock:
                self.calls.append(verdict)
            result = {"verdict": verdict, "hint": "Inspect refresh in app/auth/token.py."}
        return BytesIO(json.dumps({"choices": [
            {"finish_reason": "stop", "message": {"content": json.dumps(result)}}
        ]}).encode())


@dataclass
class Repository:
    repository_id: int
    name: str
    owner_id: int
    installation_id: int
    removed: bool = False
    suspended: bool = False
    outage: int | None = None
    rate_limited: bool = False
    extra_scope: bool = False
    token_permissions: dict[str, str] | None = None
    token_expired: bool = False
    permissions: dict[str, str] = field(default_factory=lambda: dict(CORE))
    opted_in: bool = True
    merged: bool = False
    fail_writes: bool = False
    comments: list[Json] = field(default_factory=list)
    statuses: list[Json] = field(default_factory=list)
    checks: list[Json] = field(default_factory=list)
    dispatches: list[Json] = field(default_factory=list)

    def metadata(self, *, push: bool = False) -> Json:
        return {"id": self.repository_id, "full_name": self.name,
                "owner": {"id": self.owner_id, "login": self.name.split("/", 1)[0]},
                "permissions": {"pull": True, "push": push}, "default_branch": "main"}

    def pull(self, corpus: GitCorpus) -> Json:
        when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if self.merged else None
        return {"number": 1, "state": "closed" if self.merged else "open", "merged": self.merged,
                "merged_at": when, "closed_at": when, "merge_commit_sha": corpus.head_sha if self.merged else None,
                "commits": 1, "title": "Preserve token when renewal fails", "body": "Return token after RuntimeError.",
                "user": {"id": 7, "login": "pr-author"}, "author_association": "CONTRIBUTOR", "labels": [],
                "head": {"sha": corpus.head_sha, "ref": "change", "repo": self.metadata()},
                "base": {"sha": corpus.base_sha, "ref": "main", "repo": self.metadata()},
                "changed_files": 1, "additions": 4, "deletions": 1}


@dataclass(frozen=True)
class Call:
    method: str
    path: str
    token: str = field(repr=False)
    body: Json = field(default_factory=dict)


class RegistrationHTTP:
    def __init__(self, settings: GatewaySettings, corpus: GitCorpus, key: rsa.RSAPrivateKey) -> None:
        self.settings, self.corpus, self.key = settings, corpus, key
        self.repositories = {101: Repository(101, "acme/one", 77, 201), 102: Repository(102, "acme/two", 77, 202)}
        self.calls: list[Call] = []
        self.codes: dict[str, tuple[str, str]] = {}
        self.users: dict[str, tuple[int, set[int]]] = {}
        self.tokens: dict[str, tuple[int, int, Json]] = {}
        self.workflow_tokens: dict[str, int] = {}
        self.oidc_context = (101, "pull_request_target", "1001")
        self.blocks: dict[int, tuple[Event, Event]] = {}
        self.dispatch_events: dict[int, Event] = {}
        self.jwks_fetches = 0
        self.sequence = 0
        self.lock = Lock()
        self.harness: Integration | None = None

    def signed_token(self, repository_id: int, *, event: str = "pull_request_target", run_id: str = "1001",
                     claims: Mapping[str, object] | None = None, key: rsa.RSAPrivateKey | None = None) -> str:
        repo, now = self.repositories[repository_id], int(time.time())
        payload = {"iss": ISSUER, "aud": repo.name, "sub": f"repo:{repo.name}:ref:refs/heads/main",
                   "iat": now - 5, "nbf": now - 5, "exp": now + 600,
                   "jti": f"offline-{repository_id}-{event}-{run_id}", "repository": repo.name,
                   "repository_id": str(repository_id), "repository_owner_id": str(repo.owner_id),
                   "event_name": event, "ref": "refs/heads/main",
                   "workflow_ref": f"{repo.name}/.github/workflows/{WORKFLOW}@refs/heads/main",
                   "run_id": run_id, "run_attempt": "1"}
        payload.update(claims or {})
        return jwt.encode(payload, key or self.key, algorithm="RS256", headers={"kid": "offline-key"})

    def jwks(self, client: jwt.PyJWKClient) -> Json:
        assert client.uri == ISSUER + "/.well-known/jwks"
        self.jwks_fetches += 1
        key = obj(json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key())))
        return {"keys": [{**key, "kid": "offline-key", "alg": "RS256", "use": "sig"}]}

    def oauth_code(self, url: str, *, actor: int = 7, allowed: set[int]) -> str:
        query = parse_qs(urlsplit(url).query)
        assert query["client_id"] == [self.settings.client_id]
        assert query["redirect_uri"] == [ORIGIN + "/auth/github/callback"]
        assert query["code_challenge_method"] == ["S256"]
        code = f"code-{len(self.codes)}-{len(self.users)}"
        token = "user-" + code
        self.codes[code] = (query["code_challenge"][0], token)
        self.users[token] = (actor, set(allowed))
        return code

    def send(self, request: requests.PreparedRequest, **kwargs: object) -> requests.Response:
        assert kwargs.get("timeout") is not None
        parsed = urlsplit(request.url or "")
        assert parsed.scheme == "https"
        if parsed.netloc == "bot.example":
            assert self.harness is not None
            if request.method == "GET":
                self.harness.drain()
            result = self.harness.client().open(
                parsed.path + ("?" + parsed.query if parsed.query else ""), method=request.method,
                data=request.body, headers=dict(request.headers),
            )
            return response(request, result.status_code, result.get_json(), dict(result.headers))
        if parsed.netloc == "token.actions.githubusercontent.com":
            repo_id, event, run_id = self.oidc_context
            assert parsed.path == "/id"
            assert request.headers["Authorization"] == "Bearer offline-oidc-request"
            assert parse_qs(parsed.query)["audience"] == [self.repositories[repo_id].name]
            return response(request, 200, {"value": self.signed_token(repo_id, event=event, run_id=run_id)})
        if parsed.netloc == "github.com" and parsed.path == "/login/oauth/access_token":
            return self.oauth(request)
        assert parsed.netloc == "api.github.com", "Unscripted external HTTP request: " + parsed.netloc
        call = Call(request.method or "", parsed.path, request.headers.get("Authorization", "").removeprefix("Bearer "),
                    obj(json.loads(request.body or b"{}")))
        with self.lock:
            self.calls.append(call)
        for repo_id, (entered, release) in self.blocks.items():
            if call.path == f"/repos/{self.repositories[repo_id].name}/installation":
                entered.set()
                assert release.wait(timeout=10), "Blocked HTTP discovery was not released"
        with self.lock:
            return self.github(request, call)

    def oauth(self, request: requests.PreparedRequest) -> requests.Response:
        assert request.method == "POST"
        raw = request.body
        assert isinstance(raw, (str, bytes))
        fields = parse_qs(raw.decode() if isinstance(raw, bytes) else raw)
        assert fields["grant_type"] == ["authorization_code"]
        assert fields["client_id"] == [self.settings.client_id]
        assert fields["client_secret"] == [self.settings.client_secret]
        assert fields["redirect_uri"] == [ORIGIN + "/auth/github/callback"]
        expected, token = self.codes.pop(fields["code"][0])
        challenge = base64.urlsafe_b64encode(hashlib.sha256(fields["code_verifier"][0].encode()).digest())
        assert challenge.decode().rstrip("=") == expected
        return response(request, 200, {"access_token": token, "token_type": "bearer", "expires_in": 1800})

    def github(self, request: requests.PreparedRequest, call: Call) -> requests.Response:
        if call.path == "/user":
            actor, _ = self.users[call.token]
            return response(request, 200, {"id": actor, "login": "pr-author" if actor == 7 else "writer"})
        if call.path.startswith("/app/installations/"):
            return self.installation_token(request, call)
        match = re.fullmatch(r"/repos/([^/]+/[^/]+)(.*)", call.path)
        assert match is not None, call
        name, suffix = match.groups()
        matches = [repo for repo in self.repositories.values() if repo.name == name]
        assert len(matches) == 1, name
        repo = matches[0]
        if suffix == "/installation":
            jwt.decode(call.token, self.key.public_key(), algorithms=["RS256"], issuer=self.settings.client_id,
                       options={"require": ["iss", "iat", "exp"]})
            if repo.outage is not None:
                return response(request, repo.outage, {"message": "Offline outage"},
                                {"X-RateLimit-Remaining": "0"} if repo.rate_limited else {})
            if repo.removed:
                return response(request, 404, {"message": "Removed"})
            return response(request, 200, {"id": repo.installation_id, "app_id": self.settings.app_id,
                                          "account": {"id": repo.owner_id}, "permissions": repo.permissions,
                                          "suspended_at": "2026-01-01T00:00:00Z" if repo.suspended else None})
        push = False
        if call.token in self.users:
            actor, allowed = self.users[call.token]
            if repo.repository_id not in allowed:
                return response(request, 404, {"message": "Repository unavailable"})
            assert call.method == "GET", "OAuth may not publish App-owned results"
            push = actor != 7
        elif call.token in self.workflow_tokens:
            assert self.workflow_tokens[call.token] == repo.repository_id
            assert call.method == "GET", "Actions transport must remain read-only"
        else:
            repo_id, installation_id, permissions = self.tokens[call.token]
            assert repo_id == repo.repository_id
            if installation_id != repo.installation_id or repo.removed or repo.suspended:
                return response(request, 401, {"message": "Token revoked"})
            if "check-runs" in suffix:
                assert permissions.get("checks") == "write"
        if call.method != "GET" and repo.fail_writes:
            return response(request, 503, {"message": "Offline write outage"})
        if not suffix:
            return response(request, 200, repo.metadata(push=push))
        if suffix.startswith("/contents/"):
            assert parse_qs(urlsplit(request.url or "").query) == {"ref": ["main"]}
            if not repo.opted_in:
                return response(request, 404, {"message": "No opt-in"})
            text = {".lasthuman.yml": POLICY, ".github/workflows/" + WORKFLOW: WORKFLOW_TEXT}[
                suffix.removeprefix("/contents/")]
            return response(request, 200, {"type": "file", "encoding": "base64",
                                          "content": base64.b64encode(text.encode()).decode()})
        if suffix == "/pulls/1":
            return response(request, 200, repo.pull(self.corpus))
        if suffix == "/pulls":
            return response(request, 200, [] if repo.merged else [repo.pull(self.corpus)])
        if suffix == "/pulls/1/files":
            patch = "".join(list(unified_diff(
                BEFORE.splitlines(keepends=True), AFTER.splitlines(keepends=True),
            ))[2:])
            return response(request, 200, [{"filename": "app/auth/token.py", "status": "modified",
                                           "additions": 4, "deletions": 1, "patch": patch}])
        if suffix.startswith("/issues/"):
            return self.comments(request, call, repo, suffix)
        if suffix.startswith("/statuses/"):
            assert call.method == "POST" and suffix == f"/statuses/{self.corpus.head_sha}"
            result = {**call.body, "id": self.next_id()}
            repo.statuses.insert(0, result)
            return response(request, 201, result)
        if suffix == f"/commits/{self.corpus.head_sha}/status":
            return response(request, 200, {"sha": self.corpus.head_sha, "statuses": repo.statuses})
        if suffix == f"/commits/{self.corpus.head_sha}":
            return response(request, 200, {"sha": self.corpus.head_sha, "parents": [{"sha": self.corpus.base_sha}]})
        if suffix == f"/actions/workflows/{WORKFLOW}/dispatches":
            assert call.method == "POST" and call.body["ref"] == "refs/heads/main"
            repo.dispatches.append(call.body)
            if repo.repository_id in self.dispatch_events:
                self.dispatch_events[repo.repository_id].set()
            return response(request, 204)
        if "check-runs" in suffix:
            if call.method == "GET" and suffix.startswith("/commits/"):
                return response(request, 200, {"total_count": len(repo.checks), "check_runs": repo.checks})
            if call.method == "POST":
                check = {**call.body, "id": self.next_id(), "app": {"id": self.settings.app_id},
                         "html_url": f"https://github.com/{repo.name}/checks/{self.sequence}"}
                repo.checks.append(check)
                return response(request, 201, check)
            check = next(item for item in repo.checks if item["id"] == int(suffix.rsplit("/", 1)[1]))
            if call.method == "PATCH":
                check.update(call.body)
            return response(request, 200, check)
        raise AssertionError(f"Unscripted GitHub route: {call.method} {call.path}")

    def installation_token(self, request: requests.PreparedRequest, call: Call) -> requests.Response:
        assert call.method == "POST"
        jwt.decode(call.token, self.key.public_key(), algorithms=["RS256"], issuer=self.settings.client_id)
        ids = call.body["repository_ids"]
        assert isinstance(ids, list) and len(ids) == 1
        repo = self.repositories[ids[0]]
        assert call.path == f"/app/installations/{repo.installation_id}/access_tokens"
        requested = obj(call.body["permissions"])
        assert requested in (CORE, {"checks": "write"})
        if any(repo.permissions.get(name) != grant for name, grant in requested.items()):
            return response(request, 422, {"message": "Missing grant"})
        permissions = requested if repo.token_permissions is None else repo.token_permissions
        token = f"installation-{repo.repository_id}-{repo.installation_id}-{self.next_id()}"
        self.tokens[token] = (repo.repository_id, repo.installation_id, permissions)
        repositories = [repo.metadata()]
        if repo.extra_scope:
            repositories.append({"id": 9999, "full_name": "foreign/unscoped"})
        expiry = datetime.now(timezone.utc) + timedelta(hours=-1 if repo.token_expired else 1)
        return response(request, 201, {"token": token, "expires_at": expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                      "permissions": permissions, "repositories": repositories})

    def comments(self, request: requests.PreparedRequest, call: Call, repo: Repository,
                 suffix: str) -> requests.Response:
        if call.method == "GET":
            return response(request, 200, repo.comments)
        if call.method == "POST":
            number = self.next_id()
            comment = {**call.body, "id": number,
                       "html_url": f"https://github.com/{repo.name}/pull/1#issuecomment-{number}",
                       "user": {"type": "Bot"}, "performed_via_github_app": {"id": self.settings.app_id}}
            repo.comments.append(comment)
            return response(request, 201, comment)
        assert call.method == "PATCH"
        comment = next(item for item in repo.comments if item["id"] == int(suffix.rsplit("/", 1)[1]))
        comment.update(call.body)
        return response(request, 200, comment)

    def next_id(self) -> int:
        self.sequence += 1
        return self.sequence


class GatewayClient(FlaskClient):
    def open(self, *args: object, **kwargs: object) -> TestResponse:
        kwargs.setdefault("base_url", ORIGIN)
        return super().open(*args, **kwargs)


@dataclass
class Integration:
    root: Path
    settings: GatewaySettings
    corpus: GitCorpus
    http: RegistrationHTTP
    chat: ScriptedChat
    clock: Clock
    verifier: DynamicOIDCVerifier
    app: Flask
    registry: RepositoryRegistry
    manager: TenantManager

    def client(self) -> FlaskClient:
        return self.app.test_client()

    def headers(self, repo_id: int, *, event: str = "pull_request_target", run_id: str = "1001") -> dict[str, str]:
        return {"Authorization": "Bearer " + self.http.signed_token(repo_id, event=event, run_id=run_id)}

    def actions_settings(self, repo_id: int) -> relay.ActionSettings:
        repo = self.http.repositories[repo_id]
        token = f"workflow-{repo_id}"
        self.http.workflow_tokens[token] = repo_id
        return relay.ActionSettings(
            repository=repo.name, repository_id=repo_id, owner_id=repo.owner_id, workflow=WORKFLOW,
            workflow_ref="refs/heads/main", bot_url=ORIGIN, oidc_audience=repo.name, github_token=token,
        )

    def snapshot(self, repo_id: int) -> Snapshot:
        github = relay.ActionsGitHubClient(self.actions_settings(repo_id), requests.Session())
        return SnapshotReader(github, self.root / "actions-cache" / str(repo_id)).read(1)

    def body(self, repo_id: int) -> Json:
        return {"repository_id": repo_id, "pr": 1, "action": "opened", "binding": self.snapshot(repo_id).binding()}

    def open_event(self, repo_id: int) -> Json:
        result = self.client().post("/api/actions/events", json=self.body(repo_id), headers=self.headers(repo_id))
        assert result.status_code == 202, result.get_data(as_text=True)
        self.manager.resolve(repo_id).service.clock = self.clock
        self.drain()
        accepted = obj(result.get_json())
        assert str(accepted["url"]).startswith("/api/actions/jobs/")
        completed = self.client().get(str(accepted["url"]), headers=self.headers(repo_id))
        assert completed.status_code == 200
        job = obj(completed.get_json())
        assert job["state"] == "completed", job
        assert obj(job["result"])["state"] == "pending", job
        return accepted

    def drain(self) -> None:
        self.manager.drain_events(timeout=10)

    def advance(self, seconds: float) -> None:
        self.clock.value += seconds
        for tenant in self.manager.containers():
            tenant.service.clock = self.clock

    def browser(self, repo_id: int, *, actor: int = 7, allowed: set[int] | None = None,
                client: FlaskClient | None = None) -> tuple[FlaskClient, str]:
        browser = self.client() if client is None else client
        tenant = self.manager.resolve(repo_id)
        prefix = tenant.settings.path_prefix
        login = browser.get(prefix + "/auth/github", query_string={"next": "/dashboard"})
        assert login.status_code == 302
        assert tenant.settings.session_cookie_name + "=" in login.headers["Set-Cookie"]
        assert "Path=/" in login.headers["Set-Cookie"] and "HttpOnly" in login.headers["Set-Cookie"]
        state = parse_qs(urlsplit(login.location).query)["state"][0]
        assert state.startswith(f"r{repo_id}.")
        code = self.http.oauth_code(login.location, actor=actor, allowed={repo_id} if allowed is None else allowed)
        callback = browser.get("/auth/github/callback", query_string={"state": state, "code": code})
        assert callback.status_code == 302, callback.get_data(as_text=True)
        assert callback.location == prefix + "/dashboard"
        dashboard = browser.get(callback.location)
        assert dashboard.status_code == 200, dashboard.get_data(as_text=True)
        match = re.search(r'name="csrf-token" content="([^"]+)"', dashboard.get_data(as_text=True))
        assert match is not None
        auth = tenant.app.extensions["auth"]
        assert isinstance(auth, AuthManager) and isinstance(auth.oauth, AuthlibGitHubOAuth)
        return browser, match.group(1)

    def answers(self, browser: FlaskClient, repo_id: int, text: str, request_id: str) -> Json:
        page = browser.get(f"/repos/{repo_id}/prs/1")
        assert page.status_code == 200, page.get_data(as_text=True)
        html = page.get_data(as_text=True)
        snapshot_id = re.search(r'id="snapshot-id" value="([^"]+)"', html)
        assert snapshot_id is not None
        question_ids = re.findall(r'<section class="question" data-question-id="([^"]+)"', html)
        assert len(question_ids) == 2, html
        assert f"/repos/{repo_id}/api/prs/1/submissions" in html
        return {"snapshot_id": snapshot_id.group(1), "request_id": request_id,
                "answers": [{"id": key, "text": text, "choice": 0} for key in question_ids]}

    def submit(self, browser: FlaskClient, csrf: str, repo_id: int, text: str, request_id: str) -> tuple[Json, Json]:
        result = browser.post(f"/repos/{repo_id}/api/prs/1/submissions",
                              json=self.answers(browser, repo_id, text, request_id), headers={"X-CSRF-Token": csrf})
        assert result.status_code == 202, result.get_data(as_text=True)
        accepted = obj(result.get_json())
        assert str(accepted["url"]).startswith(f"/repos/{repo_id}/api/submissions/")
        self.drain()
        completed = browser.get(str(accepted["url"]), headers={"X-CSRF-Token": csrf})
        assert completed.status_code == 200
        return accepted, obj(completed.get_json())

    def environment(self, repo_id: int, payload: Json, event: str) -> dict[str, str]:
        settings = self.actions_settings(repo_id)
        path = self.root / f"{repo_id}-{event}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.http.oidc_context = (repo_id, event, "2001")
        return {"GITHUB_REPOSITORY": settings.repository, "GITHUB_REPOSITORY_ID": str(repo_id),
                "GITHUB_REPOSITORY_OWNER_ID": str(settings.owner_id), "GITHUB_REF": "refs/heads/main",
                "GITHUB_WORKFLOW_REF": f"{settings.repository}/.github/workflows/{WORKFLOW}@refs/heads/main",
                "GITHUB_EVENT_NAME": event, "GITHUB_EVENT_PATH": str(path),
                "GITHUB_RUN_ID": "2001", "GITHUB_RUN_ATTEMPT": "1", "TLH_BOT_URL": ORIGIN,
                "GH_TOKEN": settings.github_token, "ACTIONS_ID_TOKEN_REQUEST_URL": ISSUER + "/id",
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "offline-oidc-request"}

    def five_steps(self, repo_id: int, receipt_id: str) -> Json:
        repo = self.http.repositories[repo_id]
        assert any(obj(dispatch["inputs"])["receipt_id"] == receipt_id for dispatch in repo.dispatches)
        environment = self.environment(repo_id, {"inputs": {"receipt_id": receipt_id}}, "workflow_dispatch")
        state_file = self.root / str(repo_id) / "verification" / "state.json"
        first = None
        state: Json = {}
        for step in ("receipt", "snapshot", "compare", "verify", "publication"):
            relay.run(environ=environment, session=requests.Session(), github_session=requests.Session(),
                      cache_dir=self.root / "verification-cache" / str(repo_id),
                      verification_step=step, state_file=state_file)
            state = obj(json.loads(state_file.read_text(encoding="utf-8")))
            assert state["stage"] == step and state["repository_path"] == f"/repos/{repo_id}"
            assert state["repository_id"] == repo_id
            receipt = obj(state["receipt"])
            assert set(receipt) == {"receipt_id", "binding", "actor_id", "question_version",
                                    "issued_at", "app_id", "installation_id"}
            first = receipt if first is None else first
            assert receipt == first
            assert HOLD not in state_file.read_text(encoding="utf-8")
            assert PASS not in state_file.read_text(encoding="utf-8")
            assert state_file.stat().st_mode & 0o077 == 0
        publication = obj(state["publication"])
        assert publication["context"] == "last-human/human-verified"
        assert publication["target_url"] == f"{ORIGIN}/repos/{repo_id}/receipts/{receipt_id}"
        status = next(item for item in repo.statuses if item["id"] == publication["status_id"])
        assert status["state"] == "success" and status["target_url"] == publication["target_url"]
        assert any(call.method == "POST" and call.path == f"/repos/{repo.name}/statuses/{self.corpus.head_sha}"
                   and call.body == {key: status[key] for key in ("state", "description", "context", "target_url")}
                   for call in self.http.calls)
        return state

    def restart(self) -> None:
        self.manager.shutdown()
        self.registry = RepositoryRegistry(
            self.settings, GitHubInstallationDiscovery(self.settings, requests.Session()), clock=self.clock,
        )
        self.app = create_gateway(self.settings, registry=self.registry, verifier=self.verifier,
                                  github_session_factory=requests.Session, start_worker=False)
        self.app.test_client_class = GatewayClient
        self.app.config["TESTING"] = True
        self.manager = cast(TenantManager, self.app.extensions["tenant_manager"])


@pytest.fixture
def integration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Integration]:
    for name in tuple(os.environ):
        if name.startswith(("TLH_", "LASTHUMAN_")):
            monkeypatch.delenv(name)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_file = tmp_path / "offline-app.pem"
    key_file.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    key_file.chmod(0o600)
    for name, value in {
        "TLH_REGISTRATION_MODE": "first-event", "TLH_MODE": "live", "TLH_APP_ID": "901",
        "TLH_CLIENT_ID": "Iv1.offline", "TLH_CLIENT_SECRET": "offline-client-secret-" * 2,
        "TLH_PRIVATE_KEY_FILE": str(key_file), "TLH_BASE_URL": ORIGIN, "TLH_SECRET_KEY": "offline-session-key-" * 2,
        "TLH_REGISTRATION_DATABASE": str(tmp_path / "registry.sqlite"), "TLH_STATE_ROOT": str(tmp_path / "tenants"),
        "TLH_QUESTION_COUNT": "2", "TLH_PRESENTATION_LOCALE": "en", "LASTHUMAN_PROVIDER": "openai",
        "LASTHUMAN_ENDPOINT": MODEL_URL, "LASTHUMAN_API_KEY": "offline-not-a-credential",
        "LASTHUMAN_AUTH_MODE": "default",
    }.items():
        monkeypatch.setenv(name, value)
    settings = load_settings()
    assert isinstance(settings, GatewaySettings) and settings.oidc_audience == ""
    assert not {"TLH_REPOSITORY", "TLH_REPOSITORY_ID", "TLH_OWNER_ID", "TLH_INSTALLATION_ID"} & os.environ.keys()
    corpus, chat, clock = GitCorpus(tmp_path), ScriptedChat(), Clock()
    http = RegistrationHTTP(settings, corpus, key)

    def send(_adapter: requests.adapters.HTTPAdapter, request: requests.PreparedRequest,
             **kwargs: object) -> requests.Response:
        return http.send(request, **kwargs)

    def jwks_fetch(client: jwt.PyJWKClient) -> Json:
        return http.jwks(client)

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", jwks_fetch)
    monkeypatch.setattr(urllib.request, "urlopen", chat.open)
    monkeypatch.setattr(snapshot_module, "_default_runner", corpus.run)
    monkeypatch.setattr(relay, "_JOB_POLL_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(relay, "_PUBLICATION_POLL_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(relay, "_JOB_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(relay, "_PUBLICATION_POLL_INTERVAL_SECONDS", 0.01)
    verifier = DynamicOIDCVerifier(settings)
    registry = RepositoryRegistry(settings, GitHubInstallationDiscovery(settings, requests.Session()), clock=clock)
    app = create_gateway(settings, registry=registry, verifier=verifier,
                         github_session_factory=requests.Session, start_worker=False)
    app.test_client_class, app.config["TESTING"] = GatewayClient, True
    harness = Integration(tmp_path, settings, corpus, http, chat, clock, verifier, app, registry,
                          cast(TenantManager, app.extensions["tenant_manager"]))
    http.harness = harness
    try:
        yield harness
    finally:
        for _entered, release in http.blocks.values():
            release.set()
        harness.manager.shutdown()
