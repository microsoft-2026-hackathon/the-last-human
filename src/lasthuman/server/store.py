"""SQLite persistence for the GitHub App runtime."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Iterator

from lasthuman.models import Question
from lasthuman.server.snapshot import Snapshot

_SNAPSHOT_STATES = {"pending", "neutral"}
_OUTBOX_STATUSES = {"pending", "sent"}


@dataclass(frozen=True)
class StoredQuestion:
    id: str
    ordinal: int
    question: Question


@dataclass(frozen=True)
class StoredSnapshot:
    snapshot: Snapshot
    state: str
    question_version: str
    questions: tuple[StoredQuestion, ...]
    created_at: str
    updated_at: str

    @property
    def question_count(self) -> int:
        return len(self.questions)


@dataclass(frozen=True)
class ReceiptAnswer:
    question_id: str
    anchor: str
    text: str


@dataclass(frozen=True)
class StoredReceipt:
    receipt_id: str
    snapshot_id: str
    pr: int
    repo: str
    repo_id: int
    head_sha: str
    base_sha: str
    policy_version: str
    question_version: str
    actor_id: int
    actor_login: str
    app_id: int
    installation_id: int
    created_at: str
    verified_at: str | None
    successful_answers: tuple[ReceiptAnswer, ...]

    @property
    def verified(self) -> bool:
        return self.verified_at is not None


@dataclass(frozen=True)
class PublicationRequest:
    event_id: str
    kind: str
    pr: int
    payload: Mapping[str, object]
    snapshot_id: str | None = None
    receipt_id: str | None = None
    due_at: str | None = None


@dataclass(frozen=True)
class OutboxEvent:
    event_id: str
    kind: str
    pr: int
    payload: dict[str, object]
    snapshot_id: str | None
    receipt_id: str | None
    status: str
    attempts: int
    due_at: str
    created_at: str
    updated_at: str
    last_error_code: str | None
    last_error: str | None
    remote: dict[str, object] | None


@dataclass(frozen=True)
class StoredMerge:
    pr: int
    snapshot_id: str | None
    merged_at: str | None
    merge_commit_sha: str | None
    head_sha: str | None
    measured: bool
    updated_at: str


class Store:
    """Durable SQLite store for snapshots, receipts, outbox, and merge facts."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = RLock()
        self._prepare_path()
        self._initialize()

    def save_snapshot(
        self,
        snapshot: Snapshot,
        questions: Sequence[Question],
        state: str,
        *,
        now: str,
    ) -> StoredSnapshot:
        return self.save_snapshot_with_publications(
            snapshot,
            questions,
            state,
            publications=(),
            now=now,
        )

    def save_snapshot_with_publications(
        self,
        snapshot: Snapshot,
        questions: Sequence[Question],
        state: str,
        *,
        publications: Sequence[PublicationRequest],
        now: str,
    ) -> StoredSnapshot:
        if state not in _SNAPSHOT_STATES:
            raise ValueError("snapshot state must be pending or neutral")
        stored_questions = _stored_questions(questions)
        payload = _questions_payload(stored_questions)
        question_version = _sha256_json(payload)
        snapshot_json = _json_dumps(snapshot.to_dict())
        questions_json = _json_dumps(payload)
        with self._write_connection() as connection:
            connection.execute(
                """
                INSERT INTO snapshots (
                    snapshot_id,
                    pr,
                    repo,
                    repo_id,
                    head_sha,
                    base_sha,
                    author_id,
                    author_login,
                    title,
                    state,
                    question_version,
                    question_count,
                    snapshot_json,
                    questions_json,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    state = excluded.state,
                    question_version = excluded.question_version,
                    question_count = excluded.question_count,
                    snapshot_json = excluded.snapshot_json,
                    questions_json = excluded.questions_json,
                    updated_at = excluded.updated_at
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.pr,
                    snapshot.repo,
                    snapshot.repo_id,
                    snapshot.head_sha,
                    snapshot.base_sha,
                    snapshot.author_id,
                    snapshot.author_login,
                    snapshot.title,
                    state,
                    question_version,
                    len(stored_questions),
                    snapshot_json,
                    questions_json,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO pr_snapshots (pr, snapshot_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(pr) DO UPDATE SET
                    snapshot_id = excluded.snapshot_id,
                    updated_at = excluded.updated_at
                """,
                (snapshot.pr, snapshot.snapshot_id, now),
            )
            for publication in publications:
                self._queue_publication_connection(connection, publication, now=now)
        return StoredSnapshot(
            snapshot=snapshot,
            state=state,
            question_version=question_version,
            questions=stored_questions,
            created_at=now,
            updated_at=now,
        )

    def load_current_snapshot(self, pr: int) -> StoredSnapshot | None:
        with self._read_connection() as connection:
            row = connection.execute(
                """
                SELECT s.*
                FROM pr_snapshots ps
                JOIN snapshots s ON s.snapshot_id = ps.snapshot_id
                WHERE ps.pr = ?
                """,
                (pr,),
            ).fetchone()
        return None if row is None else _stored_snapshot_from_row(row)

    def load_snapshot(self, snapshot_id: str) -> StoredSnapshot | None:
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
        return None if row is None else _stored_snapshot_from_row(row)

    def find_snapshot(
        self,
        *,
        pr: int,
        head_sha: str,
        base_sha: str,
        created_before: str | None = None,
    ) -> StoredSnapshot | None:
        query = """
            SELECT *
            FROM snapshots
            WHERE pr = ? AND head_sha = ? AND base_sha = ?
        """
        params: list[object] = [pr, head_sha, base_sha]
        if created_before is not None:
            query += " AND created_at <= ?"
            params.append(created_before)
        query += " ORDER BY created_at DESC, updated_at DESC, snapshot_id DESC LIMIT 1"
        with self._read_connection() as connection:
            row = connection.execute(query, params).fetchone()
        return None if row is None else _stored_snapshot_from_row(row)

    def save_receipt(
        self,
        stored_snapshot: StoredSnapshot,
        *,
        actor_id: int,
        actor_login: str,
        answers: Sequence[ReceiptAnswer],
        app_id: int,
        installation_id: int,
        now: str,
    ) -> StoredReceipt:
        if actor_id <= 0:
            raise ValueError("actor_id must be positive")
        if not actor_login.strip():
            raise ValueError("actor_login must not be empty")
        if not answers:
            raise ValueError("successful answers must not be empty")
        normalized_answers = tuple(_normalize_receipt_answer(answer) for answer in answers)
        with self._write_connection() as connection:
            existing = connection.execute(
                """
                SELECT *
                FROM receipts
                WHERE snapshot_id = ? AND question_version = ? AND actor_id = ?
                """,
                (
                    stored_snapshot.snapshot.snapshot_id,
                    stored_snapshot.question_version,
                    actor_id,
                ),
            ).fetchone()
            if existing is not None:
                receipt = _stored_receipt_from_row(existing)
                self._queue_publication_connection(
                    connection,
                    PublicationRequest(
                        event_id=f"dispatch:{receipt.receipt_id}",
                        kind="verifier_dispatch",
                        pr=receipt.pr,
                        snapshot_id=receipt.snapshot_id,
                        receipt_id=receipt.receipt_id,
                        payload={"receipt_id": receipt.receipt_id},
                    ),
                    now=now,
                )
                return receipt

            receipt_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO receipts (
                    receipt_id,
                    snapshot_id,
                    pr,
                    repo,
                    repo_id,
                    head_sha,
                    base_sha,
                    policy_version,
                    question_version,
                    actor_id,
                    actor_login,
                    app_id,
                    installation_id,
                    created_at,
                    verified_at,
                    successful_answers_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    stored_snapshot.snapshot.snapshot_id,
                    stored_snapshot.snapshot.pr,
                    stored_snapshot.snapshot.repo,
                    stored_snapshot.snapshot.repo_id,
                    stored_snapshot.snapshot.head_sha,
                    stored_snapshot.snapshot.base_sha,
                    stored_snapshot.snapshot.policy_version,
                    stored_snapshot.question_version,
                    actor_id,
                    actor_login,
                    app_id,
                    installation_id,
                    now,
                    None,
                    _json_dumps(_receipt_answers_payload(normalized_answers)),
                ),
            )
            self._queue_publication_connection(
                connection,
                PublicationRequest(
                    event_id=f"dispatch:{receipt_id}",
                    kind="verifier_dispatch",
                    pr=stored_snapshot.snapshot.pr,
                    snapshot_id=stored_snapshot.snapshot.snapshot_id,
                    receipt_id=receipt_id,
                    payload={"receipt_id": receipt_id},
                ),
                now=now,
            )
        return StoredReceipt(
            receipt_id=receipt_id,
            snapshot_id=stored_snapshot.snapshot.snapshot_id,
            pr=stored_snapshot.snapshot.pr,
            repo=stored_snapshot.snapshot.repo,
            repo_id=stored_snapshot.snapshot.repo_id,
            head_sha=stored_snapshot.snapshot.head_sha,
            base_sha=stored_snapshot.snapshot.base_sha,
            policy_version=stored_snapshot.snapshot.policy_version,
            question_version=stored_snapshot.question_version,
            actor_id=actor_id,
            actor_login=actor_login,
            app_id=app_id,
            installation_id=installation_id,
            created_at=now,
            verified_at=None,
            successful_answers=normalized_answers,
        )

    def load_receipt(self, receipt_id: str) -> StoredReceipt | None:
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM receipts WHERE receipt_id = ?",
                (receipt_id,),
            ).fetchone()
        return None if row is None else _stored_receipt_from_row(row)

    def load_receipt_by_key(
        self,
        snapshot_id: str,
        question_version: str,
        actor_id: int,
    ) -> StoredReceipt | None:
        with self._read_connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM receipts
                WHERE snapshot_id = ? AND question_version = ? AND actor_id = ?
                """,
                (snapshot_id, question_version, actor_id),
            ).fetchone()
        return None if row is None else _stored_receipt_from_row(row)

    def load_receipts_for_snapshot(
        self,
        snapshot_id: str,
        *,
        verified_only: bool = False,
    ) -> tuple[StoredReceipt, ...]:
        query = "SELECT * FROM receipts WHERE snapshot_id = ?"
        params: list[object] = [snapshot_id]
        if verified_only:
            query += " AND verified_at IS NOT NULL"
        query += " ORDER BY created_at ASC, receipt_id ASC"
        with self._read_connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(_stored_receipt_from_row(row) for row in rows)

    def load_receipt_publications(self, receipt_id: str) -> tuple[OutboxEvent, ...]:
        with self._read_connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM outbox
                WHERE receipt_id = ?
                  AND kind IN ('success_comment', 'success_status')
                ORDER BY created_at ASC, event_id ASC
                """,
                (receipt_id,),
            ).fetchall()
        return tuple(_outbox_event_from_row(row) for row in rows)

    def load_verifier_dispatch(self, receipt_id: str) -> OutboxEvent | None:
        with self._read_connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM outbox
                WHERE receipt_id = ?
                  AND kind = 'verifier_dispatch'
                ORDER BY created_at ASC, event_id ASC
                LIMIT 1
                """,
                (receipt_id,),
            ).fetchone()
        return None if row is None else _outbox_event_from_row(row)

    def mark_receipt_verified(
        self,
        receipt_id: str,
        *,
        publications: Sequence[PublicationRequest],
        now: str,
    ) -> StoredReceipt:
        with self._write_connection() as connection:
            connection.execute(
                """
                UPDATE receipts
                SET verified_at = COALESCE(verified_at, ?)
                WHERE receipt_id = ?
                """,
                (now, receipt_id),
            )
            row = connection.execute(
                "SELECT * FROM receipts WHERE receipt_id = ?",
                (receipt_id,),
            ).fetchone()
            if row is None:
                raise ValueError("receipt not found")
            for publication in publications:
                self._queue_publication_connection(connection, publication, now=now)
            return _stored_receipt_from_row(row)

    def queue_publication(self, publication: PublicationRequest, *, now: str) -> None:
        with self._write_connection() as connection:
            self._queue_publication_connection(connection, publication, now=now)

    def load_due_publications(
        self,
        *,
        now: str,
        limit: int = 20,
    ) -> tuple[OutboxEvent, ...]:
        with self._read_connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM outbox
                WHERE status = 'pending' AND due_at <= ?
                ORDER BY due_at ASC, created_at ASC, event_id ASC
                LIMIT ?
                """,
                (now, limit),
            ).fetchall()
        return tuple(_outbox_event_from_row(row) for row in rows)

    def mark_publication_sent(
        self,
        event_id: str,
        *,
        now: str,
        remote: Mapping[str, object] | None = None,
    ) -> None:
        with self._write_connection() as connection:
            connection.execute(
                """
                UPDATE outbox
                SET
                    status = 'sent',
                    attempts = attempts + 1,
                    updated_at = ?,
                    remote_json = ?,
                    last_error_code = NULL,
                    last_error = NULL
                WHERE event_id = ?
                """,
                (
                    now,
                    None if remote is None else _json_dumps(remote),
                    event_id,
                ),
            )

    def mark_publication_retry(
        self,
        event_id: str,
        *,
        now: str,
        due_at: str,
        error_code: str,
        error_message: str,
    ) -> None:
        with self._write_connection() as connection:
            connection.execute(
                """
                UPDATE outbox
                SET
                    status = 'pending',
                    attempts = attempts + 1,
                    due_at = ?,
                    updated_at = ?,
                    last_error_code = ?,
                    last_error = ?
                WHERE event_id = ?
                """,
                (due_at, now, error_code, error_message[:300], event_id),
            )

    def mark_publication_terminal(
        self,
        event_id: str,
        *,
        now: str,
        error_code: str,
        error_message: str,
        remote: Mapping[str, object] | None = None,
    ) -> None:
        with self._write_connection() as connection:
            connection.execute(
                """
                UPDATE outbox
                SET
                    status = 'sent',
                    attempts = attempts + 1,
                    updated_at = ?,
                    remote_json = ?,
                    last_error_code = ?,
                    last_error = ?
                WHERE event_id = ?
                """,
                (
                    now,
                    None if remote is None else _json_dumps(remote),
                    error_code,
                    error_message[:300],
                    event_id,
                ),
            )

    def requeue_unverified_dispatches(
        self,
        *,
        now: str,
        sent_before: str,
        max_attempts: int,
        limit: int = 20,
    ) -> int:
        with self._write_connection() as connection:
            rows = connection.execute(
                """
                SELECT o.event_id
                FROM outbox o
                JOIN receipts r ON r.receipt_id = o.receipt_id
                WHERE o.kind = 'verifier_dispatch'
                  AND o.status = 'sent'
                  AND o.updated_at <= ?
                  AND o.attempts < ?
                  AND r.verified_at IS NULL
                ORDER BY o.updated_at ASC, o.event_id ASC
                LIMIT ?
                """,
                (sent_before, max_attempts, limit),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE outbox
                    SET
                        status = 'pending',
                        due_at = ?,
                        updated_at = ?,
                        last_error_code = ?,
                        last_error = ?
                    WHERE event_id = ?
                    """,
                    (
                        now,
                        now,
                        "verification_timeout",
                        "Verification is still pending; retrying dispatch.",
                        row["event_id"],
                    ),
                )
        return len(rows)

    def save_merge(
        self,
        *,
        pr: int,
        snapshot_id: str | None,
        merged_at: str | None,
        merge_commit_sha: str | None,
        head_sha: str | None,
        measured: bool,
        now: str,
    ) -> None:
        with self._write_connection() as connection:
            existing = connection.execute(
                "SELECT * FROM merges WHERE pr = ?",
                (pr,),
            ).fetchone()
            if existing is not None:
                prior = _merge_from_row(existing)
                if prior.measured:
                    measured = True
                    if prior.snapshot_id is not None:
                        snapshot_id = prior.snapshot_id
                if merged_at is None:
                    merged_at = prior.merged_at
                if merge_commit_sha is None:
                    merge_commit_sha = prior.merge_commit_sha
                if head_sha is None:
                    head_sha = prior.head_sha
            connection.execute(
                """
                INSERT INTO merges (
                    pr,
                    snapshot_id,
                    merged_at,
                    merge_commit_sha,
                    head_sha,
                    measured,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pr) DO UPDATE SET
                    snapshot_id = excluded.snapshot_id,
                    merged_at = excluded.merged_at,
                    merge_commit_sha = excluded.merge_commit_sha,
                    head_sha = excluded.head_sha,
                    measured = excluded.measured,
                    updated_at = excluded.updated_at
                """,
                (
                    pr,
                    snapshot_id,
                    merged_at,
                    merge_commit_sha,
                    head_sha,
                    1 if measured else 0,
                    now,
                ),
            )

    def load_merge(self, pr: int) -> StoredMerge | None:
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM merges WHERE pr = ?",
                (pr,),
            ).fetchone()
        return None if row is None else _merge_from_row(row)

    def load_merges_since(self, *, since: str) -> tuple[StoredMerge, ...]:
        with self._read_connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM merges
                WHERE merged_at IS NOT NULL AND merged_at >= ?
                ORDER BY merged_at ASC, pr ASC
                """,
                (since,),
            ).fetchall()
        return tuple(_merge_from_row(row) for row in rows)

    def _prepare_path(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)

    def _initialize(self) -> None:
        with self._write_connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    pr INTEGER NOT NULL,
                    repo TEXT NOT NULL,
                    repo_id INTEGER NOT NULL,
                    head_sha TEXT NOT NULL,
                    base_sha TEXT NOT NULL,
                    author_id INTEGER NOT NULL,
                    author_login TEXT NOT NULL,
                    title TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('pending', 'neutral')),
                    question_version TEXT NOT NULL,
                    question_count INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    questions_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_snapshots_pr_updated
                    ON snapshots(pr, updated_at DESC);

                CREATE TABLE IF NOT EXISTS pr_snapshots (
                    pr INTEGER PRIMARY KEY,
                    snapshot_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(snapshot_id) REFERENCES snapshots(snapshot_id)
                );

                CREATE TABLE IF NOT EXISTS receipts (
                    receipt_id TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL,
                    pr INTEGER NOT NULL,
                    repo TEXT NOT NULL,
                    repo_id INTEGER NOT NULL,
                    head_sha TEXT NOT NULL,
                    base_sha TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    question_version TEXT NOT NULL,
                    actor_id INTEGER NOT NULL,
                    actor_login TEXT NOT NULL,
                    app_id INTEGER NOT NULL,
                    installation_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    verified_at TEXT,
                    successful_answers_json TEXT NOT NULL,
                    UNIQUE(snapshot_id, question_version, actor_id),
                    FOREIGN KEY(snapshot_id) REFERENCES snapshots(snapshot_id)
                );
                CREATE INDEX IF NOT EXISTS idx_receipts_snapshot
                    ON receipts(snapshot_id, created_at ASC);

                CREATE TABLE IF NOT EXISTS outbox (
                    event_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    pr INTEGER NOT NULL,
                    snapshot_id TEXT,
                    receipt_id TEXT,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'sent')),
                    attempts INTEGER NOT NULL,
                    due_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_error_code TEXT,
                    last_error TEXT,
                    remote_json TEXT,
                    FOREIGN KEY(snapshot_id) REFERENCES snapshots(snapshot_id),
                    FOREIGN KEY(receipt_id) REFERENCES receipts(receipt_id)
                );
                CREATE INDEX IF NOT EXISTS idx_outbox_due
                    ON outbox(status, due_at, created_at);

                CREATE TABLE IF NOT EXISTS merges (
                    pr INTEGER PRIMARY KEY,
                    snapshot_id TEXT,
                    merged_at TEXT,
                    merge_commit_sha TEXT,
                    head_sha TEXT,
                    measured INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(snapshot_id) REFERENCES snapshots(snapshot_id)
                );
                CREATE INDEX IF NOT EXISTS idx_merges_merged_at
                    ON merges(merged_at);
                """
            )
        if self.path.exists():
            os.chmod(self.path, 0o600)

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connect()
            try:
                yield connection
            finally:
                connection.close()

    @contextmanager
    def _write_connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connect()
            try:
                with connection:
                    yield connection
            finally:
                connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _queue_publication_connection(
        self,
        connection: sqlite3.Connection,
        publication: PublicationRequest,
        *,
        now: str,
    ) -> None:
        existing = connection.execute(
            "SELECT status FROM outbox WHERE event_id = ?",
            (publication.event_id,),
        ).fetchone()
        due_at = publication.due_at or now
        if existing is None:
            connection.execute(
                """
                INSERT INTO outbox (
                    event_id,
                    kind,
                    pr,
                    snapshot_id,
                    receipt_id,
                    payload_json,
                    status,
                    attempts,
                    due_at,
                    created_at,
                    updated_at,
                    last_error_code,
                    last_error,
                    remote_json
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, NULL, NULL, NULL)
                """,
                (
                    publication.event_id,
                    publication.kind,
                    publication.pr,
                    publication.snapshot_id,
                    publication.receipt_id,
                    _json_dumps(publication.payload),
                    due_at,
                    now,
                    now,
                ),
            )
            return
        if existing["status"] == "sent":
            return
        connection.execute(
            """
            UPDATE outbox
            SET
                kind = ?,
                pr = ?,
                snapshot_id = ?,
                receipt_id = ?,
                payload_json = ?,
                due_at = ?,
                updated_at = ?,
                last_error_code = NULL,
                last_error = NULL
            WHERE event_id = ?
            """,
            (
                publication.kind,
                publication.pr,
                publication.snapshot_id,
                publication.receipt_id,
                _json_dumps(publication.payload),
                due_at,
                now,
                publication.event_id,
            ),
        )


def _stored_questions(questions: Sequence[Question]) -> tuple[StoredQuestion, ...]:
    return tuple(
        StoredQuestion(id=str(index), ordinal=index, question=_normalize_question(question))
        for index, question in enumerate(questions)
    )


def _normalize_question(question: Question) -> Question:
    return Question(
        type=question.type,
        anchor=question.anchor,
        text=question.text,
        expected_evidence=question.expected_evidence,
        choices=tuple(question.choices),
        answer_index=question.answer_index,
    )


def _normalize_receipt_answer(answer: ReceiptAnswer) -> ReceiptAnswer:
    return ReceiptAnswer(
        question_id=answer.question_id,
        anchor=answer.anchor,
        text=answer.text,
    )


def _questions_payload(questions: Sequence[StoredQuestion]) -> list[dict[str, object]]:
    return [
        {
            "id": item.id,
            "ordinal": item.ordinal,
            "type": item.question.type,
            "anchor": item.question.anchor,
            "text": item.question.text,
            "expected_evidence": item.question.expected_evidence,
            "choices": list(item.question.choices),
            "answer_index": item.question.answer_index,
        }
        for item in questions
    ]


def _question_from_payload(data: Mapping[str, object]) -> StoredQuestion:
    identifier = str(data["id"])
    ordinal = int(data["ordinal"])
    question = Question(
        type=str(data["type"]),
        anchor=str(data["anchor"]),
        text=str(data["text"]),
        expected_evidence=str(data["expected_evidence"]),
        choices=tuple(str(item) for item in data.get("choices", [])),
        answer_index=int(data["answer_index"]),
    )
    return StoredQuestion(id=identifier, ordinal=ordinal, question=question)


def _receipt_answers_payload(
    answers: Sequence[ReceiptAnswer],
) -> list[dict[str, object]]:
    return [
        {
            "question_id": answer.question_id,
            "anchor": answer.anchor,
            "text": answer.text,
        }
        for answer in answers
    ]


def _receipt_answer_from_payload(data: Mapping[str, object]) -> ReceiptAnswer:
    return ReceiptAnswer(
        question_id=str(data["question_id"]),
        anchor=str(data["anchor"]),
        text=str(data["text"]),
    )


def _stored_snapshot_from_row(row: sqlite3.Row) -> StoredSnapshot:
    question_payload = json.loads(row["questions_json"])
    return StoredSnapshot(
        snapshot=Snapshot.from_dict(json.loads(row["snapshot_json"])),
        state=row["state"],
        question_version=row["question_version"],
        questions=tuple(_question_from_payload(item) for item in question_payload),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _stored_receipt_from_row(row: sqlite3.Row) -> StoredReceipt:
    answers = json.loads(row["successful_answers_json"])
    return StoredReceipt(
        receipt_id=row["receipt_id"],
        snapshot_id=row["snapshot_id"],
        pr=row["pr"],
        repo=row["repo"],
        repo_id=row["repo_id"],
        head_sha=row["head_sha"],
        base_sha=row["base_sha"],
        policy_version=row["policy_version"],
        question_version=row["question_version"],
        actor_id=row["actor_id"],
        actor_login=row["actor_login"],
        app_id=row["app_id"],
        installation_id=row["installation_id"],
        created_at=row["created_at"],
        verified_at=row["verified_at"],
        successful_answers=tuple(_receipt_answer_from_payload(item) for item in answers),
    )


def _outbox_event_from_row(row: sqlite3.Row) -> OutboxEvent:
    status = row["status"]
    if status not in _OUTBOX_STATUSES:
        raise ValueError("invalid outbox status")
    remote_raw = row["remote_json"]
    return OutboxEvent(
        event_id=row["event_id"],
        kind=row["kind"],
        pr=row["pr"],
        payload=json.loads(row["payload_json"]),
        snapshot_id=row["snapshot_id"],
        receipt_id=row["receipt_id"],
        status=status,
        attempts=row["attempts"],
        due_at=row["due_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_error_code=row["last_error_code"],
        last_error=row["last_error"],
        remote=None if remote_raw is None else json.loads(remote_raw),
    )


def _merge_from_row(row: sqlite3.Row) -> StoredMerge:
    return StoredMerge(
        pr=row["pr"],
        snapshot_id=row["snapshot_id"],
        merged_at=row["merged_at"],
        merge_commit_sha=row["merge_commit_sha"],
        head_sha=row["head_sha"],
        measured=bool(row["measured"]),
        updated_at=row["updated_at"],
    )


def _json_dumps(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(payload: object) -> str:
    import hashlib

    return hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()
