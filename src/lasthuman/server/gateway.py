"""Verified tenant routing and one bounded, fair multi-repository coordinator."""

from __future__ import annotations

import atexit
import json
import logging
import re
import sqlite3
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from io import BytesIO
from threading import BoundedSemaphore, Event, Lock, RLock, Thread
from typing import TYPE_CHECKING, Protocol, TypeVar, cast
from urllib.parse import urlsplit

import requests
from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from lasthuman.interview import generate_questions, grade as grade_answer
from lasthuman.models import Answer, Hunk, Question
from .config import GatewaySettings, Settings
from .events import (
    ActionsIdentity, DynamicOIDCVerifier, EventError, OIDCError, VerifiedActionsIdentity, decode_event_for_identity,
)
from .github import GitHubClient, GitHubError, GitHubInstallationDiscovery
from .registration import (
    RegistrationDeniedError, RegistrationError, RegistrationNotFoundError, RepositoryContext,
)
from .registry import InstallationDiscovery, RepositoryRegistry
from .runtime_lock import DataDirectoryLock
from .service import BotError, BotService
from .snapshot import SnapshotReader
from .store import Store

if TYPE_CHECKING:
    from .app import AppRuntime

_SCHEDULER_INTERVAL_SECONDS = 5.0
_VERIFIED_ENVIRON_KEY = "lasthuman.verified_actions_identity"
_OAUTH_STATE_RE = re.compile(r"^r([1-9][0-9]{0,18})\.[A-Za-z0-9_-]{32,128}$", re.ASCII)
_T = TypeVar("_T")


class OAuthClient(Protocol):
    def create_authorization_url(self, *, state: str, code_verifier: str) -> str:
        ...

    def fetch_token(self, *, code: str, code_verifier: str) -> Mapping[str, object]:
        ...


class GatewayRuntimeError(RuntimeError):
    def __init__(self, message: str, *, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class _VerifiedRequest:
    identity: VerifiedActionsIdentity
    context: RepositoryContext
    token: str = field(repr=False)


@dataclass
class TenantContainer:
    context: RepositoryContext
    settings: Settings
    app: Flask
    runtime: AppRuntime
    service: BotService
    github: GitHubClient
    access_guard: Callable[[], None] | None = field(default=None, repr=False)
    _lifecycle_lock: RLock = field(default_factory=RLock, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def _require_open(self) -> None:
        if self._closed:
            raise GatewayRuntimeError("tenant runtime is retired", status_code=503)
        if self.access_guard is not None:
            self.access_guard()

    def dispatch(self, environ: dict[str, object]) -> Response:
        with self._lifecycle_lock:
            self._require_open()
            return Response.from_app(self.app, environ, buffered=True)

    def tick_once(self) -> int:
        with self._lifecycle_lock:
            self._require_open()
            return self.runtime.tick_once()

    def drain_events(self, timeout: float | None = None) -> None:
        with self._lifecycle_lock:
            self._require_open()
            self.runtime.drain_events(timeout=timeout)

    def shutdown(self) -> None:
        # Coordinator ticks do not run on either child executor. Join their use
        # of the old Store as well before opening that physical DB for a new generation.
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            self.runtime.shutdown()
            self.github.invalidate_tokens()
            atexit.unregister(self.runtime.shutdown)


@dataclass(frozen=True)
class _TenantCreation:
    context: RepositoryContext
    future: Future[TenantContainer]
    finished: Event = field(default_factory=Event, compare=False)


class ModelCallLimiter:
    def __init__(self, global_limit: int) -> None:
        self._global = BoundedSemaphore(_positive_int(global_limit, "global model limit"))
        self._lock = RLock()
        self._tenant_locks: dict[int, Lock] = {}

    def run(self, context: RepositoryContext, callback: Callable[[], _T]) -> _T:
        with self._lock:
            tenant_lock = self._tenant_locks.setdefault(context.repository_id, Lock())
        if not tenant_lock.acquire(blocking=False):
            raise BotError("Tenant model worker is busy; retry shortly", code="busy", status_code=503)
        try:
            if not self._global.acquire(blocking=False):  # pylint: disable=consider-using-with
                raise BotError("Global model workers are busy; retry shortly", code="busy", status_code=503)
            try:
                return callback()
            finally:
                self._global.release()
        finally:
            tenant_lock.release()


class TenantOIDCVerifier:
    def __init__(self, settings: Settings, dynamic_verifier: DynamicOIDCVerifier) -> None:
        self.settings = settings
        self._dynamic_verifier = dynamic_verifier

    def verify(self, token: str) -> ActionsIdentity:
        verified = request.environ.get(_VERIFIED_ENVIRON_KEY)
        if isinstance(verified, _VerifiedRequest):
            if verified.token != token or not _verified_request_matches_settings(verified, self.settings):
                raise OIDCError("GitHub Actions source is untrusted")
            identity = verified.identity
        else:
            identity = self._dynamic_verifier.verify(token)
            if not _verified_identity_matches_settings(identity, self.settings):
                raise OIDCError("GitHub Actions source is untrusted")
        return ActionsIdentity(
            identity.event_name, identity.run_id, identity.run_attempt, identity.jti, identity.workflow_ref,
            identity.repository, identity.repository_id, identity.owner_id, identity.sub, identity.audience,
        )


class TenantManager:
    def __init__(
        self, settings: GatewaySettings, registry: RepositoryRegistry, *, verifier: DynamicOIDCVerifier,
        github_session_factory: Callable[[], requests.Session] | None = None,
        oauth_factory: Callable[[Settings], OAuthClient | None] | None = None,
        generate: Callable[..., list[Question]] | None = None, grade: Callable[..., Answer] | None = None,
        start_worker: bool = True, lock: DataDirectoryLock | None = None, logger: logging.Logger | None = None,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.verifier = verifier
        self.github_session_factory = github_session_factory
        self.oauth_factory = oauth_factory
        self.generate = generate
        self.grade = grade
        self.clock = time.time
        self.logger = logging.getLogger(__name__) if logger is None else logger
        self.max_tenants = settings.max_registered_repositories
        self._lock = RLock()
        self._stop = Event()
        self._shutdown_finished = Event()
        self._containers: dict[int, TenantContainer] = {}
        self._creating: dict[int, _TenantCreation] = {}
        self._inflight: dict[int, Future[None]] = {}
        self._tenant_health: dict[int, str] = {}
        self._scheduler_thread: Thread | None = None
        self._scheduler_started = False
        self._background_status = "ok"
        self._shutdown_started = False
        self._data_lock = lock
        self._model_limiter = ModelCallLimiter(settings.max_model_calls)
        # Each tenant has at most one tick. A blocked discovery cannot occupy another
        # tenant's scheduling slot; model capacity is independently bounded below.
        self._executor = ThreadPoolExecutor(max_workers=self.max_tenants, thread_name_prefix="lasthuman-tenant-tick")
        self.recover_registered()
        if start_worker:
            self.tick_once()
            self.start_scheduler()

    @property
    def scheduler_running(self) -> bool:
        return self._scheduler_started and self._scheduler_thread is not None and self._scheduler_thread.is_alive()

    @property
    def scheduler_state(self) -> str:
        return "running" if self.scheduler_running else "stopped" if self._scheduler_started else "manual"

    def background_health(self) -> str:
        if self.scheduler_state == "stopped":
            return "scheduler_stopped"
        with self._lock:
            if self._background_status != "ok":
                return self._background_status
            for repository_id, container in self._containers.items():
                health = container.runtime.background_health()
                if health != "ok":
                    self._tenant_health[repository_id] = health
            health_values = set(self._tenant_health.values())
            if not health_values:
                return "ok"
            return next(iter(health_values)) if len(health_values) == 1 else "tenant_degraded"

    def degraded_tenant_count(self) -> int:
        self.background_health()
        with self._lock:
            return len(self._tenant_health)

    def registered_tenant_count(self) -> int:
        try:
            return len(self.registry.list_registered())
        except (RegistrationError, sqlite3.Error, OSError):
            self._background_status = "tenant_recovery_degraded"
            self.logger.error("Registered tenant inventory failed")
            return 0

    def recover_registered(self) -> int:
        return self.registered_tenant_count()

    def start_scheduler(self) -> None:
        with self._lock:
            if self._scheduler_started or self._shutdown_started:
                return
            thread = Thread(target=self._scheduler_loop, name="lasthuman-gateway-scheduler", daemon=True)
            self._scheduler_thread = thread
            self._scheduler_started = True
            thread.start()

    def shutdown(self) -> None:
        with self._lock:
            wait = self._shutdown_started
            self._shutdown_started = True
        if wait:
            self._shutdown_finished.wait()
            return
        try:
            self._stop.set()
            if self._scheduler_thread is not None:
                self._scheduler_thread.join()
            self._wait_for_creations()
            self._executor.shutdown(wait=True)
            with self._lock:
                containers = tuple(self._containers.values())
                self._containers.clear()
                self._inflight.clear()
                self._tenant_health.clear()
            for container in containers:
                container.shutdown()
            self.registry.shutdown()
            if self._data_lock is not None:
                self._data_lock.release()
            atexit.unregister(self.shutdown)
        finally:
            self._shutdown_finished.set()

    def drain_events(self, timeout: float | None = None) -> None:
        deadline = None if timeout is None else self.clock() + timeout
        while True:
            with self._lock:
                futures = tuple(self._inflight.values()) + tuple(
                    creation.future for creation in self._creating.values()
                )
            if not futures:
                break
            for future in futures:
                future.result(timeout=None if deadline is None else max(0.0, deadline - self.clock()))
        for container in self.containers():
            container.drain_events(timeout=timeout)

    def containers(self) -> tuple[TenantContainer, ...]:
        with self._lock:
            return tuple(self._containers.values())

    def register(self, identity: VerifiedActionsIdentity) -> TenantContainer:
        return self.container_for_context(self.registry.register(identity))

    def resolve(self, repository_id: int) -> TenantContainer:
        return self.container_for_context(self.registry.resolve(repository_id))

    def require_identity_context(
        self, identity: VerifiedActionsIdentity, repository_id: int | None = None,
    ) -> TenantContainer:
        if repository_id is not None and repository_id != identity.repository_id:
            raise RegistrationDeniedError("verified repository identity does not match route")
        container = self.resolve(identity.repository_id)
        if container.context.repository != identity.repository or container.context.owner_id != identity.owner_id:
            raise RegistrationDeniedError("verified repository identity is stale")
        self.registry.require_current(container.context)
        return container

    def container_for_context(self, context: RepositoryContext) -> TenantContainer:
        repository_id = context.repository_id
        while True:
            self.registry.require_current(context)
            with self._lock:
                if self._shutdown_started:
                    raise GatewayRuntimeError("gateway runtime is shutting down", status_code=503)
                creation = self._creating.get(repository_id)
                creator = creation is None
                if creator:
                    existing = self._containers.get(repository_id)
                    if existing is not None and existing.context == context:
                        return existing
                    if (
                        repository_id not in self._containers
                        and len(set(self._containers) | set(self._creating)) >= self.max_tenants
                    ):
                        raise RegistrationDeniedError("runtime tenant limit reached")
                    creation = _TenantCreation(context, Future())
                    self._creating[repository_id] = creation
            assert creation is not None
            if creator:
                return self._create_and_publish_container(creation)
            creation.finished.wait()
            if creation.context == context:
                container = creation.future.result()
                self.registry.require_current(context)
                with self._lock:
                    if self._shutdown_started:
                        raise GatewayRuntimeError("gateway runtime is shutting down", status_code=503)
                return container

    def _create_and_publish_container(self, creation: _TenantCreation) -> TenantContainer:
        try:
            container = self._build_and_publish_container(creation.context)
            creation.future.set_result(container)
            return container
        except BaseException as error:
            creation.future.set_exception(error)
            raise
        finally:
            with self._lock:
                creation.finished.set()
                if self._creating.get(creation.context.repository_id) is creation:
                    self._creating.pop(creation.context.repository_id)

    def _build_and_publish_container(self, context: RepositoryContext) -> TenantContainer:
        with self._lock:
            retired = self._containers.pop(context.repository_id, None)
        if retired is not None:
            retired.shutdown()
        container: TenantContainer | None = None
        published = False
        try:
            self.registry.require_current(context)
            container = self._create_container(context)
            self.registry.require_current(context)
            with self._lock:
                if self._shutdown_started:
                    raise GatewayRuntimeError("gateway runtime is shutting down", status_code=503)
                self._containers[context.repository_id] = container
                self._tenant_health.pop(context.repository_id, None)
                published = True
            return container
        finally:
            if container is not None and not published:
                container.shutdown()

    def _create_container(self, context: RepositoryContext) -> TenantContainer:
        settings = self.settings.tenant_settings(context)
        store = Store(settings.database, tenant_generation=settings.tenant_generation)
        session = None if self.github_session_factory is None else self.github_session_factory()
        github = GitHubClient(settings, session, access_guard=lambda: self._require_current_as_github_error(context))
        service = BotService(
            settings, github, SnapshotReader(github, settings.database.parent / "snapshot-cache"), store,
            generate=self._wrap_generate(context), grade=self._wrap_grade(context),
            access_guard=lambda: self._require_current_as_bot_error(context),
        )
        try:
            oauth = None if self.oauth_factory is None else self.oauth_factory(settings)
            from .app import create_app  # pylint: disable=import-outside-toplevel

            child = create_app(
                settings, service=service, github=github, oauth=oauth,
                verifier=TenantOIDCVerifier(settings, self.verifier), start_worker=False,
            )
            runtime = cast("AppRuntime", child.extensions["runtime"])
            return TenantContainer(
                context, settings, child, runtime, service, github,
                access_guard=lambda: self.registry.require_current(context),
            )
        except BaseException:
            service.shutdown()
            github.invalidate_tokens()
            raise

    def _wrap_generate(self, context: RepositoryContext) -> Callable[..., list[Question]]:
        wrapped = self.generate or generate_questions

        def generate(*args: object, **kwargs: object) -> list[Question]:
            self._require_current_as_bot_error(context)
            result = self._model_limiter.run(context, lambda: wrapped(*args, **kwargs))
            self._require_current_as_bot_error(context)
            return result

        return generate

    def _wrap_grade(self, context: RepositoryContext) -> Callable[..., Answer]:
        wrapped = self.grade or grade_answer

        def grade(question: Question, answer_text: str, hunk: Hunk, *, choice: int | None = None) -> Answer:
            self._require_current_as_bot_error(context)
            result = self._model_limiter.run(
                context, lambda: wrapped(question, answer_text, hunk, choice=choice),
            )
            self._require_current_as_bot_error(context)
            return result

        return grade

    def tick_once(self) -> int:
        try:
            contexts = self.registry.list_registered()
        except (RegistrationError, sqlite3.Error, OSError):
            self._background_status = "tenant_recovery_degraded"
            self.logger.error("Tenant inventory failed")
            return 0
        scheduled = sum(self._schedule_context_tick(context) for context in contexts)
        self._background_status = "ok"
        return scheduled

    def _schedule_context_tick(self, context: RepositoryContext) -> bool:
        repo_id = context.repository_id
        with self._lock:
            if self._shutdown_started:
                return False
            current = self._inflight.get(repo_id)
            if current is not None and not current.done():
                return False
            future = self._executor.submit(self._tick_context, context)
            self._inflight[repo_id] = future
            future.add_done_callback(lambda result: self._finish_tick(repo_id, result))
            return True

    def _tick_context(self, context: RepositoryContext) -> None:
        container = self.container_for_context(context)
        container.tick_once()
        health = container.runtime.background_health()
        with self._lock:
            if health == "ok":
                self._tenant_health.pop(context.repository_id, None)
            else:
                self._tenant_health[context.repository_id] = health

    def _finish_tick(self, repository_id: int, future: Future[None]) -> None:
        try:
            future.result()
        except Exception:  # pylint: disable=broad-exception-caught
            with self._lock:
                self._tenant_health[repository_id] = "tenant_tick_degraded"
            self.logger.error("Tenant scheduler tick failed for repository_id=%s", repository_id)
        finally:
            with self._lock:
                if self._inflight.get(repository_id) is future:
                    self._inflight.pop(repository_id)

    def _wait_for_creations(self) -> None:
        while True:
            with self._lock:
                creations = tuple(self._creating.values())
            if not creations:
                return
            for creation in creations:
                creation.finished.wait()

    def _require_current_as_github_error(self, context: RepositoryContext) -> None:
        try:
            self.registry.require_current(context)
        except RegistrationError as error:
            raise GitHubError(str(error), status_code=error.status_code) from error

    def _require_current_as_bot_error(self, context: RepositoryContext) -> None:
        try:
            self.registry.require_current(context)
        except RegistrationError as error:
            raise BotError(str(error), code="repository_unavailable", status_code=error.status_code) from error

    def _scheduler_loop(self) -> None:
        while not self._stop.wait(_SCHEDULER_INTERVAL_SECONDS):
            self.tick_once()


def create_gateway(
    settings: GatewaySettings, *, registry: RepositoryRegistry | None = None,
    discovery: InstallationDiscovery | None = None, verifier: DynamicOIDCVerifier | None = None,
    github_session_factory: Callable[[], requests.Session] | None = None,
    oauth_factory: Callable[[Settings], OAuthClient | None] | None = None,
    generate: Callable[..., list[Question]] | None = None, grade: Callable[..., Answer] | None = None,
    start_worker: bool = True, acquire_lock: bool = True,
) -> Flask:
    if not acquire_lock and settings.mode == "live":
        raise ValueError("live gateway runtime must acquire the data directory lock")
    settings.validate_runtime_paths()
    data_lock = DataDirectoryLock(settings.state_root) if acquire_lock else None
    try:
        if data_lock is not None:
            data_lock.acquire()
        resolved_discovery = discovery or GitHubInstallationDiscovery(
            settings, None if github_session_factory is None else github_session_factory(),
        )
        resolved_registry = registry or RepositoryRegistry(settings, resolved_discovery)
        resolved_verifier = verifier or DynamicOIDCVerifier(settings)
        manager = TenantManager(
            settings, resolved_registry, verifier=resolved_verifier, github_session_factory=github_session_factory,
            oauth_factory=oauth_factory, generate=generate, grade=grade, start_worker=start_worker, lock=data_lock,
        )
    except BaseException:
        if data_lock is not None:
            data_lock.release()
        raise
    app = Flask(__name__)
    app.secret_key = settings.secret_key
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    app.extensions.update(
        registry=resolved_registry, runtime=manager, tenant_manager=manager, drain_events=manager.drain_events,
    )
    atexit.register(manager.shutdown)

    @app.errorhandler(RegistrationError)
    @app.errorhandler(GatewayRuntimeError)
    @app.errorhandler(OIDCError)
    @app.errorhandler(EventError)
    def admission_error(error: RegistrationError | GatewayRuntimeError | OIDCError | EventError) -> Response:
        return _error_response(str(error), error.status_code)

    @app.errorhandler(sqlite3.Error)
    @app.errorhandler(OSError)
    def storage_error(_error: Exception) -> Response:
        return _error_response("Storage temporarily unavailable", 503)

    @app.errorhandler(RequestEntityTooLarge)
    def oversized_body(_error: RequestEntityTooLarge) -> Response:
        return _error_response("request body is too large", 413)

    @app.get("/healthz")
    def healthz() -> Response:
        registered = manager.registered_tenant_count()
        background = manager.background_health()
        return jsonify({
            "status": "alive" if background == "ok" else "degraded", "background": background,
            "scheduler": manager.scheduler_state, "registered_tenants": registered,
            "runtime_tenants": len(manager.containers()), "degraded_tenants": manager.degraded_tenant_count(),
            "tenant_max_workers": 1, "global_model_max_workers": settings.max_model_calls,
        }), 200 if background == "ok" else 503

    @app.get("/")
    @app.get("/dashboard")
    @app.get("/prs/<int:_pr>")
    @app.get("/receipts/<_receipt_id>")
    def selection(_pr: int | None = None, _receipt_id: str | None = None) -> Response:
        return Response(
            "Select a repository-scoped URL such as /repos/<repository_id>/dashboard.",
            status=400, mimetype="text/plain",
        )

    @app.get("/auth/github/callback")
    def oauth_callback() -> Response:
        container = manager.resolve(_repo_id_from_state(request.args.get("state")))
        return _forward_to_child(container, "/auth/github/callback")

    @app.post("/api/actions/events")
    def actions_events() -> Response:
        token, identity = _verified_identity(resolved_verifier)
        payload = _request_json_object()
        decode_event_for_identity(payload, identity)
        container = manager.register(identity)
        return _forward_to_child(
            container, "/api/actions/events", verified=_VerifiedRequest(identity, container.context, token),
            json_override=payload, script_name="",
        )

    @app.route("/api/actions/<path:actions_path>", methods=["GET", "POST"])
    def global_actions(actions_path: str) -> Response:
        token, identity = _verified_identity(resolved_verifier)
        path = _supported_actions_path(actions_path)
        container = manager.require_identity_context(identity)
        return _forward_to_child(
            container, path, verified=_VerifiedRequest(identity, container.context, token), script_name="",
        )

    @app.route("/repos/<int:repository_id>/api/actions/<path:actions_path>", methods=["GET", "POST"])
    def scoped_actions(repository_id: int, actions_path: str) -> Response:
        token, identity = _verified_identity(resolved_verifier)
        path = _supported_actions_path(actions_path, allow_events=True)
        container = manager.require_identity_context(identity, repository_id)
        return _forward_to_child(
            container, path, verified=_VerifiedRequest(identity, container.context, token), script_name="",
        )

    @app.route("/repos/<int:repository_id>", defaults={"child_path": ""}, methods=["GET", "POST"])
    @app.route("/repos/<int:repository_id>/<path:child_path>", methods=["GET", "POST"])
    def tenant_route(repository_id: int, child_path: str) -> Response:
        verified = None
        if request.headers.get("Authorization"):
            token, identity = _verified_identity(resolved_verifier)
            container = manager.require_identity_context(identity, repository_id)
            verified = _VerifiedRequest(identity, container.context, token)
        else:
            container = manager.resolve(repository_id)
        return _forward_to_child(container, "/" + child_path if child_path else "/", verified=verified)

    return app


def _verified_identity(verifier: DynamicOIDCVerifier) -> tuple[str, VerifiedActionsIdentity]:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer ") or not header[7:].strip():
        raise OIDCError("GitHub Actions OIDC token is invalid")
    token = header[7:].strip()
    return token, verifier.verify(token)


def _forward_to_child(
    container: TenantContainer, path_info: str, *, verified: _VerifiedRequest | None = None,
    json_override: Mapping[str, object] | None = None, script_name: str | None = None,
) -> Response:
    environ = request.environ.copy()
    environ["SCRIPT_NAME"] = container.settings.path_prefix if script_name is None else script_name
    environ["PATH_INFO"] = path_info
    if verified is not None:
        environ[_VERIFIED_ENVIRON_KEY] = verified
    if json_override is not None:
        body = json.dumps(json_override, sort_keys=True, separators=(",", ":")).encode("utf-8")
        environ["CONTENT_TYPE"] = "application/json"
    else:
        body = request.get_data(cache=True)
    environ["wsgi.input"] = BytesIO(body)
    environ["CONTENT_LENGTH"] = str(len(body))
    response = container.dispatch(environ)
    _rewrite_location(response, container.settings.path_prefix)
    return response


def _rewrite_location(response: Response, prefix: str) -> None:
    location = response.headers.get("Location")
    if not location:
        return
    parsed = urlsplit(location)
    if parsed.scheme or parsed.netloc:
        return
    if (
        not location.startswith("/") or location.startswith("//") or "\\" in location
        or any(ord(character) < 32 for character in location)
    ):
        raise GatewayRuntimeError("invalid local redirect", status_code=400)
    if parsed.path == prefix or parsed.path.startswith(prefix + "/"):
        return
    if parsed.path == "/" or parsed.path == "/dashboard" or any(
        parsed.path.startswith(candidate) for candidate in ("/auth/", "/prs/", "/receipts/", "/api/")
    ):
        response.headers["Location"] = prefix + location
        return
    raise GatewayRuntimeError("invalid repository redirect", status_code=400)


def _supported_actions_path(actions_path: str, *, allow_events: bool = False) -> str:
    if allow_events and actions_path == "events":
        return "/api/actions/events"
    if re.fullmatch(r"jobs/[A-Za-z0-9._-]+|receipts/[A-Za-z0-9._-]+(?:/(?:publication|verify))?", actions_path):
        return "/api/actions/" + actions_path
    raise EventError("unsupported actions route")


def _repo_id_from_state(state: str | None) -> int:
    match = _OAUTH_STATE_RE.fullmatch(state or "")
    if match is None:
        raise RegistrationNotFoundError("repository selection is required")
    return int(match.group(1))


def _request_json_object() -> dict[str, object]:
    if not request.is_json:
        raise GatewayRuntimeError("request body must be application/json", status_code=415)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise GatewayRuntimeError("request body must be a JSON object", status_code=400)
    return dict(data)


def _error_response(message: str, status_code: int) -> Response:
    if "/api/" in request.path:
        return jsonify({"error": message}), status_code
    return Response(message, status=status_code, mimetype="text/plain")


def _positive_int(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{context} must be a positive integer")
    return value


def _verified_request_matches_settings(verified: _VerifiedRequest, settings: Settings) -> bool:
    context = verified.context
    return (
        context.repository_id == settings.repository_id and context.repository == settings.repository
        and context.owner_id == settings.owner_id and context.installation_id == settings.installation_id
        and context.generation == settings.tenant_generation
        and _verified_identity_matches_settings(verified.identity, settings)
    )


def _verified_identity_matches_settings(identity: VerifiedActionsIdentity, settings: Settings) -> bool:
    expected_workflow = (
        f"{settings.repository}/.github/workflows/{settings.workflow}@{settings.workflow_ref}"
    )
    return (
        identity.repository_id == settings.repository_id and identity.repository == settings.repository
        and identity.owner_id == settings.owner_id
        and identity.workflow_ref == expected_workflow
        and identity.sub == f"repo:{settings.repository}:ref:{settings.workflow_ref}"
        and identity.audience in {settings.oidc_audience, settings.repository}
    )
