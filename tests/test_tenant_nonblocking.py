"""Same-tenant admission and retirement with real offline transport and stores."""

from __future__ import annotations

import atexit
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
from io import BytesIO
from threading import Barrier, Event
from typing import TypeVar
from urllib.parse import urlsplit
from unittest.mock import Mock

import pytest
import requests
from flask import Response
from werkzeug.test import EnvironBuilder

from registration_transport import Integration, integration, obj
from test_org_dashboard_routes import OrganizationHarness, organization_runtime
from lasthuman.server import gateway as gateway_module, relay
from lasthuman.server.gateway import GatewayRuntimeError
from lasthuman.server.github import GitHubError
from lasthuman.server.organization import RepositoryView
from lasthuman.server.registration import RepositoryContext
from lasthuman.server.runtime_lock import DataDirectoryLock

__all__ = ["integration", "organization_runtime"]
_T = TypeVar("_T")


@dataclass
class Gate:
    entered: Event = field(default_factory=Event)
    release: Event = field(default_factory=Event)

    def block(self) -> None:
        self.entered.set()
        assert self.release.wait(timeout=10), "Offline operation was not released"


def bounded(executor: ThreadPoolExecutor, callback: Callable[[], _T]) -> _T:
    started = Event()

    def run() -> _T:
        started.set()
        return callback()

    future = executor.submit(run)
    assert started.wait(timeout=5), "Request thread did not start"
    return future.result(timeout=1)


@pytest.mark.parametrize("slow_transport", ["model", "snapshot"])
def test_real_actions_poll_and_admission_while_coordinator_waits_for_service(
    integration: Integration, monkeypatch: pytest.MonkeyPatch, slow_transport: str,
) -> None:
    body = integration.body(101)
    tenant = integration.manager.register(integration.verifier.verify(integration.http.signed_token(101)))
    headers = integration.headers(101)
    another_owner = integration.headers(101, run_id="2002")
    unauthorized = integration.headers(101, run_id="3003")
    foreign = integration.headers(102)
    gate, tick_attempted = Gate(), Event()
    original_open, original_send = integration.chat.open, integration.http.send
    original_purge = tenant.service.purge_expired

    def blocked_model(request: urllib.request.Request, *, timeout: float) -> BytesIO:
        gate.block()
        return original_open(request, timeout=timeout)

    def blocked_snapshot(request: requests.PreparedRequest, **kwargs: object) -> requests.Response:
        if urlsplit(request.url or "").path == "/repos/acme/one/pulls/1":
            gate.block()
        return original_send(request, **kwargs)

    def purge() -> int:
        tick_attempted.set()
        return original_purge()

    if slow_transport == "model":
        monkeypatch.setattr(urllib.request, "urlopen", blocked_model)
    else:
        monkeypatch.setattr(integration.http, "send", blocked_snapshot)
    monkeypatch.setattr(tenant.service, "purge_expired", purge)
    with ThreadPoolExecutor(max_workers=1) as executor:
        try:
            accepted = bounded(executor, lambda: integration.client().post(
                "/api/actions/events", json=body, headers=headers,
            ))
            assert accepted.status_code == 202
            job = obj(accepted.get_json())
            path = str(job["url"])
            assert gate.entered.wait(timeout=5)
            acquired = tenant.service._lock.acquire(blocking=False)
            if acquired:
                tenant.service._lock.release()
            assert not acquired, "Regression must hold the real service lock"
            assert integration.manager.tick_once() == 1
            assert tick_attempted.wait(timeout=5), "Actual coordinator did not attempt service access"
            for owner in (headers, another_owner):
                if owner is another_owner:
                    repeated = bounded(executor, lambda: integration.client().post(
                        "/api/actions/events", json=body, headers=another_owner,
                    ))
                    assert repeated.status_code == 202
                    assert obj(repeated.get_json())["job_id"] == job["job_id"]
                polled = bounded(executor, partial(integration.client().get, path, headers=owner))
                assert polled.status_code == 200
                assert obj(polled.get_json()) == {"job_id": job["job_id"], "state": "running"}
            for poll_path, owner, status in (
                (path, {}, 401), (path, unauthorized, 403), (path, foreign, 404),
                ("/repos/101" + path, foreign, 403),
            ):
                denied = bounded(executor, partial(integration.client().get, poll_path, headers=owner))
                assert denied.status_code == status
            assert not gate.release.is_set()
            assert not integration.http.repositories[101].comments
            assert not any(status["state"] == "success" for status in integration.http.repositories[101].statuses)
            assert not tenant.service.store.load_receipts_for_snapshot(str(obj(body["binding"])["snapshot_id"]))
        finally:
            gate.release.set()
            integration.drain()
    finished = integration.client().get(path, headers=headers)
    assert obj(finished.get_json())["state"] == "completed"
    stored = tenant.service.store.load_current_snapshot(1)
    assert stored is not None and stored.snapshot.risk.triggered and stored.question_count == 2
    assert integration.chat.calls == ["questions"]
    assert integration.http.jwks_fetches > 0
    assert integration.http.repositories[101].comments
    assert not any(status["state"] == "success" for status in integration.http.repositories[101].statuses)


@pytest.mark.parametrize("operation", ["request", "tick", "drain", "organization", "organization-request"])
def test_shutdown_drains_admitted_operations_and_rejects_new_work(
    integration: Integration, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    job = integration.open_event(101)
    tenant = integration.manager.resolve(101)
    browser, _csrf = integration.browser(101)
    token = next(iter(integration.http.users))
    gate, closing, executor_shutdown = Gate(), Event(), Event()
    original_shutdown, original_wait = tenant.runtime.shutdown, tenant._lifecycle_lock.wait_for

    def wait_for(predicate: Callable[[], bool], timeout: float | None = None) -> bool:
        closing.set()
        return original_wait(predicate, timeout)

    def shutdown() -> None:
        executor_shutdown.set()
        original_shutdown()

    monkeypatch.setattr(tenant._lifecycle_lock, "wait_for", wait_for)
    monkeypatch.setattr(tenant.runtime, "shutdown", shutdown)
    if operation == "request":
        original = tenant.runtime.action_job_payload

        def blocked_job(*args: object) -> dict[str, object]:
            gate.block()
            return original(*args)

        monkeypatch.setattr(tenant.runtime, "action_job_payload", blocked_job)
        invoke = partial(integration.client().get, str(job["url"]), headers=integration.headers(101))
    elif operation in {"organization", "organization-request"}:
        original_dashboard = tenant.service.dashboard

        def blocked_dashboard(**kwargs: object) -> dict[str, object]:
            gate.block()
            return original_dashboard(**kwargs)

        monkeypatch.setattr(tenant.service, "dashboard", blocked_dashboard)
        monkeypatch.setattr(tenant.github, "current_codeowners", lambda *_args, **_kwargs: {})
        if operation == "organization":
            invoke = partial(tenant.organization_read, token, datetime.now(timezone.utc))
        else:
            invoke = partial(browser.get, "/repos/101/dashboard/organization")
    else:
        original_operation = getattr(tenant.runtime, "tick_once" if operation == "tick" else "drain_events")

        def blocked_operation(**kwargs: object) -> object:
            gate.block()
            return original_operation(**kwargs)

        monkeypatch.setattr(tenant.runtime, "tick_once" if operation == "tick" else "drain_events", blocked_operation)
        invoke = tenant.tick_once if operation == "tick" else tenant.drain_events
    with ThreadPoolExecutor(max_workers=4) as executor:
        active = executor.submit(invoke)
        try:
            assert gate.entered.wait(timeout=5)
            closer = executor.submit(tenant.shutdown)
            assert closing.wait(timeout=5)
            another_closer = executor.submit(tenant.shutdown)
            rejected = bounded(executor, lambda: integration.client().get(
                "/api/actions/jobs/unknown", headers=integration.headers(101),
            ))
            assert rejected.status_code == 503
            assert not executor_shutdown.is_set()
            assert not closer.done() and not another_closer.done()
            assert tenant._active_operations > 0
        finally:
            gate.release.set()
        if operation == "organization":
            with pytest.raises(GatewayRuntimeError, match="retired"):
                active.result(timeout=5)
        else:
            result = active.result(timeout=5)
            if operation in {"request", "organization-request"}:
                assert result.status_code == 503
        closer.result(timeout=5)
        another_closer.result(timeout=5)
    assert tenant._active_operations == 0 and executor_shutdown.is_set()
    assert tenant.github._installation_token is None


@pytest.mark.parametrize("operation", ["request", "tick", "drain", "organization"])
@pytest.mark.parametrize("failure", ["guard", "body"])
def test_operation_errors_release_admission(
    integration: Integration, monkeypatch: pytest.MonkeyPatch, operation: str, failure: str,
) -> None:
    tenant = integration.manager.register(integration.verifier.verify(integration.http.signed_token(101)))
    error = RuntimeError("offline operation failure")
    failing = Mock(side_effect=error)
    if failure == "guard":
        monkeypatch.setattr(tenant, "access_guard", failing)
    else:
        targets = {
            "request": (Response, "from_app"),
            "tick": (tenant.runtime, "tick_once"),
            "drain": (tenant.runtime, "drain_events"),
            "organization": (gateway_module, "authorized_repository_info"),
        }
        target, name = targets[operation]
        monkeypatch.setattr(target, name, failing)
    operations = {
        "request": lambda: tenant.dispatch(EnvironBuilder(path="/probe").get_environ()),
        "tick": tenant.tick_once,
        "drain": tenant.drain_events,
        "organization": lambda: tenant.organization_read("offline-user", datetime.now(timezone.utc)),
    }
    with pytest.raises(RuntimeError) as caught:
        operations[operation]()
    assert caught.value is error and tenant._active_operations == 0
    tenant.shutdown()
    assert tenant._shutdown_finished.is_set()
    for invoke in operations.values():
        with pytest.raises(GatewayRuntimeError, match="retired"):
            invoke()
    assert tenant._active_operations == 0


@pytest.mark.parametrize("guard_call", [2, 3, 4])
def test_organization_post_read_guards_release_and_discard_result(
    integration: Integration, monkeypatch: pytest.MonkeyPatch, guard_call: int,
) -> None:
    tenant = integration.manager.register(integration.verifier.verify(integration.http.signed_token(101)))
    failure = GatewayRuntimeError("offline retired generation", status_code=503)
    guard = Mock(side_effect=[None] * (guard_call - 1) + [failure])
    monkeypatch.setattr(tenant, "access_guard", guard)
    monkeypatch.setattr(gateway_module, "authorized_repository_info", Mock(return_value={}))
    monkeypatch.setattr(gateway_module, "read_authorized_repository",
                        Mock(return_value=RepositoryView("101", "acme/one", "/dashboard")))
    with pytest.raises(GatewayRuntimeError) as caught:
        tenant.organization_read("offline-user", datetime.now(timezone.utc))
    assert caught.value is failure
    assert guard.call_count == guard_call and tenant._active_operations == 0
    tenant.shutdown()


def test_response_generation_guard_releases_and_discards_response(
    integration: Integration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = integration.manager.register(integration.verifier.verify(integration.http.signed_token(101)))
    failure = GatewayRuntimeError("offline retired generation", status_code=503)
    monkeypatch.setattr(tenant, "access_guard", Mock(side_effect=[None, failure]))
    monkeypatch.setattr(Response, "from_app", Mock(return_value=Response("must not escape")))
    with pytest.raises(GatewayRuntimeError) as caught:
        tenant.dispatch(EnvironBuilder(path="/dashboard/organization").get_environ())
    assert caught.value is failure and tenant._active_operations == 0
    tenant.shutdown()


def test_organization_user_recheck_failure_discards_projection_and_releases(
    integration: Integration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = integration.manager.register(integration.verifier.verify(integration.http.signed_token(101)))
    failure = GitHubError("offline revoked user permission", status_code=404)
    authorize = Mock(side_effect=[{}, failure])
    read = Mock(return_value=RepositoryView("101", "acme/one", "/dashboard"))
    monkeypatch.setattr(gateway_module, "authorized_repository_info", authorize)
    monkeypatch.setattr(gateway_module, "read_authorized_repository", read)
    with pytest.raises(GitHubError) as caught:
        tenant.organization_read("offline-user", datetime.now(timezone.utc))
    assert caught.value is failure and authorize.call_count == 2 and read.call_count == 1
    assert tenant._active_operations == 0
    tenant.shutdown()


@pytest.mark.parametrize("fail", [False, True])
def test_concurrent_shutdown_waits_for_child_executors_and_replays_failure(
    integration: Integration, monkeypatch: pytest.MonkeyPatch, fail: bool,
) -> None:
    tenant = integration.manager.register(integration.verifier.verify(integration.http.signed_token(101)))
    gate, waiting = Gate(), Event()
    original_shutdown, original_wait = tenant.runtime.shutdown, tenant._shutdown_finished.wait
    error = RuntimeError("offline shutdown failure")

    def shutdown() -> None:
        gate.block()
        original_shutdown()
        if fail:
            raise error

    def wait(timeout: float | None = None) -> bool:
        waiting.set()
        return original_wait(timeout)

    monkeypatch.setattr(tenant.runtime, "shutdown", shutdown)
    monkeypatch.setattr(tenant._shutdown_finished, "wait", wait)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(tenant.shutdown)
        try:
            assert gate.entered.wait(timeout=5)
            second = executor.submit(tenant.shutdown)
            assert waiting.wait(timeout=5)
            assert not second.done()
        finally:
            gate.release.set()
        for future in (first, second):
            if fail:
                with pytest.raises(RuntimeError) as caught:
                    future.result(timeout=5)
                assert caught.value is error
            else:
                future.result(timeout=5)
    if fail:
        with pytest.raises(RuntimeError) as caught:
            tenant.shutdown()
        assert caught.value is error
        # The simulated failure happened after real executor drainage; let fixture
        # teardown close its manager without replaying this intentional failure.
        monkeypatch.setattr(tenant, "shutdown", original_shutdown)


def next_generation(harness: Integration, repo_id: int = 101) -> RepositoryContext:
    harness.http.repositories[repo_id].installation_id += 100
    harness.advance(harness.settings.positive_ttl_seconds + 1)
    return harness.registry.register(harness.verifier.verify(harness.http.signed_token(repo_id)))


def test_replacement_store_waits_for_operations_and_both_real_child_executors(
    integration: Integration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = integration.manager.register(integration.verifier.verify(integration.http.signed_token(101)))
    operation, runtime_worker, service_worker = Gate(), Gate(), Gate()
    closing, runtime_shutdown, service_shutdown, created = Event(), Event(), Event(), Event()
    original_wait = old._lifecycle_lock.wait_for
    original_runtime_shutdown, original_service_shutdown = old.runtime.shutdown, old.service.shutdown
    original_store = gateway_module.Store

    def wait_for(predicate: Callable[[], bool], timeout: float | None = None) -> bool:
        closing.set()
        return original_wait(predicate, timeout)

    def shutdown_runtime() -> None:
        runtime_shutdown.set()
        original_runtime_shutdown()

    def shutdown_service() -> None:
        service_shutdown.set()
        original_service_shutdown()

    def store(*args: object, **kwargs: object) -> gateway_module.Store:
        assert old._active_operations == 0 and old._shutdown_finished.is_set()
        assert runtime_job.done() and service_job.done()
        created.set()
        return original_store(*args, **kwargs)

    monkeypatch.setattr(old.runtime, "tick_once", operation.block)
    monkeypatch.setattr(old._lifecycle_lock, "wait_for", wait_for)
    monkeypatch.setattr(old.runtime, "shutdown", shutdown_runtime)
    monkeypatch.setattr(old.service, "shutdown", shutdown_service)
    monkeypatch.setattr(gateway_module, "Store", store)
    runtime_job = old.runtime._executor.submit(runtime_worker.block)
    service_job = old.service._executor.submit(service_worker.block)
    with ThreadPoolExecutor(max_workers=3) as executor:
        active = executor.submit(old.tick_once)
        try:
            assert all(gate.entered.wait(timeout=5) for gate in (operation, runtime_worker, service_worker))
            context = next_generation(integration)
            creator = executor.submit(integration.manager.container_for_context, context)
            assert closing.wait(timeout=5)
            waiter = executor.submit(integration.manager.container_for_context, context)
            assert not created.is_set() and not runtime_shutdown.is_set()
            assert not integration.manager.containers()
            operation.release.set()
            active.result(timeout=5)
            assert runtime_shutdown.wait(timeout=5)
            assert not created.is_set() and not service_shutdown.is_set()
            runtime_worker.release.set()
            assert service_shutdown.wait(timeout=5)
            assert not created.is_set()
        finally:
            for gate in (operation, runtime_worker, service_worker):
                gate.release.set()
        current = creator.result(timeout=5)
        assert waiter.result(timeout=5) is current
    assert created.is_set()
    assert current.service.store is not old.service.store
    assert current.settings.database == old.settings.database
    assert current.service.store.tenant_generation == context.generation == 2


def test_failed_retirement_cannot_be_bypassed_by_later_creation(
    integration: Integration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = integration.manager.register(integration.verifier.verify(integration.http.signed_token(101)))
    context = next_generation(integration)
    gate = Gate()
    error = RuntimeError("offline retirement failure")
    original_shutdown = old.runtime.shutdown
    create = Mock(wraps=integration.manager._create_container)

    def shutdown() -> None:
        gate.block()
        original_shutdown()
        raise error

    monkeypatch.setattr(old.runtime, "shutdown", shutdown)
    monkeypatch.setattr(integration.manager, "_create_container", create)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(integration.manager.container_for_context, context)
        try:
            assert gate.entered.wait(timeout=5)
            second = executor.submit(integration.manager.container_for_context, context)
        finally:
            gate.release.set()
        for future in (first, second):
            with pytest.raises(RuntimeError) as caught:
                future.result(timeout=5)
            assert caught.value is error
    for _ in range(2):
        with pytest.raises(RuntimeError) as caught:
            integration.manager.container_for_context(context)
        assert caught.value is error
    assert not create.called
    assert integration.manager._retiring[101] is old
    assert not integration.manager._creating
    monkeypatch.setattr(old, "shutdown", original_shutdown)


def test_concurrent_manager_shutdown_replays_failure_and_retains_data_lock(
    integration: Integration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = integration.manager
    manager.register(integration.verifier.verify(integration.http.signed_token(101)))
    gate, waiting = Gate(), Event()
    original_shutdown, original_wait = integration.registry.shutdown, manager._shutdown_finished.wait
    error = RuntimeError("offline registry shutdown failure")

    def shutdown() -> None:
        gate.block()
        original_shutdown()
        raise error

    def wait(timeout: float | None = None) -> bool:
        waiting.set()
        return original_wait(timeout)

    monkeypatch.setattr(integration.registry, "shutdown", shutdown)
    monkeypatch.setattr(manager._shutdown_finished, "wait", wait)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(manager.shutdown)
            try:
                assert gate.entered.wait(timeout=5)
                second = executor.submit(manager.shutdown)
                assert waiting.wait(timeout=5)
                assert not second.done()
            finally:
                gate.release.set()
            for future in (first, second):
                with pytest.raises(RuntimeError) as caught:
                    future.result(timeout=5)
                assert caught.value is error
        with pytest.raises(RuntimeError) as caught:
            manager.shutdown()
        assert caught.value is error
        with pytest.raises(RuntimeError, match="already holds"):
            DataDirectoryLock(integration.settings.state_root).acquire()
        assert not manager.containers()
    finally:
        # All real executors and the fixture registry closed before our injected
        # failure. Release only this fixture's lock and intentional failed closer.
        assert manager._data_lock is not None
        manager._data_lock.release()
        atexit.unregister(manager.shutdown)
        monkeypatch.setattr(manager, "shutdown", lambda: None)


def test_organization_anchor_does_not_wait_for_peer_generation_creation(
    organization_runtime: OrganizationHarness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = organization_runtime
    one, two = harness.register(101), harness.register(102)
    browser, _csrf = harness.browser()
    gate = Gate()
    original_shutdown = two.runtime.shutdown

    def shutdown() -> None:
        gate.block()
        original_shutdown()

    monkeypatch.setattr(two.runtime, "shutdown", shutdown)
    context = next_generation(harness.runtime, 102)
    with ThreadPoolExecutor(max_workers=2) as executor:
        creator = executor.submit(harness.runtime.manager.container_for_context, context)
        try:
            assert gate.entered.wait(timeout=5)
            result = bounded(executor, partial(browser.get, "/repos/101/dashboard/organization"))
            assert result.status_code == 200
            assert "acme/two" not in result.get_data(as_text=True)
            assert one._active_operations == 0
            assert not creator.done()
        finally:
            gate.release.set()
        assert creator.result(timeout=5).context == context


def test_anchor_retirement_drains_whole_organization_request_including_peer_read(
    organization_runtime: OrganizationHarness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = organization_runtime
    one, two = harness.register(101), harness.register(102)
    browser, _csrf = harness.browser()
    gate, closing, created = Gate(), Event(), Event()
    original_dashboard = two.service.dashboard
    original_wait = one._lifecycle_lock.wait_for
    original_create = harness.runtime.manager._create_container

    def dashboard(**kwargs: object) -> dict[str, object]:
        gate.block()
        return original_dashboard(**kwargs)

    def wait_for(predicate: Callable[[], bool], timeout: float | None = None) -> bool:
        closing.set()
        return original_wait(predicate, timeout)

    def create(context: RepositoryContext) -> gateway_module.TenantContainer:
        assert one._active_operations == 0 and two._active_operations == 0
        assert one._shutdown_finished.is_set()
        created.set()
        return original_create(context)

    monkeypatch.setattr(two.service, "dashboard", dashboard)
    monkeypatch.setattr(one._lifecycle_lock, "wait_for", wait_for)
    monkeypatch.setattr(harness.runtime.manager, "_create_container", create)
    with ThreadPoolExecutor(max_workers=2) as executor:
        active = executor.submit(browser.get, "/repos/101/dashboard/organization")
        try:
            assert gate.entered.wait(timeout=5)
            context = next_generation(harness.runtime)
            creator = executor.submit(harness.runtime.manager.container_for_context, context)
            assert closing.wait(timeout=5)
            assert not created.is_set() and not creator.done()
            assert one._active_operations == two._active_operations == 1
        finally:
            gate.release.set()
        assert active.result(timeout=5).status_code != 200
        assert creator.result(timeout=5).context == context
    assert created.is_set()


def test_two_organization_anchors_do_not_nest_lifecycle_mutexes(
    organization_runtime: OrganizationHarness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = organization_runtime
    one, two = harness.register(101), harness.register(102)
    first, _ = harness.runtime.browser(101, allowed={101, 102})
    second, _ = harness.runtime.browser(102, allowed={101, 102})
    barrier = Barrier(2)
    original = one.service.dashboard

    def dashboard(**kwargs: object) -> dict[str, object]:
        barrier.wait(timeout=5)
        return original(**kwargs)

    monkeypatch.setattr(one.service, "dashboard", dashboard)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(first.get, "/repos/101/dashboard/organization"),
            executor.submit(second.get, "/repos/102/dashboard/organization"),
        ]
        try:
            assert all(future.result(timeout=5).status_code == 200 for future in futures)
        finally:
            barrier.abort()
    assert one._active_operations == two._active_operations == 0


@pytest.mark.parametrize("failure_type", [requests.Timeout, requests.ConnectionError])
@pytest.mark.parametrize("stage", ["event submission", "job polling", "OIDC fetch"])
def test_relay_network_failures_name_only_static_stage(
    failure_type: type[requests.RequestException], stage: str,
) -> None:
    secret = "PRIVATE_TOKEN_COOKIE_ANSWER_BODY"
    session = Mock(spec=requests.Session)
    session.request.side_effect = failure_type("https://private.invalid/" + secret)
    settings = relay.ActionSettings("acme/one", 101, 77, "lasthuman-app.yml", "refs/heads/main",
                                    "https://bot.example", "acme/one", secret)
    url = "https://bot.example/api/actions/events"
    with pytest.raises(relay.RelayError) as caught:
        if stage == "event submission":
            relay._submit_actions_job(session, url, secret, {"repository_id": 101})
        elif stage == "job polling":
            relay._poll_actions_job(
                session, settings, secret, relay.AcceptedJob("job", "/api/actions/jobs/job"),
                submit_url=url, submit_body={},
            )
        else:
            relay._fetch_oidc_token(session, "https://token.actions.githubusercontent.com/id", secret, "acme/one")
    suffix = "timed out" if failure_type is requests.Timeout else "connection failed"
    assert str(caught.value) == f"relay {stage} {suffix}"
    assert caught.value.__suppress_context__
    assert session.request.call_count == (1 if stage == "job polling" else 3)
    assert all(call.kwargs["timeout"] == (5, 30) for call in session.request.call_args_list)
    assert relay._JOB_POLL_TIMEOUT_SECONDS == 180
