"""Trusted GitHub Actions relay for metadata-only PR lifecycle events."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import requests

from .github import GitHubClient, GitHubError, JsonObject
from .snapshot import SnapshotError, SnapshotReader

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
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_WORKFLOW_RE = re.compile(r"^[A-Za-z0-9._-]+\.ya?ml$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_PR_ACTIONS = frozenset({"opened", "synchronize", "reopened", "edited", "labeled", "unlabeled", "closed"})
_BINDING_KEYS = frozenset(
    {"repository_id", "pr", "head_sha", "base_sha", "policy_version", "snapshot_id", "score", "triggered"}
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
) -> None:
    context = _load_context(dict(os.environ if environ is None else environ))
    bot_session = requests.Session() if session is None else session
    github = ActionsGitHubClient(
        context.settings,
        requests.Session() if github_session is None else github_session,
    )
    github.verify_repository()
    payload = _load_event_payload(context.event_path)
    relay_cache = cache_dir or Path(".work/relay-cache")
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


def main() -> int:
    try:
        run()
    except (GitHubError, SnapshotError, RelayError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


def _load_context(environ: Mapping[str, str]) -> ActionContext:
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
            oidc_audience=(environ.get("TLH_OIDC_AUDIENCE", repository).strip() or repository),
            github_token=_require_env(environ, "GH_TOKEN"),
        ),
        event_name=event_name,
        event_path=_require_file_path(_require_env(environ, "GITHUB_EVENT_PATH")),
        token_request_url=_require_https_url(_require_env(environ, "ACTIONS_ID_TOKEN_REQUEST_URL")),
        token_request_token=_require_env(environ, "ACTIONS_ID_TOKEN_REQUEST_TOKEN"),
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
    receipt = _request_json(
        session,
        "GET",
        f"{settings.bot_url}{receipt_path}",
        token=read_token,
        expected_statuses=(200,),
    )
    if _require_nonempty_string(receipt.get("receipt_id"), "receipt_id") != receipt_id:
        raise RelayError("receipt id mismatch")
    binding = _require_binding(receipt.get("binding"))
    if _require_positive_int(binding.get("repository_id"), "binding.repository_id") != settings.repository_id:
        raise RelayError("receipt repository mismatch")
    pr = _require_positive_int(binding.get("pr"), "binding.pr")
    snapshot = SnapshotReader(github, cache_dir).read(pr)
    if snapshot.author_id != _require_positive_int(receipt.get("actor_id"), "actor_id"):
        raise RelayError("receipt actor mismatch")
    _require_nonempty_string(receipt.get("question_version"), "question_version")
    _require_nonempty_string(receipt.get("issued_at"), "issued_at")
    _require_positive_int(receipt.get("app_id"), "app_id")
    _require_positive_int(receipt.get("installation_id"), "installation_id")
    snapshot_binding = snapshot.binding()
    if snapshot_binding != binding:
        raise RelayError("receipt binding mismatch")
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


def _fetch_oidc_token(session: requests.Session, request_url: str, request_token: str, audience: str) -> str:
    payload = _request_json(
        session,
        "GET",
        _oidc_url(request_url, audience),
        extra_headers={"Authorization": f"Bearer {request_token}"},
        expected_statuses=(200,),
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
) -> None:
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
            return
        if state in _JOB_TERMINAL_ERROR_STATES:
            message = payload.get("error")
            if isinstance(message, str) and message:
                raise RelayError(f"relay job {state}: {message}")
            raise RelayError(f"relay job {state}")
        if state not in _JOB_PENDING_STATES:
            raise RelayError("relay response was invalid")
        time.sleep(min(_JOB_POLL_INTERVAL_SECONDS, _job_remaining(deadline)))


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


if __name__ == "__main__":
    raise SystemExit(main())
