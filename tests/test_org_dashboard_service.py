"""Pinned, actual-only dashboard reads over isolated Store fixtures."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
from test_app_service import FakeClock, make_pull, make_questions, make_service, make_snapshot

from lasthuman.server.service import BotError, BotService
from lasthuman.server.snapshot import Snapshot
from lasthuman.server.store import ReceiptAnswer, Store, StoredSnapshot

AS_OF = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
UNTIL = "2026-09-17T12:00:00Z"
SINCE = "2026-08-18T12:00:00Z"
MERGED_AT = "2026-09-16T12:00:00Z"
BEFORE_MERGE = "2026-09-16T11:59:59Z"
AFTER_MERGE = "2026-09-16T12:00:01Z"
AUTH_ANCHOR = "app/auth/token.py:L10"
DOCS_ANCHOR = "docs/guide.md:L3"


@pytest.fixture
def dashboard_service(tmp_path: Path) -> Iterator[BotService]:
    snapshot = make_snapshot()
    service, _github = make_service(
        tmp_path,
        snapshot=snapshot,
        pulls=[make_pull(snapshot)],
        clock=FakeClock(AS_OF.timestamp()),
    )
    try:
        yield service
    finally:
        service.shutdown()


def save_merge(
    store: Store,
    pr: int,
    merged_at: str | None,
    record: StoredSnapshot | None = None,
) -> None:
    store.save_merge(
        pr=pr,
        snapshot_id=record.snapshot.snapshot_id if record is not None else None,
        merged_at=merged_at,
        merge_commit_sha="d" * 40,
        head_sha=record.snapshot.head_sha if record is not None else None,
        measured=record is not None,
        now=UNTIL,
    )


def save_snapshot(service: BotService, pr: int, *, triggered: bool = True) -> StoredSnapshot:
    original = make_snapshot(pr=pr, triggered=triggered)
    snapshot = Snapshot.create(
        repo=original.repo, repo_id=original.repo_id, pr=original.pr,
        head_sha=original.head_sha, base_sha=original.base_sha,
        author_id=original.author_id, author_login=original.author_login,
        title=original.title, body=original.body, risk=original.risk,
        config=original.config, diff=original.diff, structure=original.structure,
        zones=("app/auth/", "docs/", "app/orders/"), policy_version=original.policy_version,
    )
    return service.store.save_snapshot(
        snapshot,
        make_questions(),
        "pending" if triggered else "neutral",
        now=BEFORE_MERGE,
    )


def save_receipt(
    service: BotService,
    record: StoredSnapshot,
    actor_id: int,
    anchors: tuple[str, ...],
    *,
    created_at: str = BEFORE_MERGE,
    verified_at: str | None = BEFORE_MERGE,
) -> None:
    receipt = service.store.save_receipt(
        record,
        actor_id=actor_id,
        actor_login=f"private-actor-{actor_id}",
        answers=tuple(
            ReceiptAnswer(question_id=str(index), anchor=anchor, text="Successful answer")
            for index, anchor in enumerate(anchors)
        ),
        app_id=service.settings.app_id,
        installation_id=service.settings.installation_id,
        now=created_at,
    )
    if verified_at is not None:
        service.store.mark_receipt_verified(receipt.receipt_id, publications=(), now=verified_at)


def zone_rows(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    rows = cast(list[dict[str, object]], payload["zones"])
    return {str(row["zone"]): row for row in rows}


def test_store_merge_window_is_inclusive_and_optional(dashboard_service: BotService) -> None:
    store = dashboard_service.store
    for pr, merged_at in (
        (1, "2026-08-18T11:59:59Z"),
        (2, SINCE),
        (3, MERGED_AT),
        (4, UNTIL),
        (5, "2026-09-17T12:00:01Z"),
        (6, None),
        (7, UNTIL),
    ):
        save_merge(store, pr, merged_at)

    assert [merge.pr for merge in store.load_merges_since(since=SINCE, until=UNTIL)] == [2, 3, 4, 7]
    assert [merge.pr for merge in store.load_merges_since(since=SINCE)] == [2, 3, 4, 7, 5]
    assert store.load_merges_since(since=SINCE, until=None) == store.load_merges_since(since=SINCE)
    assert store.load_merges_since(since=UNTIL, until=SINCE) == ()
    assert store.load_merges_since(since=SINCE, until="' OR 1=1 --") == ()


def test_dashboard_pins_window_and_generated_at_without_reading_clock(dashboard_service: BotService) -> None:
    for pr, merged_at in enumerate(
        ("2026-08-18T11:59:59Z", SINCE, MERGED_AT, UNTIL, "2026-09-17T12:00:01Z"),
        start=1,
    ):
        record = save_snapshot(dashboard_service, pr)
        save_merge(dashboard_service.store, pr, merged_at, record)
    dashboard_service.clock = Mock(side_effect=AssertionError("Pinned reads must not use the clock"))

    payload = dashboard_service.dashboard(days=30, include_seed=False, as_of=AS_OF)

    assert payload["generated_at"] == UNTIL
    assert payload["window_days"] == 30
    assert payload["merged_total"] == payload["measured_total"] == 3
    assert zone_rows(payload)["app/auth/"]["prs"] == [4, 3, 2]


def test_dashboard_without_as_of_retains_unbounded_upper_and_clock_default(dashboard_service: BotService) -> None:
    save_merge(dashboard_service.store, 1, "2026-09-17T12:00:01Z")
    later_clock = AS_OF.timestamp() + 10
    clock = Mock(side_effect=[AS_OF.timestamp(), later_clock])
    dashboard_service.clock = clock

    payload = dashboard_service.dashboard()

    assert payload["merged_total"] == 1
    assert payload["generated_at"] == "2026-09-17T12:00:10Z"
    assert payload["window_days"] == 30
    assert clock.call_count == 2


def test_actual_skips_configured_seed_and_default_remains_additive(
    dashboard_service: BotService, tmp_path: Path,
) -> None:
    record = save_snapshot(dashboard_service, 1)
    save_merge(dashboard_service.store, 1, MERGED_AT, record)
    save_receipt(dashboard_service, record, 7, (AUTH_ANCHOR,))
    seed_path = tmp_path / "dashboard-seed.json"
    seed_path.write_text(json.dumps({
        "totals": {"merged": 5, "gated": 5, "attested": 4, "forced": 1, "waiting": 2},
        "zones": [
            {"zone": "app/auth/", "merged": 5, "gated": 5, "attested": 4,
             "forced": 1, "answerers": 3, "prs": [99]},
            {"zone": "sample-only/", "merged": 1},
        ],
    }), encoding="utf-8")
    dashboard_service.settings = replace(dashboard_service.settings, demo_seed=seed_path)
    before = dashboard_service.store.path.read_bytes()

    actual = dashboard_service.dashboard(include_seed=False, as_of=AS_OF)
    default = dashboard_service.dashboard()
    explicit_seed = dashboard_service.dashboard(include_seed=True)

    assert actual["demo_seeded"] is False
    assert actual["merged_total"] == actual["attested_total"] == 1
    assert zone_rows(actual)["app/auth/"]["answerers"] == 1
    assert "sample-only/" not in zone_rows(actual)
    assert default == explicit_seed
    assert default["demo_seeded"] is True
    assert default["merged_total"] == 6
    assert default["attested_total"] == 5
    assert default["waiting_total"] == 2
    assert zone_rows(default)["app/auth/"]["answerers"] == 4
    assert "sample-only/" in zone_rows(default)
    assert dashboard_service.store.path.read_bytes() == before

    seed_path.unlink()
    assert dashboard_service.dashboard(include_seed=False, as_of=AS_OF) == actual


def test_pinned_actual_preserves_premerge_anchor_and_distinct_actor_semantics(
    dashboard_service: BotService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "lasthuman.server.organization._demo_fixture", lambda: pytest.fail("Actual loaded demo metadata"),
    )
    first = save_snapshot(dashboard_service, 1)
    save_merge(dashboard_service.store, 1, MERGED_AT, first)
    save_receipt(dashboard_service, first, 7, (AUTH_ANCHOR, AUTH_ANCHOR))
    save_receipt(dashboard_service, first, 8, (AUTH_ANCHOR,), created_at=MERGED_AT, verified_at=MERGED_AT)
    save_receipt(dashboard_service, first, 9, (DOCS_ANCHOR,), verified_at=AFTER_MERGE)
    save_receipt(dashboard_service, first, 10, (DOCS_ANCHOR,), created_at=AFTER_MERGE)
    save_receipt(dashboard_service, first, 11, (DOCS_ANCHOR,), verified_at=None)

    second = save_snapshot(dashboard_service, 2)
    save_merge(dashboard_service.store, 2, MERGED_AT, second)
    save_receipt(dashboard_service, second, 7, (AUTH_ANCHOR,))

    third = save_snapshot(dashboard_service, 3)
    save_merge(dashboard_service.store, 3, MERGED_AT, third)
    save_receipt(dashboard_service, third, 12, ("malformed", "unknown/file.py:L1", "app/orders/cart.py:L1"))

    neutral = save_snapshot(dashboard_service, 4, triggered=False)
    save_merge(dashboard_service.store, 4, MERGED_AT, neutral)
    save_merge(dashboard_service.store, 5, MERGED_AT)
    still_open = save_snapshot(dashboard_service, 6)
    save_receipt(dashboard_service, still_open, 13, (AUTH_ANCHOR, DOCS_ANCHOR))
    before = dashboard_service.store.path.read_bytes()

    payload = dashboard_service.dashboard(include_seed=False, as_of=AS_OF)
    rows = zone_rows(payload)

    assert payload["merged_total"] == 5
    assert payload["measured_total"] == 4
    assert payload["unmeasured_total"] == 1
    assert payload["gated_total"] == payload["attested_total"] == 3
    assert payload["forced_total"] == 0
    assert payload["waiting_total"] == 1
    assert rows["app/auth/"]["merged"] == 4
    assert rows["app/auth/"]["gated"] == 3
    assert rows["app/auth/"]["attested"] == 2
    assert rows["app/auth/"]["answerers"] == 2
    assert rows["docs/"]["gated"] == 3
    assert rows["docs/"]["attested"] == rows["docs/"]["answerers"] == rows["docs/"]["forced"] == 0
    assert rows["app/orders/"]["gated"] == rows["app/orders/"]["answerers"] == 0
    assert rows["app/orders/"]["sample_state"] == "no_data"
    assert rows["app/auth/"]["rate"] is None
    assert [row["zone"] for row in payload["zones"]] == ["docs/", "app/auth/", "app/orders/"]
    assert rows["docs/"]["prs"] == rows["app/auth/"]["prs"] == [4, 3, 2, 1]
    assert all(row["owner"] == "" for row in rows.values())
    assert payload["actions"] == [{
        "zone": "docs/", "owner": "", "action": "One more verified change in this zone", "from": 0, "to": 1,
    }]
    assert "private-actor-" not in json.dumps(payload)
    assert "successful_answers" not in payload
    assert dashboard_service.store.path.read_bytes() == before


@pytest.mark.parametrize(("count", "rate"), [(4, None), (5, 0.0)])
def test_actual_retains_minimum_sample_percentage_suppression(
    dashboard_service: BotService, count: int, rate: float | None,
) -> None:
    for pr in range(1, count + 1):
        record = save_snapshot(dashboard_service, pr)
        save_merge(dashboard_service.store, pr, MERGED_AT, record)

    payload = dashboard_service.dashboard(include_seed=False, as_of=AS_OF)

    assert payload["min_sample"] == 5
    assert payload["attested_rate"] == rate
    assert zone_rows(payload)["app/auth/"]["rate"] == rate
    assert zone_rows(payload)["app/auth/"]["low_sample"] is (count < 5)


class NonFiniteDatetime(datetime):
    def timestamp(self) -> float:
        return float("nan")


@pytest.mark.parametrize("as_of", [
    UNTIL, AS_OF.timestamp(), float("inf"), True,
    AS_OF.replace(tzinfo=None),
    AS_OF.astimezone(timezone(timedelta(hours=9))),
    datetime.min.replace(tzinfo=timezone.utc),
    datetime.max.replace(tzinfo=timezone.utc),
    NonFiniteDatetime(2026, 9, 17, tzinfo=timezone.utc),
])
def test_dashboard_rejects_invalid_as_of_without_reading_store(
    dashboard_service: BotService, as_of: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    read = Mock(side_effect=AssertionError("Invalid inputs must not query merges"))
    monkeypatch.setattr(dashboard_service.store, "load_merges_since", read)

    with pytest.raises(BotError, match="as_of") as caught:
        dashboard_service.dashboard(as_of=cast(datetime, as_of))

    assert caught.value.code == "invalid_request"
    assert caught.value.status_code == 400
    read.assert_not_called()


@pytest.mark.parametrize("include_seed", [None, 0, 1, "false", "true", [], {}])
def test_dashboard_rejects_non_boolean_include_seed(
    dashboard_service: BotService, include_seed: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    read = Mock(side_effect=AssertionError("Invalid inputs must not query merges"))
    monkeypatch.setattr(dashboard_service.store, "load_merges_since", read)

    with pytest.raises(BotError, match="include_seed") as caught:
        dashboard_service.dashboard(include_seed=cast(bool, include_seed), as_of=AS_OF)

    assert caught.value.code == "invalid_request"
    assert caught.value.status_code == 400
    read.assert_not_called()
