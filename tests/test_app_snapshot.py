from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import pytest

from lasthuman.config import parse_config
from lasthuman.diff import parse_hunks
from lasthuman.models import PrMeta
from lasthuman.risk import score
from lasthuman.server.config import Settings
from lasthuman.server.snapshot import (
    Snapshot,
    SnapshotError,
    SnapshotReader,
    UnsupportedSnapshot,
    _structure_from_object,
    _structure_to_dict,
)
from lasthuman.structure import Callee, StructureContext, SymbolUse

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40

BASE_CONFIG = """mode: internal
threshold: 40
signals:
  criticalPaths:
    - 'app/auth/**': 30
  patterns:
    - 'except\\s': 10
  testsRemoved: 25
  agentHint: 10
  externalContributor: 40
"""

RAW_DIFF = """diff --git .lasthuman.yml .lasthuman.yml
index 1111111..2222222 100644
--- .lasthuman.yml
+++ .lasthuman.yml
@@ -1,5 +1,5 @@
 mode: internal
-threshold: 40
+threshold: 999
 signals:
   criticalPaths:
     - 'app/auth/**': 30
diff --git app/auth/token.py app/auth/token.py
index 3333333..4444444 100644
--- app/auth/token.py
+++ app/auth/token.py
@@ -30,2 +30,6 @@ async def refresh(transport, token):
-    body = await post_json(transport, TOKEN_ENDPOINT, payload)
+    try:
+        body = await post_json(transport, TOKEN_ENDPOINT, payload)
+    except HttpError as err:
+        return token
+    return body
"""

CODEOWNERS = "/app/auth/ @sec\nREADME.md @docs\n"


@dataclass
class FakeGitResult:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


@dataclass
class RecordedGitCall:
    args: tuple[str, ...]
    cwd: Path | None
    env: dict[str, str]
    timeout: int


class FakeGitRunner:
    def __init__(
        self,
        *,
        diff: str = RAW_DIFF,
        base_files: Mapping[str, bytes] | None = None,
        tree_entries: list[tuple[str, str, str, str]] | None = None,
        blobs: Mapping[str, bytes] | None = None,
        rev_parse: str = HEAD_SHA,
    ) -> None:
        self.diff = diff.encode("utf-8")
        self.base_files = dict(base_files or {})
        self.tree_entries = list(tree_entries or [])
        self.blobs = dict(blobs or {})
        self.rev_parse = rev_parse
        self.calls: list[RecordedGitCall] = []

    def __call__(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path | None,
        env: Mapping[str, str],
        timeout: int,
    ) -> FakeGitResult:
        self.calls.append(
            RecordedGitCall(
                args=args,
                cwd=cwd,
                env=dict(env),
                timeout=timeout,
            )
        )
        if args[:3] == ("git", "init", "--bare"):
            return FakeGitResult(0)
        if args[:3] == ("git", "remote", "remove"):
            return FakeGitResult(0)
        if args[:3] == ("git", "remote", "add"):
            return FakeGitResult(0)
        if args[:2] == ("git", "fetch"):
            return FakeGitResult(0)
        if args[:2] == ("git", "rev-parse"):
            return FakeGitResult(0, stdout=(self.rev_parse + "\n").encode("utf-8"))
        if args[:2] == ("git", "diff"):
            return FakeGitResult(0, stdout=self.diff)
        if args[:2] == ("git", "show"):
            value = self.base_files.get(args[2])
            if value is None:
                return FakeGitResult(128, stderr=b"fatal: path does not exist in tree\n")
            return FakeGitResult(0, stdout=value)
        if args[:4] == ("git", "ls-tree", "-r", "-z"):
            records = bytearray()
            for mode, kind, sha, path in self.tree_entries:
                size = len(self.blobs.get(sha, b""))
                records.extend(f"{mode} {kind} {sha} {size}\t{path}".encode("utf-8"))
                records.append(0)
            return FakeGitResult(0, stdout=bytes(records))
        if args[:3] == ("git", "cat-file", "-s"):
            blob = self.blobs.get(args[3], b"")
            return FakeGitResult(0, stdout=f"{len(blob)}\n".encode("utf-8"))
        if args[:3] == ("git", "cat-file", "blob"):
            blob = self.blobs.get(args[3])
            if blob is None:
                return FakeGitResult(128, stderr=b"fatal: bad object\n")
            return FakeGitResult(0, stdout=blob)
        raise AssertionError(f"unexpected git command: {args}")


class FakeGitHubClient:
    def __init__(
        self,
        settings: Settings,
        *,
        pulls: list[dict[str, object]],
        files_payload: list[dict[str, object]],
        same_head_payload: list[dict[str, object]] | None = None,
        token: str = "secret-token",
    ) -> None:
        self.settings = settings
        self._pulls = [deepcopy(item) for item in pulls]
        self._files = [deepcopy(item) for item in files_payload]
        self._same_head = [deepcopy(item) for item in (same_head_payload or [])]
        self._pull_calls = 0
        self._token = token

    def pull(self, pr: int, user_token: str | None = None) -> dict[str, object]:
        del user_token
        payload = self._pulls[min(self._pull_calls, len(self._pulls) - 1)]
        self._pull_calls += 1
        if payload["number"] != pr:
            raise AssertionError("wrong PR requested")
        return deepcopy(payload)

    def files(self, pr: int) -> list[dict[str, object]]:
        if pr != self._pulls[0]["number"]:
            raise AssertionError("wrong PR requested")
        return deepcopy(self._files)

    def pulls_with_head(self, sha: str) -> list[dict[str, object]]:
        if sha != self._pulls[0]["head"]["sha"]:
            raise AssertionError("wrong head requested")
        return deepcopy(self._same_head)

    def installation_token(self) -> str:
        return self._token


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
        base_url="http://localhost:8000",
        secret_key="k" * 32,
        database=tmp_path / "lasthuman.sqlite3",
        mode="development",
        status_context="comprehension-gate-dev",
        workflow="lasthuman-app.yml",
        workflow_ref="refs/heads/main",
        oidc_audience="hunhoon21/the-last-human",
    )


def repo_payload(settings: Settings) -> dict[str, object]:
    return {
        "id": settings.repository_id,
        "full_name": settings.repository,
        "owner": {"id": settings.owner_id},
    }


def make_pull(
    settings: Settings,
    *,
    state: str = "open",
    body: str = "Explains refresh fallback",
    labels: tuple[str, ...] = ("bugfix",),
    changed_files: int = 2,
    additions: int = 6,
    deletions: int = 2,
    author_id: int = 7,
    author_login: str = "octocat",
    author_association: str = "OWNER",
    base_ref: str = "main",
    head_ref: str = "feat/snapshot",
    head_sha: str = HEAD_SHA,
    base_sha: str = BASE_SHA,
) -> dict[str, object]:
    return {
        "number": 7,
        "state": state,
        "title": "Handle token refresh errors",
        "body": body,
        "author_association": author_association,
        "changed_files": changed_files,
        "additions": additions,
        "deletions": deletions,
        "user": {"id": author_id, "login": author_login},
        "labels": [{"name": name} for name in labels],
        "base": {
            "ref": base_ref,
            "sha": base_sha,
            "repo": repo_payload(settings),
        },
        "head": {
            "ref": head_ref,
            "sha": head_sha,
        },
    }


def make_files() -> list[dict[str, object]]:
    return [
        {
            "filename": ".lasthuman.yml",
            "status": "modified",
            "additions": 1,
            "deletions": 1,
            "patch": "@@",
        },
        {
            "filename": "app/auth/token.py",
            "status": "modified",
            "additions": 5,
            "deletions": 1,
            "patch": "@@",
        },
    ]


def make_tree() -> tuple[list[tuple[str, str, str, str]], dict[str, bytes]]:
    token_sha = "1" * 40
    init_sha = "2" * 40
    consumer_sha = "3" * 40
    token_lines = [f"# filler {i}" for i in range(1, 30)]
    token_lines.extend(
        [
            "async def refresh(transport, token):",
            "    try:",
            "        body = await post_json(transport, TOKEN_ENDPOINT, payload)",
            "    except HttpError as err:",
            "        return token",
            "    return body",
        ]
    )
    entries = [
        ("100644", "blob", token_sha, "app/auth/token.py"),
        ("100644", "blob", init_sha, "app/auth/__init__.py"),
        ("100644", "blob", consumer_sha, "app/consumer.py"),
    ]
    blobs = {
        token_sha: ("\n".join(token_lines) + "\n").encode("utf-8"),
        init_sha: b"",
        consumer_sha: (
            "from app.auth import token\n\n"
            "def run():\n"
            "    return token.refresh(None, None)\n"
        ).encode("utf-8"),
    }
    return entries, blobs


def make_reader(tmp_path: Path, client: FakeGitHubClient, runner: FakeGitRunner) -> SnapshotReader:
    return SnapshotReader(client, tmp_path / ".work", runner=runner)


@pytest.mark.parametrize("changed_source", ["risk.py", "interview.py"])
def test_policy_version_covers_question_schema_source(tmp_path: Path, changed_source: str) -> None:
    settings = make_settings(tmp_path)
    client = FakeGitHubClient(settings, pulls=[make_pull(settings)], files_payload=make_files())
    for name in ("risk.py", "interview.py"):
        (tmp_path / name).write_bytes(f"original {name}".encode())
    reader = SnapshotReader(client, tmp_path / ".work", runner=FakeGitRunner(), source_root=tmp_path)
    original = reader._policy_version(BASE_CONFIG.encode())
    assert original == reader._policy_version(BASE_CONFIG.encode())

    (tmp_path / changed_source).write_bytes(b"changed schema or risk policy")

    assert original != reader._policy_version(BASE_CONFIG.encode())


def test_snapshot_reader_matches_core_diff_and_risk_and_uses_base_policy(tmp_path: Path):
    settings = make_settings(tmp_path)
    tree_entries, blobs = make_tree()
    runner = FakeGitRunner(
        base_files={
            f"{BASE_SHA}:.lasthuman.yml": BASE_CONFIG.encode("utf-8"),
            f"{BASE_SHA}:.github/CODEOWNERS": CODEOWNERS.encode("utf-8"),
        },
        tree_entries=tree_entries,
        blobs=blobs,
    )
    client = FakeGitHubClient(settings, pulls=[make_pull(settings)], files_payload=make_files())

    snapshot = make_reader(tmp_path, client, runner).read(7)

    expected_diff = parse_hunks(RAW_DIFF)
    expected_config = parse_config(BASE_CONFIG)
    expected_risk = score(
        expected_diff,
        expected_config,
        PrMeta(
            title="Handle token refresh errors",
            body="Explains refresh fallback",
            author="octocat",
            author_association="OWNER",
            agent_hint=False,
            labels=("bugfix",),
        ),
    )

    assert snapshot.repo == settings.repository
    assert snapshot.repo_id == settings.repository_id
    assert snapshot.pr == 7
    assert snapshot.head_sha == HEAD_SHA
    assert snapshot.base_sha == BASE_SHA
    assert snapshot.config == expected_config
    assert snapshot.diff == expected_diff
    assert snapshot.risk == expected_risk
    assert snapshot.zones == ("app/auth/", "README.md")
    assert any(symbol.symbol == "refresh" for symbol in snapshot.structure.symbols)
    assert snapshot.binding() == {
        "repository_id": settings.repository_id,
        "pr": 7,
        "head_sha": HEAD_SHA,
        "base_sha": BASE_SHA,
        "policy_version": snapshot.policy_version,
        "snapshot_id": snapshot.snapshot_id,
        "score": expected_risk.score,
        "triggered": expected_risk.triggered,
    }

    restored = Snapshot.from_dict(json.loads(json.dumps(snapshot.to_dict())))
    assert restored == snapshot

    fetch_call = next(call for call in runner.calls if call.args[1] == "fetch")
    assert all("checkout" not in " ".join(call.args) for call in runner.calls)
    assert all("secret-token" not in " ".join(call.args) for call in runner.calls)
    git_config = {
        fetch_call.env[f"GIT_CONFIG_KEY_{i}"]: fetch_call.env[f"GIT_CONFIG_VALUE_{i}"]
        for i in range(int(fetch_call.env["GIT_CONFIG_COUNT"]))
    }
    assert git_config["http.https://github.com/.extraheader"].startswith("Authorization: Basic ")


def test_snapshot_reader_rejects_open_prs_sharing_same_head(tmp_path: Path):
    settings = make_settings(tmp_path)
    current = make_pull(settings, state="open")
    other = make_pull(settings, state="open")
    other["number"] = 9
    client = FakeGitHubClient(
        settings,
        pulls=[current],
        files_payload=make_files(),
        same_head_payload=[current, other],
    )
    runner = FakeGitRunner()

    with pytest.raises(UnsupportedSnapshot, match="same head"):
        make_reader(tmp_path, client, runner).read(7)

    assert not runner.calls


def test_snapshot_reader_rejects_missing_pull_stats(tmp_path: Path):
    settings = make_settings(tmp_path)
    pull = make_pull(settings, state="closed")
    del pull["changed_files"]
    client = FakeGitHubClient(settings, pulls=[pull], files_payload=make_files())
    runner = FakeGitRunner()

    with pytest.raises(SnapshotError, match="stats"):
        make_reader(tmp_path, client, runner).read(7)

    assert not runner.calls


@pytest.mark.parametrize(
    ("entries", "blobs"),
    [
        ([("120000", "blob", "4" * 40, "app/evil.py")], {"4" * 40: b"app/auth/token.py"}),
        ([("100644", "blob", "5" * 40, "../escape.py")], {"5" * 40: b"print('x')\n"}),
    ],
)
def test_snapshot_reader_rejects_malicious_python_tree_entries(
    tmp_path: Path,
    entries: list[tuple[str, str, str, str]],
    blobs: dict[str, bytes],
):
    settings = make_settings(tmp_path)
    runner = FakeGitRunner(
        base_files={f"{BASE_SHA}:.lasthuman.yml": BASE_CONFIG.encode("utf-8")},
        tree_entries=entries,
        blobs=blobs,
    )
    client = FakeGitHubClient(settings, pulls=[make_pull(settings, state="closed")], files_payload=make_files())

    with pytest.raises(UnsupportedSnapshot):
        make_reader(tmp_path, client, runner).read(7)


def test_snapshot_reader_rejects_pull_changes_during_read(tmp_path: Path):
    settings = make_settings(tmp_path)
    tree_entries, blobs = make_tree()
    runner = FakeGitRunner(
        base_files={f"{BASE_SHA}:.lasthuman.yml": BASE_CONFIG.encode("utf-8")},
        tree_entries=tree_entries,
        blobs=blobs,
    )
    first = make_pull(settings, state="closed", labels=("bugfix",))
    second = make_pull(settings, state="closed", labels=("bugfix", "oss-mode"))
    client = FakeGitHubClient(settings, pulls=[first, second], files_payload=make_files())

    with pytest.raises(SnapshotError, match="changed while building"):
        make_reader(tmp_path, client, runner).read(7)


def test_snapshot_reader_rejects_file_count_bound(tmp_path: Path):
    settings = make_settings(tmp_path)
    pull = make_pull(settings, state="closed", changed_files=201, additions=6, deletions=2)
    client = FakeGitHubClient(settings, pulls=[pull], files_payload=make_files())
    runner = FakeGitRunner()

    with pytest.raises(UnsupportedSnapshot, match="200"):
        make_reader(tmp_path, client, runner).read(7)


def test_structure_round_trip_keeps_callees_and_reads_old_payloads_without_them():
    structure = StructureContext(
        changed_files=("app/auth/token.py",),
        importers={"app/auth/token.py": ("app/auth/session.py",)},
        symbols=(SymbolUse(symbol="ensure_fresh", defined_in="app/auth/token.py", used_in=("app/auth/session.py",)),),
        sibling_files=("app/db/client.py",),
        callees=(
            Callee(symbol="post_json", defined_in="app/http_client.py", line=30, constants=("MAX_ATTEMPTS = 3",)),
        ),
    )
    payload = _structure_to_dict(structure)
    assert payload["callees"] == [
        {"symbol": "post_json", "defined_in": "app/http_client.py", "line": 30, "constants": ["MAX_ATTEMPTS = 3"]}
    ]
    assert _structure_from_object(payload) == structure

    legacy = dict(payload)
    del legacy["callees"]
    restored = _structure_from_object(legacy)
    assert restored.callees == ()
    assert restored.symbols == structure.symbols
    # snapshot_id 는 이 payload 의 해시다. callees 가 없는 구조는 예전과 byte 단위로
    # 같은 payload 를 내야 저장된 snapshot 이 계속 읽힌다.
    assert "callees" not in _structure_to_dict(restored)
    assert _structure_to_dict(restored) == legacy

    # excerpt 도 같은 규칙: 비어 있으면 키를 내지 않고, 있으면 그대로 왕복한다.
    with_excerpt = StructureContext(
        changed_files=structure.changed_files,
        importers=structure.importers,
        symbols=structure.symbols,
        sibling_files=structure.sibling_files,
        callees=(
            Callee(
                symbol="post_json",
                defined_in="app/http_client.py",
                line=30,
                constants=("MAX_ATTEMPTS = 3",),
                excerpt=("for attempt in range(1, MAX_ATTEMPTS + 1):",),
            ),
        ),
    )
    excerpt_payload = _structure_to_dict(with_excerpt)
    assert excerpt_payload["callees"][0]["excerpt"] == ["for attempt in range(1, MAX_ATTEMPTS + 1):"]
    assert _structure_from_object(excerpt_payload) == with_excerpt


def test_snapshots_stored_before_callees_still_load_with_their_original_id():
    """운영 DB 에 있던 snapshot 이 새 코드에서 'id does not match' 로 깨지지 않는다."""
    structure = StructureContext(
        changed_files=("app/auth/token.py",),
        importers={"app/auth/token.py": ()},
        symbols=(),
        sibling_files=("app/db/client.py",),
    )
    snapshot = make_snapshot_for_digest(structure)
    stored = snapshot.to_dict()
    assert "callees" not in stored["structure"]
    # 예전 코드가 저장했을 모양 그대로(키 없음) 다시 읽어도 id 검증을 통과한다.
    assert Snapshot.from_dict(stored).snapshot_id == snapshot.snapshot_id


def make_snapshot_for_digest(structure: StructureContext) -> Snapshot:
    raw = (
        "diff --git app/auth/token.py app/auth/token.py\n--- app/auth/token.py\n+++ app/auth/token.py\n"
        "@@ -1,1 +1,2 @@\n line\n+added\n"
    )
    diff = parse_hunks(raw)
    config = parse_config("threshold: 40\n")
    return Snapshot.create(
        repo="hunhoon21/the-last-human",
        repo_id=1,
        pr=1,
        head_sha="a" * 40,
        base_sha="b" * 40,
        author_id=1,
        author_login="author",
        title="t",
        body="",
        risk=score(diff, config, PrMeta()),
        config=config,
        diff=diff,
        structure=structure,
        zones=(),
        policy_version="c" * 64,
    )
