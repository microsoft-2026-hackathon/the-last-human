from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from threading import Event, Thread

import pytest
from test_app_http import NoopAuth, make_app
from test_app_service import FakeClock, make_pull, make_service, make_snapshot

from lasthuman.diff import parse_hunks
from lasthuman.models import Question
from lasthuman.server.app import AppRuntime
from lasthuman.server.snapshot import Snapshot, SnapshotError
from lasthuman.server.store import PublicationRequest, Store

DELETED = """diff --git a/app/auth/file.py b/app/auth/file.py
deleted file mode 100644
index 1111111..0000000
--- a/app/auth/file.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def token():
-    return True
"""

ADDED = """diff --git a/app/auth/file.py b/app/auth/file.py
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/app/auth/file.py
@@ -0,0 +1,2 @@
+def token():
+    return True
"""

EMPTIED = """diff --git a/app/auth/file.py b/app/auth/file.py
index 1111111..2222222 100644
--- a/app/auth/file.py
+++ b/app/auth/file.py
@@ -1,2 +0,0 @@
-def token():
-    return True
"""


def snapshot_from_diff(raw: str, *, pr: int = 7) -> Snapshot:
    base = make_snapshot(pr=pr)
    diff = parse_hunks(raw)
    return Snapshot.create(
        repo=base.repo,
        repo_id=base.repo_id,
        pr=base.pr,
        head_sha=base.head_sha,
        base_sha=base.base_sha,
        author_id=base.author_id,
        author_login=base.author_login,
        title=base.title,
        body=base.body,
        risk=replace(base.risk, top_hunks=diff.hunks),
        config=base.config,
        diff=diff,
        structure=base.structure,
        zones=base.zones,
        policy_version=base.policy_version,
    )


@pytest.mark.parametrize(("raw", "old_start", "new_start"), [
    (DELETED, 1, 0),
    (ADDED, 0, 1),
    (EMPTIED, 1, 0),
])
def test_real_diff_snapshot_roundtrips_through_json_and_store(
    tmp_path: Path, raw: str, old_start: int, new_start: int,
) -> None:
    snapshot = snapshot_from_diff(raw)
    hunk = snapshot.diff.hunks[0]
    assert (hunk.old_start, hunk.new_start) == (old_start, new_start)
    restored = Snapshot.from_dict(json.loads(json.dumps(snapshot.to_dict())))
    assert restored == snapshot
    assert restored.binding() == snapshot.binding()
    assert restored.diff.hunks[0].anchor == hunk.anchor

    path = tmp_path / "snapshot.sqlite3"
    Store(path).save_snapshot(snapshot, (), "pending", now="2026-09-11T00:00:00Z")
    record = Store(path).load_snapshot(snapshot.snapshot_id)
    assert record is not None
    assert record.snapshot == snapshot


@pytest.mark.parametrize("field", ["new_start", "old_start"])
@pytest.mark.parametrize("value", [-1, False, True, "0", None])
def test_invalid_hunk_coordinates_still_fail(field: str, value: object) -> None:
    snapshot = snapshot_from_diff(DELETED)
    payload = json.loads(json.dumps(snapshot.to_dict()))
    payload["diff"]["hunks"][0][field] = value
    with pytest.raises(SnapshotError, match=field):
        Snapshot.from_dict(payload)


@pytest.mark.parametrize("raw", [DELETED, ADDED, EMPTIED])
def test_actual_scheduler_publishes_restored_zero_start_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str,
) -> None:
    snapshot = snapshot_from_diff(raw)
    questions = [
        Question(
            type="claim",
            anchor=snapshot.diff.hunks[0].anchor,
            text=f"Explain change {index}",
            expected_evidence="private evidence",
        )
        for index in range(3)
    ]
    service, github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot)] * 20,
        clock=FakeClock(),
        live=True,
        checks_enabled=True,
        questions=questions,
    )
    service.sync(snapshot.pr)
    service.store = Store(service.settings.database)
    published = Event()
    original = github.ensure_check_run

    def capture_check(*args, **kwargs):
        result = original(*args, **kwargs)
        published.set()
        return result

    monkeypatch.setattr(github, "ensure_check_run", capture_check)
    monkeypatch.setattr("lasthuman.server.app._SCHEDULER_INTERVAL_SECONDS", 0.01)
    runtime = AppRuntime(service.settings, service, github, NoopAuth(), start_scheduler=True)
    try:
        assert published.wait(timeout=3.0)
        assert runtime.scheduler_running is True
        assert github.pr_card is not None
        assert github.status_calls[-1]["state"] == "pending"
        assert github.check_calls[-1]["status"] == "in_progress"
        assert "Human&#45;verified" not in str(github.pr_card["body"])
        assert "private evidence" not in str(github.pr_card["body"])
        assert runtime.background_health() == "ok"
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("invalid_json", [False, True])
def test_bad_stored_snapshot_does_not_block_valid_publications(
    tmp_path: Path, invalid_json: bool,
) -> None:
    clock = FakeClock()
    good = make_snapshot(pr=8)
    service, github = make_service(
        tmp_path,
        snapshot=good,
        pulls=[make_pull(good)] * 20,
        clock=clock,
        live=True,
    )
    bad = snapshot_from_diff(DELETED)
    service.store.save_snapshot(bad, (), "pending", now=service._now_iso())
    payload = json.loads(json.dumps(bad.to_dict()))
    payload["diff"]["hunks"][0]["new_start"] = -1
    encoded = "RAW_SECRET invalid JSON" if invalid_json else json.dumps(payload)
    with sqlite3.connect(service.settings.database) as connection:
        connection.execute(
            "UPDATE snapshots SET snapshot_json=? WHERE snapshot_id=?",
            (encoded, bad.snapshot_id),
        )
    service.store.queue_publication(
        PublicationRequest(
            event_id="invalid-snapshot",
            kind="pending_status",
            pr=bad.pr,
            snapshot_id=bad.snapshot_id,
            payload={"description": "Pending", "target_url": "https://example.com/prs/7"},
        ),
        now=service._now_iso(),
    )
    clock.value += 1
    service.sync(good.pr)
    runtime = AppRuntime(service.settings, service, github, NoopAuth(), start_scheduler=False)
    try:
        assert runtime.tick_once() > 0
        assert github.pr_card is not None and github.pr_card["pr"] == good.pr
        assert github.status_calls[-1]["state"] == "pending"
        remaining = service.store.load_due_publications(now="9999-12-31T23:59:59Z")
        assert len(remaining) == 1
        assert remaining[0].event_id == "invalid-snapshot"
        assert remaining[0].attempts == 1
        assert remaining[0].last_error_code == "stored_snapshot_invalid"
        assert "RAW_SECRET" not in str(remaining[0].last_error)
        assert runtime.background_health() == "publisher_degraded"
    finally:
        runtime.shutdown()


def test_dead_scheduler_is_not_reported_as_healthy(tmp_path: Path) -> None:
    client, app, _service, _github, _settings, _clock = make_app(tmp_path)
    runtime = app.extensions["runtime"]
    runtime._scheduler_started = True
    runtime._scheduler_thread = Thread(target=lambda: None)
    try:
        response = client.get("/healthz")
        assert response.status_code == 503
        assert response.get_json()["background"] == "scheduler_stopped"
        assert response.get_json()["scheduler"] == "stopped"
    finally:
        runtime.shutdown()


def test_snapshot_error_at_tick_boundary_is_visible_and_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    client, app, service, _github, _settings, _clock = make_app(tmp_path)
    runtime = app.extensions["runtime"]

    def fail_flush():
        raise SnapshotError("RAW_SECRET malformed stored snapshot")

    monkeypatch.setattr(service, "flush_publications", fail_flush)
    try:
        assert runtime.tick_once() == 0
        assert client.get("/healthz").status_code == 503
        assert runtime.background_health() == "publisher_failed"
        assert "RAW_SECRET" not in caplog.text
    finally:
        runtime.shutdown()
