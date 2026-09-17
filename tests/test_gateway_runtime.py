"""Actual coordinator lifecycle, scheduler fairness and bounded model execution."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from urllib.parse import urlsplit

import pytest

from registration_transport import PASS, Integration, integration, obj
from lasthuman.server import gateway as gateway_module
from lasthuman.server.gateway import ModelCallLimiter
from lasthuman.server.registration import RepositoryContext
from lasthuman.server.runtime_lock import DataDirectoryLock
from lasthuman.server.service import BotError

__all__ = ["integration"]


def test_actual_scheduler_delivers_other_tenant_while_discovery_blocks(
    integration: Integration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for repo_id in (101, 102):
        integration.open_event(repo_id)
        browser, csrf = integration.browser(repo_id)
        integration.http.repositories[repo_id].fail_writes = True
        _, result = integration.submit(browser, csrf, repo_id, PASS, "scheduler-pending")
        assert result["state"] == "awaiting_verification"
    integration.advance(361)
    for repo in integration.http.repositories.values():
        repo.fail_writes = False
    entered, release, dispatched = Event(), Event(), Event()
    integration.http.blocks[101] = (entered, release)
    integration.http.dispatch_events[102] = dispatched
    monkeypatch.setattr(gateway_module, "_SCHEDULER_INTERVAL_SECONDS", 0.01)
    integration.manager.start_scheduler()
    try:
        assert entered.wait(timeout=5), "Scheduler did not revalidate expired discovery"
        assert dispatched.wait(timeout=5), "Blocked repository starved another repository"
        assert not integration.http.repositories[101].dispatches
        assert integration.manager.scheduler_running
        assert all(tenant.runtime.scheduler_state == "manual" for tenant in integration.manager.containers())
        assert integration.client().get("/healthz").get_json()["scheduler"] == "running"
    finally:
        release.set()
        integration.manager.shutdown()
    assert not integration.manager.scheduler_running


def test_restart_revalidates_and_finishes_queue_without_model_replay(integration: Integration) -> None:
    integration.open_event(101)
    browser, csrf = integration.browser(101)
    old = integration.manager.resolve(101)
    repo = integration.http.repositories[101]
    repo.fail_writes = True
    _, passed = integration.submit(browser, csrf, 101, PASS, "restart-request")
    receipt_id = str(passed["receipt_id"])
    saved = old.service.store.load_receipt(receipt_id)
    assert saved is not None
    before = list(integration.chat.calls)
    context = old.context
    cookie_domain = urlsplit(old.settings.base_url).hostname
    assert cookie_domain is not None
    previous_sid = browser.get_cookie(old.settings.session_cookie_name, domain=cookie_domain)
    assert previous_sid is not None
    integration.restart()
    assert not integration.manager.containers()
    assert integration.registry.list_registered() == (context,)
    repo.outage = 501
    assert integration.client().get("/repos/101/dashboard").status_code == 503
    assert not integration.manager.containers()
    repo.outage, repo.fail_writes = None, False
    integration.advance(301)
    integration.http.calls.clear()
    assert integration.manager.tick_once() == 1
    integration.drain()
    current = integration.manager.resolve(101)
    current.service.clock = integration.clock
    integration.manager.tick_once()
    integration.drain()
    assert current is not old and current.context == context
    assert current.service.store.load_receipt(receipt_id) == saved
    assert integration.chat.calls == before
    assert any(call.path.endswith("/installation") for call in integration.http.calls)
    assert any(obj(item["inputs"])["receipt_id"] == receipt_id for item in repo.dispatches)
    restarted_browser = integration.client()
    restarted_browser.set_cookie(
        current.settings.session_cookie_name, previous_sid.value, domain=previous_sid.domain,
    )
    assert restarted_browser.get("/repos/101/prs/1").status_code == 302


def test_generation_replacement_waits_for_real_old_store_shutdown(
    integration: Integration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    integration.open_event(101)
    old = integration.manager.resolve(101)
    integration.http.repositories[101].installation_id = 301
    integration.advance(61)
    context = integration.registry.register(integration.verifier.verify(integration.http.signed_token(101)))
    entered, release = Event(), Event()
    original_shutdown = old.runtime.shutdown

    def blocked_shutdown() -> None:
        entered.set()
        assert release.wait(timeout=10)
        original_shutdown()

    monkeypatch.setattr(old.runtime, "shutdown", blocked_shutdown)
    with ThreadPoolExecutor(max_workers=2) as executor:
        creator = executor.submit(integration.manager.container_for_context, context)
        try:
            assert entered.wait(timeout=5)
            waiter = executor.submit(integration.manager.container_for_context, context)
            with pytest.raises(TimeoutError):
                waiter.result(timeout=0.1)
            assert not integration.manager.containers()
            assert old.service.store.tenant_generation == 1
        finally:
            release.set()
        current = creator.result(timeout=5)
        assert current is waiter.result(timeout=5)
    assert current.service.store is not old.service.store
    assert current.settings.database == old.settings.database
    assert current.service.store.tenant_generation == context.generation == 2
    assert old.github._installation_token is None


def test_global_four_and_per_tenant_one_limits_release_slots_after_error() -> None:
    limiter = ModelCallLimiter(global_limit=4)
    contexts = [RepositoryContext(i, f"acme/repo-{i}", 77, 200 + i, 1) for i in range(1, 6)]
    entered, release = Barrier(5), Event()

    def occupy() -> None:
        entered.wait(timeout=10)
        assert release.wait(timeout=10)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(limiter.run, context, occupy) for context in contexts[:4]]
        try:
            entered.wait(timeout=10)
            for context in (contexts[0], contexts[4]):
                with pytest.raises(BotError) as failure:
                    limiter.run(context, lambda: pytest.fail("Busy model callback executed"))
                assert failure.value.code == "busy" and failure.value.status_code == 503
        finally:
            release.set()
        for future in futures:
            future.result(timeout=5)

    def fail() -> None:
        raise ValueError("offline model failure")

    with pytest.raises(ValueError, match="offline model failure"):
        limiter.run(contexts[4], fail)
    assert limiter.run(contexts[4], lambda: "slot recovered") == "slot recovered"


def test_gateway_data_lock_is_owned_until_shutdown(integration: Integration) -> None:
    with pytest.raises(RuntimeError, match="already holds"):
        DataDirectoryLock(integration.settings.state_root).acquire()
    integration.manager.shutdown()
    with DataDirectoryLock(integration.settings.state_root):
        assert not integration.manager.containers()
