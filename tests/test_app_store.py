from __future__ import annotations

from pathlib import Path

from lasthuman.config import Config
from lasthuman.models import DiffResult, FileChange, Hunk, Question, RiskResult
from lasthuman.server.config import Settings
from lasthuman.server.snapshot import Snapshot
from lasthuman.server.store import PublicationRequest, ReceiptAnswer, Store
from lasthuman.structure import StructureContext, SymbolUse

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40


def make_settings(tmp_path: Path, *, live: bool = False) -> Settings:
    key_file = tmp_path / "app.pem"
    key_file.write_text("not used", encoding="utf-8")
    return Settings(
        app_id=101,
        client_id="Iv1.test",
        client_secret="s" * 32,
        private_key_file=key_file,
        installation_id=202,
        repository="hunhoon21/the-last-human",
        repository_id=1361123778,
        owner_id=36983960,
        base_url="https://example.com" if live else "http://localhost:8000",
        secret_key="k" * 32,
        database=tmp_path / "state" / "lasthuman.sqlite3",
        mode="live" if live else "development",
        status_context="last-human/human-verified" if live else "last-human/human-verified-dev",
        workflow="lasthuman-app.yml",
        workflow_ref="refs/heads/main",
        oidc_audience="hunhoon21/the-last-human",
    )


def make_snapshot(*, triggered: bool = True) -> Snapshot:
    auth_hunk = Hunk(
        file="app/auth/token.py",
        new_start=10,
        old_start=10,
        anchor="app/auth/token.py:L10",
        added=("+    return refresh(token)",),
        removed=("-    return token",),
        body=(
            "@@ -10,1 +10,1 @@\n"
            "-    return token\n"
            "+    return refresh(token)\n"
        ),
        file_status="modified",
    )
    docs_hunk = Hunk(
        file="docs/guide.md",
        new_start=3,
        old_start=3,
        anchor="docs/guide.md:L3",
        added=("+Updated guide",),
        removed=("-Old guide",),
        body=(
            "@@ -3,1 +3,1 @@\n"
            "-Old guide\n"
            "+Updated guide\n"
        ),
        file_status="modified",
    )
    diff = DiffResult(
        hunks=(auth_hunk, docs_hunk),
        files=(
            FileChange(
                file="app/auth/token.py",
                status="modified",
                binary=False,
                additions=1,
                deletions=1,
                hunk_count=1,
            ),
            FileChange(
                file="docs/guide.md",
                status="modified",
                binary=False,
                additions=1,
                deletions=1,
                hunk_count=1,
            ),
        ),
    )
    return Snapshot.create(
        repo="hunhoon21/the-last-human",
        repo_id=1361123778,
        pr=7,
        head_sha=HEAD_SHA,
        base_sha=BASE_SHA,
        author_id=7,
        author_login="octocat",
        title="Handle refresh",
        body="Explains refresh handling.",
        risk=RiskResult(
            score=80 if triggered else 10,
            triggered=triggered,
            reasons=("critical path changed",) if triggered else ("below threshold",),
            top_hunks=(auth_hunk, docs_hunk),
        ),
        config=Config(),
        diff=diff,
        structure=StructureContext(
            changed_files=("app/auth/token.py",),
            importers={"app/auth/token.py": ("app/main.py",)},
            symbols=(SymbolUse("refresh", "app/auth/token.py", ("app/main.py",)),),
            sibling_files=("app/main.py", "README.md"),
        ),
        zones=("app/auth/", "docs/"),
        policy_version="1" * 64,
    )


def make_questions() -> list[Question]:
    return [
        Question(
            type="claim",
            anchor="app/auth/token.py:L10",
            text="What does refresh return now?",
            expected_evidence="refresh(token)",
            choices=("token", "refresh(token)", "None", "exception"),
            answer_index=1,
        ),
        Question(
            type="structure",
            anchor="app/auth/token.py:L10",
            text="Who calls refresh?",
            expected_evidence="app/main.py",
            choices=("app/main.py", "docs/guide.md", "README.md", "tests/test_app.py"),
            answer_index=0,
        ),
        Question(
            type="rationale",
            anchor="docs/guide.md:L3",
            text="What changed in the guide?",
            expected_evidence="Updated guide",
            choices=(),
            answer_index=-1,
        ),
    ]


def test_store_creates_secure_db_and_roundtrips_snapshot_receipt_and_outbox(tmp_path: Path):
    settings = make_settings(tmp_path)
    store = Store(settings.database)
    snapshot = make_snapshot()
    record = store.save_snapshot(
        snapshot,
        make_questions(),
        "pending",
        now="2026-09-08T12:00:00Z",
    )

    parent_mode = settings.database.parent.stat().st_mode & 0o777
    file_mode = settings.database.stat().st_mode & 0o777
    assert parent_mode == 0o700
    assert file_mode == 0o600

    loaded = store.load_current_snapshot(snapshot.pr)
    assert loaded is not None
    assert loaded.snapshot == snapshot
    assert loaded.question_version == record.question_version
    assert [item.id for item in loaded.questions] == ["0", "1", "2"]
    assert loaded.questions[0].question.anchor == loaded.questions[1].question.anchor

    receipt = store.save_receipt(
        loaded,
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
    same = store.save_receipt(
        loaded,
        actor_id=snapshot.author_id,
        actor_login=snapshot.author_login,
        answers=receipt.successful_answers,
        app_id=settings.app_id,
        installation_id=settings.installation_id,
        now="2026-09-08T12:02:00Z",
    )

    assert same.receipt_id == receipt.receipt_id
    due = store.load_due_publications(now="2026-09-08T12:02:00Z")
    assert [(event.event_id, event.kind) for event in due] == [
        (f"dispatch:{receipt.receipt_id}", "verifier_dispatch")
    ]


def test_store_persists_verification_publications_and_merge_facts(tmp_path: Path):
    settings = make_settings(tmp_path, live=True)
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
    verified = store.mark_receipt_verified(
        receipt.receipt_id,
        publications=(
            PublicationRequest(
                event_id=f"success-status:{receipt.receipt_id}",
                kind="success_status",
                pr=snapshot.pr,
                receipt_id=receipt.receipt_id,
                snapshot_id=snapshot.snapshot_id,
                payload={"description": "verified", "target_url": "https://example.com/receipts/x"},
            ),
            PublicationRequest(
                event_id=f"success-comment:{receipt.receipt_id}",
                kind="success_comment",
                pr=snapshot.pr,
                receipt_id=receipt.receipt_id,
                snapshot_id=snapshot.snapshot_id,
                payload={"body": "Comprehension check complete."},
            ),
        ),
        now="2026-09-08T12:02:00Z",
    )
    store.save_merge(
        pr=snapshot.pr,
        snapshot_id=snapshot.snapshot_id,
        merged_at="2026-09-08T12:03:00Z",
        merge_commit_sha="c" * 40,
        head_sha=snapshot.head_sha,
        measured=True,
        now="2026-09-08T12:03:00Z",
    )

    reopened = Store(settings.database)
    stored_receipt = reopened.load_receipt(verified.receipt_id)
    assert stored_receipt is not None
    assert stored_receipt.verified_at == "2026-09-08T12:02:00Z"
    due = reopened.load_due_publications(now="2026-09-08T12:03:00Z")
    assert {event.kind for event in due} == {
        "verifier_dispatch",
        "success_status",
        "success_comment",
    }
    reopened.mark_publication_sent(
        f"success-comment:{receipt.receipt_id}",
        now="2026-09-08T12:03:30Z",
        remote={"id": 1},
    )
    reopened.mark_publication_retry(
        f"success-status:{receipt.receipt_id}",
        now="2026-09-08T12:03:30Z",
        due_at="2026-09-08T12:04:00Z",
        error_code="github-502",
        error_message="x" * 400,
    )
    publications = reopened.load_receipt_publications(receipt.receipt_id)
    assert [event.kind for event in publications] == ["success_comment", "success_status"]
    assert publications[0].remote == {"id": 1}
    assert publications[1].last_error_code == "github-502"
    assert publications[1].last_error == "x" * 300
    reopened.save_merge(
        pr=snapshot.pr,
        snapshot_id=None,
        merged_at="2026-09-08T12:03:00Z",
        merge_commit_sha="c" * 40,
        head_sha=snapshot.head_sha,
        measured=False,
        now="2026-09-08T12:04:00Z",
    )
    merge = reopened.load_merge(snapshot.pr)
    assert merge is not None
    assert merge.snapshot_id == snapshot.snapshot_id
    assert merge.measured is True


def test_store_requeues_unverified_dispatch_after_timeout_without_flooding(tmp_path: Path):
    settings = make_settings(tmp_path)
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

    store.mark_publication_sent(
        f"dispatch:{receipt.receipt_id}",
        now="2026-09-08T12:01:30Z",
        remote={"receipt_id": receipt.receipt_id},
    )
    store.requeue_unverified_dispatches(
        now="2026-09-08T12:06:31Z",
        sent_before="2026-09-08T12:01:31Z",
        max_attempts=3,
    )
    first = store.load_verifier_dispatch(receipt.receipt_id)
    due = store.load_due_publications(now="2026-09-08T12:06:31Z")
    assert first is not None
    assert first.status == "pending"
    assert first.attempts == 1
    assert first.last_error_code == "verification_timeout"
    assert [(event.event_id, event.kind) for event in due] == [
        (f"dispatch:{receipt.receipt_id}", "verifier_dispatch")
    ]

    store.mark_publication_sent(
        f"dispatch:{receipt.receipt_id}",
        now="2026-09-08T12:06:40Z",
        remote={"receipt_id": receipt.receipt_id},
    )
    store.requeue_unverified_dispatches(
        now="2026-09-08T12:11:41Z",
        sent_before="2026-09-08T12:06:41Z",
        max_attempts=3,
    )
    second = store.load_verifier_dispatch(receipt.receipt_id)
    assert second is not None
    assert second.status == "pending"
    assert second.attempts == 2

    store.mark_publication_sent(
        f"dispatch:{receipt.receipt_id}",
        now="2026-09-08T12:11:50Z",
        remote={"receipt_id": receipt.receipt_id},
    )
    store.requeue_unverified_dispatches(
        now="2026-09-08T12:16:51Z",
        sent_before="2026-09-08T12:11:51Z",
        max_attempts=3,
    )
    final = store.load_verifier_dispatch(receipt.receipt_id)
    assert final is not None
    assert final.status == "sent"
    assert final.attempts == 3
    assert store.load_due_publications(now="2026-09-08T12:16:51Z") == ()


def test_store_persists_operational_presentation_metadata_without_receipt_changes(tmp_path: Path):
    settings = make_settings(tmp_path)
    store = Store(settings.database)
    snapshot = make_snapshot()
    store.save_snapshot(
        snapshot,
        (),
        "pending",
        now="2026-09-08T12:00:00Z",
    )

    store.mark_snapshot_preparation_error(
        snapshot.snapshot_id,
        code="model_error",
        message="Model processing failed; retry later",
        now="2026-09-08T12:00:01Z",
    )
    error = store.load_snapshot_preparation_error(snapshot.snapshot_id)
    assert error is not None
    assert error.code == "model_error"
    assert error.message.startswith("Model processing failed")

    store.save_presentation_check_run(
        pr=snapshot.pr,
        snapshot_id=snapshot.snapshot_id,
        head_sha=snapshot.head_sha,
        external_id=f"pr-{snapshot.pr}-snapshot-{snapshot.snapshot_id}",
        check_run_id=51,
        status="in_progress",
        conclusion=None,
        now="2026-09-08T12:00:02Z",
    )
    active = store.load_presentation_check_run(snapshot.pr)
    assert active is not None
    assert active.external_id == f"pr-{snapshot.pr}-snapshot-{snapshot.snapshot_id}"
    assert active.check_run_id == 51

    store.clear_snapshot_preparation_error(snapshot.snapshot_id, now="2026-09-08T12:00:03Z")
    assert store.load_snapshot_preparation_error(snapshot.snapshot_id) is None
