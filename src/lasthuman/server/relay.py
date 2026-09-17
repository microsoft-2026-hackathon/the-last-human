"""Trusted GitHub Actions relay for metadata-only PR lifecycle events."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import cast
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import requests

from .github import GitHubClient, GitHubError, JsonObject
from .snapshot import Snapshot, SnapshotError, SnapshotReader

_DEFAULT_WORKFLOW = "lasthuman-app.yml"
_TRUSTED_WORKFLOW_REF = "refs/heads/main"
_REQUEST_TIMEOUT = (5, 30)
_POST_STATUSES = (202,)
_RETRYABLE_STATUSES = {502, 503, 504}
_JOB_POLL_INTERVAL_SECONDS = 2.0
_JOB_POLL_TIMEOUT_SECONDS = 180.0
_MAX_JOB_RESUBMITS = 2
_JOB_PENDING_STATES = frozenset({"queued", "running"})
_JOB_TERMINAL_ERROR_STATES = frozenset({"error", "conflict", "stale"})
_JOB_MISSING_STATUSES = frozenset({404, 410})
_VERIFICATION_STEPS = ("receipt", "snapshot", "compare", "verify", "publication")
_STATE_SCHEMA_VERSION = 1
_MAX_STATE_BYTES = 64 * 1024 - 1
_PUBLICATION_POLL_TIMEOUT_SECONDS = 180.0
_PUBLICATION_POLL_INTERVAL_SECONDS = 2.0
_STATUS_PAGE_SIZE = 100
_MAX_STATUS_PAGES = 10
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_WORKFLOW_RE = re.compile(r"^[A-Za-z0-9._-]+\.ya?ml$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_RECEIPT_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_ERROR_CODE_RE = re.compile(r"^[a-z0-9_-]{1,64}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_REPOSITORY_PATH_RE = re.compile(r"^/repos/[1-9][0-9]*$")
_PR_ACTIONS = frozenset({"opened", "synchronize", "reopened", "edited", "labeled", "unlabeled", "closed"})
_BINDING_KEYS = frozenset(
    {"repository_id", "pr", "head_sha", "base_sha", "policy_version", "snapshot_id", "score", "triggered"}
)
_RECEIPT_KEYS = frozenset(
    {
        "receipt_id",
        "binding",
        "actor_id",
        "question_version",
        "issued_at",
        "app_id",
        "installation_id",
    }
)
_GATE_KEYS = frozenset({"context", "target_url", "state", "status_id", "error_code"})
_GATE_STATES = frozenset(
    {
        "waiting_verification",
        "waiting_publication",
        "published",
        "missing",
        "unavailable",
        "skipped",
        "invalid",
    }
)
_BASE_STATE_KEYS = frozenset(
    {
        "schema_version",
        "stage",
        "repository",
        "repository_id",
        "owner_id",
        "workflow",
        "workflow_ref",
        "run_id",
        "run_attempt",
        "origin",
        "audience",
        "repository_path",
        "receipt",
    }
)


class RelayError(RuntimeError):
    """Sanitized relay failure."""


@dataclass(frozen=True)
class ActionSettings:
    repository: str
    repository_id: int
    owner_id: int
    workflow: str
    workflow_ref: str
    bot_url: str
    oidc_audience: str
    github_token: str = field(repr=False)


@dataclass(frozen=True)
class ActionContext:
    settings: ActionSettings
    event_name: str
    event_path: Path
    token_request_url: str
    token_request_token: str = field(repr=False)
    run_id: str | None = None
    run_attempt: str | None = None


@dataclass(frozen=True)
class AcceptedJob:
    job_id: str
    url: str


class ActionsGitHubClient(GitHubClient):
    """Read-only GitHub client backed by the workflow token."""

    settings: ActionSettings

    def installation_token(self) -> str:
        token = self.settings.github_token.strip()
        if not token:
            raise GitHubError("GitHub Actions token is unavailable")
        return token

    def verify_repository(self) -> None:
        if self._repository_verified:
            return
        with self._verification_lock:
            if self._repository_verified:
                return
            self.repository_info()
            self._repository_verified = True


def run(
    *,
    environ: Mapping[str, str] | None = None,
    session: requests.Session | None = None,
    github_session: requests.Session | None = None,
    cache_dir: Path | None = None,
    verification_step: str | None = None,
    state_file: Path | None = None,
) -> None:
    context = _load_context(
        dict(os.environ if environ is None else environ),
        require_run_identity=verification_step is not None,
    )
    bot_session = requests.Session() if session is None else session
    github = ActionsGitHubClient(
        context.settings,
        requests.Session() if github_session is None else github_session,
    )
    payload = _load_event_payload(context.event_path)
    relay_cache = cache_dir or Path(".work/relay-cache")
    if verification_step is not None:
        if verification_step not in _VERIFICATION_STEPS or state_file is None:
            raise RelayError("verification step arguments are invalid")
        if context.event_name != "workflow_dispatch":
            raise RelayError("verification steps require workflow_dispatch")
        _run_verification_step(
            context,
            github,
            bot_session,
            payload,
            relay_cache,
            verification_step,
            state_file,
        )
        return
    if state_file is not None:
        raise RelayError("verification step arguments are invalid")
    github.verify_repository()
    if context.event_name == "pull_request_target":
        _relay_pull_request_event(
            context.settings,
            github,
            bot_session,
            payload,
            relay_cache,
            token_request_url=context.token_request_url,
            token_request_token=context.token_request_token,
        )
        return
    if context.event_name == "workflow_dispatch":
        _relay_receipt_verification(
            context.settings,
            github,
            bot_session,
            payload,
            relay_cache,
            token_request_url=context.token_request_url,
            token_request_token=context.token_request_token,
        )
        return
    raise RelayError("GitHub Actions event is not supported")


def main(argv: Sequence[str] = ()) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verification-step", choices=_VERIFICATION_STEPS)
    parser.add_argument("--state-file", type=Path)
    args = parser.parse_args(argv)
    if (args.verification_step is None) != (args.state_file is None):
        parser.error("--verification-step and --state-file must be used together")
    try:
        if args.verification_step is None:
            run()
        else:
            run(
                verification_step=cast(str, args.verification_step),
                state_file=cast(Path, args.state_file),
            )
    except (GitHubError, SnapshotError, RelayError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


def _load_context(
    environ: Mapping[str, str],
    *,
    require_run_identity: bool = False,
) -> ActionContext:
    repository = _require_repository(_require_env(environ, "GITHUB_REPOSITORY"))
    workflow = _require_workflow(environ.get("TLH_WORKFLOW", _DEFAULT_WORKFLOW))
    workflow_ref = _require_env(environ, "GITHUB_REF")
    if workflow_ref != _TRUSTED_WORKFLOW_REF:
        raise RelayError("GitHub Actions source ref is untrusted")
    expected_source = f"{repository}/.github/workflows/{workflow}@{workflow_ref}"
    if _require_env(environ, "GITHUB_WORKFLOW_REF") != expected_source:
        raise RelayError("GitHub Actions workflow source is untrusted")
    event_name = _require_env(environ, "GITHUB_EVENT_NAME")
    if event_name not in {"pull_request_target", "workflow_dispatch"}:
        raise RelayError("GitHub Actions event is not supported")
    run_id = environ.get("GITHUB_RUN_ID")
    run_attempt = environ.get("GITHUB_RUN_ATTEMPT")
    if require_run_identity:
        run_id = _require_numeric_string(_require_env(environ, "GITHUB_RUN_ID"), "GITHUB_RUN_ID")
        run_attempt = _require_numeric_string(
            _require_env(environ, "GITHUB_RUN_ATTEMPT"),
            "GITHUB_RUN_ATTEMPT",
        )
    return ActionContext(
        settings=ActionSettings(
            repository=repository,
            repository_id=_require_positive_int(_require_env(environ, "GITHUB_REPOSITORY_ID"), "GITHUB_REPOSITORY_ID"),
            owner_id=_require_positive_int(
                _require_env(environ, "GITHUB_REPOSITORY_OWNER_ID"), "GITHUB_REPOSITORY_OWNER_ID"
            ),
            workflow=workflow,
            workflow_ref=workflow_ref,
            bot_url=_require_https_origin(_require_env(environ, "TLH_BOT_URL")),
            oidc_audience=_require_bounded_string(
                environ.get("TLH_OIDC_AUDIENCE", repository).strip() or repository,
                "TLH_OIDC_AUDIENCE",
                512,
            ),
            github_token=_require_env(environ, "GH_TOKEN"),
        ),
        event_name=event_name,
        event_path=_require_file_path(_require_env(environ, "GITHUB_EVENT_PATH")),
        token_request_url=_require_https_url(_require_env(environ, "ACTIONS_ID_TOKEN_REQUEST_URL")),
        token_request_token=_require_env(environ, "ACTIONS_ID_TOKEN_REQUEST_TOKEN"),
        run_id=run_id,
        run_attempt=run_attempt,
    )


def _relay_pull_request_event(
    settings: ActionSettings,
    github: ActionsGitHubClient,
    session: requests.Session,
    payload: JsonObject,
    cache_dir: Path,
    *,
    token_request_url: str,
    token_request_token: str,
) -> None:
    _validate_event_repository(payload, settings)
    pr = _pull_request_number(payload)
    action = _pull_request_action(payload)
    body: JsonObject
    if action == "closed":
        body = {
            "repository_id": settings.repository_id,
            "pr": pr,
            "action": action,
            "head_sha": _pull_request_head_sha(payload),
        }
    else:
        body = {
            "repository_id": settings.repository_id,
            "pr": pr,
            "action": action,
            "binding": SnapshotReader(github, cache_dir).read(pr).binding(),
        }
    oidc_token = _fetch_oidc_token(
        session,
        token_request_url,
        token_request_token,
        settings.oidc_audience,
    )
    accepted = _submit_actions_job(
        session,
        f"{settings.bot_url}/api/actions/events",
        oidc_token,
        body,
    )
    _poll_actions_job(
        session,
        settings,
        oidc_token,
        accepted,
        submit_url=f"{settings.bot_url}/api/actions/events",
        submit_body=body,
    )


def _relay_receipt_verification(
    settings: ActionSettings,
    github: ActionsGitHubClient,
    session: requests.Session,
    payload: JsonObject,
    cache_dir: Path,
    *,
    token_request_url: str,
    token_request_token: str,
) -> None:
    _validate_event_repository(payload, settings, allow_missing=True)
    receipt_id = _workflow_dispatch_receipt_id(payload)
    receipt_path = f"/api/actions/receipts/{quote(receipt_id, safe='')}"
    read_token = _fetch_oidc_token(
        session,
        token_request_url,
        token_request_token,
        settings.oidc_audience,
    )
    receipt_payload = _request_json(
        session,
        "GET",
        f"{settings.bot_url}{receipt_path}",
        token=read_token,
        expected_statuses=(200,),
    )
    receipt, _repository_path = _require_receipt_envelope(receipt_payload, receipt_id, settings)
    binding = cast(JsonObject, receipt["binding"])
    pr = _require_positive_int(binding.get("pr"), "binding.pr")
    snapshot = SnapshotReader(github, cache_dir).read(pr)
    snapshot_binding = _compare_receipt_snapshot(receipt, snapshot, settings)
    write_token = _fetch_oidc_token(
        session,
        token_request_url,
        token_request_token,
        settings.oidc_audience,
    )
    accepted = _submit_actions_job(
        session,
        f"{settings.bot_url}{receipt_path}/verify",
        write_token,
        {"binding": snapshot_binding},
    )
    _poll_actions_job(
        session,
        settings,
        write_token,
        accepted,
        submit_url=f"{settings.bot_url}{receipt_path}/verify",
        submit_body={"binding": snapshot_binding},
    )


def _run_verification_step(
    context: ActionContext,
    github: ActionsGitHubClient,
    session: requests.Session,
    payload: JsonObject,
    cache_dir: Path,
    step: str,
    state_file: Path,
) -> None:
    _validate_event_repository(payload, context.settings, allow_missing=True)
    receipt_id = _require_receipt_id(_workflow_dispatch_receipt_id(payload))
    if step == "receipt":
        _verification_receipt_step(context, session, receipt_id, state_file)
        return

    predecessor = {
        "snapshot": "receipt",
        "compare": "snapshot",
        "verify": "compare",
        "publication": "verify",
    }[step]
    state = _load_verification_state(
        state_file,
        context,
        receipt_id,
        expected_stage=predecessor,
    )
    if step == "snapshot":
        _verification_snapshot_step(
            context,
            github,
            cache_dir,
            state_file,
            state,
        )
    elif step == "compare":
        _verification_compare_step(context, state_file, state)
    elif step == "verify":
        _verification_verify_step(context, session, state_file, state)
    else:
        _verification_publication_step(
            context,
            github,
            session,
            state_file,
            state,
        )


def _verification_receipt_step(
    context: ActionContext,
    session: requests.Session,
    receipt_id: str,
    state_file: Path,
) -> None:
    token = _fetch_oidc_token(
        session,
        context.token_request_url,
        context.token_request_token,
        context.settings.oidc_audience,
    )
    publication_url = (
        f"{context.settings.bot_url}/api/actions/receipts/"
        f"{quote(receipt_id, safe='')}/publication"
    )
    status_code, payload = _request_json_response(
        session,
        "GET",
        publication_url,
        token=token,
        expected_statuses=(200, 404),
        attempts=1,
    )
    if status_code == 404:
        raise RelayError("publication endpoint is not supported by backend or receipt was not found")
    receipt, _verified_at, gate, repository_path = _require_publication_view(
        payload,
        receipt_id,
        context.settings,
    )
    if gate["state"] in {"missing", "unavailable", "skipped", "invalid"}:
        raise RelayError(f"receipt publication is {gate['state']}")
    state = _new_verification_state(context, "receipt", receipt, repository_path=repository_path)
    _write_verification_state(state_file, state)


def _verification_snapshot_step(
    context: ActionContext,
    github: ActionsGitHubClient,
    cache_dir: Path,
    state_file: Path,
    state: JsonObject,
) -> None:
    receipt = cast(JsonObject, state["receipt"])
    binding = cast(JsonObject, receipt["binding"])
    github.verify_repository()
    snapshot = SnapshotReader(github, cache_dir).read(
        _require_positive_int(binding.get("pr"), "binding.pr")
    )
    next_state = dict(state)
    next_state["stage"] = "snapshot"
    next_state["snapshot"] = {
        "binding": _require_binding(snapshot.binding()),
        "author_id": _require_positive_int(snapshot.author_id, "snapshot.author_id"),
    }
    _validate_verification_state(next_state, context, receipt["receipt_id"], "snapshot")
    _write_verification_state(state_file, next_state)


def _verification_compare_step(
    context: ActionContext,
    state_file: Path,
    state: JsonObject,
) -> None:
    receipt = cast(JsonObject, state["receipt"])
    snapshot = _require_snapshot_metadata(state.get("snapshot"))
    _compare_receipt_metadata(receipt, snapshot, context.settings)
    next_state = dict(state)
    next_state["stage"] = "compare"
    _validate_verification_state(next_state, context, receipt["receipt_id"], "compare")
    _write_verification_state(state_file, next_state)


def _verification_verify_step(
    context: ActionContext,
    session: requests.Session,
    state_file: Path,
    state: JsonObject,
) -> None:
    receipt = cast(JsonObject, state["receipt"])
    snapshot = _require_snapshot_metadata(state.get("snapshot"))
    receipt_id = cast(str, receipt["receipt_id"])
    binding = cast(JsonObject, snapshot["binding"])
    token = _fetch_oidc_token(
        session,
        context.token_request_url,
        context.token_request_token,
        context.settings.oidc_audience,
    )
    submit_url = (
        f"{context.settings.bot_url}/api/actions/receipts/"
        f"{quote(receipt_id, safe='')}/verify"
    )
    accepted = _submit_actions_job(
        session,
        submit_url,
        token,
        {"binding": binding},
    )
    result = _poll_actions_job(
        session,
        context.settings,
        token,
        accepted,
        submit_url=submit_url,
        submit_body={"binding": binding},
    )
    if frozenset(result) != {"state", "receipt_id", "pr", "verified_at"}:
        raise RelayError("verification result was invalid")
    if result.get("state") != "verified":
        raise RelayError("verification did not complete")
    if _require_receipt_id(result.get("receipt_id")) != receipt_id:
        raise RelayError("verification receipt mismatch")
    if _require_positive_int(result.get("pr"), "verification.pr") != binding["pr"]:
        raise RelayError("verification pull request mismatch")
    verified_at = _require_timestamp(result.get("verified_at"), "verification.verified_at")
    next_state = dict(state)
    next_state["stage"] = "verify"
    next_state["verified_at"] = verified_at
    _validate_verification_state(next_state, context, receipt_id, "verify")
    _write_verification_state(state_file, next_state)


def _verification_publication_step(
    context: ActionContext,
    github: ActionsGitHubClient,
    session: requests.Session,
    state_file: Path,
    state: JsonObject,
) -> None:
    receipt = cast(JsonObject, state["receipt"])
    receipt_id = cast(str, receipt["receipt_id"])
    snapshot = _require_snapshot_metadata(state.get("snapshot"))
    binding = cast(JsonObject, snapshot["binding"])
    author_id = cast(int, snapshot["author_id"])
    deadline = time.monotonic() + _PUBLICATION_POLL_TIMEOUT_SECONDS
    github.timeout = _job_request_timeout(deadline)
    github.verify_repository()
    token = _fetch_oidc_token(
        session,
        context.token_request_url,
        context.token_request_token,
        context.settings.oidc_audience,
        attempts=1,
        timeout=_job_request_timeout(deadline),
    )
    publication_url = (
        f"{context.settings.bot_url}/api/actions/receipts/"
        f"{quote(receipt_id, safe='')}/publication"
    )
    expected_verified_at = cast(str, state["verified_at"])
    while True:
        github.timeout = _job_request_timeout(deadline)
        _require_current_pull(github, binding, author_id)
        timeout = _job_request_timeout(deadline)
        payload = _request_json(
            session,
            "GET",
            publication_url,
            token=token,
            expected_statuses=(200,),
            attempts=1,
            timeout=timeout,
        )
        repository_path = _state_repository_path(state, context.settings)
        current_receipt, verified_at, gate, current_repository_path = _require_publication_view(
            payload,
            receipt_id,
            context.settings,
            expected_repository_path=repository_path,
        )
        if (
            current_receipt != receipt or verified_at != expected_verified_at
            or current_repository_path != repository_path
        ):
            raise RelayError("receipt publication view is stale")
        gate_state = cast(str, gate["state"])
        if gate_state == "waiting_publication":
            _sleep_until_retry(deadline)
            continue
        if gate_state != "published":
            raise RelayError(f"receipt publication is {gate_state}")

        github.timeout = _job_request_timeout(deadline)
        status = _latest_context_status(
            github,
            cast(str, binding["head_sha"]),
            cast(str, gate["context"]),
            deadline,
        )
        if status is None:
            _sleep_until_retry(deadline)
            continue
        remote_state = _require_nonempty_string(status.get("state"), "commit status.state")
        if remote_state == "pending":
            _sleep_until_retry(deadline)
            continue
        if remote_state in {"error", "failure"}:
            raise RelayError("latest commit status reports a failure")
        if remote_state != "success":
            raise RelayError("commit status response was invalid")
        target_url = status.get("target_url")
        if target_url is not None and not isinstance(target_url, str):
            raise RelayError("commit status response was invalid")
        if (
            _require_positive_int(status.get("id"), "commit status.id")
            != gate["status_id"]
            or _require_nonempty_string(
                status.get("context"),
                "commit status.context",
            )
            != gate["context"]
            or target_url != gate["target_url"]
        ):
            _sleep_until_retry(deadline)
            continue

        github.timeout = _job_request_timeout(deadline)
        _require_current_pull(github, binding, author_id)
        next_state = dict(state)
        next_state["stage"] = "publication"
        next_state["publication"] = {
            "context": gate["context"],
            "target_url": gate["target_url"],
            "status_id": gate["status_id"],
        }
        _validate_verification_state(
            next_state,
            context,
            receipt_id,
            "publication",
        )
        _write_verification_state(state_file, next_state)
        return


def _fetch_oidc_token(
    session: requests.Session,
    request_url: str,
    request_token: str,
    audience: str,
    *,
    attempts: int = 3,
    timeout: tuple[float, float] = _REQUEST_TIMEOUT,
) -> str:
    payload = _request_json(
        session,
        "GET",
        _oidc_url(request_url, audience),
        extra_headers={"Authorization": f"Bearer {request_token}"},
        expected_statuses=(200,),
        attempts=attempts,
        timeout=timeout,
    )
    return _require_nonempty_string(payload.get("value"), "value")


def _submit_actions_job(
    session: requests.Session,
    url: str,
    token: str,
    json_body: JsonObject,
    *,
    attempts: int = 3,
    timeout: tuple[float, float] = _REQUEST_TIMEOUT,
) -> AcceptedJob:
    payload = _request_json(
        session,
        "POST",
        url,
        token=token,
        json_body=json_body,
        expected_statuses=_POST_STATUSES,
        attempts=attempts,
        timeout=timeout,
    )
    return _accepted_job(payload)


def _poll_actions_job(
    session: requests.Session,
    settings: ActionSettings,
    token: str,
    accepted: AcceptedJob,
    *,
    submit_url: str,
    submit_body: JsonObject,
) -> JsonObject:
    deadline = time.monotonic() + _JOB_POLL_TIMEOUT_SECONDS
    current = accepted
    resubmits = 0
    while True:
        timeout = _job_request_timeout(deadline)
        status_code, payload = _request_json_response(
            session,
            "GET",
            f"{settings.bot_url}{current.url}",
            token=token,
            expected_statuses=(200, 404, 410),
            allow_empty=True,
            attempts=1,
            timeout=timeout,
        )
        if status_code in _JOB_MISSING_STATUSES:
            if resubmits >= _MAX_JOB_RESUBMITS:
                raise RelayError("relay job was lost after restart")
            current = _submit_actions_job(
                session,
                submit_url,
                token,
                submit_body,
                attempts=1,
                timeout=_job_request_timeout(deadline),
            )
            resubmits += 1
            continue
        if _require_nonempty_string(payload.get("job_id"), "job_id") != current.job_id:
            raise RelayError("relay response was invalid")
        state = _require_nonempty_string(payload.get("state"), "state")
        if state == "completed":
            result = payload.get("result")
            if not isinstance(result, dict):
                raise RelayError("relay response was invalid")
            return cast(JsonObject, result)
        if state in _JOB_TERMINAL_ERROR_STATES:
            message = payload.get("error")
            if isinstance(message, str) and message:
                raise RelayError(f"relay job {state}: {message}")
            raise RelayError(f"relay job {state}")
        if state not in _JOB_PENDING_STATES:
            raise RelayError("relay response was invalid")
        time.sleep(min(_JOB_POLL_INTERVAL_SECONDS, _job_remaining(deadline)))


def _require_receipt_metadata(
    value: object,
    expected_receipt_id: str,
    settings: ActionSettings,
) -> JsonObject:
    if not isinstance(value, dict) or frozenset(value) != _RECEIPT_KEYS:
        raise RelayError("receipt metadata shape is invalid")
    receipt = cast(JsonObject, value)
    receipt_id = _require_receipt_id(receipt.get("receipt_id"))
    if receipt_id != expected_receipt_id:
        raise RelayError("receipt id mismatch")
    binding = _require_binding(receipt.get("binding"))
    if binding["repository_id"] != settings.repository_id:
        raise RelayError("receipt repository mismatch")
    actor_id = _require_positive_int(receipt.get("actor_id"), "actor_id")
    question_version = _require_bounded_string(
        receipt.get("question_version"),
        "question_version",
        128,
    )
    issued_at = _require_timestamp(receipt.get("issued_at"), "issued_at")
    app_id = _require_positive_int(receipt.get("app_id"), "app_id")
    installation_id = _require_positive_int(
        receipt.get("installation_id"),
        "installation_id",
    )
    return {
        "receipt_id": receipt_id,
        "binding": binding,
        "actor_id": actor_id,
        "question_version": question_version,
        "issued_at": issued_at,
        "app_id": app_id,
        "installation_id": installation_id,
    }


def _require_receipt_envelope(
    value: object, expected_receipt_id: str, settings: ActionSettings,
) -> tuple[JsonObject, str | None]:
    if not isinstance(value, dict):
        raise RelayError("receipt metadata shape is invalid")
    repository_path = _optional_repository_path(value, settings)
    candidate = {key: item for key, item in value.items() if key != "repository_path"}
    return _require_receipt_metadata(candidate, expected_receipt_id, settings), repository_path


def _compare_receipt_snapshot(
    receipt: JsonObject,
    snapshot: Snapshot,
    settings: ActionSettings,
) -> JsonObject:
    snapshot_metadata: JsonObject = {
        "binding": _require_binding(snapshot.binding()),
        "author_id": _require_positive_int(snapshot.author_id, "snapshot.author_id"),
    }
    return cast(
        JsonObject,
        _compare_receipt_metadata(receipt, snapshot_metadata, settings)["binding"],
    )


def _compare_receipt_metadata(
    receipt: JsonObject,
    snapshot: JsonObject,
    settings: ActionSettings,
) -> JsonObject:
    normalized_receipt = _require_receipt_metadata(
        receipt,
        _require_receipt_id(receipt.get("receipt_id")),
        settings,
    )
    normalized_snapshot = _require_snapshot_metadata(snapshot)
    if normalized_snapshot["author_id"] != normalized_receipt["actor_id"]:
        raise RelayError("receipt actor mismatch")
    if normalized_snapshot["binding"] != normalized_receipt["binding"]:
        raise RelayError("receipt binding mismatch")
    return normalized_snapshot


def _require_publication_view(
    value: object,
    expected_receipt_id: str,
    settings: ActionSettings,
    *,
    expected_repository_path: str | None = None,
) -> tuple[JsonObject, str | None, JsonObject, str | None]:
    if not isinstance(value, dict):
        raise RelayError("publication response shape is invalid")
    if not set(value).issubset({"receipt", "verified_at", "gate", "repository_path"}) or not {
        "receipt", "verified_at", "gate",
    }.issubset(value):
        raise RelayError("publication response shape is invalid")
    repository_path = _optional_repository_path(value, settings)
    if expected_repository_path is not None and repository_path != expected_repository_path:
        raise RelayError("publication repository path mismatch")
    receipt = _require_receipt_metadata(
        value.get("receipt"),
        expected_receipt_id,
        settings,
    )
    raw_verified_at = value.get("verified_at")
    verified_at = (
        None
        if raw_verified_at is None
        else _require_timestamp(raw_verified_at, "verified_at")
    )
    raw_gate = value.get("gate")
    if not isinstance(raw_gate, dict) or frozenset(raw_gate) != _GATE_KEYS:
        raise RelayError("publication gate shape is invalid")
    gate = cast(JsonObject, raw_gate)
    context = _require_limited_string(gate.get("context"), "gate.context", 128)
    target_url = _require_https_url_value(gate.get("target_url"), "gate.target_url")
    expected_target = _expected_receipt_target(settings, expected_receipt_id, repository_path)
    if target_url != expected_target:
        raise RelayError("publication target URL mismatch")
    state = _require_nonempty_string(gate.get("state"), "gate.state")
    if state not in _GATE_STATES:
        raise RelayError("publication gate state is invalid")
    status_id = gate.get("status_id")
    if status_id is not None:
        status_id = _require_positive_int(status_id, "gate.status_id")
    error_code = gate.get("error_code")
    if error_code is not None:
        if not isinstance(error_code, str) or not _ERROR_CODE_RE.fullmatch(error_code):
            raise RelayError("publication error code is invalid")
    if state == "waiting_verification":
        if verified_at is not None or status_id is not None or error_code is not None:
            raise RelayError("publication verification state is invalid")
    else:
        if verified_at is None:
            raise RelayError("publication verification state is invalid")
        if state == "published":
            if status_id is None or error_code is not None:
                raise RelayError("published gate metadata is invalid")
        elif status_id is not None:
            raise RelayError("publication gate metadata is invalid")
    return (
        receipt,
        verified_at,
        {
            "context": context,
            "target_url": target_url,
            "state": state,
            "status_id": status_id,
            "error_code": error_code,
        },
        repository_path,
    )


def _new_verification_state(
    context: ActionContext,
    stage: str,
    receipt: JsonObject,
    *,
    repository_path: str | None = None,
) -> JsonObject:
    run_id, run_attempt = _require_context_run_identity(context)
    state: JsonObject = {
        "schema_version": _STATE_SCHEMA_VERSION,
        "stage": stage,
        "repository": context.settings.repository,
        "repository_id": context.settings.repository_id,
        "owner_id": context.settings.owner_id,
        "workflow": context.settings.workflow,
        "workflow_ref": context.settings.workflow_ref,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "origin": context.settings.bot_url,
        "audience": context.settings.oidc_audience,
        "receipt": receipt,
    }
    if repository_path is not None:
        state["repository_path"] = _require_repository_path(repository_path, context.settings)
    return state


def _load_verification_state(
    path: Path,
    context: ActionContext,
    receipt_id: str,
    *,
    expected_stage: str,
) -> JsonObject:
    raw = _read_private_state_file(path)
    try:
        value = json.loads(raw, object_pairs_hook=_json_object_without_duplicates)
    except (json.JSONDecodeError, UnicodeError, ValueError):
        raise RelayError("verification state is invalid") from None
    if not isinstance(value, dict):
        raise RelayError("verification state is invalid")
    state = cast(JsonObject, value)
    _validate_verification_state(state, context, receipt_id, expected_stage)
    return state


def _validate_verification_state(
    state: JsonObject,
    context: ActionContext,
    receipt_id: object,
    expected_stage: str,
) -> None:
    normalized_receipt_id = _require_receipt_id(receipt_id)
    expected_keys = set(_BASE_STATE_KEYS)
    if "repository_path" not in state:
        expected_keys.remove("repository_path")
    if expected_stage in {"snapshot", "compare", "verify", "publication"}:
        expected_keys.add("snapshot")
    if expected_stage in {"verify", "publication"}:
        expected_keys.add("verified_at")
    if expected_stage == "publication":
        expected_keys.add("publication")
    if set(state) != expected_keys:
        raise RelayError("verification state shape is invalid")
    version = state.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != _STATE_SCHEMA_VERSION:
        raise RelayError("verification state version is invalid")
    if state.get("stage") != expected_stage:
        raise RelayError("verification stage predecessor is invalid")
    run_id, run_attempt = _require_context_run_identity(context)
    expected_context: dict[str, object] = {
        "repository": context.settings.repository,
        "repository_id": context.settings.repository_id,
        "owner_id": context.settings.owner_id,
        "workflow": context.settings.workflow,
        "workflow_ref": context.settings.workflow_ref,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "origin": context.settings.bot_url,
        "audience": context.settings.oidc_audience,
    }
    for key, expected in expected_context.items():
        if state.get(key) != expected:
            raise RelayError("verification state context mismatch")
    repository_path = _state_repository_path(state, context.settings)
    receipt = _require_receipt_metadata(
        state.get("receipt"),
        normalized_receipt_id,
        context.settings,
    )
    if state["receipt"] != receipt:
        raise RelayError("verification receipt metadata is invalid")
    if "snapshot" in expected_keys:
        snapshot = _require_snapshot_metadata(state.get("snapshot"))
        if state["snapshot"] != snapshot:
            raise RelayError("verification snapshot metadata is invalid")
        snapshot_binding = cast(JsonObject, snapshot["binding"])
        if snapshot_binding["repository_id"] != context.settings.repository_id:
            raise RelayError("verification snapshot repository mismatch")
        if expected_stage in {"compare", "verify", "publication"}:
            _compare_receipt_metadata(receipt, snapshot, context.settings)
    if "verified_at" in expected_keys:
        if state.get("verified_at") != _require_timestamp(
            state.get("verified_at"),
            "verified_at",
        ):
            raise RelayError("verification timestamp is invalid")
    if "publication" in expected_keys:
        publication = state.get("publication")
        if not isinstance(publication, dict) or set(publication) != {
            "context",
            "target_url",
            "status_id",
        }:
            raise RelayError("verification publication metadata is invalid")
        _require_limited_string(publication.get("context"), "publication.context", 128)
        _require_https_url_value(
            publication.get("target_url"),
            "publication.target_url",
        )
        _require_positive_int(publication.get("status_id"), "publication.status_id")
        expected_target = _expected_receipt_target(context.settings, normalized_receipt_id, repository_path)
        if publication.get("target_url") != expected_target:
            raise RelayError("verification publication target mismatch")


def _require_snapshot_metadata(value: object) -> JsonObject:
    if not isinstance(value, dict) or set(value) != {"binding", "author_id"}:
        raise RelayError("snapshot metadata shape is invalid")
    return {
        "binding": _require_binding(value.get("binding")),
        "author_id": _require_positive_int(value.get("author_id"), "snapshot.author_id"),
    }


def _validate_state_path_for_write(path: Path) -> None:
    parent = path.parent
    try:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent_info = parent.lstat()
    except OSError:
        raise RelayError("verification state path is unavailable") from None
    if not stat.S_ISDIR(parent_info.st_mode) or parent.is_symlink():
        raise RelayError("verification state directory is unsafe")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    except OSError:
        raise RelayError("verification state path is unavailable") from None
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise RelayError("verification state file is unsafe")


def _read_private_state_file(path: Path) -> str:
    _validate_state_path_for_write(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise RelayError("verification state file is missing") from None
    except OSError:
        raise RelayError("verification state file is unavailable") from None
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > _MAX_STATE_BYTES
        ):
            raise RelayError("verification state file is unsafe")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read(_MAX_STATE_BYTES + 1)
    except OSError:
        raise RelayError("verification state file could not be read") from None
    finally:
        os.close(descriptor)
    if len(content) > _MAX_STATE_BYTES:
        raise RelayError("verification state file is too large")
    try:
        return content.decode("utf-8")
    except UnicodeError:
        raise RelayError("verification state is invalid") from None


def _write_verification_state(path: Path, state: JsonObject) -> None:
    _validate_state_path_for_write(path)
    encoded = json.dumps(
        state,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > _MAX_STATE_BYTES:
        raise RelayError("verification state is too large")
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.new"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    temporary_created = False
    try:
        descriptor = os.open(temporary, flags, 0o600)
        temporary_created = True
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        temporary_created = False
    except OSError:
        raise RelayError("verification state could not be written") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_created:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                raise RelayError("verification temporary state could not be removed") from None


def _json_object_without_duplicates(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _require_context_run_identity(context: ActionContext) -> tuple[str, str]:
    if context.run_id is None or context.run_attempt is None:
        raise RelayError("GitHub Actions run identity is unavailable")
    return (
        _require_numeric_string(context.run_id, "GITHUB_RUN_ID"),
        _require_numeric_string(context.run_attempt, "GITHUB_RUN_ATTEMPT"),
    )


def _require_current_pull(
    github: ActionsGitHubClient,
    binding: JsonObject,
    author_id: int,
) -> None:
    payload = github.pull(_require_positive_int(binding.get("pr"), "binding.pr"))
    if _require_nonempty_string(payload.get("state"), "pull request.state") != "open":
        raise RelayError("pull request is no longer open")
    user = payload.get("user")
    head = payload.get("head")
    base = payload.get("base")
    if not isinstance(user, dict) or not isinstance(head, dict) or not isinstance(base, dict):
        raise RelayError("pull request response was invalid")
    if _require_positive_int(user.get("id"), "pull request.user.id") != author_id:
        raise RelayError("pull request author mismatch")
    if _require_sha(head.get("sha"), "pull request.head.sha") != binding["head_sha"]:
        raise RelayError("pull request head changed")
    if _require_sha(base.get("sha"), "pull request.base.sha") != binding["base_sha"]:
        raise RelayError("pull request base changed")


def _latest_context_status(
    github: ActionsGitHubClient,
    sha: str,
    context: str,
    deadline: float,
) -> JsonObject | None:
    for page in range(1, _MAX_STATUS_PAGES + 1):
        github.timeout = _job_request_timeout(deadline)
        payload = github.request(
            "GET",
            (
                f"repos/{github.settings.repository}/commits/{sha}/status"
                f"?per_page={_STATUS_PAGE_SIZE}&page={page}"
            ),
        )
        if not isinstance(payload, dict):
            raise RelayError("combined status response was invalid")
        if _require_sha(payload.get("sha"), "combined status.sha") != sha:
            raise RelayError("combined status SHA mismatch")
        statuses = payload.get("statuses")
        if not isinstance(statuses, list):
            raise RelayError("combined status response was invalid")
        for raw_status in statuses:
            if not isinstance(raw_status, dict):
                raise RelayError("commit status response was invalid")
            status = cast(JsonObject, raw_status)
            status_context = _require_limited_string(
                status.get("context"),
                "commit status.context",
                128,
            )
            if status_context == context:
                return status
        if len(statuses) < _STATUS_PAGE_SIZE:
            return None
    raise RelayError("commit status pagination limit exceeded")


def _sleep_until_retry(deadline: float) -> None:
    time.sleep(
        min(
            _PUBLICATION_POLL_INTERVAL_SECONDS,
            _job_remaining(deadline),
        )
    )


def _accepted_job(payload: JsonObject) -> AcceptedJob:
    job_id = _require_nonempty_string(payload.get("job_id"), "job_id")
    url = _require_nonempty_string(payload.get("url"), "url")
    expected_url = f"/api/actions/jobs/{quote(job_id, safe='')}"
    if url != expected_url:
        raise RelayError("relay response was invalid")
    return AcceptedJob(job_id=job_id, url=url)


def _job_remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RelayError("relay job timed out")
    return remaining


def _job_request_timeout(deadline: float) -> tuple[float, float]:
    remaining = _job_remaining(deadline)
    read_timeout = max(1.0, min(float(_REQUEST_TIMEOUT[1]), remaining))
    connect_timeout = max(1.0, min(float(_REQUEST_TIMEOUT[0]), read_timeout))
    return (connect_timeout, read_timeout)


def _request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    token: str | None = None,
    extra_headers: Mapping[str, str] | None = None,
    json_body: JsonObject | None = None,
    expected_statuses: tuple[int, ...],
    allow_empty: bool = False,
    attempts: int = 3,
    timeout: tuple[float, float] = _REQUEST_TIMEOUT,
) -> JsonObject:
    _status_code, payload = _request_json_response(
        session,
        method,
        url,
        token=token,
        extra_headers=extra_headers,
        json_body=json_body,
        expected_statuses=expected_statuses,
        allow_empty=allow_empty,
        attempts=attempts,
        timeout=timeout,
    )
    return payload


def _request_json_response(
    session: requests.Session,
    method: str,
    url: str,
    *,
    token: str | None = None,
    extra_headers: Mapping[str, str] | None = None,
    json_body: JsonObject | None = None,
    expected_statuses: tuple[int, ...],
    allow_empty: bool = False,
    attempts: int = 3,
    timeout: tuple[float, float] = _REQUEST_TIMEOUT,
) -> tuple[int, JsonObject]:
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    if extra_headers is not None:
        headers.update(dict(extra_headers))
    remaining = attempts if attempts > 0 else 1
    while remaining > 0:
        remaining -= 1
        try:
            response = session.request(
                method.upper(),
                url,
                headers=headers,
                json=json_body,
                timeout=timeout,
                allow_redirects=False,
            )
        except requests.exceptions.Timeout:
            if remaining > 0:
                continue
            raise RelayError("relay request timed out") from None
        except requests.exceptions.ConnectionError:
            if remaining > 0:
                continue
            raise RelayError("relay connection failed") from None
        except requests.exceptions.RequestException:
            raise RelayError("relay request failed") from None
        if response.status_code not in expected_statuses:
            if remaining > 0 and response.status_code in _RETRYABLE_STATUSES:
                continue
            raise RelayError(f"relay request failed (HTTP {response.status_code})")
        if not response.content:
            if allow_empty:
                return response.status_code, {}
            raise RelayError("relay response was empty")
        try:
            payload = response.json()
        except ValueError:
            raise RelayError("relay response was invalid") from None
        if not isinstance(payload, dict):
            raise RelayError("relay response was invalid")
        return response.status_code, cast(JsonObject, payload)
    raise RelayError("relay request failed")


def _load_event_payload(path: Path) -> JsonObject:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RelayError("GitHub Actions event payload is invalid") from None
    if not isinstance(payload, dict):
        raise RelayError("GitHub Actions event payload is invalid")
    return cast(JsonObject, payload)


def _validate_event_repository(payload: JsonObject, settings: ActionSettings, *, allow_missing: bool = False) -> None:
    repository = payload.get("repository")
    if repository is None and allow_missing:
        return
    if not isinstance(repository, dict):
        raise RelayError("GitHub Actions event payload is invalid")
    if _require_positive_int(repository.get("id"), "repository.id") != settings.repository_id:
        raise RelayError("event repository mismatch")
    if (
        _require_nonempty_string(repository.get("full_name"), "repository.full_name").casefold()
        != settings.repository.casefold()
    ):
        raise RelayError("event repository mismatch")
    owner = repository.get("owner")
    if not isinstance(owner, dict):
        raise RelayError("GitHub Actions event payload is invalid")
    if _require_positive_int(owner.get("id"), "repository.owner.id") != settings.owner_id:
        raise RelayError("event owner mismatch")


def _pull_request_number(payload: JsonObject) -> int:
    value = payload.get("number")
    if value is not None:
        return _require_positive_int(value, "number")
    pull_request = payload.get("pull_request")
    if not isinstance(pull_request, dict):
        raise RelayError("GitHub Actions pull request event is invalid")
    return _require_positive_int(pull_request.get("number"), "pull_request.number")


def _pull_request_action(payload: JsonObject) -> str:
    action = _require_nonempty_string(payload.get("action"), "action")
    if action not in _PR_ACTIONS:
        raise RelayError("GitHub Actions pull request action is invalid")
    return action


def _pull_request_head_sha(payload: JsonObject) -> str:
    pull_request = payload.get("pull_request")
    if not isinstance(pull_request, dict):
        raise RelayError("GitHub Actions pull request event is invalid")
    head = pull_request.get("head")
    if not isinstance(head, dict):
        raise RelayError("GitHub Actions pull request event is invalid")
    return _require_sha(head.get("sha"), "pull_request.head.sha")


def _workflow_dispatch_receipt_id(payload: JsonObject) -> str:
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        raise RelayError("GitHub Actions workflow_dispatch payload is invalid")
    return _require_nonempty_string(inputs.get("receipt_id"), "inputs.receipt_id")


def _require_binding(value: object) -> JsonObject:
    if not isinstance(value, dict) or frozenset(value) != _BINDING_KEYS:
        raise RelayError("receipt binding shape is invalid")
    binding = cast(JsonObject, value)
    _require_positive_int(binding.get("repository_id"), "binding.repository_id")
    _require_positive_int(binding.get("pr"), "binding.pr")
    _require_sha(binding.get("head_sha"), "binding.head_sha")
    _require_sha(binding.get("base_sha"), "binding.base_sha")
    _require_hex64(binding.get("policy_version"), "binding.policy_version")
    _require_hex64(binding.get("snapshot_id"), "binding.snapshot_id")
    _require_nonnegative_int(binding.get("score"), "binding.score")
    if not isinstance(binding.get("triggered"), bool):
        raise RelayError("binding.triggered must be a boolean")
    return dict(binding)


def _oidc_url(request_url: str, audience: str) -> str:
    parsed = urlsplit(request_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["audience"] = audience
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def _require_env(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise RelayError(f"{name} is required")
    return value


def _require_repository(value: str) -> str:
    if not _REPOSITORY_RE.fullmatch(value):
        raise RelayError("GITHUB_REPOSITORY is invalid")
    return value


def _require_workflow(value: str) -> str:
    workflow = value.strip()
    if not _WORKFLOW_RE.fullmatch(workflow):
        raise RelayError("TLH_WORKFLOW is invalid")
    return workflow


def _require_numeric_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.isdigit() or int(value) <= 0:
        raise RelayError(f"{field_name} must be a positive integer string")
    return value


def _require_positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise RelayError(f"{field_name} must be a positive integer")
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit():
        number = int(value)
        if number > 0:
            return number
    raise RelayError(f"{field_name} must be a positive integer")


def _require_nonnegative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RelayError(f"{field_name} must be a non-negative integer")
    return value


def _require_nonempty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RelayError(f"{field_name} must be a non-empty string")
    return value


def _require_bounded_string(
    value: object,
    field_name: str,
    maximum_length: int,
) -> str:
    text = _require_nonempty_string(value, field_name)
    if len(text) > maximum_length or any(character.isspace() for character in text):
        raise RelayError(f"{field_name} is invalid")
    return text


def _require_limited_string(
    value: object,
    field_name: str,
    maximum_length: int,
) -> str:
    text = _require_nonempty_string(value, field_name)
    if (
        len(text) > maximum_length
        or text != text.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in text)
    ):
        raise RelayError(f"{field_name} is invalid")
    return text


def _require_receipt_id(value: object) -> str:
    if not isinstance(value, str) or not _RECEIPT_ID_RE.fullmatch(value):
        raise RelayError("receipt_id is invalid")
    return value


def _optional_repository_path(payload: Mapping[str, object], settings: ActionSettings) -> str | None:
    if "repository_path" not in payload:
        return None
    return _require_repository_path(payload["repository_path"], settings)


def _state_repository_path(state: Mapping[str, object], settings: ActionSettings) -> str | None:
    return _optional_repository_path(state, settings)


def _require_repository_path(value: object, settings: ActionSettings) -> str:
    if not isinstance(value, str) or not _REPOSITORY_PATH_RE.fullmatch(value):
        raise RelayError("repository path is invalid")
    if value != f"/repos/{settings.repository_id}":
        raise RelayError("repository path mismatch")
    return value


def _expected_receipt_target(settings: ActionSettings, receipt_id: str, repository_path: str | None) -> str:
    prefix = "" if repository_path is None else _require_repository_path(repository_path, settings)
    return f"{settings.bot_url}{prefix}/receipts/{quote(_require_receipt_id(receipt_id), safe='')}"


def _require_timestamp(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _TIMESTAMP_RE.fullmatch(value):
        raise RelayError(f"{field_name} is invalid")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise RelayError(f"{field_name} is invalid") from None
    return value


def _require_sha(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise RelayError(f"{field_name} must be a 40-character hexadecimal string")
    return value


def _require_hex64(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _HEX64_RE.fullmatch(value):
        raise RelayError(f"{field_name} must be a 64-character hexadecimal string")
    return value


def _require_file_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_file():
        raise RelayError("GITHUB_EVENT_PATH must point to an existing file")
    return path


def _require_https_origin(raw: str) -> str:
    parsed = urlsplit(raw)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise RelayError("TLH_BOT_URL must be an https origin")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise RelayError("TLH_BOT_URL must be an origin without a path")
    if parsed.username or parsed.password:
        raise RelayError("TLH_BOT_URL must not embed credentials")
    host = parsed.hostname
    if host is None:
        raise RelayError("TLH_BOT_URL must include a host")
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"https://{'[' + host + ']' if ':' in host else host}{port}"


def _require_https_url(raw: str) -> str:
    parsed = urlsplit(raw)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise RelayError("ACTIONS_ID_TOKEN_REQUEST_URL must be an https URL")
    if parsed.username or parsed.password:
        raise RelayError("ACTIONS_ID_TOKEN_REQUEST_URL must not embed credentials")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _require_https_url_value(value: object, field_name: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise RelayError(f"{field_name} is invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise RelayError(f"{field_name} is invalid")
    return value


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
