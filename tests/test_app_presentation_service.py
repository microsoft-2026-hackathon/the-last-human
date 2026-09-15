from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from test_app_service import (
    OTHER_HEAD_SHA,
    FakeClock,
    FakeGitHub,
    FakeReader,
    make_pull,
    make_questions,
    make_service,
    make_settings,
    make_snapshot,
)

from lasthuman.interview import ModelError
from lasthuman.models import Answer
from lasthuman.server.presentation_copy import catalog_for_locale
from lasthuman.server.service import BotError, BotService
from lasthuman.server.snapshot import Snapshot
from lasthuman.server.store import PublicationRequest, ReceiptAnswer, Store


def _heading(phase: str) -> str:
    return catalog_for_locale("ko").phase[phase].heading


@pytest.mark.parametrize("verified", [False, True])
def test_reconfiguration_refreshes_existing_card_check_and_status_links(tmp_path: Path, verified: bool) -> None:
    snapshot = make_snapshot()
    service, github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot)] * 50,
        clock=FakeClock(),
        live=True,
        checks_enabled=True,
    )
    service.sync(snapshot.pr)
    service.flush_publications()
    receipt_id = None
    if verified:
        job = service.submit(snapshot.pr, snapshot.author_id, snapshot.snapshot_id, "pass", _answers())
        service.drain()
        receipt_id = str(service.result(job, snapshot.author_id)["receipt_id"])
        service.verify(receipt_id, snapshot.binding())
        service.flush_publications()
    card_id = github.pr_card["id"]
    settings = replace(
        service.settings,
        base_url="https://moved.example.org",
        presentation_name="Human Check",
        presentation_locale="en",
        presentation_max_chars=4000,
    )
    clock = service.clock
    service.shutdown()

    def unexpected_generation(*args, **kwargs):
        raise AssertionError("Presentation reconfiguration must not regenerate questions")

    restarted = BotService(
        settings,
        github,
        FakeReader(snapshot),
        Store(settings.database),
        generate=unexpected_generation,
        clock=clock,
    )
    restarted.sync(snapshot.pr)
    restarted.flush_publications()
    assert github.pr_card["id"] == card_id
    assert "## Human Check" in str(github.pr_card["body"])
    assert "https://example.com/" not in str(github.pr_card["body"])
    target = f"{settings.base_url}/receipts/{receipt_id}" if verified else f"{settings.base_url}/prs/{snapshot.pr}"
    assert target in str(github.pr_card["body"])
    assert github.status_calls[-1]["target_url"] == target
    assert github.check_calls[-1]["details_url"] == target
    assert github.check_calls[-1]["conclusion"] == ("success" if verified else None)
    assert len(github.check_runs) == 1
    assert restarted.store.load_current_snapshot(snapshot.pr).snapshot.binding() == snapshot.binding()
    if receipt_id is not None:
        assert restarted.store.load_receipt(receipt_id).verified is True

    counts = (len(github.comment_calls), len(github.check_calls), len(github.status_calls))
    restarted.sync(snapshot.pr)
    restarted.flush_publications()
    assert counts == (len(github.comment_calls), len(github.check_calls), len(github.status_calls))
    restarted.shutdown()


def _closed_pull(
    snapshot: Snapshot,
    *,
    closed_at: str,
    merged: bool = False,
    base_sha: str | None = None,
) -> dict[str, object]:
    payload = make_pull(
        snapshot,
        state="closed",
        merged=merged,
        merged_at=closed_at if merged else None,
    )
    payload["closed_at"] = closed_at
    if base_sha is not None:
        payload["base"] = {**dict(payload["base"]), "sha": base_sha}
    return payload


def _answers() -> list[dict[str, object]]:
    return [
        {"id": "0", "text": "returns refresh(token)", "choice": 1},
        {"id": "1", "text": "called from app/main.py", "choice": 0},
        {"id": "2", "text": "Updated guide"},
    ]


def _restart_service(
    service: BotService,
    github: FakeGitHub,
    snapshot: Snapshot,
    clock: FakeClock,
) -> BotService:
    return BotService(
        service.settings,
        github,
        FakeReader(snapshot),
        Store(service.settings.database),
        generate=lambda *args, **kwargs: make_questions(),
        grade=lambda *args, **kwargs: Answer(anchor="x", text="", verdict="pass"),
        clock=clock,
    )


def _interrupt_next_check_identity_save(monkeypatch: pytest.MonkeyPatch) -> None:
    original = Store.save_presentation_check_run
    state = {"armed": True}

    def save_once(self: Store, **kwargs: object) -> None:
        if state["armed"]:
            state["armed"] = False
            raise RuntimeError("simulated process interruption")
        original(self, **kwargs)

    monkeypatch.setattr(Store, "save_presentation_check_run", save_once)


def test_presentation_lifecycle_uses_card_check_and_keeps_hold_private(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    verdicts = {f"{make_questions()[0].anchor}|{make_questions()[0].text}": "hold"}
    service, github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot)] * 20,
        clock=clock,
        live=True,
        checks_enabled=True,
        verdicts=verdicts,
    )

    service.sync(snapshot.pr)
    service.flush_publications()
    assert github.pr_card is not None
    assert _heading("awaiting_author") in str(github.pr_card["body"])
    assert github.check_calls[-1]["status"] == "in_progress"
    assert github.check_calls[-1]["conclusion"] is None
    assert github.check_calls[-1]["external_id"] == f"pr-{snapshot.pr}-snapshot-{snapshot.snapshot_id}"

    hold_job = service.submit(
        snapshot.pr,
        snapshot.author_id,
        snapshot.snapshot_id,
        "hold-once",
        _answers(),
    )
    service.drain()
    assert service.result(hold_job, snapshot.author_id)["state"] == "needs_followup"
    assert b"hold-once" not in service.settings.database.read_bytes()

    verdicts.clear()
    pass_job = service.submit(
        snapshot.pr,
        snapshot.author_id,
        snapshot.snapshot_id,
        "pass",
        _answers(),
    )
    service.drain()
    awaiting = service.result(pass_job, snapshot.author_id)
    receipt_id = str(awaiting["receipt_id"])
    assert awaiting["state"] == "awaiting_verification"
    service.flush_publications()
    assert github.dispatch_calls == [receipt_id]
    assert _heading("verifying") in str(github.pr_card["body"])
    assert github.check_calls[-1]["status"] == "in_progress"

    service.verify(receipt_id, snapshot.binding())
    service.flush_publications()
    verified = service.publication_status(receipt_id, snapshot.author_id)
    assert verified["published"] is True
    assert github.status_calls[-1]["state"] == "success"
    assert "이 변경에 대한 이해 확인을 완료했습니다" in str(github.pr_card["body"])
    assert github.check_calls[-1]["status"] == "completed"
    assert github.check_calls[-1]["conclusion"] == "success"

    card_updates = len(github.comment_calls)
    check_updates = len(github.check_calls)
    status_updates = len(github.status_calls)
    service.sync(snapshot.pr)
    service.flush_publications()
    assert len(github.comment_calls) == card_updates
    assert len(github.check_calls) == check_updates
    assert len(github.status_calls) == status_updates
    assert service.publication_status(receipt_id, snapshot.author_id)["published"] is True


@pytest.mark.parametrize("same_head_new_base", [False, True])
def test_reconciles_interrupted_initial_check_before_new_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    same_head_new_base: bool,
) -> None:
    clock = FakeClock()
    old = make_snapshot()
    new = (
        make_snapshot(base_sha="e" * 40)
        if same_head_new_base
        else make_snapshot(head_sha=OTHER_HEAD_SHA)
    )
    service, github = make_service(
        tmp_path,
        snapshot=old,
        pulls=[make_pull(old)] * 50,
        clock=clock,
        live=True,
        checks_enabled=True,
    )

    service.sync(old.pr)
    _interrupt_next_check_identity_save(monkeypatch)
    with pytest.raises(RuntimeError, match="simulated process interruption"):
        service.flush_publications()

    old_external_id = f"pr-{old.pr}-snapshot-{old.snapshot_id}"
    old_remote = github.find_check_run(old.head_sha, old_external_id)
    assert old_remote is not None
    assert old_remote["status"] == "in_progress"

    restarted = _restart_service(service, github, new, clock)
    github._pulls = [make_pull(new)] * 50
    restarted.sync(new.pr)
    restarted.flush_publications()

    old_remote = github.find_check_run(old.head_sha, old_external_id)
    assert old_remote is not None
    assert old_remote["status"] == "completed"
    assert old_remote["conclusion"] == "cancelled"
    assert github.cancel_check_calls[-1]["external_id"] == old_external_id

    new_external_id = f"pr-{new.pr}-snapshot-{new.snapshot_id}"
    new_remote = github.find_check_run(new.head_sha, new_external_id)
    assert new_remote is not None
    assert new_remote["status"] == "in_progress"
    assert new_remote["conclusion"] is None
    assert set(github.check_runs) == {old_external_id, new_external_id}
    assert [call["state"] for call in github.status_calls] == ["pending", "pending"]


def test_reconciles_interrupted_success_check_as_historical_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    old = make_snapshot()
    service, github = make_service(
        tmp_path,
        snapshot=old,
        pulls=[make_pull(old)] * 80,
        clock=clock,
        live=True,
        checks_enabled=True,
    )
    service.sync(old.pr)
    service.flush_publications()
    job_id = service.submit(old.pr, old.author_id, old.snapshot_id, "pass", _answers())
    service.drain()
    receipt_id = str(service.result(job_id, old.author_id)["receipt_id"])
    service.flush_publications()
    service.verify(receipt_id, old.binding())

    _interrupt_next_check_identity_save(monkeypatch)
    with pytest.raises(RuntimeError, match="simulated process interruption"):
        service.flush_publications()

    old_external_id = f"pr-{old.pr}-snapshot-{old.snapshot_id}"
    old_remote = github.find_check_run(old.head_sha, old_external_id)
    assert old_remote is not None
    assert old_remote["status"] == "completed"
    assert old_remote["conclusion"] == "success"

    new = make_snapshot(head_sha=OTHER_HEAD_SHA)
    restarted = _restart_service(service, github, new, clock)
    github._pulls = [make_pull(new)] * 80
    restarted.sync(new.pr)
    restarted.flush_publications()

    old_remote = github.find_check_run(old.head_sha, old_external_id)
    assert old_remote is not None
    assert old_remote["conclusion"] == "success"
    assert not [call for call in github.cancel_check_calls if call["external_id"] == old_external_id]
    receipt = restarted.store.load_receipt(receipt_id)
    assert receipt is not None
    assert receipt.verified

    new_external_id = f"pr-{new.pr}-snapshot-{new.snapshot_id}"
    new_remote = github.find_check_run(new.head_sha, new_external_id)
    assert new_remote is not None
    assert new_remote["status"] == "in_progress"
    assert set(github.check_runs) == {old_external_id, new_external_id}


def test_reconciles_missing_historical_check_without_creating_phantom_cancel(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    old = make_snapshot()
    service, github = make_service(
        tmp_path,
        snapshot=old,
        pulls=[make_pull(old)] * 50,
        clock=clock,
        live=True,
        checks_enabled=True,
    )
    service.sync(old.pr)
    assert github.check_runs == {}

    new = make_snapshot(head_sha=OTHER_HEAD_SHA)
    restarted = _restart_service(service, github, new, clock)
    github._pulls = [make_pull(new)] * 50
    restarted.sync(new.pr)
    restarted.flush_publications()

    old_external_id = f"pr-{old.pr}-snapshot-{old.snapshot_id}"
    new_external_id = f"pr-{new.pr}-snapshot-{new.snapshot_id}"
    assert github.find_check_run(old.head_sha, old_external_id) is None
    assert github.find_check_run(new.head_sha, new_external_id) is not None
    assert set(github.check_runs) == {new_external_id}
    assert github.cancel_check_calls == []
    cancel_events = [
        event
        for event in restarted.store.load_presentation_publications(pr=old.pr)
        if event.kind == "presentation_check_cancel"
    ]
    assert cancel_events
    assert cancel_events[-1].remote is not None
    assert cancel_events[-1].remote["reason"] == "not_published"


def test_new_snapshot_and_same_head_base_change_cancel_older_pending_check(tmp_path: Path):
    clock = FakeClock()
    first = make_snapshot()
    service, github = make_service(
        tmp_path,
        snapshot=first,
        pulls=[make_pull(first)] * 10,
        clock=clock,
        live=True,
        checks_enabled=True,
    )
    service.sync(first.pr)
    service.flush_publications()
    assert github.check_calls[-1]["status"] == "in_progress"

    changed_base = make_snapshot(base_sha="e" * 40)
    service.reader = FakeReader(changed_base)
    github._pulls = [make_pull(changed_base)] * 10
    service.sync(changed_base.pr)
    service.flush_publications()

    assert github.cancel_check_calls
    assert github.cancel_check_calls[-1]["external_id"] == f"pr-{first.pr}-snapshot-{first.snapshot_id}"
    assert github.check_calls[-1]["external_id"] == f"pr-{changed_base.pr}-snapshot-{changed_base.snapshot_id}"
    assert github.check_calls[-1]["status"] == "in_progress"


def test_low_risk_and_closed_update_existing_card_and_check_without_new_card(tmp_path: Path):
    clock = FakeClock()
    high = make_snapshot()
    service, github = make_service(
        tmp_path,
        snapshot=high,
        pulls=[make_pull(high)] * 10,
        clock=clock,
        live=True,
        checks_enabled=True,
    )
    service.sync(high.pr)
    service.flush_publications()
    assert github.pr_card is not None
    card_id = github.pr_card["id"]

    low = make_snapshot(head_sha=OTHER_HEAD_SHA, triggered=False)
    service.reader = FakeReader(low)
    github._pulls = [make_pull(low)] * 10
    service.sync(low.pr, expected_binding=low.binding())
    service.flush_publications()
    assert github.pr_card["id"] == card_id
    assert _heading("neutral") in str(github.pr_card["body"])
    assert github.check_calls[-1]["conclusion"] == "neutral"
    assert github.status_calls[-1]["state"] == "success"

    closed = make_pull(low, state="closed", merged=False)
    github._pulls = [closed] * 10
    assert service.sync(low.pr)["state"] == "closed"
    service.flush_publications()
    assert github.pr_card["id"] == card_id
    assert _heading("closed") in str(github.pr_card["body"])
    assert github.check_calls[-1]["conclusion"] == "cancelled"

    fresh_path = tmp_path / "fresh-low"
    fresh_path.mkdir()
    fresh_low_service, fresh_github = make_service(
        fresh_path,
        snapshot=low,
        pulls=[make_pull(low)] * 10,
        clock=clock,
        live=True,
        checks_enabled=True,
    )
    fresh_low_service.sync(low.pr, expected_binding=low.binding())
    fresh_low_service.flush_publications()
    assert fresh_github.pr_card is None
    assert fresh_github.check_calls[-1]["conclusion"] == "neutral"


def test_delayed_same_head_closed_delivery_is_skipped_after_reopen(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    service, github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot)] * 40,
        clock=clock,
        live=True,
        checks_enabled=True,
    )
    service.sync(snapshot.pr)
    service.flush_publications()

    github._pulls = [_closed_pull(snapshot, closed_at="2026-09-11T01:00:00Z")] * 40
    github.fail_comment = 1
    github.fail_check = 1
    service.sync(snapshot.pr)
    service.flush_publications()

    github._pulls = [make_pull(snapshot)] * 40
    service.sync(snapshot.pr)
    service.flush_publications()
    comment_updates = len(github.comment_calls)
    check_updates = len(github.check_calls)
    cancel_updates = len(github.cancel_check_calls)
    card_body = str(github.pr_card["body"])

    clock.advance(10)
    service.flush_publications()

    assert len(github.comment_calls) == comment_updates
    assert len(github.check_calls) == check_updates
    assert len(github.cancel_check_calls) == cancel_updates
    assert str(github.pr_card["body"]) == card_body
    assert _heading("awaiting_author") in card_body
    closed_events = [
        event
        for event in service.store.load_presentation_publications(pr=snapshot.pr)
        if event.payload.get("phase") == "closed"
    ]
    assert closed_events
    assert all(event.remote is not None and event.remote.get("skipped") is True for event in closed_events)


def test_delayed_old_closed_delivery_is_skipped_after_new_head_reopen(tmp_path: Path):
    clock = FakeClock()
    old = make_snapshot()
    service, github = make_service(
        tmp_path,
        snapshot=old,
        pulls=[make_pull(old)] * 50,
        clock=clock,
        live=True,
        checks_enabled=True,
    )
    service.sync(old.pr)
    service.flush_publications()

    github._pulls = [_closed_pull(old, closed_at="2026-09-11T01:00:00Z")] * 50
    github.fail_comment = 1
    github.fail_check = 1
    service.sync(old.pr)
    service.flush_publications()

    reopened = make_snapshot(head_sha=OTHER_HEAD_SHA)
    service.reader = FakeReader(reopened)
    github._pulls = [make_pull(reopened)] * 50
    service.sync(reopened.pr)
    service.flush_publications()
    comment_updates = len(github.comment_calls)
    check_updates = len(github.check_calls)
    cancel_updates = len(github.cancel_check_calls)
    card_body = str(github.pr_card["body"])

    clock.advance(10)
    service.flush_publications()

    assert len(github.comment_calls) == comment_updates
    assert len(github.check_calls) == check_updates
    assert len(github.cancel_check_calls) == cancel_updates
    assert str(github.pr_card["body"]) == card_body
    assert github.check_calls[-1]["external_id"] == f"pr-{reopened.pr}-snapshot-{reopened.snapshot_id}"
    assert github.check_calls[-1]["status"] == "in_progress"


def test_restart_skips_delayed_closed_outbox_after_reopen(tmp_path: Path):
    clock = FakeClock()
    settings = make_settings(tmp_path, live=True, checks_enabled=True)
    snapshot = make_snapshot()
    github = FakeGitHub([make_pull(snapshot)] * 50)
    service = BotService(
        settings,
        github,
        FakeReader(snapshot),
        Store(settings.database),
        generate=lambda *args, **kwargs: make_questions(),
        grade=lambda *args, **kwargs: Answer(anchor="x", text="", verdict="pass"),
        clock=clock,
    )
    service.sync(snapshot.pr)
    service.flush_publications()

    github._pulls = [_closed_pull(snapshot, closed_at="2026-09-11T01:00:00Z")] * 50
    github.fail_comment = 1
    github.fail_check = 1
    service.sync(snapshot.pr)
    service.flush_publications()

    restarted = BotService(
        settings,
        github,
        FakeReader(snapshot),
        Store(settings.database),
        generate=lambda *args, **kwargs: make_questions(),
        grade=lambda *args, **kwargs: Answer(anchor="x", text="", verdict="pass"),
        clock=clock,
    )
    github._pulls = [make_pull(snapshot)] * 50
    restarted.sync(snapshot.pr)
    restarted.flush_publications()
    comment_updates = len(github.comment_calls)
    check_updates = len(github.check_calls)

    clock.advance(10)
    restarted.flush_publications()

    assert len(github.comment_calls) == comment_updates
    assert len(github.check_calls) == check_updates
    assert _heading("awaiting_author") in str(github.pr_card["body"])


def test_duplicate_closed_delivery_is_idempotent_and_pending_closed_still_cancels(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    service, github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot)] * 30,
        clock=clock,
        live=True,
        checks_enabled=True,
    )
    service.sync(snapshot.pr)
    service.flush_publications()

    github._pulls = [_closed_pull(snapshot, closed_at="2026-09-11T01:00:00Z")] * 30
    service.sync(snapshot.pr)
    service.flush_publications()

    assert _heading("closed") in str(github.pr_card["body"])
    assert github.check_calls[-1]["external_id"] == f"pr-{snapshot.pr}-snapshot-{snapshot.snapshot_id}"
    assert github.check_calls[-1]["conclusion"] == "cancelled"
    comment_updates = len(github.comment_calls)
    check_updates = len(github.check_calls)

    service.sync(snapshot.pr)
    service.flush_publications()

    assert len(github.comment_calls) == comment_updates
    assert len(github.check_calls) == check_updates


def test_closed_projection_cancels_superseded_pending_check(tmp_path: Path):
    clock = FakeClock()
    old = make_snapshot()
    service, github = make_service(
        tmp_path,
        snapshot=old,
        pulls=[make_pull(old)] * 30,
        clock=clock,
        live=True,
        checks_enabled=True,
    )
    service.sync(old.pr)
    service.flush_publications()

    current = make_snapshot(head_sha=OTHER_HEAD_SHA)
    service.store.save_snapshot(
        current,
        make_questions(),
        "pending",
        now="2026-09-11T01:00:00Z",
    )
    github._pulls = [_closed_pull(current, closed_at="2026-09-11T01:01:00Z")] * 30

    assert service.sync(current.pr)["state"] == "closed"
    service.flush_publications()

    assert github.cancel_check_calls
    assert github.cancel_check_calls[-1]["external_id"] == f"pr-{old.pr}-snapshot-{old.snapshot_id}"
    assert github.check_calls[-1]["external_id"] == f"pr-{current.pr}-snapshot-{current.snapshot_id}"
    assert github.check_calls[-1]["conclusion"] == "cancelled"


def test_verified_presentation_survives_merged_close_with_changed_base(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    service, github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot)] * 50,
        clock=clock,
        live=True,
        checks_enabled=True,
    )
    service.sync(snapshot.pr)
    service.flush_publications()
    job_id = service.submit(
        snapshot.pr,
        snapshot.author_id,
        snapshot.snapshot_id,
        "pass",
        _answers(),
    )
    service.drain()
    receipt_id = str(service.result(job_id, snapshot.author_id)["receipt_id"])
    service.flush_publications()
    service.verify(receipt_id, snapshot.binding())
    service.flush_publications()
    assert github.check_calls[-1]["conclusion"] == "success"

    check_updates = len(github.check_calls)
    status_updates = len(github.status_calls)
    github._pulls = [
        _closed_pull(
            snapshot,
            closed_at="2026-09-11T01:05:00Z",
            merged=True,
            base_sha="e" * 40,
        )
    ] * 50

    assert service.sync(snapshot.pr)["state"] == "merged"
    service.flush_publications()

    assert len(github.check_calls) == check_updates
    assert len(github.status_calls) == status_updates
    assert github.cancel_check_calls == []
    assert "이 변경에 대한 이해 확인을 완료했습니다" in str(github.pr_card["body"])
    assert github.check_calls[-1]["conclusion"] == "success"


def test_generation_error_renders_operational_action_required_and_recovers(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    attempts = {"count": 0}

    def generate(risk, title, body, n, *, structure):
        del risk, title, body, n, structure
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ModelError("RAW_SECRET failed")
        return make_questions()

    service, github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot)] * 20,
        clock=clock,
        live=True,
        checks_enabled=True,
        generate_func=generate,
    )

    with pytest.raises(BotError):
        service.sync(snapshot.pr)
    service.flush_publications()
    assert github.check_calls[-1]["status"] == "completed"
    assert github.check_calls[-1]["conclusion"] == "action_required"
    assert "운영자 확인" in str(github.pr_card["body"])
    assert b"RAW_SECRET" not in service.settings.database.read_bytes()

    service.sync(snapshot.pr)
    service.flush_publications()
    assert github.check_calls[-1]["status"] == "in_progress"
    assert github.check_calls[-1]["conclusion"] is None
    assert _heading("awaiting_author") in str(github.pr_card["body"])


def test_restart_migrates_pending_legacy_comments_through_current_card(tmp_path: Path):
    clock = FakeClock(start=datetime(2026, 9, 9, tzinfo=timezone.utc).timestamp())
    settings = make_settings(tmp_path, live=True, checks_enabled=True)
    store = Store(settings.database)
    snapshot = make_snapshot()
    stored = store.save_snapshot(
        snapshot,
        make_questions(),
        "pending",
        now="2026-09-08T12:00:00Z",
    )
    receipt = store.save_receipt(
        stored,
        actor_id=snapshot.author_id,
        actor_login=snapshot.author_login,
        answers=(
            ReceiptAnswer(question_id="0", anchor="app/auth/token.py:L10", text="returns refresh"),
            ReceiptAnswer(question_id="1", anchor="app/auth/token.py:L10", text="called by app/main.py"),
            ReceiptAnswer(question_id="2", anchor="docs/guide.md:L3", text="guide says Updated guide"),
        ),
        app_id=settings.app_id,
        installation_id=settings.installation_id,
        now="2026-09-08T12:01:00Z",
    )
    store.mark_receipt_verified(
        receipt.receipt_id,
        publications=(
            PublicationRequest(
                event_id=f"start-comment:{snapshot.snapshot_id}",
                kind="start_comment",
                pr=snapshot.pr,
                snapshot_id=snapshot.snapshot_id,
                payload={"body": "LEGACY START BODY"},
            ),
            PublicationRequest(
                event_id=f"success-comment:{receipt.receipt_id}",
                kind="success_comment",
                pr=snapshot.pr,
                snapshot_id=snapshot.snapshot_id,
                receipt_id=receipt.receipt_id,
                payload={"body": "LEGACY SUCCESS BODY"},
            ),
            PublicationRequest(
                event_id=f"success-status:{receipt.receipt_id}",
                kind="success_status",
                pr=snapshot.pr,
                snapshot_id=snapshot.snapshot_id,
                receipt_id=receipt.receipt_id,
                payload={
                    "description": "Human-verified",
                    "target_url": "https://example.com/receipts/legacy",
                },
            ),
        ),
        now="2026-09-08T12:02:00Z",
    )
    github = FakeGitHub([make_pull(snapshot)] * 10)
    service = BotService(
        settings,
        github,
        FakeReader(snapshot),
        Store(settings.database),
        generate=lambda *args, **kwargs: make_questions(),
        grade=lambda *args, **kwargs: Answer(anchor="x", text="", verdict="pass"),
        clock=clock,
    )

    service.flush_publications()
    bodies = "\n".join(str(call["body"]) for call in github.comment_calls)
    assert "LEGACY START BODY" not in bodies
    assert "LEGACY SUCCESS BODY" not in bodies
    assert "이 변경에 대한 이해 확인을 완료했습니다" in str(github.pr_card["body"])
    assert github.status_calls[-1]["state"] == "success"


def test_supplemental_check_failure_is_bounded_and_does_not_starve_final_status(tmp_path: Path):
    clock = FakeClock()
    snapshot = make_snapshot()
    service, github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot)] * 30,
        clock=clock,
        live=True,
        checks_enabled=True,
    )
    service.sync(snapshot.pr)
    service.flush_publications()
    job_id = service.submit(
        snapshot.pr,
        snapshot.author_id,
        snapshot.snapshot_id,
        "pass",
        _answers(),
    )
    service.drain()
    receipt_id = str(service.result(job_id, snapshot.author_id)["receipt_id"])
    service.flush_publications()
    service.verify(receipt_id, snapshot.binding())

    github.fail_check = 3
    for _ in range(3):
        service.flush_publications()
        clock.advance(10)

    status = service.publication_status(receipt_id, snapshot.author_id)
    assert status["published"] is True
    assert status["publication_failed"] == 0
    assert status["presentation"]["check"]["failed"] == 1
    assert github.status_calls[-1]["state"] == "success"
    assert "이 변경에 대한 이해 확인을 완료했습니다" in str(github.pr_card["body"])
