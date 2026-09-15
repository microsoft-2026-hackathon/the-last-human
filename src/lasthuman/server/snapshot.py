"""Trusted pull request snapshots for the server/runtime boundary."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Protocol

import yaml

from ..config import Config, DEFAULT_CONFIG_NAME, LinesChanged, parse_config
from ..diff import parse_hunks
from ..ledger import CODEOWNERS_PATHS, parse_codeowners
from ..models import DiffResult, FileChange, FileStatus, Hunk, PrMeta, RiskResult
from ..risk import score
from ..structure import MAX_FILES as STRUCTURE_MAX_FILES
from ..structure import SKIP_DIRS, Callee, StructureContext, SymbolUse, build_context
from .github import GitHubClient

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")

_MAX_CHANGED_FILES = 200
_MAX_CHANGED_LINES = 10_000
_MAX_RAW_DIFF_BYTES = 4 * 1024 * 1024
_MAX_STRUCTURE_FILE_BYTES = 128 * 1024
_GIT_TIMEOUT = 60

_STATUS_MAP: dict[str, FileStatus] = {
    "added": "added",
    "modified": "modified",
    "removed": "deleted",
    "renamed": "renamed",
}

_MISSING_OBJECT_MARKERS = (
    "does not exist in",
    "exists on disk, but not in",
    "invalid object name",
    "pathspec",
)


class SnapshotError(RuntimeError):
    """Sanitized snapshot failure."""


class UnsupportedSnapshot(SnapshotError):
    """Explicitly unsupported pull request shape."""


class _GitRunner(Protocol):
    def __call__(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path | None,
        env: Mapping[str, str],
        timeout: int,
    ) -> "_GitResult":
        ...


@dataclass(frozen=True)
class _GitResult:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


@dataclass(frozen=True)
class _ApiFile:
    filename: str
    status: FileStatus
    additions: int
    deletions: int
    previous_file: str | None


@dataclass(frozen=True)
class _PullData:
    repo: str
    repo_id: int
    pr: int
    state: str
    head_sha: str
    head_ref: str
    base_sha: str
    base_ref: str
    author_id: int
    author_login: str
    author_association: str
    title: str
    body: str
    labels: tuple[str, ...]
    changed_files: int
    additions: int
    deletions: int

    def meta(self) -> PrMeta:
        return PrMeta(
            title=self.title,
            body=self.body,
            author=self.author_login,
            author_association=self.author_association,
            agent_hint=False,
            labels=self.labels,
        )

    def fingerprint(self) -> tuple[object, ...]:
        return (
            self.head_sha,
            self.base_sha,
            self.base_ref,
            self.author_id,
            self.author_login,
            self.author_association,
            self.title,
            self.body,
            self.labels,
            self.changed_files,
            self.additions,
            self.deletions,
        )


@dataclass(frozen=True)
class Snapshot:
    repo: str
    repo_id: int
    pr: int
    head_sha: str
    base_sha: str
    author_id: int
    author_login: str
    title: str
    body: str
    risk: RiskResult
    config: Config
    diff: DiffResult
    structure: StructureContext
    zones: tuple[str, ...]
    policy_version: str
    snapshot_id: str

    def __post_init__(self) -> None:
        if not _REPOSITORY_RE.fullmatch(self.repo):
            raise SnapshotError("Snapshot repository is invalid")
        _require_positive_int(self.repo_id, "snapshot repository id")
        _require_positive_int(self.pr, "snapshot pull request number")
        _require_sha(self.head_sha, "snapshot head sha")
        _require_sha(self.base_sha, "snapshot base sha")
        _require_positive_int(self.author_id, "snapshot author id")
        _require_nonempty_str(self.author_login, "snapshot author login")
        _require_nonempty_str(self.title, "snapshot title")
        _require_hash(self.policy_version, "snapshot policy version")
        _require_hash(self.snapshot_id, "snapshot id")
        if self.snapshot_id != _snapshot_digest(self):
            raise SnapshotError("Snapshot id does not match snapshot content")

    @classmethod
    def create(
        cls,
        *,
        repo: str,
        repo_id: int,
        pr: int,
        head_sha: str,
        base_sha: str,
        author_id: int,
        author_login: str,
        title: str,
        body: str,
        risk: RiskResult,
        config: Config,
        diff: DiffResult,
        structure: StructureContext,
        zones: tuple[str, ...],
        policy_version: str,
    ) -> "Snapshot":
        snapshot_id = _hash_payload(
            _snapshot_payload(
                repo=repo,
                repo_id=repo_id,
                pr=pr,
                head_sha=head_sha,
                base_sha=base_sha,
                author_id=author_id,
                author_login=author_login,
                title=title,
                body=body,
                risk=risk,
                config=config,
                diff=diff,
                structure=structure,
                zones=zones,
                policy_version=policy_version,
            )
        )
        return cls(
            repo=repo,
            repo_id=repo_id,
            pr=pr,
            head_sha=head_sha,
            base_sha=base_sha,
            author_id=author_id,
            author_login=author_login,
            title=title,
            body=body,
            risk=risk,
            config=config,
            diff=diff,
            structure=structure,
            zones=zones,
            policy_version=policy_version,
            snapshot_id=snapshot_id,
        )

    def binding(self) -> dict[str, object]:
        return {
            "repository_id": self.repo_id,
            "pr": self.pr,
            "head_sha": self.head_sha,
            "base_sha": self.base_sha,
            "policy_version": self.policy_version,
            "snapshot_id": self.snapshot_id,
            "score": self.risk.score,
            "triggered": self.risk.triggered,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "repo": self.repo,
            "repo_id": self.repo_id,
            "pr": self.pr,
            "head_sha": self.head_sha,
            "base_sha": self.base_sha,
            "author_id": self.author_id,
            "author_login": self.author_login,
            "title": self.title,
            "body": self.body,
            "risk": _risk_to_dict(self.risk),
            "config": _config_to_dict(self.config),
            "diff": _diff_to_dict(self.diff),
            "structure": _structure_to_dict(self.structure),
            "zones": list(self.zones),
            "policy_version": self.policy_version,
            "snapshot_id": self.snapshot_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "Snapshot":
        return cls(
            repo=_require_nonempty_str(data.get("repo"), "snapshot repo"),
            repo_id=_require_positive_int(data.get("repo_id"), "snapshot repo_id"),
            pr=_require_positive_int(data.get("pr"), "snapshot pr"),
            head_sha=_require_sha(data.get("head_sha"), "snapshot head_sha"),
            base_sha=_require_sha(data.get("base_sha"), "snapshot base_sha"),
            author_id=_require_positive_int(data.get("author_id"), "snapshot author_id"),
            author_login=_require_nonempty_str(
                data.get("author_login"), "snapshot author_login"
            ),
            title=_require_nonempty_str(data.get("title"), "snapshot title"),
            body=_require_str(data.get("body"), "snapshot body"),
            risk=_risk_from_object(data.get("risk")),
            config=_config_from_object(data.get("config")),
            diff=_diff_from_object(data.get("diff")),
            structure=_structure_from_object(data.get("structure")),
            zones=_require_str_tuple(data.get("zones"), "snapshot zones"),
            policy_version=_require_hash(
                data.get("policy_version"), "snapshot policy_version"
            ),
            snapshot_id=_require_hash(data.get("snapshot_id"), "snapshot snapshot_id"),
        )


class SnapshotReader:
    """Read a bounded, trusted snapshot for one pull request."""

    def __init__(
        self,
        client: GitHubClient,
        cache_dir: Path,
        *,
        runner: _GitRunner | None = None,
        source_root: Path | None = None,
    ) -> None:
        self.client = client
        self.cache_dir = cache_dir
        self._runner = _default_runner if runner is None else runner
        self._source_root = source_root or Path(__file__).resolve().parents[1]

    def read(self, pr: int) -> Snapshot:
        pr_number = _require_positive_int(pr, "pull request number")
        first = self._read_pull(pr_number)
        self._reject_merge_queue(first)
        self._require_supported_base(first)
        if first.state == "open":
            self._reject_shared_head(first)
        self._validate_pull_bounds(first)
        api_files = self._read_api_files(pr_number, first.changed_files)

        token = self.client.installation_token()
        repo_path = self._cache_repo_path()
        self._prepare_cache(repo_path, token)
        pr_ref = f"refs/tlh/pr/{pr_number}"
        self._fetch_refs(repo_path, token, first.base_sha, pr_number)
        fetched_head = self._git_text(repo_path, token, ("git", "rev-parse", pr_ref))
        if fetched_head.strip() != first.head_sha:
            raise SnapshotError("Pull request head changed while reading snapshot")

        raw_diff = self._git_bytes(
            repo_path,
            token,
            (
                "git",
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--no-prefix",
                "--find-renames",
                f"{first.base_sha}...{pr_ref}",
            ),
        )
        if len(raw_diff) > _MAX_RAW_DIFF_BYTES:
            raise UnsupportedSnapshot("Pull request diff exceeds 4 MiB")
        diff = parse_hunks(raw_diff.decode("utf-8", errors="replace"))
        self._validate_diff(first, api_files, diff)

        config_bytes = self._read_optional_show(repo_path, token, f"{first.base_sha}:{DEFAULT_CONFIG_NAME}")
        config = self._parse_base_config(config_bytes)
        policy_version = self._policy_version(config_bytes or b"")
        zones = self._load_base_zones(repo_path, token, first.base_sha)
        structure = self._build_structure(repo_path, token, pr_ref, diff.hunks)

        latest = self._read_pull(pr_number)
        if latest.fingerprint() != first.fingerprint():
            raise SnapshotError("Pull request changed while building snapshot")

        risk = score(diff, config, latest.meta())
        return Snapshot.create(
            repo=latest.repo,
            repo_id=latest.repo_id,
            pr=latest.pr,
            head_sha=latest.head_sha,
            base_sha=latest.base_sha,
            author_id=latest.author_id,
            author_login=latest.author_login,
            title=latest.title,
            body=latest.body,
            risk=risk,
            config=config,
            diff=diff,
            structure=structure,
            zones=zones,
            policy_version=policy_version,
        )

    def _read_pull(self, pr: int) -> _PullData:
        payload = _require_mapping(self.client.pull(pr), "pull request")
        base = _require_mapping(payload.get("base"), "pull request base")
        base_repo = _require_mapping(base.get("repo"), "pull request base repo")
        head = _require_mapping(payload.get("head"), "pull request head")
        user = _require_mapping(payload.get("user"), "pull request user")
        labels = _require_sequence(payload.get("labels"), "pull request labels")
        repo = _require_nonempty_str(base_repo.get("full_name"), "pull request repository")
        if repo.casefold() != self.client.settings.repository.casefold():
            raise SnapshotError("Pull request repository does not match configured repository")
        repo_id = _require_positive_int(base_repo.get("id"), "pull request repository id")
        if repo_id != self.client.settings.repository_id:
            raise SnapshotError("Pull request repository id does not match configured repository")
        number = _require_positive_int(payload.get("number"), "pull request number")
        if number != pr:
            raise SnapshotError("Pull request number does not match the requested PR")
        author_labels = []
        for item in labels:
            label = _require_mapping(item, "pull request label")
            author_labels.append(_require_nonempty_str(label.get("name"), "pull request label name"))
        return _PullData(
            repo=repo,
            repo_id=repo_id,
            pr=number,
            state=_require_nonempty_str(payload.get("state"), "pull request state"),
            head_sha=_require_sha(head.get("sha"), "pull request head sha"),
            head_ref=_require_nonempty_str(head.get("ref"), "pull request head ref"),
            base_sha=_require_sha(base.get("sha"), "pull request base sha"),
            base_ref=_require_nonempty_str(base.get("ref"), "pull request base ref"),
            author_id=_require_positive_int(user.get("id"), "pull request author id"),
            author_login=_require_nonempty_str(user.get("login"), "pull request author login"),
            author_association=_require_nonempty_str(
                payload.get("author_association"),
                "pull request author_association",
            ),
            title=_require_nonempty_str(payload.get("title"), "pull request title"),
            body=_require_str(payload.get("body"), "pull request body"),
            labels=tuple(sorted(author_labels)),
            changed_files=_require_nonnegative_int(
                payload.get("changed_files"), "pull request changed_files"
            ),
            additions=_require_nonnegative_int(payload.get("additions"), "pull request additions"),
            deletions=_require_nonnegative_int(payload.get("deletions"), "pull request deletions"),
        )

    def _reject_merge_queue(self, pull: _PullData) -> None:
        for ref_name in (pull.base_ref, pull.head_ref):
            if ref_name == "gh-readonly-queue" or ref_name.startswith("gh-readonly-queue/"):
                raise UnsupportedSnapshot("Merge queue pull requests are not supported")

    def _require_supported_base(self, pull: _PullData) -> None:
        target = self.client.settings.workflow_ref.removeprefix("refs/heads/")
        if pull.base_ref != target:
            raise UnsupportedSnapshot("Only pull requests targeting the workflow branch are supported")

    def _reject_shared_head(self, pull: _PullData) -> None:
        others: list[int] = []
        for item in self.client.pulls_with_head(pull.head_sha):
            data = _require_mapping(item, "pull request head match")
            number = _require_positive_int(data.get("number"), "pull request head match number")
            if number != pull.pr:
                others.append(number)
        if others:
            raise UnsupportedSnapshot("Open pull requests with the same head are unsupported")

    def _validate_pull_bounds(self, pull: _PullData) -> None:
        if pull.changed_files > _MAX_CHANGED_FILES:
            raise UnsupportedSnapshot("Pull request exceeds the 200 file limit")
        if pull.additions + pull.deletions > _MAX_CHANGED_LINES:
            raise UnsupportedSnapshot("Pull request exceeds the 10000 line limit")

    def _read_api_files(self, pr: int, expected_count: int) -> tuple[_ApiFile, ...]:
        files = self.client.files(pr)
        api_files: list[_ApiFile] = []
        seen: set[str] = set()
        for item in files:
            data = _require_mapping(item, "pull request file")
            filename = _require_nonempty_str(data.get("filename"), "pull request file filename")
            status_name = _require_nonempty_str(data.get("status"), "pull request file status")
            status = _STATUS_MAP.get(status_name)
            if status is None:
                raise UnsupportedSnapshot("Pull request file status is not supported")
            additions = _require_nonnegative_int(
                data.get("additions"), "pull request file additions"
            )
            deletions = _require_nonnegative_int(
                data.get("deletions"), "pull request file deletions"
            )
            previous_file = None
            previous_raw = data.get("previous_filename")
            if previous_raw is not None:
                previous_file = _require_nonempty_str(
                    previous_raw,
                    "pull request file previous_filename",
                )
            patch = data.get("patch")
            if status == "renamed" and additions == 0 and deletions == 0:
                raise UnsupportedSnapshot("Pure rename pull request files are not supported")
            if not isinstance(patch, str) or not patch.strip():
                raise UnsupportedSnapshot("Pull request files without a text patch are not supported")
            if filename in seen:
                raise SnapshotError("Pull request files must be unique")
            seen.add(filename)
            api_files.append(
                _ApiFile(
                    filename=filename,
                    status=status,
                    additions=additions,
                    deletions=deletions,
                    previous_file=previous_file,
                )
            )
        if len(api_files) != expected_count:
            raise SnapshotError("Pull request stats are missing file details")
        return tuple(api_files)

    def _prepare_cache(self, repo_path: Path, token: str) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if not _REPOSITORY_RE.fullmatch(self.client.settings.repository):
            raise SnapshotError("Configured repository is invalid")
        self._git(
            None,
            token,
            ("git", "init", "--bare", str(repo_path)),
            failure="Git cache initialization failed",
        )
        self._git(
            repo_path,
            token,
            ("git", "remote", "remove", "origin"),
            failure="Git cache remote reset failed",
            allow_failure=True,
        )
        self._git(
            repo_path,
            token,
            ("git", "remote", "add", "origin", self._origin_url()),
            failure="Git cache remote setup failed",
        )

    def _fetch_refs(self, repo_path: Path, token: str, base_sha: str, pr: int) -> None:
        self._git(
            repo_path,
            token,
            (
                "git",
                "fetch",
                "--no-tags",
                "--no-recurse-submodules",
                "origin",
                base_sha,
                f"+refs/pull/{pr}/head:refs/tlh/pr/{pr}",
            ),
            failure="Git fetch failed",
        )

    def _validate_diff(
        self,
        pull: _PullData,
        api_files: Sequence[_ApiFile],
        diff: DiffResult,
    ) -> None:
        if len(diff.files) != pull.changed_files:
            raise SnapshotError("Pull request stats do not match the parsed diff")
        if diff.total_additions != pull.additions or diff.total_deletions != pull.deletions:
            raise SnapshotError("Pull request line stats do not match the parsed diff")

        parsed: dict[str, FileChange] = {}
        for file_change in diff.files:
            if file_change.binary:
                raise UnsupportedSnapshot("Binary pull request files are not supported")
            if file_change.status == "renamed" and file_change.hunk_count == 0:
                raise UnsupportedSnapshot("Pure rename pull request files are not supported")
            if file_change.file in parsed:
                raise SnapshotError("Parsed diff contains duplicate file entries")
            parsed[file_change.file] = file_change

        for api_file in api_files:
            parsed_file = parsed.get(api_file.filename)
            if parsed_file is None:
                raise SnapshotError("Pull request file list does not match the parsed diff")
            if parsed_file.status != api_file.status:
                raise SnapshotError("Pull request file status does not match the parsed diff")
            if parsed_file.additions != api_file.additions:
                raise SnapshotError("Pull request additions do not match the parsed diff")
            if parsed_file.deletions != api_file.deletions:
                raise SnapshotError("Pull request deletions do not match the parsed diff")
            if parsed_file.previous_file != api_file.previous_file:
                raise SnapshotError("Pull request rename metadata does not match the parsed diff")

    def _parse_base_config(self, raw: bytes | None) -> Config:
        if raw is None:
            return Config()
        try:
            return parse_config(raw.decode("utf-8"))
        except (UnicodeError, ValueError, TypeError, AttributeError, yaml.YAMLError):
            raise SnapshotError("Base .lasthuman.yml is invalid") from None

    def _policy_version(self, config_bytes: bytes) -> str:
        payload = bytearray()
        payload.extend(config_bytes)
        for name in ("risk.py", "interview.py"):
            path = self._source_root / name
            try:
                payload.extend(path.read_bytes())
            except OSError:
                raise SnapshotError("Policy source could not be read") from None
        return hashlib.sha256(bytes(payload)).hexdigest()

    def read_file(self, head_sha: str, path: str) -> tuple[str, ...] | None:
        """head 커밋의 파일을 줄 단위로 돌려준다 — 보류 화면이 열어 줄 근거의 원문.

        파일이 없거나 캐시가 없으면 None. 128 KiB 를 넘는 파일은 읽지 않는다.
        """
        _require_sha(head_sha, "head sha")
        rel = _validate_repo_path(path)
        repo_path = self._cache_repo_path()
        if not repo_path.exists():
            return None
        token = self.client.installation_token()
        raw = self._read_optional_show(repo_path, token, f"{head_sha}:{rel}")
        if raw is None or len(raw) > _MAX_STRUCTURE_FILE_BYTES:
            return None
        return tuple(line[:200] for line in raw.decode("utf-8", errors="replace").splitlines())

    def zone_owners(self, base_sha: str) -> dict[str, str]:
        """CODEOWNERS 의 선언된 담당. 대시보드가 답할 수 있는 사람 수 옆에 놓는다.

        snapshot 에는 구역 경로만 들어가고(snapshot_id 가 내용 해시라 필드를
        더할 수 없다), 담당은 이미 fetch 된 base 커밋에서 다시 읽는다.
        """
        _require_sha(base_sha, "base sha")
        token = self.client.installation_token()
        repo_path = self._cache_repo_path()
        if not repo_path.exists():
            return {}
        for candidate in CODEOWNERS_PATHS:
            raw = self._read_optional_show(repo_path, token, f"{base_sha}:{candidate}")
            if raw is None:
                continue
            rules = parse_codeowners(raw.decode("utf-8", errors="replace"))
            if rules:
                return dict(rules)
        return {}

    def _load_base_zones(self, repo_path: Path, token: str, base_sha: str) -> tuple[str, ...]:
        for candidate in CODEOWNERS_PATHS:
            raw = self._read_optional_show(repo_path, token, f"{base_sha}:{candidate}")
            if raw is None:
                continue
            rules = parse_codeowners(raw.decode("utf-8", errors="replace"))
            if rules:
                return tuple(pattern for pattern, _owners in rules)
        return ()

    def _build_structure(
        self,
        repo_path: Path,
        token: str,
        head_ref: str,
        hunks: Sequence[Hunk],
    ) -> StructureContext:
        raw_tree = self._git_text(
            repo_path,
            token,
            ("git", "ls-tree", "-r", "-z", "-l", head_ref),
        )
        entries = _parse_ls_tree(raw_tree)
        py_entries: list[tuple[str, str]] = []
        for mode, sha, path, size in entries:
            rel = _validate_repo_path(path)
            is_python = rel.endswith(".py")
            if is_python and mode == "120000":
                raise UnsupportedSnapshot("Python symlinks are not supported")
            if any(part in SKIP_DIRS for part in PurePosixPath(rel).parts):
                continue
            if not is_python:
                continue
            if mode not in {"100644", "100755"}:
                raise UnsupportedSnapshot("Only regular Python files are supported")
            if size < 0 or size > _MAX_STRUCTURE_FILE_BYTES:
                raise UnsupportedSnapshot("Python structure files must stay within 128 KiB")
            py_entries.append((sha, rel))
        if len(py_entries) > STRUCTURE_MAX_FILES:
            raise UnsupportedSnapshot("Python structure input exceeds the 400 file limit")
        with TemporaryDirectory(prefix="lasthuman-snapshot-") as tmpdir:
            root = Path(tmpdir)
            for sha, rel in py_entries:
                blob = self._git_bytes(
                    repo_path,
                    token,
                    ("git", "cat-file", "blob", sha),
                )
                if len(blob) > _MAX_STRUCTURE_FILE_BYTES:
                    raise UnsupportedSnapshot("Python structure files must stay within 128 KiB")
                dest = root / PurePosixPath(rel)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(blob)
            return build_context(root, hunks)

    def _read_optional_show(self, repo_path: Path, token: str, spec: str) -> bytes | None:
        result = self._run(("git", "show", spec), cwd=repo_path, env=self._git_env(token), timeout=_GIT_TIMEOUT)
        if result.returncode == 0:
            return result.stdout
        stderr = result.stderr.decode("utf-8", errors="replace")
        if any(marker in stderr for marker in _MISSING_OBJECT_MARKERS):
            return None
        raise SnapshotError("Git show failed")

    def _cache_repo_path(self) -> Path:
        return self.cache_dir / f"{self.client.settings.repository_id}.git"

    def _origin_url(self) -> str:
        return f"https://github.com/{self.client.settings.repository}.git"

    def _git(
        self, cwd: Path | None, token: str, args: tuple[str, ...], *, failure: str, allow_failure: bool = False
    ) -> _GitResult:
        result = self._run(args, cwd=cwd, env=self._git_env(token), timeout=_GIT_TIMEOUT)
        if not allow_failure and result.returncode != 0:
            raise SnapshotError(failure)
        return result

    def _git_text(self, cwd: Path, token: str, args: tuple[str, ...]) -> str:
        return self._git_bytes(cwd, token, args).decode("utf-8", errors="replace")

    def _git_bytes(self, cwd: Path, token: str, args: tuple[str, ...]) -> bytes:
        return self._git(cwd, token, args, failure="Git command failed").stdout

    def _git_env(self, token: str) -> dict[str, str]:
        credential = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
        env = dict(os.environ)
        env.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
                "GCM_INTERACTIVE": "Never",
            }
        )
        entries = (
            ("core.hooksPath", os.devnull),
            ("protocol.file.allow", "never"),
            ("protocol.ext.allow", "never"),
            ("submodule.recurse", "false"),
            ("diff.external", ""),
            ("http.https://github.com/.extraheader", f"Authorization: Basic {credential}"),
        )
        env["GIT_CONFIG_COUNT"] = str(len(entries))
        for index, (key, value) in enumerate(entries):
            env[f"GIT_CONFIG_KEY_{index}"] = key
            env[f"GIT_CONFIG_VALUE_{index}"] = value
        return env

    def _run(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path | None,
        env: Mapping[str, str],
        timeout: int,
    ) -> _GitResult:
        try:
            return self._runner(args, cwd=cwd, env=env, timeout=timeout)
        except OSError:
            raise SnapshotError("Git command could not be executed") from None


def _default_runner(
    args: tuple[str, ...],
    *,
    cwd: Path | None,
    env: Mapping[str, str],
    timeout: int,
) -> _GitResult:
    try:
        completed = subprocess.run(
            list(args),
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env),
            capture_output=True,
            shell=False,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise SnapshotError("Git command timed out") from None
    return _GitResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _snapshot_payload(
    *,
    repo: str,
    repo_id: int,
    pr: int,
    head_sha: str,
    base_sha: str,
    author_id: int,
    author_login: str,
    title: str,
    body: str,
    risk: RiskResult,
    config: Config,
    diff: DiffResult,
    structure: StructureContext,
    zones: tuple[str, ...],
    policy_version: str,
) -> dict[str, object]:
    return {
        "repo": repo,
        "repo_id": repo_id,
        "pr": pr,
        "head_sha": head_sha,
        "base_sha": base_sha,
        "author_id": author_id,
        "author_login": author_login,
        "title": title,
        "body": body,
        "risk": _risk_to_dict(risk),
        "config": _config_to_dict(config),
        "diff": _diff_to_dict(diff),
        "structure": _structure_to_dict(structure),
        "zones": list(zones),
        "policy_version": policy_version,
    }


def _snapshot_digest(snapshot: Snapshot) -> str:
    return _hash_payload(
        _snapshot_payload(
            repo=snapshot.repo,
            repo_id=snapshot.repo_id,
            pr=snapshot.pr,
            head_sha=snapshot.head_sha,
            base_sha=snapshot.base_sha,
            author_id=snapshot.author_id,
            author_login=snapshot.author_login,
            title=snapshot.title,
            body=snapshot.body,
            risk=snapshot.risk,
            config=snapshot.config,
            diff=snapshot.diff,
            structure=snapshot.structure,
            zones=snapshot.zones,
            policy_version=snapshot.policy_version,
        )
    )


def _hash_payload(payload: Mapping[str, object]) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _config_to_dict(config: Config) -> dict[str, object]:
    return {
        "mode": config.mode,
        "threshold": config.threshold,
        "critical_paths": [[pattern, weight] for pattern, weight in config.critical_paths],
        "lines_changed": {
            "per100": config.lines_changed.per100,
            "max": config.lines_changed.max,
        },
        "patterns": [[pattern, weight] for pattern, weight in config.patterns],
        "tests_removed": config.tests_removed,
        "agent_hint": config.agent_hint,
        "external_contributor": config.external_contributor,
        "zero_coverage": config.zero_coverage,
        "max_hunks": config.max_hunks,
        "external_contributor_label": config.external_contributor_label,
        "oss_mode_label": config.oss_mode_label,
    }


def _config_from_object(value: object) -> Config:
    data = _require_mapping(value, "config")
    lines = _require_mapping(data.get("lines_changed"), "config lines_changed")
    return Config(
        mode=_require_nonempty_str(data.get("mode"), "config mode"),
        threshold=_require_positive_or_zero_int(data.get("threshold"), "config threshold"),
        critical_paths=_require_weight_pairs(data.get("critical_paths"), "config critical_paths"),
        lines_changed=LinesChanged(
            per100=_require_positive_or_zero_int(lines.get("per100"), "config per100"),
            max=_require_positive_or_zero_int(lines.get("max"), "config max"),
        ),
        patterns=_require_weight_pairs(data.get("patterns"), "config patterns"),
        tests_removed=_require_positive_or_zero_int(
            data.get("tests_removed"), "config tests_removed"
        ),
        agent_hint=_require_positive_or_zero_int(data.get("agent_hint"), "config agent_hint"),
        external_contributor=_require_positive_or_zero_int(
            data.get("external_contributor"),
            "config external_contributor",
        ),
        zero_coverage=_require_positive_or_zero_int(
            data.get("zero_coverage"), "config zero_coverage"
        ),
        max_hunks=_require_positive_or_zero_int(data.get("max_hunks"), "config max_hunks"),
        external_contributor_label=_require_nonempty_str(
            data.get("external_contributor_label"),
            "config external_contributor_label",
        ),
        oss_mode_label=_require_nonempty_str(data.get("oss_mode_label"), "config oss_mode_label"),
    )


def _risk_to_dict(risk: RiskResult) -> dict[str, object]:
    return {
        "score": risk.score,
        "triggered": risk.triggered,
        "reasons": list(risk.reasons),
        "top_hunks": [_hunk_to_dict(hunk) for hunk in risk.top_hunks],
    }


def _risk_from_object(value: object) -> RiskResult:
    data = _require_mapping(value, "risk")
    top_hunks = _require_sequence(data.get("top_hunks"), "risk top_hunks")
    return RiskResult(
        score=_require_positive_or_zero_int(data.get("score"), "risk score"),
        triggered=_require_bool(data.get("triggered"), "risk triggered"),
        reasons=_require_str_tuple(data.get("reasons"), "risk reasons"),
        top_hunks=tuple(_hunk_from_object(item) for item in top_hunks),
    )


def _diff_to_dict(diff: DiffResult) -> dict[str, object]:
    return {
        "hunks": [_hunk_to_dict(hunk) for hunk in diff.hunks],
        "files": [_file_change_to_dict(file_change) for file_change in diff.files],
    }


def _diff_from_object(value: object) -> DiffResult:
    data = _require_mapping(value, "diff")
    hunks = _require_sequence(data.get("hunks"), "diff hunks")
    files = _require_sequence(data.get("files"), "diff files")
    return DiffResult(
        hunks=tuple(_hunk_from_object(item) for item in hunks),
        files=tuple(_file_change_from_object(item) for item in files),
    )


def _hunk_to_dict(hunk: Hunk) -> dict[str, object]:
    return {
        "file": hunk.file,
        "new_start": hunk.new_start,
        "old_start": hunk.old_start,
        "anchor": hunk.anchor,
        "added": list(hunk.added),
        "removed": list(hunk.removed),
        "body": hunk.body,
        "file_status": hunk.file_status,
    }


def _hunk_from_object(value: object) -> Hunk:
    data = _require_mapping(value, "hunk")
    return Hunk(
        file=_require_nonempty_str(data.get("file"), "hunk file"),
        new_start=_require_positive_or_zero_int(data.get("new_start"), "hunk new_start"),
        old_start=_require_positive_or_zero_int(data.get("old_start"), "hunk old_start"),
        anchor=_require_nonempty_str(data.get("anchor"), "hunk anchor"),
        added=_require_str_tuple(data.get("added"), "hunk added"),
        removed=_require_str_tuple(data.get("removed"), "hunk removed"),
        body=_require_str(data.get("body"), "hunk body"),
        file_status=_require_file_status(data.get("file_status"), "hunk file_status"),
    )


def _file_change_to_dict(file_change: FileChange) -> dict[str, object]:
    return {
        "file": file_change.file,
        "status": file_change.status,
        "binary": file_change.binary,
        "additions": file_change.additions,
        "deletions": file_change.deletions,
        "hunk_count": file_change.hunk_count,
        "previous_file": file_change.previous_file,
    }


def _file_change_from_object(value: object) -> FileChange:
    data = _require_mapping(value, "file change")
    previous_file = data.get("previous_file")
    return FileChange(
        file=_require_nonempty_str(data.get("file"), "file change file"),
        status=_require_file_status(data.get("status"), "file change status"),
        binary=_require_bool(data.get("binary"), "file change binary"),
        additions=_require_positive_or_zero_int(data.get("additions"), "file change additions"),
        deletions=_require_positive_or_zero_int(data.get("deletions"), "file change deletions"),
        hunk_count=_require_positive_or_zero_int(data.get("hunk_count"), "file change hunk_count"),
        previous_file=None
        if previous_file is None
        else _require_nonempty_str(previous_file, "file change previous_file"),
    )


def _structure_to_dict(structure: StructureContext) -> dict[str, object]:
    return {
        "changed_files": list(structure.changed_files),
        "importers": {
            key: list(value)
            for key, value in sorted(structure.importers.items())
        },
        "symbols": [_symbol_use_to_dict(symbol) for symbol in structure.symbols],
        "sibling_files": list(structure.sibling_files),
        "callees": [_callee_to_dict(callee) for callee in structure.callees],
    }


def _structure_from_object(value: object) -> StructureContext:
    data = _require_mapping(value, "structure")
    importers_raw = _require_mapping(data.get("importers"), "structure importers")
    importers: dict[str, tuple[str, ...]] = {}
    for key, entry in importers_raw.items():
        importers[_require_nonempty_str(key, "structure importer key")] = _require_str_tuple(
            entry,
            "structure importer value",
        )
    symbols = _require_sequence(data.get("symbols"), "structure symbols")
    # callees 는 나중에 추가된 필드다. 옛 snapshot 에는 없으므로 비어 있는 것으로 읽는다.
    callees_raw = data.get("callees", [])
    callees = _require_sequence(callees_raw, "structure callees") if callees_raw else ()
    return StructureContext(
        changed_files=_require_str_tuple(data.get("changed_files"), "structure changed_files"),
        importers=importers,
        symbols=tuple(_symbol_use_from_object(item) for item in symbols),
        sibling_files=_require_str_tuple(data.get("sibling_files"), "structure sibling_files"),
        callees=tuple(_callee_from_object(item) for item in callees),
    )


def _callee_to_dict(callee: Callee) -> dict[str, object]:
    return {
        "symbol": callee.symbol,
        "defined_in": callee.defined_in,
        "line": callee.line,
        "constants": list(callee.constants),
    }


def _callee_from_object(value: object) -> Callee:
    data = _require_mapping(value, "callee")
    return Callee(
        symbol=_require_nonempty_str(data.get("symbol"), "callee symbol"),
        defined_in=_require_nonempty_str(data.get("defined_in"), "callee defined_in"),
        line=_require_positive_int(data.get("line"), "callee line"),
        constants=_require_str_tuple(data.get("constants", []), "callee constants"),
    )


def _symbol_use_to_dict(symbol: SymbolUse) -> dict[str, object]:
    return {
        "symbol": symbol.symbol,
        "defined_in": symbol.defined_in,
        "used_in": list(symbol.used_in),
    }


def _symbol_use_from_object(value: object) -> SymbolUse:
    data = _require_mapping(value, "symbol use")
    return SymbolUse(
        symbol=_require_nonempty_str(data.get("symbol"), "symbol use symbol"),
        defined_in=_require_nonempty_str(data.get("defined_in"), "symbol use defined_in"),
        used_in=_require_str_tuple(data.get("used_in"), "symbol use used_in"),
    )


def _parse_ls_tree(raw: str) -> tuple[tuple[str, str, str, int], ...]:
    entries: list[tuple[str, str, str, int]] = []
    for chunk in raw.split("\0"):
        if not chunk:
            continue
        meta, separator, path = chunk.partition("\t")
        if not separator:
            raise SnapshotError("Git tree output is invalid")
        parts = meta.split()
        if len(parts) != 4:
            raise SnapshotError("Git tree output is invalid")
        mode, kind, sha, size_raw = parts
        if kind != "blob":
            continue
        try:
            size = int(size_raw)
        except ValueError:
            raise SnapshotError("Git tree output is invalid") from None
        entries.append((mode, sha, path, size))
    return tuple(entries)


def _validate_repo_path(path: str) -> str:
    normalized = PurePosixPath(path)
    if normalized.is_absolute() or ".." in normalized.parts:
        raise UnsupportedSnapshot("Repository paths must stay within the tree")
    if not path or path.endswith("/"):
        raise UnsupportedSnapshot("Repository paths must point to files")
    return normalized.as_posix()


def _require_mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SnapshotError(f"{context} must be an object")
    return value


def _require_sequence(value: object, context: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise SnapshotError(f"{context} must be a sequence")
    return value


def _require_str(value: object, context: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SnapshotError(f"{context} must be a string")
    return value


def _require_nonempty_str(value: object, context: str) -> str:
    text = _require_str(value, context)
    if not text:
        raise SnapshotError(f"{context} must not be empty")
    return text


def _require_nonnegative_int(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SnapshotError(f"{context} stats are missing or invalid")
    return value


def _require_positive_or_zero_int(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SnapshotError(f"{context} must be a non-negative integer")
    return value


def _require_positive_int(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SnapshotError(f"{context} must be a positive integer")
    return value


def _require_bool(value: object, context: str) -> bool:
    if not isinstance(value, bool):
        raise SnapshotError(f"{context} must be a boolean")
    return value


def _require_sha(value: object, context: str) -> str:
    text = _require_nonempty_str(value, context)
    if not _SHA_RE.fullmatch(text):
        raise SnapshotError(f"{context} must be a 40-character hexadecimal sha")
    return text


def _require_hash(value: object, context: str) -> str:
    text = _require_nonempty_str(value, context)
    if not _HEX64_RE.fullmatch(text):
        raise SnapshotError(f"{context} must be a 64-character hexadecimal hash")
    return text


def _require_file_status(value: object, context: str) -> FileStatus:
    text = _require_nonempty_str(value, context)
    if text not in {"added", "modified", "deleted", "renamed"}:
        raise SnapshotError(f"{context} must be a valid file status")
    return text


def _require_str_tuple(value: object, context: str) -> tuple[str, ...]:
    items = _require_sequence(value, context)
    return tuple(_require_str(item, context) for item in items)


def _require_weight_pairs(value: object, context: str) -> tuple[tuple[str, int], ...]:
    items = _require_sequence(value, context)
    pairs: list[tuple[str, int]] = []
    for item in items:
        pair = _require_sequence(item, context)
        if len(pair) != 2:
            raise SnapshotError(f"{context} must contain [pattern, weight] pairs")
        pairs.append(
            (
                _require_nonempty_str(pair[0], f"{context} pattern"),
                _require_positive_or_zero_int(pair[1], f"{context} weight"),
            )
        )
    return tuple(pairs)
