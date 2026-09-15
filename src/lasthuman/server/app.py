"""Flask HTTP runtime for the GitHub App service."""

from __future__ import annotations

import atexit
import json
import logging
import os
import secrets
import sqlite3
import time
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock, RLock, Thread
from urllib.parse import urlsplit

from flask import Flask, Response, g, jsonify, redirect, render_template, request, url_for
from jinja2 import select_autoescape
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from .auth import AuthError, AuthManager, AuthorizedSession, SESSION_COOKIE_NAME, normalize_next_path
from .config import Settings
from .events import ActionsIdentity, EventError, OIDCError, OIDCVerifier, decode_event
from .github import GitHubClient, GitHubError
from .service import BotError, BotService
from .snapshot import SnapshotReader
from .store import Store

_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"
_MAX_EVENT_JOBS = 50
_JOB_TTL_SECONDS = 1800.0
_SCHEDULER_INTERVAL_SECONDS = 5.0
_MAX_REQUEST_BYTES = 64 * 1024
_SCHEDULER_LOCK = Lock()
_STARTED_PIDS: set[int] = set()
_TERMINAL_JOB_STATES = frozenset({"completed", "error", "conflict", "stale"})


class AppRuntimeError(RuntimeError):
    """Sanitized HTTP runtime failure."""

    def __init__(self, message: str, *, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class SubmissionOwner:
    pr: int
    actor_id: int
    expires_at: float


@dataclass
class BackgroundJob:
    job_id: str
    dedupe_key: str
    kind: str
    repository_id: int
    created_at: float
    expires_at: float
    owner_run_id: str | None
    owner_run_attempt: str | None
    owner_event_name: str | None
    owner_workflow_ref: str | None
    authorized_actions: set[tuple[str, str, str, str]] = field(default_factory=set)
    pr: int | None = None
    receipt_id: str | None = None
    payload: dict[str, object] = field(default_factory=dict)
    state: str = "queued"
    error: str = ""
    result: dict[str, object] | None = None
    future: Future[None] | None = None


class AppRuntime:
    """Background orchestration for sync and verification jobs."""

    def __init__(
        self,
        settings: Settings,
        service: BotService,
        github: GitHubClient,
        auth: AuthManager,
        *,
        start_scheduler: bool,
        logger: logging.Logger | None = None,
    ) -> None:
        self.settings = settings
        self.service = service
        self.github = github
        self.auth = auth
        self.clock = getattr(service, "clock", time.time)
        self.logger = logging.getLogger("lasthuman.server.app") if logger is None else logger
        self._lock = RLock()
        self._stop = Event()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lasthuman-events")
        self._jobs: dict[str, BackgroundJob] = {}
        self._job_keys: dict[str, str] = {}
        self._submissions: dict[str, SubmissionOwner] = {}
        self._scheduler_started = False
        self._scheduler_thread: Thread | None = None
        self._background_status = "ok"
        self._start_scheduler = start_scheduler
        self._shutdown_started = False
        if start_scheduler:
            self.start_scheduler()

    @property
    def scheduler_running(self) -> bool:
        return self._scheduler_started and self._scheduler_thread is not None

    def start_scheduler(self) -> None:
        if self._scheduler_started:
            return
        pid = os.getpid()
        with _SCHEDULER_LOCK:
            if pid in _STARTED_PIDS:
                return
            thread = Thread(
                target=self._scheduler_loop,
                name="lasthuman-scheduler",
                daemon=True,
            )
            thread.start()
            self._scheduler_started = True
            self._scheduler_thread = thread
            _STARTED_PIDS.add(pid)

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown_started:
                return
            self._shutdown_started = True
        self._stop.set()
        thread = self._scheduler_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        with _SCHEDULER_LOCK:
            if self._scheduler_started:
                _STARTED_PIDS.discard(os.getpid())
        self._executor.shutdown(wait=True)
        self.service.shutdown()

    def background_health(self) -> str:
        return self._background_status

    def tick_once(self) -> int:
        with self._lock:
            self._purge_submission_index_locked()
            self._purge_jobs_locked()
        try:
            self.auth.purge_expired()
            self.service.purge_expired()
            processed = self.service.flush_publications()
        except BotError as error:
            if error.code == "storage_error":
                self._background_status = "storage_unavailable"
                self.logger.error("Background storage temporarily unavailable")
                return 0
            self._background_status = "publisher_failed"
            self.logger.error("Background publication failed")
            return 0
        except sqlite3.Error:
            self._background_status = "storage_unavailable"
            self.logger.error("Background storage temporarily unavailable")
            return 0
        except GitHubError:
            self._background_status = "publisher_failed"
            self.logger.error("Background publication failed")
            return 0
        except OSError:
            self._background_status = "storage_unavailable"
            self.logger.error("Background storage temporarily unavailable")
            return 0
        except (TypeError, ValueError):
            self._background_status = "publisher_failed"
            self.logger.error("Background publication failed")
            return 0
        try:
            if self._pending_publication_failures():
                self._background_status = "publisher_degraded"
            else:
                self._background_status = "ok"
        except sqlite3.Error:
            self._background_status = "storage_unavailable"
            self.logger.error("Background storage temporarily unavailable")
            return 0
        return processed

    def drain_events(self, timeout: float | None = None) -> None:
        self.service.drain(timeout=timeout)
        with self._lock:
            futures = [job.future for job in self._jobs.values() if job.future is not None]
        for future in futures:
            future.result(timeout=timeout)
        self.tick_once()

    def record_submission(self, job_id: str, *, pr: int, actor_id: int) -> None:
        with self._lock:
            self._submissions[job_id] = SubmissionOwner(
                pr=pr,
                actor_id=actor_id,
                expires_at=float(self.clock()) + self.settings.session_ttl.total_seconds(),
            )
            self._purge_submission_index_locked()

    def submission_owner(self, job_id: str) -> SubmissionOwner | None:
        with self._lock:
            self._purge_submission_index_locked()
            return self._submissions.get(job_id)

    def enqueue_manual_sync(self, pr: int) -> BackgroundJob:
        return self._enqueue_job(
            dedupe_key=f"manual:{pr}",
            kind="manual_sync",
            repository_id=self.settings.repository_id,
            pr=pr,
            owner=None,
            payload={},
        )

    def enqueue_actions_event(self, identity: ActionsIdentity, payload: Mapping[str, object]) -> BackgroundJob:
        action = payload.get("action")
        if action == "closed":
            key = _stable_json_key(
                {
                    "repository_id": payload.get("repository_id"),
                    "pr": payload.get("pr"),
                    "head_sha": payload.get("head_sha"),
                }
            )
            kind = "closed_event"
        else:
            key = _stable_json_key(_mapping(payload.get("binding"), "binding"))
            kind = "sync_event"
        return self._enqueue_job(
            dedupe_key=f"actions:{kind}:{key}",
            kind=kind,
            repository_id=_positive_int(payload.get("repository_id"), "repository_id"),
            pr=_positive_int(payload.get("pr"), "pr"),
            owner=identity,
            payload=dict(payload),
        )

    def enqueue_receipt_verify(
        self,
        identity: ActionsIdentity,
        receipt_id: str,
        binding: Mapping[str, object],
    ) -> BackgroundJob:
        key = _stable_json_key({"receipt_id": receipt_id, "binding": dict(binding)})
        return self._enqueue_job(
            dedupe_key=f"receipt-verify:{key}",
            kind="receipt_verify",
            repository_id=self.settings.repository_id,
            pr=_positive_int(binding.get("pr"), "binding.pr"),
            receipt_id=receipt_id,
            owner=identity,
            payload={"binding": dict(binding)},
        )

    def action_job_payload(self, identity: ActionsIdentity, job_id: str) -> dict[str, object]:
        with self._lock:
            self._purge_jobs_locked()
            job = self._jobs.get(job_id)
            if job is None:
                raise AppRuntimeError("job not found", status_code=404)
            self._require_job_owner(identity, job)
            payload: dict[str, object] = {
                "job_id": job.job_id,
                "state": job.state,
            }
            if job.error:
                payload["error"] = job.error
            if job.result is not None:
                payload["result"] = dict(job.result)
            return payload

    def manual_job_payload(self, pr: int, job_id: str) -> dict[str, object]:
        with self._lock:
            self._purge_jobs_locked()
            job = self._jobs.get(job_id)
            if job is None:
                raise AppRuntimeError("Sync result expired; retry sync", status_code=410)
            if job.kind != "manual_sync" or job.pr != pr:
                raise AppRuntimeError("job not found", status_code=404)
            payload: dict[str, object] = {
                "job_id": job.job_id,
                "state": job.state,
            }
            if job.error:
                payload["error"] = job.error
            if job.result is not None:
                payload["result"] = dict(job.result)
            return payload

    def _enqueue_job(
        self,
        *,
        dedupe_key: str,
        kind: str,
        repository_id: int,
        pr: int | None,
        owner: ActionsIdentity | None,
        payload: dict[str, object],
        receipt_id: str | None = None,
    ) -> BackgroundJob:
        now = float(self.clock())
        with self._lock:
            self._purge_jobs_locked()
            existing_id = self._job_keys.get(dedupe_key)
            if existing_id is not None:
                existing = self._jobs.get(existing_id)
                if existing is not None:
                    if self._should_replace_job(kind, existing):
                        self._jobs.pop(existing.job_id, None)
                        self._job_keys.pop(dedupe_key, None)
                    else:
                        if owner is not None:
                            self._record_equivalent_actions_owner(existing, owner)
                        return existing
                self._job_keys.pop(dedupe_key, None)
            self._trim_terminal_jobs_locked()
            if len(self._jobs) >= _MAX_EVENT_JOBS:
                raise AppRuntimeError("background job queue is full", status_code=503)
            job = BackgroundJob(
                job_id=secrets.token_urlsafe(18),
                dedupe_key=dedupe_key,
                kind=kind,
                repository_id=repository_id,
                created_at=now,
                expires_at=now + _JOB_TTL_SECONDS,
                owner_run_id=None if owner is None else owner.run_id,
                owner_run_attempt=None if owner is None else owner.run_attempt,
                owner_event_name=None if owner is None else owner.event_name,
                owner_workflow_ref=None if owner is None else owner.workflow_ref,
                authorized_actions=set()
                if owner is None
                else {_actions_owner_key(owner)},
                pr=pr,
                receipt_id=receipt_id,
                payload=dict(payload),
            )
            self._jobs[job.job_id] = job
            self._job_keys[job.dedupe_key] = job.job_id
            job.future = self._executor.submit(self._run_job, job.job_id)
            return job

    def _run_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.state = "running"
            kind = job.kind
            payload = dict(job.payload)
            pr = job.pr
            receipt_id = job.receipt_id
        try:
            if kind == "manual_sync":
                if pr is None:
                    raise AppRuntimeError("job state is invalid", status_code=500)
                result = self.service.sync(pr)
                self._complete_job(job_id, result=_sync_result_payload(result))
                self.tick_once()
                return
            if kind == "sync_event":
                if pr is None:
                    raise AppRuntimeError("job state is invalid", status_code=500)
                binding = _mapping(payload.get("binding"), "binding")
                result = self.service.sync(pr, expected_binding=binding)
                self._complete_job(job_id, result=_sync_result_payload(result))
                self.tick_once()
                return
            if kind == "closed_event":
                if pr is None:
                    raise AppRuntimeError("job state is invalid", status_code=500)
                current_pull = _mapping(self.github.pull(pr), "pull")
                expected_head = _nonempty_str(payload.get("head_sha"), "head_sha")
                actual_head = _head_sha(current_pull)
                if actual_head != expected_head:
                    self._finish_job_conflict(
                        job_id,
                        "closed event head does not match the current pull request",
                    )
                    return
                result = self.service.sync_merged(pr)
                self._complete_job(job_id, result=_merge_result_payload(result))
                return
            if kind == "receipt_verify":
                if receipt_id is None:
                    raise AppRuntimeError("job state is invalid", status_code=500)
                binding = _mapping(payload.get("binding"), "binding")
                result = self.service.verify(receipt_id, binding)
                self._complete_job(job_id, result=_verify_result_payload(result))
                self.tick_once()
                return
            raise AppRuntimeError("job state is invalid", status_code=500)
        except AppRuntimeError as error:
            self._finish_job_error(job_id, BotError(str(error), code="invalid_state", status_code=error.status_code))
        except BotError as error:
            self._finish_job_error(job_id, error)
        except GitHubError:
            self._finish_job_error(
                job_id,
                BotError("GitHub request failed", code="github_error", status_code=502),
            )
        except OSError:
            self._finish_job_error(
                job_id,
                BotError("Background job failed", code="storage_error", status_code=500),
            )
        except (TypeError, ValueError):
            self._finish_job_error(
                job_id,
                BotError("Background job failed", code="invalid_state", status_code=500),
            )

    def _complete_job(self, job_id: str, *, result: dict[str, object]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.state = "completed"
            job.result = result
            job.error = ""

    def _finish_job_conflict(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.state = "conflict"
            job.error = message
            job.result = None

    def _finish_job_error(self, job_id: str, error: BotError) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if error.code == "stale":
                job.state = "stale"
            else:
                job.state = "error"
            job.error = _safe_bot_error_message(error)
            job.result = None

    def _record_equivalent_actions_owner(
        self,
        job: BackgroundJob,
        identity: ActionsIdentity,
    ) -> None:
        if job.owner_event_name is None or job.owner_workflow_ref is None:
            return
        if (
            job.repository_id == self.settings.repository_id
            and job.owner_event_name == identity.event_name
            and job.owner_workflow_ref == identity.workflow_ref
        ):
            job.authorized_actions.add(_actions_owner_key(identity))

    def _require_job_owner(self, identity: ActionsIdentity, job: BackgroundJob) -> None:
        if job.owner_event_name is None:
            raise AppRuntimeError("job is not an actions job", status_code=404)
        allowed = job.authorized_actions
        if not allowed:
            allowed = {
                (
                    job.owner_run_id or "",
                    job.owner_run_attempt or "",
                    job.owner_event_name,
                    job.owner_workflow_ref or "",
                )
            }
        if _actions_owner_key(identity) not in allowed:
            raise AppRuntimeError("job access is forbidden", status_code=403)

    def _scheduler_loop(self) -> None:
        while not self._stop.wait(_SCHEDULER_INTERVAL_SECONDS):
            self.tick_once()

    def _purge_submission_index_locked(self) -> None:
        now = float(self.clock())
        expired = [
            job_id
            for job_id, owner in self._submissions.items()
            if owner.expires_at <= now
        ]
        for job_id in expired:
            self._submissions.pop(job_id, None)

    def _purge_jobs_locked(self) -> None:
        now = float(self.clock())
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if job.expires_at <= now and job.state in _TERMINAL_JOB_STATES
        ]
        for job_id in expired:
            job = self._jobs.pop(job_id)
            self._job_keys.pop(job.dedupe_key, None)

    def _trim_terminal_jobs_locked(self) -> None:
        terminal = [
            job
            for job in self._jobs.values()
            if job.state in _TERMINAL_JOB_STATES
        ]
        overflow = len(self._jobs) - _MAX_EVENT_JOBS + 1
        if overflow <= 0 or not terminal:
            return
        victims = sorted(terminal, key=lambda item: (item.created_at, item.job_id))
        for job in victims[:overflow]:
            self._jobs.pop(job.job_id, None)
            self._job_keys.pop(job.dedupe_key, None)

    def _should_replace_job(self, requested_kind: str, existing: BackgroundJob) -> bool:
        if existing.state not in _TERMINAL_JOB_STATES:
            return False
        if requested_kind == "manual_sync":
            return True
        return existing.state in {"error", "conflict", "stale"}

    def _pending_publication_failures(self) -> bool:
        connection = sqlite3.connect(self.settings.database)
        try:
            row = connection.execute(
                """
                SELECT 1
                FROM outbox
                WHERE last_error_code IS NOT NULL
                  AND (
                    status = 'pending'
                    OR kind IN (
                      'presentation_card',
                      'presentation_check',
                      'presentation_check_cancel',
                      'start_comment',
                      'success_comment'
                    )
                  )
                LIMIT 1
                """
            ).fetchone()
            return row is not None
        finally:
            connection.close()


def build_runtime_dependencies(
    settings: Settings | None = None,
    *,
    service: BotService | None = None,
    github: GitHubClient | None = None,
    oauth: object | None = None,
    verifier: OIDCVerifier | None = None,
) -> tuple[Settings, BotService, GitHubClient, AuthManager, OIDCVerifier]:
    resolved_settings = settings
    if resolved_settings is None and service is not None and hasattr(service, "settings"):
        resolved_settings = getattr(service, "settings")
    if resolved_settings is None and github is not None and hasattr(github, "settings"):
        resolved_settings = getattr(github, "settings")
    if resolved_settings is None:
        resolved_settings = Settings.from_env()

    resolved_github = github
    if resolved_github is None and service is not None and hasattr(service, "github"):
        resolved_github = getattr(service, "github")
    if resolved_github is None:
        resolved_github = GitHubClient(resolved_settings)

    resolved_service = service
    if resolved_service is None:
        store = Store(resolved_settings.database)
        reader = SnapshotReader(
            resolved_github,
            resolved_settings.database.parent / "snapshot-cache",
        )
        resolved_service = BotService(
            resolved_settings,
            resolved_github,
            reader,
            store,
        )

    clock = getattr(resolved_service, "clock", time.time)
    auth = AuthManager(
        resolved_settings,
        resolved_github,
        oauth=oauth,
        clock=clock,
    )
    resolved_verifier = verifier if verifier is not None else OIDCVerifier(resolved_settings)
    return resolved_settings, resolved_service, resolved_github, auth, resolved_verifier


def create_app(
    settings: Settings | None = None,
    *,
    service: BotService | None = None,
    github: GitHubClient | None = None,
    oauth: object | None = None,
    verifier: OIDCVerifier | None = None,
    start_worker: bool = True,
) -> Flask:
    (
        resolved_settings,
        resolved_service,
        resolved_github,
        auth,
        resolved_verifier,
    ) = build_runtime_dependencies(
        settings,
        service=service,
        github=github,
        oauth=oauth,
        verifier=verifier,
    )

    app = Flask(__name__, template_folder=str(_TEMPLATE_DIR))
    app.jinja_env.autoescape = select_autoescape(["html", "htm", "xml", "j2"])
    app.secret_key = resolved_settings.secret_key
    app.config["MAX_CONTENT_LENGTH"] = _MAX_REQUEST_BYTES
    app.config["TRUSTED_HOSTS"] = _trusted_hosts(resolved_settings)
    runtime = AppRuntime(
        resolved_settings,
        resolved_service,
        resolved_github,
        auth,
        start_scheduler=start_worker,
    )
    atexit.register(runtime.shutdown)

    app.extensions["service"] = resolved_service
    app.extensions["auth"] = auth
    app.extensions["verifier"] = resolved_verifier
    app.extensions["runtime"] = runtime
    app.extensions["drain_events"] = runtime.drain_events

    @app.before_request
    def _assign_request_nonce() -> None:
        g.csp_nonce = secrets.token_urlsafe(16)

    @app.after_request
    def _secure_response(response: Response) -> Response:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "base-uri 'none'; "
            "connect-src 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "img-src 'self' data:; "
            "object-src 'none'; "
            "style-src 'self' 'unsafe-inline'; "
            f"script-src 'self' 'nonce-{g.csp_nonce}'"
        )
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def _request_too_large(_error: RequestEntityTooLarge) -> Response:
        return _response_error(
            "request body is too large",
            413,
            as_json=_request_prefers_json(),
        )

    @app.errorhandler(sqlite3.Error)
    def _sqlite_error(_error: sqlite3.Error) -> Response:
        return _response_error(
            "Storage temporarily unavailable",
            503,
            as_json=_request_prefers_json(),
        )

    @app.get("/healthz")
    def healthz() -> Response:
        background = runtime.background_health()
        status_code = 200 if background == "ok" else 503
        return (
            jsonify(
                {
                    "status": "alive" if background == "ok" else "degraded",
                    "background": background,
                    "service_max_workers": 1,
                    "event_max_workers": 1,
                    "scheduler": "running" if runtime.scheduler_running else "manual",
                }
            ),
            status_code,
        )

    @app.get("/")
    def root() -> Response:
        return redirect(url_for("dashboard"), code=302)

    @app.get("/auth/github")
    def github_login() -> Response:
        try:
            sid, authorization_url = auth.begin(
                request.cookies.get(SESSION_COOKIE_NAME),
                request.args.get("next"),
            )
        except AuthError as error:
            return _text_error(str(error), error.status_code)
        response = redirect(authorization_url, code=302)
        _set_sid_cookie(response, auth, resolved_settings, sid)
        return response

    @app.get("/auth/github/callback")
    def github_callback() -> Response:
        try:
            session, next_path = auth.callback(
                request.cookies.get(SESSION_COOKIE_NAME),
                state=request.args.get("state"),
                code=request.args.get("code"),
            )
        except AuthError as error:
            return _text_error(str(error), error.status_code)
        response = redirect(next_path, code=302)
        _set_sid_cookie(response, auth, resolved_settings, session.sid)
        return response

    @app.post("/auth/logout")
    def logout() -> Response:
        sid = request.cookies.get(SESSION_COOKIE_NAME)
        try:
            auth.validate_csrf(sid, _csrf_token_from_request())
        except AuthError as error:
            return _text_error(str(error), error.status_code)
        auth.logout(sid)
        response = redirect(url_for("root"), code=303)
        _clear_sid_cookie(response, auth, resolved_settings)
        return response

    @app.get("/dashboard")
    def dashboard() -> Response:
        session, failure = _authorize_html(auth, next_path="/dashboard")
        if failure is not None:
            return failure
        assert session is not None
        try:
            payload = resolved_service.dashboard(days=_requested_days())
        except BotError as error:
            return _bot_error_response(error, as_json=False)
        return Response(
            render_template(
                "app_dashboard.html.j2",
                repo=resolved_settings.repository,
                dashboard=payload,
                actor_login=session.actor_login,
                csrf_token=session.csrf_token,
                csp_nonce=g.csp_nonce,
                mode_label=_mode_label(resolved_settings),
            ),
            mimetype="text/html",
        )

    @app.get("/api/dashboard")
    def dashboard_api() -> Response:
        session, failure = _authorize_api(auth)
        if failure is not None:
            return failure
        assert session is not None
        try:
            auth.validate_csrf(session.sid, _csrf_token_from_request())
            payload = resolved_service.dashboard(days=_requested_days())
        except AuthError as error:
            return _json_error(str(error), error.status_code)
        except BotError as error:
            return _bot_error_response(error, as_json=True)
        return jsonify(payload)

    @app.get("/prs/<int:pr>")
    def pr_page(pr: int) -> Response:
        session, failure = _authorize_html(auth, next_path=f"/prs/{pr}", pr=pr, require_author=True)
        if failure is not None:
            return failure
        assert session is not None
        current_pull = _mapping(session.pull, "pull") if session.pull is not None else {}
        try:
            interview = resolved_service.interview(pr, session.actor_id)
        except BotError as error:
            if error.code != "not_found":
                return _bot_error_response(error, as_json=False)
            interview = {
                "state": "not_prepared",
                "id": "",
                "question_count": 0,
                "questions": [],
                "reasons": [],
            }
        receipt = _current_receipt_context(resolved_service, pr, session.actor_id)
        return Response(
            render_template(
                "app_pr.html.j2",
                repo=resolved_settings.repository,
                pr=pr,
                title=_pull_title(current_pull),
                head_sha=_head_sha(current_pull),
                pull_state=_pull_state(current_pull),
                interview=interview,
                receipt=receipt,
                csrf_token=session.csrf_token,
                csp_nonce=g.csp_nonce,
                mode_label=_mode_label(resolved_settings),
                initial_request_id=secrets.token_urlsafe(12),
            ),
            mimetype="text/html",
        )

    @app.post("/prs/<int:pr>/sync")
    def pr_sync(pr: int) -> Response:
        if _wants_json():
            session, failure = _authorize_api(auth, pr=pr, require_author=True)
        else:
            session, failure = _authorize_html(
                auth,
                next_path=f"/prs/{pr}",
                pr=pr,
                require_author=True,
            )
        if failure is not None:
            return failure
        assert session is not None
        try:
            auth.validate_csrf(session.sid, _csrf_token_from_request())
            job = runtime.enqueue_manual_sync(pr)
        except AuthError as error:
            return _response_error(str(error), error.status_code, as_json=_wants_json())
        except AppRuntimeError as error:
            return _response_error(str(error), error.status_code, as_json=_wants_json())
        if _wants_json():
            return (
                jsonify(
                    {
                        "job_id": job.job_id,
                        "url": url_for("pr_sync_result", pr=pr, job_id=job.job_id),
                    }
                ),
                202,
            )
        return redirect(url_for("pr_page", pr=pr), code=303)

    @app.get("/api/prs/<int:pr>/sync/<job_id>")
    def pr_sync_result(pr: int, job_id: str) -> Response:
        session, failure = _authorize_api(auth, pr=pr, require_author=True)
        if failure is not None:
            return failure
        assert session is not None
        try:
            auth.validate_csrf(session.sid, _csrf_token_from_request())
            payload = runtime.manual_job_payload(pr, job_id)
        except AuthError as error:
            return _json_error(str(error), error.status_code)
        except AppRuntimeError as error:
            return _json_error(str(error), error.status_code)
        return jsonify(payload)

    @app.post("/api/prs/<int:pr>/submissions")
    def submit_answers(pr: int) -> Response:
        session, failure = _authorize_api(auth, pr=pr, require_author=True)
        if failure is not None:
            return failure
        assert session is not None
        try:
            auth.validate_csrf(session.sid, _csrf_token_from_request())
            data = _request_json_object()
            if set(data) != {"snapshot_id", "request_id", "answers"}:
                raise AppRuntimeError("submission payload is invalid", status_code=400)
            answers = data.get("answers")
            if not isinstance(answers, list):
                raise AppRuntimeError("submission payload is invalid", status_code=400)
            job_id = resolved_service.submit(
                pr,
                session.actor_id,
                _nonempty_str(data.get("snapshot_id"), "snapshot_id"),
                _nonempty_str(data.get("request_id"), "request_id"),
                tuple(_mapping(item, "answer") for item in answers),
            )
            runtime.record_submission(job_id, pr=pr, actor_id=session.actor_id)
        except AuthError as error:
            return _json_error(str(error), error.status_code)
        except AppRuntimeError as error:
            return _json_error(str(error), error.status_code)
        except BotError as error:
            return _bot_error_response(error, as_json=True)
        except (TypeError, ValueError):
            return _json_error("submission payload is invalid", 400)
        return (
            jsonify(
                {
                    "job_id": job_id,
                    "url": url_for("submission_result", job_id=job_id),
                }
            ),
            202,
        )

    @app.get("/api/submissions/<job_id>")
    def submission_result(job_id: str) -> Response:
        owner = runtime.submission_owner(job_id)
        if owner is None:
            return _json_error("Result expired; resubmit your answers", 410)
        session, failure = _authorize_api(auth, pr=owner.pr, require_author=True)
        if failure is not None:
            return failure
        assert session is not None
        try:
            auth.validate_csrf(session.sid, _csrf_token_from_request())
            if session.actor_id != owner.actor_id:
                return _json_error("Only the author can read this result", 403)
            payload = resolved_service.result(job_id, session.actor_id)
        except AuthError as error:
            return _json_error(str(error), error.status_code)
        except BotError as error:
            return _bot_error_response(error, as_json=True)
        return jsonify(payload)

    @app.get("/receipts/<receipt_id>")
    def receipt_page(receipt_id: str) -> Response:
        session, failure = _authorize_html(auth, next_path="/dashboard")
        if failure is not None:
            return failure
        assert session is not None
        try:
            detail = resolved_service.receipt_detail(receipt_id, session.actor_id)
            auth.authorize(session.sid, pr=_positive_int(detail.get("pr"), "pr"), require_author=True)
        except AuthError as error:
            if error.status_code == 401:
                return redirect(url_for("github_login", next="/dashboard"), code=302)
            return _text_error(str(error), error.status_code)
        except BotError as error:
            return _bot_error_response(error, as_json=False)
        return Response(
            render_template(
                "app_receipt.html.j2",
                repo=resolved_settings.repository,
                receipt=detail,
                csrf_token=session.csrf_token,
                csp_nonce=g.csp_nonce,
                mode_label=_mode_label(resolved_settings),
            ),
            mimetype="text/html",
        )

    @app.post("/api/actions/events")
    def actions_events() -> Response:
        try:
            identity = _actions_identity(resolved_verifier)
            payload = _request_json_object()
            event = decode_event(payload, identity, resolved_settings)
            job = runtime.enqueue_actions_event(
                identity,
                {
                    "repository_id": resolved_settings.repository_id,
                    "pr": event.pr,
                    "action": event.action,
                    **(
                        {"head_sha": event.expected_binding["head_sha"]}
                        if event.action == "closed"
                        else {"binding": dict(event.expected_binding)}
                    ),
                },
            )
        except OIDCError as error:
            return _json_error(str(error), error.status_code)
        except EventError as error:
            return _json_error(str(error), error.status_code)
        except AppRuntimeError as error:
            return _json_error(str(error), error.status_code)
        return jsonify({"job_id": job.job_id, "url": url_for("actions_job", job_id=job.job_id)}), 202

    @app.get("/api/actions/jobs/<job_id>")
    def actions_job(job_id: str) -> Response:
        try:
            identity = _actions_identity(resolved_verifier)
            payload = runtime.action_job_payload(identity, job_id)
        except OIDCError as error:
            return _json_error(str(error), error.status_code)
        except AppRuntimeError as error:
            return _json_error(str(error), error.status_code)
        return jsonify(payload)

    @app.get("/api/actions/receipts/<receipt_id>")
    def actions_receipt(receipt_id: str) -> Response:
        try:
            identity = _actions_identity(resolved_verifier)
            if identity.event_name != "workflow_dispatch":
                raise EventError("workflow_dispatch must use the receipt verification route")
            payload = resolved_service.receipt_binding(receipt_id)
        except OIDCError as error:
            return _json_error(str(error), error.status_code)
        except EventError as error:
            return _json_error(str(error), error.status_code)
        except BotError as error:
            return _bot_error_response(error, as_json=True)
        return jsonify(payload)

    @app.post("/api/actions/receipts/<receipt_id>/verify")
    def actions_verify_receipt(receipt_id: str) -> Response:
        try:
            identity = _actions_identity(resolved_verifier)
            if identity.event_name != "workflow_dispatch":
                raise EventError("workflow_dispatch must use the receipt verification route")
            data = _request_json_object()
            if set(data) != {"binding"}:
                raise EventError("verification payload is invalid")
            binding = _mapping(data.get("binding"), "binding")
            job = runtime.enqueue_receipt_verify(identity, receipt_id, binding)
        except OIDCError as error:
            return _json_error(str(error), error.status_code)
        except EventError as error:
            return _json_error(str(error), error.status_code)
        except AppRuntimeError as error:
            return _json_error(str(error), error.status_code)
        except (TypeError, ValueError):
            return _json_error("verification payload is invalid", 400)
        except BotError as error:
            return _bot_error_response(error, as_json=True)
        return jsonify({"job_id": job.job_id, "url": url_for("actions_job", job_id=job.job_id)}), 202

    return app


def _authorize_html(
    auth: AuthManager,
    *,
    next_path: str,
    pr: int | None = None,
    require_author: bool = False,
) -> tuple[AuthorizedSession | None, Response | None]:
    try:
        session = auth.authorize(
            request.cookies.get(SESSION_COOKIE_NAME),
            pr=pr,
            require_author=require_author,
        )
        return session, None
    except AuthError as error:
        if error.status_code == 401:
            redirect_next = _safe_login_redirect(next_path)
            return None, redirect(url_for("github_login", next=redirect_next), code=302)
        return None, _text_error(str(error), error.status_code)


def _authorize_api(
    auth: AuthManager,
    *,
    pr: int | None = None,
    require_author: bool = False,
) -> tuple[AuthorizedSession | None, Response | None]:
    try:
        session = auth.authorize(
            request.cookies.get(SESSION_COOKIE_NAME),
            pr=pr,
            require_author=require_author,
        )
        return session, None
    except AuthError as error:
        return None, _json_error(str(error), error.status_code)


def _set_sid_cookie(response: Response, auth: AuthManager, settings: Settings, sid: str) -> None:
    cookie = auth.sessions.cookie_settings(settings)
    response.set_cookie(cookie.pop("key"), sid, **cookie)


def _clear_sid_cookie(response: Response, auth: AuthManager, settings: Settings) -> None:
    cookie = auth.sessions.cookie_settings(settings)
    key = str(cookie.pop("key"))
    cookie.pop("max_age", None)
    response.set_cookie(key, "", **cookie, max_age=0)


def _csrf_token_from_request() -> str | None:
    header = request.headers.get("X-CSRF-Token")
    if header is not None:
        return header
    if request.form:
        value = request.form.get("csrf_token")
        if isinstance(value, str):
            return value
    return None


def _request_json_object() -> dict[str, object]:
    if not request.is_json:
        raise AppRuntimeError("request body must be application/json", status_code=415)
    try:
        data = request.get_json(silent=False)
    except BadRequest:
        raise AppRuntimeError("request body must be a JSON object", status_code=400) from None
    if not isinstance(data, dict):
        raise AppRuntimeError("request body must be a JSON object", status_code=400)
    return dict(data)


def _json_error(message: str, status_code: int) -> Response:
    return jsonify({"error": message}), status_code


def _text_error(message: str, status_code: int) -> Response:
    return Response(message, status=status_code, mimetype="text/plain")


def _response_error(message: str, status_code: int, *, as_json: bool) -> Response:
    if as_json:
        return _json_error(message, status_code)
    return _text_error(message, status_code)


def _bot_error_response(error: BotError, *, as_json: bool) -> Response:
    return _response_error(
        _safe_bot_error_message(error),
        _safe_bot_error_status(error),
        as_json=as_json,
    )


def _actions_identity(verifier: OIDCVerifier) -> ActionsIdentity:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise OIDCError("GitHub Actions OIDC token is invalid")
    token = header[7:].strip()
    if not token:
        raise OIDCError("GitHub Actions OIDC token is invalid")
    return verifier.verify(token)


def _current_receipt_context(service: BotService, pr: int, actor_id: int) -> dict[str, object] | None:
    record = service.store.load_current_snapshot(pr)
    if record is None:
        return None
    receipt = service.store.load_receipt_by_key(
        record.snapshot.snapshot_id,
        record.question_version,
        actor_id,
    )
    if receipt is None:
        return None
    payload = service.publication_status(receipt.receipt_id, actor_id)
    return {"receipt_id": receipt.receipt_id, **payload}


def _safe_login_redirect(next_path: str) -> str:
    try:
        return normalize_next_path(next_path)
    except AuthError:
        return "/dashboard"


def _requested_days() -> int:
    raw = request.args.get("days")
    if raw is None or raw == "":
        return 30
    if not raw.isdigit():
        raise BotError("days must be positive", code="invalid_request", status_code=400)
    value = int(raw)
    if value <= 0:
        raise BotError("days must be positive", code="invalid_request", status_code=400)
    return value


def _mode_label(settings: Settings) -> str:
    if settings.mode == "live" and urlsplit(settings.base_url).scheme.lower() == "https":
        return "LIVE"
    return "DEV local only"


def _mapping(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    return dict(value)


def _positive_int(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{context} is invalid")
    return value


def _nonempty_str(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} is invalid")
    return value


def _pull_title(pull: Mapping[str, object]) -> str:
    title = pull.get("title")
    return title if isinstance(title, str) and title else "Pull request"


def _pull_state(pull: Mapping[str, object]) -> str:
    state = pull.get("state")
    return state if isinstance(state, str) and state else "open"


def _head_sha(pull: Mapping[str, object]) -> str:
    head = pull.get("head")
    if not isinstance(head, Mapping):
        return ""
    sha = head.get("sha")
    return sha if isinstance(sha, str) else ""


def _wants_json() -> bool:
    best = request.accept_mimetypes.best
    return best == "application/json"


def _request_prefers_json() -> bool:
    return request.path.startswith("/api/") or request.is_json or _wants_json()


def _stable_json_key(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _actions_owner_key(identity: ActionsIdentity) -> tuple[str, str, str, str]:
    return (
        identity.run_id,
        identity.run_attempt,
        identity.event_name,
        identity.workflow_ref,
    )


def _sync_result_payload(result: Mapping[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {
        "state": result.get("state"),
        "pr": result.get("pr"),
    }
    for key in ("snapshot_id", "question_count"):
        if key in result:
            payload[key] = result[key]
    return payload


def _merge_result_payload(result: Mapping[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {
        "state": result.get("state"),
        "pr": result.get("pr"),
        "measured": result.get("measured"),
    }
    if "merged_at" in result:
        payload["merged_at"] = result.get("merged_at")
    return payload


def _verify_result_payload(result: Mapping[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {
        "state": result.get("state"),
        "pr": result.get("pr"),
        "receipt_id": result.get("receipt_id"),
    }
    if "verified_at" in result:
        payload["verified_at"] = result.get("verified_at")
    return payload


def _safe_bot_error_message(error: BotError) -> str:
    if error.code == "model_error":
        return "Model processing failed; retry later"
    if error.code == "storage_error":
        return "Storage temporarily unavailable"
    return str(error)


def _safe_bot_error_status(error: BotError) -> int:
    if error.code == "storage_error":
        return 503
    return error.status_code


def _trusted_hosts(settings: Settings) -> list[str]:
    hosts = ["localhost", "127.0.0.1"]
    hostname = urlsplit(settings.base_url).hostname
    if hostname and hostname not in hosts:
        hosts.insert(0, hostname)
    return hosts
