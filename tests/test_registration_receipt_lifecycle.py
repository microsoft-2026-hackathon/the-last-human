"""Revocation and receipt history through the real gateway and real stores."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from registration_transport import HOLD, PASS, Integration, Repository, integration, obj
from test_first_event_integration import registry_row
from test_app_store import make_questions, make_snapshot
from lasthuman.server.github import GitHubError
from lasthuman.server.service import BotError
from lasthuman.server.store import PublicationRequest, ReceiptAnswer, Store, StoredReceipt, StoredSnapshot

__all__ = ["integration"]

RECEIPT_COLUMNS = (
    "receipt_id", "snapshot_id", "pr", "repo", "repo_id", "head_sha", "base_sha", "policy_version",
    "question_version", "actor_id", "actor_login", "app_id", "installation_id", "created_at", "verified_at",
    "successful_answers_json",
)
RECEIPT_KEY = ("snapshot_id", "question_version", "actor_id", "app_id", "installation_id", "tenant_generation")


@pytest.mark.parametrize("revocation", ["removed", "suspended"])
def test_revocation_after_cache_expiry_blocks_browser_and_queued_dispatch(
    integration: Integration, revocation: str,
) -> None:
    integration.open_event(101)
    browser, csrf = integration.browser(101)
    tenant = integration.manager.resolve(101)
    repo = integration.http.repositories[101]
    repo.fail_writes = True
    _, result = integration.submit(browser, csrf, 101, PASS, "queued-before-revocation")
    receipt_id = str(result["receipt_id"])
    dispatch = tenant.service.store.load_verifier_dispatch(receipt_id)
    assert dispatch is not None and dispatch.status == "pending"
    setattr(repo, revocation, True)
    assert integration.registry.resolve(101) == tenant.context
    integration.advance(integration.settings.positive_ttl_seconds + 301)
    repo.fail_writes = False
    integration.http.calls.clear()
    assert browser.get("/repos/101/dashboard").status_code == 403
    assert registry_row(integration, 101)["state"] == "retired"
    with pytest.raises(BotError) as unavailable:
        tenant.service.flush_publications()
    assert unavailable.value.code == "repository_unavailable"
    assert not [call for call in integration.http.calls if call.method in {"POST", "PATCH"}]
    assert not repo.dispatches
    with pytest.raises(GitHubError):
        tenant.github.installation_token()


@pytest.mark.parametrize(("status", "rate_limited"), [(501, False), (429, False), (403, True)])
def test_api_outages_preserve_active_inventory_but_deny_access(
    integration: Integration, status: int, rate_limited: bool,
) -> None:
    integration.open_event(101)
    tenant = integration.manager.resolve(101)
    repo = integration.http.repositories[101]
    repo.outage, repo.rate_limited = status, rate_limited
    integration.advance(integration.settings.positive_ttl_seconds + 1)
    assert integration.client().get("/repos/101/dashboard").status_code == 503
    row = registry_row(integration, 101)
    assert row["state"] == "active" and row["generation"] == tenant.context.generation
    with pytest.raises(GitHubError):
        tenant.github.set_status(integration.corpus.head_sha, "success", "Must not publish",
                                 tenant.settings.public_base_url + "/prs/1")
    assert not any(item["state"] == "success" for item in repo.statuses)
    repo.outage, repo.rate_limited = None, False
    assert integration.client().get("/repos/101/dashboard").status_code == 302
    assert integration.manager.resolve(101).context == tenant.context


def test_reinstall_requires_negative_cache_expiry(integration: Integration) -> None:
    integration.open_event(101)
    old = integration.manager.resolve(101)
    repo = integration.http.repositories[101]
    repo.removed = True
    integration.advance(61)
    assert integration.client().get("/repos/101/dashboard").status_code == 403
    retired = registry_row(integration, 101)["generation"]
    repo.removed, repo.installation_id = False, 301
    body = integration.body(101)
    integration.http.calls.clear()
    assert integration.client().post("/api/actions/events", json=body,
                                     headers=integration.headers(101)).status_code == 403
    assert not integration.http.calls
    integration.advance(integration.settings.negative_ttl_seconds + 1)
    integration.open_event(101)
    current = integration.manager.resolve(101)
    assert current.context.installation_id == 301
    assert current.context.generation > retired > old.context.generation
    assert current.settings.database == old.settings.database


@pytest.mark.parametrize("change", ["reinstall", "rename", "transfer"])
def test_identity_refresh_retires_old_runtime_and_queue(integration: Integration, change: str) -> None:
    integration.open_event(101)
    browser, csrf = integration.browser(101)
    old = integration.manager.resolve(101)
    repo = integration.http.repositories[101]
    repo.fail_writes = True
    _, result = integration.submit(browser, csrf, 101, PASS, "old-receipt")
    receipt_id = str(result["receipt_id"])
    queued = old.service.store.load_verifier_dispatch(receipt_id)
    assert queued is not None and queued.status == "pending"
    if change == "rename":
        repo.name = "acme/renamed"
    elif change == "transfer":
        repo.name, repo.owner_id, repo.installation_id = "new-owner/one", 88, 301
    else:
        repo.installation_id = 301
    integration.advance(361)
    repo.fail_writes = False
    integration.open_event(101)
    current = integration.manager.resolve(101)
    assert current is not old and current.github is not old.github
    assert current.context.generation == old.context.generation + 1
    assert current.settings.database == old.settings.database
    assert not any(obj(item["inputs"])["receipt_id"] == receipt_id for item in repo.dispatches)
    retired = current.service.store.load_verifier_dispatch(receipt_id)
    assert retired is not None and retired.status == "sent"
    assert retired.remote == {"skipped": True, "reason": "stale_generation"}
    with pytest.raises(GitHubError):
        old.github.installation_token()
    publication = integration.client().get(f"/api/actions/receipts/{receipt_id}/publication",
                                            headers=integration.headers(101, event="workflow_dispatch"))
    assert publication.status_code == 409


def test_same_name_new_id_uses_distinct_store(integration: Integration) -> None:
    integration.open_event(101)
    old = integration.manager.resolve(101)
    integration.http.repositories[101].name = "acme/renamed-old"
    integration.http.repositories[103] = Repository(103, "acme/one", 77, 203)
    integration.open_event(103)
    current = integration.manager.resolve(103)
    assert current.settings.database != old.settings.database
    assert current.context.generation == 1
    first, second = (tenant.service.store.load_current_snapshot(1) for tenant in (old, current))
    assert first is not None and second is not None
    assert first.snapshot.snapshot_id != second.snapshot.snapshot_id


def test_new_pass_after_reinstall_cannot_reuse_old_receipt(integration: Integration) -> None:
    integration.open_event(101)
    browser, csrf = integration.browser(101)
    _, old = integration.submit(browser, csrf, 101, PASS, "before-reinstall")
    integration.http.repositories[101].installation_id = 301
    integration.advance(61)
    integration.open_event(101)
    browser, csrf = integration.browser(101)
    _, fresh = integration.submit(browser, csrf, 101, PASS, "after-reinstall")
    assert fresh["receipt_id"] != old["receipt_id"]
    saved = integration.manager.resolve(101).service.store.load_receipt(str(fresh["receipt_id"]))
    assert saved is not None and saved.installation_id == 301
    integration.five_steps(101, str(fresh["receipt_id"]))


def test_old_verified_success_cannot_be_retagged_after_reinstall(integration: Integration) -> None:
    integration.open_event(101)
    browser, csrf = integration.browser(101)
    _, passed = integration.submit(browser, csrf, 101, PASS, "verified-before-reinstall")
    receipt_id = str(passed["receipt_id"])
    old, repo = integration.manager.resolve(101), integration.http.repositories[101]
    repo.fail_writes = True
    result = integration.client().post(
        f"/api/actions/receipts/{receipt_id}/verify", json={"binding": integration.snapshot(101).binding()},
        headers=integration.headers(101, event="workflow_dispatch"),
    )
    assert result.status_code == 202
    integration.drain()
    receipt = old.service.store.load_receipt(receipt_id)
    assert receipt is not None and receipt.verified
    assert any(item.kind == "success_status" and item.status == "pending"
               for item in old.service.store.load_receipt_publications(receipt_id))
    repo.installation_id, repo.fail_writes = 301, False
    integration.advance(361)
    integration.open_event(101)
    assert integration.manager.resolve(101).context.generation == old.context.generation + 1
    assert not any(item["state"] == "success" for item in repo.statuses)


@pytest.mark.parametrize("prior_state", ["unverified", "queued-success", "published"])
def test_same_installation_readmission_requires_fresh_pass_and_verification(
    integration: Integration, prior_state: str,
) -> None:
    integration.open_event(101)
    browser, csrf = integration.browser(101)
    _, passed = integration.submit(browser, csrf, 101, PASS, "before-suspension")
    receipt_id = str(passed["receipt_id"])
    old, repo = integration.manager.resolve(101), integration.http.repositories[101]
    if prior_state == "published":
        integration.five_steps(101, receipt_id)
    elif prior_state == "queued-success":
        repo.fail_writes = True
        verification = integration.client().post(
            f"/api/actions/receipts/{receipt_id}/verify",
            json={"binding": integration.snapshot(101).binding()},
            headers=integration.headers(101, event="workflow_dispatch"),
        )
        assert verification.status_code == 202
        integration.drain()
    retained = old.service.store.load_receipt(receipt_id)
    assert retained is not None and retained.verified == (prior_state != "unverified")
    with sqlite3.connect(old.settings.database) as connection:
        before = connection.execute("SELECT * FROM receipts WHERE receipt_id = ?", (receipt_id,)).fetchone()
    statuses, dispatches = len(repo.statuses), len(repo.dispatches)
    repo.suspended = True
    integration.advance(integration.settings.positive_ttl_seconds + 301)
    assert browser.get("/repos/101/dashboard").status_code == 403
    repo.suspended, repo.fail_writes = False, False
    integration.advance(integration.settings.negative_ttl_seconds + 1)
    integration.open_event(101)
    current = integration.manager.resolve(101)
    assert current.context.generation > old.context.generation
    assert current.settings.installation_id == old.settings.installation_id
    assert current.settings.app_id == old.settings.app_id
    record = current.service.store.load_current_snapshot(1)
    assert record is not None
    assert current.service._presentation_projection(record) == ("awaiting_author", None)
    assert not any(item["state"] == "success" for item in repo.statuses[statuses:])
    assert len(repo.dispatches) == dispatches
    for suffix in ("", "/publication"):
        result = integration.client().get(
            f"/api/actions/receipts/{receipt_id}{suffix}",
            headers=integration.headers(101, event="workflow_dispatch"),
        )
        assert result.status_code == 409
    verification = integration.client().post(
        f"/api/actions/receipts/{receipt_id}/verify", json={"binding": record.snapshot.binding()},
        headers=integration.headers(101, event="workflow_dispatch"),
    )
    assert verification.status_code == 202
    integration.drain()
    job = integration.client().get(
        str(obj(verification.get_json())["url"]), headers=integration.headers(101, event="workflow_dispatch"),
    )
    assert obj(job.get_json())["state"] == "stale"
    dispatch = current.service.store.load_verifier_dispatch(receipt_id)
    assert dispatch is not None and dispatch.tenant_generation == old.context.generation
    kinds = ("verifier_dispatch", "success_status") if retained.verified else ("verifier_dispatch",)
    for kind in kinds:
        forged = replace(
            dispatch, event_id=f"forged-{kind}", kind=kind, tenant_generation=current.context.generation,
        )
        with pytest.raises(BotError) as stale:
            current.service._deliver_event(forged)
        assert stale.value.code == "stale"
    with pytest.raises(ValueError, match="current tenant generation"):
        current.service.store.mark_receipt_verified(
            receipt_id, publications=(), now="2026-09-17T00:00:00Z",
        )
    browser, csrf = integration.browser(101)
    detail = current.service.receipt_detail(receipt_id, retained.actor_id)
    assert set(asdict(retained)) == set(RECEIPT_COLUMNS) - {"successful_answers_json"} | {"successful_answers"}
    assert set(detail) == set(asdict(retained)) | {"publication"}
    assert detail["successful_answers"] == [asdict(answer) for answer in retained.successful_answers]
    _, held = integration.submit(browser, csrf, 101, HOLD, "hold-after-suspension")
    assert held["state"] == "needs_followup"
    assert current.service.store.load_receipts_for_snapshot(record.snapshot.snapshot_id) == (retained,)
    assert not any(item["state"] == "success" for item in repo.statuses[statuses:])
    _, fresh = integration.submit(browser, csrf, 101, PASS, "pass-after-suspension")
    fresh_id = str(fresh["receipt_id"])
    assert fresh_id != receipt_id and fresh["state"] == "awaiting_verification"
    assert not any(item["state"] == "success" for item in repo.statuses[statuses:])
    _, duplicate = integration.submit(browser, csrf, 101, PASS, "duplicate-after-suspension")
    assert duplicate["receipt_id"] == fresh_id
    integration.five_steps(101, fresh_id)
    wire = integration.client().get(
        f"/api/actions/receipts/{fresh_id}", headers=integration.headers(101, event="workflow_dispatch"),
    )
    assert set(obj(wire.get_json())) == {
        "receipt_id", "binding", "actor_id", "question_version", "issued_at",
        "app_id", "installation_id", "repository_path",
    }
    with sqlite3.connect(current.settings.database) as connection:
        assert connection.execute("SELECT * FROM receipts WHERE receipt_id = ?", (receipt_id,)).fetchone() == before
        assert connection.execute("SELECT COUNT(*) FROM receipts").fetchone() == (2,)
        assert connection.execute(
            "SELECT tenant_generation FROM receipts WHERE receipt_id = ?", (fresh_id,),
        ).fetchone() == (current.context.generation,)
        assert not connection.execute(
            "SELECT 1 FROM outbox WHERE receipt_id = ? AND tenant_generation = ?",
            (receipt_id, current.context.generation),
        ).fetchall()


def test_historical_terminal_presentation_failure_does_not_degrade_new_generation(integration: Integration) -> None:
    integration.open_event(101)
    old = integration.manager.resolve(101)
    record = old.service.store.load_current_snapshot(1)
    assert record is not None
    old.service.store.queue_publication(PublicationRequest(
        event_id="historical-terminal-presentation", kind="presentation_card", pr=1,
        snapshot_id=record.snapshot.snapshot_id, payload={"phase": "awaiting_author"},
    ), now="2026-09-16T07:00:00Z")
    old.service.store.mark_publication_terminal(
        "historical-terminal-presentation", now="2026-09-16T07:01:00Z",
        error_code="presentation_retry_exhausted", error_message="Retained delivery failure",
        remote={"skipped": True, "reason": "retry_exhausted"},
    )
    integration.drain()
    assert old.runtime.background_health() == "publisher_degraded"
    assert integration.client().get("/healthz").status_code == 503
    with sqlite3.connect(old.settings.database) as connection:
        before = connection.execute(
            "SELECT * FROM outbox WHERE event_id = 'historical-terminal-presentation'",
        ).fetchone()
    integration.http.repositories[101].installation_id = 301
    integration.advance(integration.settings.positive_ttl_seconds + 1)
    integration.open_event(101)
    current = integration.manager.resolve(101)
    assert current.context.generation == 2 and old.context.generation == 1
    assert current.runtime.background_health() == "ok"
    assert integration.client().get("/healthz").status_code == 200
    with sqlite3.connect(current.settings.database) as connection:
        assert connection.execute(
            "SELECT * FROM outbox WHERE event_id = 'historical-terminal-presentation'",
        ).fetchone() == before


def save_receipt(store: Store, record: StoredSnapshot, *, installation_id: int = 202) -> StoredReceipt:
    return store.save_receipt(
        record, actor_id=7, actor_login="octocat",
        answers=tuple(
            ReceiptAnswer(item.id, item.question.anchor, "Retained successful answer") for item in record.questions
        ),
        app_id=101, installation_id=installation_id, now="2026-09-16T07:00:00Z",
    )


def legacy_receipt_schema(path: Path, *, dimensions: int = 3) -> StoredSnapshot:
    store = Store(path)
    record = store.save_snapshot(make_snapshot(), make_questions(), "pending", now="2026-09-16T07:00:00Z")
    saved = save_receipt(store, record)
    store.mark_receipt_verified(saved.receipt_id, publications=(), now="2026-09-16T07:10:00Z")
    with sqlite3.connect(path) as connection:
        columns = """receipt_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, pr INTEGER NOT NULL,
            repo TEXT NOT NULL, repo_id INTEGER NOT NULL, head_sha TEXT NOT NULL, base_sha TEXT NOT NULL,
            policy_version TEXT NOT NULL, question_version TEXT NOT NULL, actor_id INTEGER NOT NULL,
            actor_login TEXT NOT NULL, app_id INTEGER NOT NULL, installation_id INTEGER NOT NULL,
            created_at TEXT NOT NULL, verified_at TEXT, successful_answers_json TEXT NOT NULL"""
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        key = ", ".join(RECEIPT_KEY[:dimensions])
        connection.execute(f"CREATE TABLE receipts_legacy ({columns}, "
                           f"UNIQUE({key}), "
                           "FOREIGN KEY(snapshot_id) REFERENCES snapshots(snapshot_id))")
        connection.execute(f"INSERT INTO receipts_legacy SELECT {', '.join(RECEIPT_COLUMNS)} FROM receipts")
        connection.execute("DROP TABLE receipts")
        connection.execute("ALTER TABLE receipts_legacy RENAME TO receipts")
        connection.execute("CREATE INDEX idx_receipts_snapshot ON receipts(snapshot_id, created_at ASC)")
        connection.execute("""CREATE TRIGGER immutable_receipt_installation BEFORE UPDATE OF installation_id ON receipts
                           BEGIN SELECT RAISE(ABORT, 'receipt installation is immutable'); END""")
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    return record


def receipt_keys(path: Path) -> set[tuple[str, ...]]:
    with sqlite3.connect(path) as connection:
        return {
            tuple(row[0] for row in connection.execute(
                "SELECT name FROM pragma_index_info(?) ORDER BY seqno", (index[1],)
            ))
            for index in connection.execute("PRAGMA index_list(receipts)").fetchall() if index[3] == "u"
        }


@pytest.mark.parametrize("dimensions", [3, 5])
def test_actual_old_constraint_upgrades_without_changing_history_and_new_pass_is_idempotent(
    tmp_path: Path, dimensions: int,
) -> None:
    path = tmp_path / "legacy.sqlite"
    record = legacy_receipt_schema(path, dimensions=dimensions)
    with sqlite3.connect(path) as connection:
        before = connection.execute("SELECT * FROM receipts").fetchall()
        trigger = connection.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger'").fetchall()
    assert receipt_keys(path) == {RECEIPT_KEY[:dimensions]}
    current = Store(path, tenant_generation=2)
    assert receipt_keys(path) == {RECEIPT_KEY}
    with sqlite3.connect(path) as connection:
        assert connection.execute(f"SELECT {', '.join(RECEIPT_COLUMNS)} FROM receipts").fetchall() == before
        assert connection.execute("SELECT tenant_generation FROM receipts").fetchall() == [(0,)]
        assert connection.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger'").fetchall() == trigger
    assert current.load_receipt_by_key(record.snapshot.snapshot_id, record.question_version, 7) is None
    fixed = Store(path)
    legacy = fixed.load_receipt_by_key(record.snapshot.snapshot_id, record.question_version, 7)
    assert legacy is not None and legacy.verified
    assert save_receipt(fixed, record) == legacy
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(save_receipt, Store(path, tenant_generation=2), record)
                   for _ in range(2)]
        receipts = [future.result(timeout=10) for future in futures]
    assert receipts[0] == receipts[1] and receipts[0].receipt_id != before[0][0]
    assert not receipts[0].verified
    assert len(current.load_receipts_for_snapshot(record.snapshot.snapshot_id)) == 2
    with sqlite3.connect(path) as connection:
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        assert connection.execute(
            f"SELECT {', '.join(RECEIPT_COLUMNS)} FROM receipts WHERE receipt_id = ?", (before[0][0],),
        ).fetchall() == before
    assert fixed.load_receipt_by_key(record.snapshot.snapshot_id, record.question_version, 7) == legacy
    with pytest.raises(ValueError, match="current tenant generation"):
        current.mark_receipt_verified(legacy.receipt_id, publications=(), now="2026-09-17T00:00:00Z")
    image = sqlite_image(path)
    assert Store(path, tenant_generation=2).load_receipt(receipts[0].receipt_id) == receipts[0]
    assert sqlite_image(path) == image


def sqlite_image(path: Path) -> tuple[tuple[tuple[object, ...], ...], str]:
    with sqlite3.connect(path) as connection:
        schema = tuple(connection.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name"))
        return schema, "\n".join(connection.iterdump())


def test_dispatch_lookup_and_requeue_use_receipt_origin_not_new_queue_generation(tmp_path: Path) -> None:
    path = tmp_path / "dispatch-provenance.sqlite"
    original = Store(path, tenant_generation=1)
    record = original.save_snapshot(make_snapshot(), make_questions(), "pending", now="2026-09-16T07:00:00Z")
    receipt = save_receipt(original, record)
    dispatch = original.load_verifier_dispatch(receipt.receipt_id)
    assert dispatch is not None and dispatch.tenant_generation == 1
    current = Store(path, tenant_generation=2)
    current.queue_publication(PublicationRequest(
        event_id="forged-current-generation", kind="verifier_dispatch", pr=receipt.pr,
        snapshot_id=receipt.snapshot_id, receipt_id=receipt.receipt_id,
        payload={"receipt_id": receipt.receipt_id},
    ), now="2026-09-16T08:00:00Z")
    current.mark_publication_sent("forged-current-generation", now="2026-09-16T08:00:00Z", remote={})
    assert current.load_verifier_dispatch(receipt.receipt_id) == dispatch
    assert current.requeue_unverified_dispatches(
        now="2026-09-17T00:00:00Z", sent_before="2026-09-17T00:00:00Z", max_attempts=3,
        tenant_generation=2,
    ) == 0
    before = sqlite_image(path)
    with pytest.raises(ValueError, match="current tenant generation"):
        current.mark_receipt_verified(receipt.receipt_id, publications=(PublicationRequest(
            event_id="forged-success", kind="success_status", pr=receipt.pr,
            snapshot_id=receipt.snapshot_id, receipt_id=receipt.receipt_id, payload={},
        ),), now="2026-09-17T00:00:00Z")
    assert sqlite_image(path) == before


@pytest.mark.parametrize("failure", ["rename", "foreign-key", "unknown-column"])
@pytest.mark.parametrize("dimensions", [3, 5])
def test_receipt_upgrade_rolls_back_without_discarding_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, dimensions: int,
) -> None:
    path = tmp_path / "legacy.sqlite"
    legacy_receipt_schema(path, dimensions=dimensions)
    with sqlite3.connect(path) as connection:
        if failure == "foreign-key":
            connection.execute("UPDATE outbox SET receipt_id = 'missing-receipt'")
        elif failure == "unknown-column":
            connection.execute("ALTER TABLE receipts ADD COLUMN retained_note TEXT DEFAULT 'keep this'")
    before = sqlite_image(path)
    original = Store._connect

    def connect(store: Store) -> sqlite3.Connection:
        connection = original(store)

        def authorize(action: int, _first: str | None, second: str | None,
                      _database: str | None, _trigger: str | None) -> int:
            if failure == "rename" and action == sqlite3.SQLITE_ALTER_TABLE and second == "receipts_upgrade":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)
        return connection

    monkeypatch.setattr(Store, "_connect", connect)
    with pytest.raises(sqlite3.DatabaseError):
        Store(path, tenant_generation=2)
    assert sqlite_image(path) == before
    assert receipt_keys(path) == {RECEIPT_KEY[:dimensions]}


def test_process_death_during_receipt_upgrade_preserves_rows_and_recovers_new_constraint(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    record = legacy_receipt_schema(path)
    before = sqlite_image(path)
    script = """
import os
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "src"))
from lasthuman.server.store import Store
original = sqlite3.connect
class CrashConnection(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        result = super().execute(sql, parameters)
        if sql == "ALTER TABLE receipts_upgrade RENAME TO receipts":
            os._exit(78)
        return result
def crash_connect(database, *args, **kwargs):
    return original(database, *args, factory=CrashConnection, **kwargs)
sqlite3.connect = crash_connect
Store(Path(sys.argv[1]), tenant_generation=2)
"""
    result = subprocess.run([sys.executable, "-c", script, str(path)], cwd=Path(__file__).resolve().parent.parent,
                            check=False, capture_output=True, text=True, timeout=30)
    assert result.returncode == 78, result.stderr
    assert sqlite_image(path) == before
    current = Store(path, tenant_generation=2)
    assert receipt_keys(path) == {RECEIPT_KEY}
    fresh = save_receipt(current, record, installation_id=303)
    assert fresh.installation_id == 303 and not fresh.verified
    assert len(current.load_receipts_for_snapshot(record.snapshot.snapshot_id)) == 2
    with sqlite3.connect(path) as connection:
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
