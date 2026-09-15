"""Service layer for the GitHub App runtime."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from urllib.parse import urlsplit

from lasthuman.diff import parse_anchor
from lasthuman.interview import (
    QUESTION_TYPES as _QUESTION_TYPES,
    ModelError,
    generate_questions,
    grade as grade_answer,
)
from lasthuman.models import Answer, Hunk, Question
from lasthuman.server.config import Settings
from lasthuman.server.github import GitHubError
from lasthuman.server.snapshot import Snapshot, SnapshotError, UnsupportedSnapshot
from lasthuman.server.store import (
    OutboxEvent,
    PublicationRequest,
    ReceiptAnswer,
    Store,
    StoredReceipt,
    StoredSnapshot,
)
from lasthuman.ledger import MIN_SAMPLE, zone_of

_BINDING_KEYS = (
    "repository_id",
    "pr",
    "head_sha",
    "base_sha",
    "policy_version",
    "snapshot_id",
    "score",
    "triggered",
)
_MAX_ACTIVE_JOBS = 16
_MAX_HINT_LENGTH = 200
_VERIFIER_RETRY_AFTER_SECONDS = 300
_MAX_VERIFIER_DISPATCH_ATTEMPTS = 3


class BotError(RuntimeError):
    """Sanitized service error."""

    def __init__(self, message: str, *, code: str, status_code: int) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class PullFacts:
    pr: int
    state: str
    merged: bool
    merged_at: str | None
    merge_commit_sha: str | None
    head_sha: str
    base_sha: str
    author_id: int
    author_login: str
    commit_count: int | None = None


@dataclass
class MemoryJob:
    job_id: str
    pr: int
    actor_id: int
    snapshot_id: str
    request_id: str
    body_digest: str
    expires_at: float
    state: str = "queued"
    message: str = ""
    feedback: list[dict[str, str]] = field(default_factory=list)
    receipt_id: str | None = None
    question_version: str | None = None
    verified_at: str | None = None
    future: Future[None] | None = None


class BotService:
    """Single-process GitHub App runtime service."""

    def __init__(
        self,
        settings: Settings,
        github,
        reader,
        store: Store,
        *,
        generate: Callable[..., list[Question]] = generate_questions,
        grade: Callable[..., Answer] = grade_answer,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.github = github
        self.reader = reader
        self.store = store
        self.generate = generate
        self.grade = grade
        self.clock = clock
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._jobs: dict[str, MemoryJob] = {}
        self._requests: dict[tuple[int, int, str], str] = {}

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)

    def sync(self, pr: int, *, expected_binding: Mapping[str, object] | None = None) -> dict[str, object]:
        with self._lock:
            pull = self._pull_facts(pr)
            if pull.state != "open":
                if pull.merged:
                    return self.sync_merged(pr, pull=pull)
                return {"state": "closed", "pr": pr, "measured": False}

            snapshot = self._read_snapshot(pr)
            if expected_binding is not None:
                self._require_binding_match(snapshot.binding(), expected_binding)

            existing = self.store.load_snapshot(snapshot.snapshot_id)
            now = self._now_iso()
            if not snapshot.risk.triggered:
                record = self.store.save_snapshot(snapshot, (), "neutral", now=now)
                if expected_binding is not None and self._can_publish_status():
                    self.store.queue_publication(
                        PublicationRequest(
                            event_id=f"neutral-status:{snapshot.snapshot_id}",
                            kind="neutral_status",
                            pr=pr,
                            snapshot_id=snapshot.snapshot_id,
                            payload={
                                "description": "Comprehension check not required",
                                "target_url": self._pr_url(pr),
                            },
                        ),
                        now=now,
                    )
                return {
                    "state": "neutral",
                    "snapshot_id": record.snapshot.snapshot_id,
                    "question_version": record.question_version,
                    "question_count": record.question_count,
                }

            publications = self._sync_publications(snapshot)
            if existing is not None and existing.question_count == self.settings.question_count:
                record = self.store.save_snapshot_with_publications(
                    snapshot,
                    [item.question for item in existing.questions],
                    "pending",
                    publications=publications,
                    now=now,
                )
            else:
                self.store.save_snapshot_with_publications(
                    snapshot,
                    (),
                    "pending",
                    publications=publications,
                    now=now,
                )
                questions = self._validated_questions(snapshot)
                self._assert_pull_current(snapshot, self._pull_facts(pr))
                record = self.store.save_snapshot(snapshot, questions, "pending", now=now)

            return {
                "state": "pending",
                "snapshot_id": record.snapshot.snapshot_id,
                "question_version": record.question_version,
                "question_count": record.question_count,
            }

    def interview(self, pr: int, actor_id: int) -> dict[str, object]:
        with self._lock:
            record = self.store.load_current_snapshot(pr)
            if record is None:
                raise BotError("No stored snapshot for this pull request", code="not_found", status_code=404)
            self._require_actor(record.snapshot, actor_id)
            current = self._pull_facts(pr)
            state = self._interview_state(record, current)
            payload = {
                "state": state,
                "id": record.snapshot.snapshot_id,
                "question_version": record.question_version,
                "head_sha": record.snapshot.head_sha,
                "title": record.snapshot.title,
                "repo": record.snapshot.repo,
                "question_count": record.question_count,
                "questions": [
                    {
                        "id": item.id,
                        "anchor": item.question.anchor,
                        "text": item.question.text,
                        "choices": list(item.question.choices),
                        "code": self._hunk_for_anchor(record.snapshot, item.question.anchor).body,
                    }
                    for item in record.questions
                ],
                "reasons": list(record.snapshot.risk.reasons),
            }
            message = self._interview_message(state)
            if message is not None:
                payload["message"] = message
            return payload

    def submit(
        self,
        pr: int,
        actor_id: int,
        snapshot_id: str,
        request_id: str,
        answers: Sequence[Mapping[str, object]],
    ) -> str:
        with self._lock:
            self._purge_expired_jobs()
            record = self.store.load_current_snapshot(pr)
            if record is None:
                raise BotError("No stored snapshot for this pull request", code="not_found", status_code=404)
            self._require_actor(record.snapshot, actor_id)
            if record.snapshot.snapshot_id != snapshot_id:
                raise BotError("Stored snapshot is stale; resync and resubmit", code="stale", status_code=409)
            if record.state != "pending":
                raise BotError("This pull request does not need an interview", code="not_required", status_code=409)
            if record.question_count != self.settings.question_count:
                raise self._submission_not_ready_error(record.question_count)

            normalized = self._normalized_submission(record, answers)
            request_key = (pr, actor_id, self._normalized_request_id(request_id))
            body_digest = self._sha256_json(normalized)
            existing_job_id = self._requests.get(request_key)
            if existing_job_id is not None:
                existing_job = self._jobs.get(existing_job_id)
                if (
                    existing_job is not None
                    and existing_job.snapshot_id == snapshot_id
                    and existing_job.body_digest == body_digest
                ):
                    return existing_job.job_id
                raise BotError("request_id already used with different content", code="conflict", status_code=409)
            queued_jobs = sum(1 for job in self._jobs.values() if job.state in {"queued", "running"})
            if queued_jobs >= _MAX_ACTIVE_JOBS:
                raise BotError("Too many verification jobs are queued; retry shortly", code="busy", status_code=503)

            self._assert_pull_current(record.snapshot, self._pull_facts(pr))
            job = MemoryJob(
                job_id=self._sha256_json({"request": request_key, "body": body_digest, "ts": self.clock()})[:32],
                pr=pr,
                actor_id=actor_id,
                snapshot_id=snapshot_id,
                request_id=request_key[2],
                body_digest=body_digest,
                expires_at=self.clock() + self.settings.session_ttl.total_seconds(),
            )
            self._jobs[job.job_id] = job
            self._requests[request_key] = job.job_id
            future = self._executor.submit(
                self._run_job,
                job.job_id,
                record,
                normalized,
            )
            job.future = future
            return job.job_id

    def result(self, job_id: str, actor_id: int) -> dict[str, object]:
        with self._lock:
            self._purge_expired_jobs()
            job = self._jobs.get(job_id)
            if job is None:
                raise BotError("Result expired; resubmit your answers", code="expired", status_code=410)
            if job.actor_id != actor_id:
                raise BotError("Only the author can read this result", code="forbidden", status_code=403)
            return self._job_payload(job)

    def purge_expired(self) -> int:
        with self._lock:
            return self._purge_expired_jobs()

    def receipt_binding(self, receipt_id: str) -> dict[str, object]:
        with self._lock:
            receipt = self._receipt_or_404(receipt_id)
            snapshot = self._snapshot_or_404(receipt.snapshot_id)
            return {
                "receipt_id": receipt.receipt_id,
                "binding": snapshot.snapshot.binding(),
                "actor_id": receipt.actor_id,
                "question_version": receipt.question_version,
                "issued_at": receipt.created_at,
                "app_id": receipt.app_id,
                "installation_id": receipt.installation_id,
            }

    def publication_status(self, receipt_id: str, actor_id: int) -> dict[str, object]:
        with self._lock:
            receipt = self._receipt_or_404(receipt_id)
            if actor_id != receipt.actor_id:
                raise BotError("Only the author can read this result", code="forbidden", status_code=403)
            self._requeue_unverified_dispatches()
            return {
                "receipt_id": receipt.receipt_id,
                **self._receipt_publication_payload(receipt),
            }

    def verify(self, receipt_id: str, binding: Mapping[str, object]) -> dict[str, object]:
        with self._lock:
            receipt = self._receipt_or_404(receipt_id)
            snapshot = self._snapshot_or_404(receipt.snapshot_id)
            self._require_binding_match(snapshot.snapshot.binding(), binding)
            self._require_trusted_receipt(receipt, snapshot.snapshot)
            current = self._pull_facts(receipt.pr)
            if current.state != "open":
                raise BotError("Pull request is no longer open", code="stale", status_code=409)
            self._assert_pull_current(snapshot.snapshot, current)
            fresh = self._read_snapshot(receipt.pr)
            self._require_binding_match(snapshot.snapshot.binding(), fresh.binding())
            verified = self.store.mark_receipt_verified(
                receipt_id,
                publications=self._success_publications(snapshot.snapshot, receipt_id),
                now=self._now_iso(),
            )
            self._mark_jobs_verified(receipt_id, verified.verified_at)
            return {
                "state": "verified",
                "receipt_id": receipt_id,
                "pr": verified.pr,
                "verified_at": verified.verified_at,
            }

    def flush_publications(self) -> int:
        with self._lock:
            processed = 0
            now = self._requeue_unverified_dispatches()
            due = self.store.load_due_publications(now=now)
            for event in due:
                if event.kind == "verifier_dispatch" and event.attempts >= _MAX_VERIFIER_DISPATCH_ATTEMPTS:
                    self.store.mark_publication_terminal(
                        event.event_id,
                        now=now,
                        error_code=event.last_error_code or "verification_retry_exhausted",
                        error_message=event.last_error or "Verification dispatch retry limit reached.",
                        remote={"skipped": True, "reason": "retry_exhausted"},
                    )
                    processed += 1
                    continue
                try:
                    outcome = self._deliver_event(event)
                except GitHubError as error:
                    if event.kind == "verifier_dispatch" and event.attempts + 1 >= _MAX_VERIFIER_DISPATCH_ATTEMPTS:
                        self.store.mark_publication_terminal(
                            event.event_id,
                            now=now,
                            error_code=f"github-{error.status_code or 0}",
                            error_message=self._publication_retry_message(error),
                            remote={"skipped": True, "reason": "retry_exhausted"},
                        )
                        processed += 1
                        continue
                    self.store.mark_publication_retry(
                        event.event_id,
                        now=now,
                        due_at=self._future_iso(event.attempts + 1),
                        error_code=f"github-{error.status_code or 0}",
                        error_message=self._publication_retry_message(error),
                    )
                    continue
                except BotError as error:
                    if error.code in {"github_error", "github_response", "snapshot_error"}:
                        if event.kind == "verifier_dispatch" and event.attempts + 1 >= _MAX_VERIFIER_DISPATCH_ATTEMPTS:
                            self.store.mark_publication_terminal(
                                event.event_id,
                                now=now,
                                error_code=error.code,
                                error_message=self._publication_retry_message(error),
                                remote={"skipped": True, "reason": "retry_exhausted"},
                            )
                            processed += 1
                            continue
                        self.store.mark_publication_retry(
                            event.event_id,
                            now=now,
                            due_at=self._future_iso(event.attempts + 1),
                            error_code=error.code,
                            error_message=self._publication_retry_message(error),
                        )
                        continue
                    if error.code in {"stale", "not_found"}:
                        self.store.mark_publication_sent(
                            event.event_id,
                            now=now,
                            remote={"skipped": True, "reason": error.code},
                        )
                        processed += 1
                        continue
                    raise
                self.store.mark_publication_sent(
                    event.event_id,
                    now=now,
                    remote=outcome,
                )
                processed += 1
            return processed

    def sync_merged(self, pr: int, *, pull: PullFacts | None = None) -> dict[str, object]:
        with self._lock:
            current = pull or self._pull_facts(pr)
            if current.state != "closed":
                raise BotError("Pull request is not closed", code="invalid_state", status_code=409)
            if not current.merged:
                return {"state": "closed", "pr": pr, "measured": False}
            prior = self.store.load_merge(pr)
            measured = bool(prior is not None and prior.measured)
            snapshot_id = prior.snapshot_id if prior is not None and prior.measured else None
            if not measured:
                matched = self._merge_snapshot(pr, current)
                if matched is not None:
                    measured = True
                    snapshot_id = matched.snapshot.snapshot_id
            self.store.save_merge(
                pr=pr,
                snapshot_id=snapshot_id,
                merged_at=current.merged_at,
                merge_commit_sha=current.merge_commit_sha,
                head_sha=current.head_sha,
                measured=measured,
                now=self._now_iso(),
            )
            return {
                "state": "merged",
                "pr": pr,
                "measured": measured,
                "snapshot_id": snapshot_id,
                "merged_at": current.merged_at,
            }

    def dashboard(self, *, days: int = 30) -> dict[str, object]:
        with self._lock:
            if days <= 0:
                raise BotError("days must be positive", code="invalid_request", status_code=400)
            since = self._iso_from_timestamp(self.clock() - days * 86400)
            merges = self.store.load_merges_since(since=since)
            merged_total = 0
            measured_total = 0
            gated_total = 0
            attested_total = 0
            unmeasured_total = 0
            zone_stats: dict[str, dict[str, object]] = {}
            zone_answerers: dict[str, set[int]] = {}
            for merge in merges:
                merged_total += 1
                if not merge.measured or not merge.snapshot_id:
                    unmeasured_total += 1
                    continue
                snapshot = self.store.load_snapshot(merge.snapshot_id)
                if snapshot is None:
                    unmeasured_total += 1
                    continue
                measured_total += 1
                touched = {
                    zone
                    for file_change in snapshot.snapshot.diff.files
                    if (zone := zone_of(file_change.file, snapshot.snapshot.zones))
                }
                for zone in touched:
                    zone_stats.setdefault(
                        zone,
                        {
                            "zone": zone,
                            "merged": 0,
                            "gated": 0,
                            "attested": 0,
                        },
                    )
                    zone_answerers.setdefault(zone, set())
                    zone_stats[zone]["merged"] = int(zone_stats[zone]["merged"]) + 1
                if not snapshot.snapshot.risk.triggered:
                    continue
                gated_total += 1
                for zone in touched:
                    zone_stats[zone]["gated"] = int(zone_stats[zone]["gated"]) + 1
                receipts = self.store.load_receipts_for_snapshot(merge.snapshot_id, verified_only=True)
                eligible = tuple(
                    receipt
                    for receipt in receipts
                    if (
                        merge.merged_at is not None
                        and receipt.verified_at is not None
                        and receipt.created_at <= merge.merged_at
                        and receipt.verified_at <= merge.merged_at
                    )
                )
                if eligible:
                    attested_total += 1
                credited: set[str] = set()
                for receipt in eligible:
                    for answer in receipt.successful_answers:
                        parsed = parse_anchor(answer.anchor)
                        if parsed is None:
                            continue
                        zone = zone_of(parsed[0], snapshot.snapshot.zones)
                        if zone is None or zone not in touched:
                            continue
                        credited.add(zone)
                        zone_answerers.setdefault(zone, set()).add(receipt.actor_id)
                for zone in credited:
                    zone_stats[zone]["attested"] = int(zone_stats[zone]["attested"]) + 1
            zones = []
            for zone in sorted(zone_stats):
                gated = int(zone_stats[zone]["gated"])
                attested = int(zone_stats[zone]["attested"])
                answerers = len(zone_answerers.get(zone, set()))
                zones.append(
                    {
                        "zone": zone,
                        "merged": int(zone_stats[zone]["merged"]),
                        "gated": gated,
                        "attested": attested,
                        "answerers": answerers,
                        "rate": None if gated < MIN_SAMPLE else (attested / gated if gated else None),
                        "low_sample": 0 < gated < MIN_SAMPLE,
                        "sample_state": (
                            "no_data" if gated == 0 else ("small_sample" if gated < MIN_SAMPLE else "measured")
                        ),
                    }
                )
            return {
                "repo": self.settings.repository,
                "generated_at": self._now_iso(),
                "window_days": days,
                "min_sample": MIN_SAMPLE,
                "merged_total": merged_total,
                "measured_total": measured_total,
                "gated_total": gated_total,
                "attested_total": attested_total,
                "unmeasured_total": unmeasured_total,
                "zones": zones,
            }

    def receipt_detail(self, receipt_id: str, actor_id: int) -> dict[str, object]:
        with self._lock:
            receipt = self._receipt_or_404(receipt_id)
            snapshot = self._snapshot_or_404(receipt.snapshot_id)
            self._require_actor(snapshot.snapshot, actor_id)
            return {
                "receipt_id": receipt.receipt_id,
                "snapshot_id": receipt.snapshot_id,
                "repo": receipt.repo,
                "repo_id": receipt.repo_id,
                "pr": receipt.pr,
                "head_sha": receipt.head_sha,
                "base_sha": receipt.base_sha,
                "policy_version": receipt.policy_version,
                "question_version": receipt.question_version,
                "actor_id": receipt.actor_id,
                "actor_login": receipt.actor_login,
                "app_id": receipt.app_id,
                "installation_id": receipt.installation_id,
                "created_at": receipt.created_at,
                "verified_at": receipt.verified_at,
                "successful_answers": [
                    {
                        "question_id": answer.question_id,
                        "anchor": answer.anchor,
                        "text": answer.text,
                    }
                    for answer in receipt.successful_answers
                ],
            }

    def drain(self, timeout: float | None = None) -> None:
        futures = []
        with self._lock:
            futures = [job.future for job in self._jobs.values() if job.future is not None]
        for future in futures:
            future.result(timeout=timeout)

    def _run_job(
        self,
        job_id: str,
        record: StoredSnapshot,
        answers: Sequence[dict[str, object]],
    ) -> None:
        try:
            self._run_job_inner(job_id, record, answers)
        except (
            BotError,
            GitHubError,
            ModelError,
            SnapshotError,
            UnsupportedSnapshot,
            sqlite3.Error,
            OSError,
            ValueError,
            TypeError,
        ) as error:
            self._finish_job_error(job_id, error)

    def _run_job_inner(
        self,
        job_id: str,
        record: StoredSnapshot,
        answers: Sequence[dict[str, object]],
    ) -> None:
        with self._lock:
            job = self._active_job(job_id)
            if job is None:
                return
            job.state = "running"
        hunk_by_anchor = {hunk.anchor: hunk for hunk in record.snapshot.diff.hunks}
        by_id = {item.id: item for item in record.questions}
        feedback: list[dict[str, str]] = []
        successful: list[ReceiptAnswer] = []
        for payload in answers:
            with self._lock:
                if self._active_job(job_id) is None:
                    return
            question = by_id[str(payload["id"])]
            text = str(payload["text"])
            choice = payload.get("choice")
            if not text.strip() or (question.question.choices and choice is None):
                feedback.append(
                    {
                        "id": question.id,
                        "hint": f"inspect {question.question.anchor}",
                    }
                )
                continue
            graded = self.grade(
                question.question,
                text,
                hunk_by_anchor[question.question.anchor],
                choice=choice,
            )
            with self._lock:
                if self._active_job(job_id) is None:
                    return
            if graded.verdict not in {"pass", "hold"}:
                raise BotError("grader returned an invalid verdict", code="grade_error", status_code=502)
            if graded.verdict == "hold":
                feedback.append(
                    {
                        "id": question.id,
                        "hint": self._safe_hint(question.question.anchor, graded.hint),
                    }
                )
                continue
            successful.append(
                ReceiptAnswer(
                    question_id=question.id,
                    anchor=question.question.anchor,
                    text=text,
                )
            )

        with self._lock:
            job = self._active_job(job_id)
            if job is None:
                return
            self._assert_pull_current(record.snapshot, self._pull_facts(record.snapshot.pr))
            job = self._active_job(job_id)
            if job is None:
                return
            if feedback:
                job.state = "needs_followup"
                job.feedback = feedback
                return
            receipt = self.store.save_receipt(
                record,
                actor_id=job.actor_id,
                actor_login=record.snapshot.author_login,
                answers=successful,
                app_id=self.settings.app_id,
                installation_id=self.settings.installation_id,
                now=self._now_iso(),
            )
            job.state = "verified" if receipt.verified else "awaiting_verification"
            job.receipt_id = receipt.receipt_id
            job.question_version = receipt.question_version

    def _finish_job_error(self, job_id: str, error: BaseException) -> None:
        safe_error = self._botify(error)
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if job.expires_at <= self.clock():
                return
            job.state = "stale" if safe_error.code == "stale" else "error"
            job.message = str(safe_error)

    def _sync_publications(self, snapshot: Snapshot) -> tuple[PublicationRequest, ...]:
        publications = [
            PublicationRequest(
                event_id=f"start-comment:{snapshot.snapshot_id}",
                kind="start_comment",
                pr=snapshot.pr,
                snapshot_id=snapshot.snapshot_id,
                payload={"body": self._start_comment_body(snapshot)},
            )
        ]
        if self._can_publish_status():
            publications.append(
                PublicationRequest(
                    event_id=f"pending-status:{snapshot.snapshot_id}",
                    kind="pending_status",
                    pr=snapshot.pr,
                    snapshot_id=snapshot.snapshot_id,
                    payload={
                        "description": "Comprehension check required",
                        "target_url": self._pr_url(snapshot.pr),
                    },
                )
            )
        return tuple(publications)

    def _deliver_event(self, event: OutboxEvent) -> Mapping[str, object]:
        if event.kind == "verifier_dispatch":
            receipt = self.store.load_receipt(event.receipt_id or "")
            if receipt is None or receipt.verified:
                return {"skipped": True, "reason": "already_verified"}
            self.github.dispatch_verification(receipt.receipt_id)
            return {"receipt_id": receipt.receipt_id}

        if event.kind in {"start_comment", "pending_status", "neutral_status"}:
            snapshot = self.store.load_snapshot(event.snapshot_id or "")
            if snapshot is None:
                return {"skipped": True, "reason": "missing_snapshot"}
            current = self.store.load_current_snapshot(snapshot.snapshot.pr)
            if current is None or current.snapshot.snapshot_id != snapshot.snapshot.snapshot_id:
                return {"skipped": True, "reason": "stale_snapshot"}
            if not self._event_pull_is_current(snapshot.snapshot):
                return {"skipped": True, "reason": "stale_snapshot"}
            if self._current_verified_receipt(current.snapshot.pr) is not None:
                return {"skipped": True, "reason": "already_verified"}
            if event.kind == "start_comment":
                return self.github.ensure_comment(
                    snapshot.snapshot.pr,
                    str(event.payload["body"]),
                    event.event_id,
                )
            if event.kind == "pending_status":
                if not self._can_publish_status():
                    return {"skipped": True, "reason": "local_only"}
                return self.github.set_status(
                    snapshot.snapshot.head_sha,
                    "pending",
                    str(event.payload["description"]),
                    str(event.payload["target_url"]),
                )
            if not self._can_publish_status():
                return {"skipped": True, "reason": "local_only"}
            if snapshot.snapshot.risk.triggered:
                return {"skipped": True, "reason": "not_neutral"}
            return self.github.set_status(
                snapshot.snapshot.head_sha,
                "success",
                str(event.payload["description"]),
                str(event.payload["target_url"]),
            )

        if event.kind in {"success_status", "success_comment"}:
            receipt = self.store.load_receipt(event.receipt_id or "")
            if receipt is None:
                return {"skipped": True, "reason": "missing_receipt"}
            if not receipt.verified:
                return {"skipped": True, "reason": "not_verified"}
            snapshot = self.store.load_snapshot(receipt.snapshot_id)
            if snapshot is None:
                return {"skipped": True, "reason": "missing_snapshot"}
            current_record = self.store.load_current_snapshot(receipt.pr)
            if (
                current_record is None
                or current_record.snapshot.snapshot_id != snapshot.snapshot.snapshot_id
                or current_record.question_version != receipt.question_version
            ):
                return {"skipped": True, "reason": "stale_snapshot"}
            current = self._pull_facts(receipt.pr)
            if current.state != "open":
                return {"skipped": True, "reason": "not_open"}
            if (
                current.author_id != snapshot.snapshot.author_id
                or current.head_sha != snapshot.snapshot.head_sha
                or current.base_sha != snapshot.snapshot.base_sha
            ):
                return {"skipped": True, "reason": "stale_snapshot"}
            fresh = self._read_snapshot(receipt.pr)
            try:
                self._require_binding_match(snapshot.snapshot.binding(), fresh.binding())
            except BotError as error:
                if error.code == "stale":
                    return {"skipped": True, "reason": "stale_snapshot"}
                raise
            if event.kind == "success_comment":
                return self.github.ensure_comment(
                    receipt.pr,
                    str(event.payload["body"]),
                    event.event_id,
                )
            if not self._can_publish_status():
                return {"skipped": True, "reason": "local_only"}
            return self.github.set_status(
                snapshot.snapshot.head_sha,
                "success",
                str(event.payload["description"]),
                str(event.payload["target_url"]),
            )

        raise BotError("unknown publication kind", code="invalid_state", status_code=500)

    def _validated_questions(self, snapshot: Snapshot) -> list[Question]:
        try:
            questions = self.generate(
                snapshot.risk,
                snapshot.title,
                snapshot.body,
                self.settings.question_count,
                structure=snapshot.structure,
            )
        except (ModelError, ValueError, TypeError) as error:
            raise self._botify(error) from None
        if len(questions) != self.settings.question_count:
            raise BotError("generated question count is invalid", code="question_count", status_code=502)
        valid_anchors = {hunk.anchor for hunk in snapshot.diff.hunks}
        validated: list[Question] = []
        for question in questions:
            if question.type not in _QUESTION_TYPES:
                raise BotError("generated question type is invalid", code="question_shape", status_code=502)
            if not question.anchor or question.anchor not in valid_anchors:
                raise BotError("generated question anchor is invalid", code="question_shape", status_code=502)
            if not question.text.strip() or not question.expected_evidence.strip():
                raise BotError("generated question text is invalid", code="question_shape", status_code=502)
            if question.choices:
                if len(question.choices) < 2:
                    raise BotError("generated question choices are invalid", code="question_shape", status_code=502)
                if not 0 <= question.answer_index < len(question.choices):
                    raise BotError("generated question choices are invalid", code="question_shape", status_code=502)
            elif question.answer_index != -1:
                raise BotError("generated question choices are invalid", code="question_shape", status_code=502)
            validated.append(question)
        return validated

    def _normalized_submission(
        self,
        record: StoredSnapshot,
        answers: Sequence[Mapping[str, object]],
    ) -> tuple[dict[str, object], ...]:
        expected_ids = {item.id for item in record.questions}
        seen: set[str] = set()
        normalized: list[dict[str, object]] = []
        if len(answers) != len(record.questions):
            raise BotError("answers must cover every question exactly once", code="invalid_answers", status_code=400)
        for raw in answers:
            keys = set(raw)
            if not keys <= {"id", "text", "choice"}:
                raise BotError("answer payload contains unsupported fields", code="invalid_answers", status_code=400)
            identifier = str(raw.get("id", "")).strip()
            if not identifier or identifier not in expected_ids or identifier in seen:
                raise BotError(
                    "answers must cover every question exactly once", code="invalid_answers", status_code=400
                )
            text = raw.get("text", "")
            if not isinstance(text, str):
                raise BotError("answer text must be a string", code="invalid_answers", status_code=400)
            if len(text) > 8000:
                raise BotError("answer text is too long", code="invalid_answers", status_code=400)
            choice = raw.get("choice")
            if choice is not None and (isinstance(choice, bool) or not isinstance(choice, int)):
                raise BotError("answer choice must be an integer", code="invalid_answers", status_code=400)
            question = next(item for item in record.questions if item.id == identifier)
            if choice is not None and question.question.choices and not 0 <= choice < len(question.question.choices):
                raise BotError("answer choice is out of range", code="invalid_answers", status_code=400)
            normalized.append({"id": identifier, "text": text, "choice": choice})
            seen.add(identifier)
        if seen != expected_ids:
            raise BotError("answers must cover every question exactly once", code="invalid_answers", status_code=400)
        normalized.sort(key=lambda item: int(str(item["id"])))
        return tuple(normalized)

    def _job_payload(self, job: MemoryJob) -> dict[str, object]:
        payload: dict[str, object] = {"state": job.state}
        if job.feedback:
            payload["feedback"] = list(job.feedback)
        if job.message:
            payload["message"] = job.message
        if job.receipt_id is not None:
            payload["receipt_id"] = job.receipt_id
            receipt = self.store.load_receipt(job.receipt_id)
            if receipt is not None:
                payload["publication"] = self._receipt_publication_payload(receipt)
        if job.question_version is not None:
            payload["question_version"] = job.question_version
        if job.verified_at is not None:
            payload["verified_at"] = job.verified_at
        return payload

    def _mark_jobs_verified(self, receipt_id: str, verified_at: str | None) -> None:
        for job in self._jobs.values():
            if job.receipt_id == receipt_id:
                job.state = "verified"
                job.verified_at = verified_at

    def _interview_state(self, record: StoredSnapshot, current: PullFacts) -> str:
        if current.state != "open":
            return "stale"
        if (
            current.head_sha != record.snapshot.head_sha
            or current.base_sha != record.snapshot.base_sha
            or current.author_id != record.snapshot.author_id
        ):
            return "stale"
        if record.state == "neutral":
            return "confirmed"
        if record.question_count != self.settings.question_count:
            return self._preparation_state(record.question_count)
        if self._current_verified_receipt(record.snapshot.pr) is not None:
            return "confirmed"
        return "pending"

    def _preparation_state(self, question_count: int) -> str:
        if question_count <= 0 or question_count > self.settings.question_count:
            return "preparation_error"
        return "preparing"

    def _interview_message(self, state: str) -> str | None:
        if state == "preparation_error":
            return "Questions were not prepared. Re-sync this pull request."
        if state == "preparing":
            return "Questions are still being prepared. Re-sync this pull request."
        return None

    def _submission_not_ready_error(self, question_count: int) -> BotError:
        state = self._preparation_state(question_count)
        return BotError(
            self._interview_message(state) or "Questions are not ready. Re-sync this pull request.",
            code=state,
            status_code=409,
        )

    def _current_verified_receipt(self, pr: int) -> StoredReceipt | None:
        record = self.store.load_current_snapshot(pr)
        if record is None:
            return None
        receipt = self.store.load_receipt_by_key(
            record.snapshot.snapshot_id,
            record.question_version,
            record.snapshot.author_id,
        )
        if receipt is None or not receipt.verified:
            return None
        return receipt

    def _success_publications(
        self,
        snapshot: Snapshot,
        receipt_id: str,
    ) -> tuple[PublicationRequest, ...]:
        publications = [
            PublicationRequest(
                event_id=f"success-comment:{receipt_id}",
                kind="success_comment",
                pr=snapshot.pr,
                snapshot_id=snapshot.snapshot_id,
                receipt_id=receipt_id,
                payload={"body": "Comprehension check complete."},
            )
        ]
        if self._can_publish_status():
            publications.append(
                PublicationRequest(
                    event_id=f"success-status:{receipt_id}",
                    kind="success_status",
                    pr=snapshot.pr,
                    snapshot_id=snapshot.snapshot_id,
                    receipt_id=receipt_id,
                    payload={
                        "description": "Comprehension check verified",
                        "target_url": self._receipt_url(receipt_id),
                    },
                )
            )
        return tuple(publications)

    def _receipt_or_404(self, receipt_id: str) -> StoredReceipt:
        receipt = self.store.load_receipt(receipt_id)
        if receipt is None:
            raise BotError("receipt not found", code="not_found", status_code=404)
        return receipt

    def _snapshot_or_404(self, snapshot_id: str) -> StoredSnapshot:
        snapshot = self.store.load_snapshot(snapshot_id)
        if snapshot is None:
            raise BotError("snapshot not found", code="not_found", status_code=404)
        return snapshot

    def _read_snapshot(self, pr: int) -> Snapshot:
        try:
            return self.reader.read(pr)
        except (GitHubError, SnapshotError, UnsupportedSnapshot, ValueError, TypeError) as error:
            raise self._botify(error) from None

    def _merge_snapshot(self, pr: int, current: PullFacts) -> StoredSnapshot | None:
        parents = self._merge_commit_parents(current.merge_commit_sha)
        if not parents or len(parents) > 2:
            return None
        if len(parents) == 1 and current.commit_count != 1:
            return None
        if len(parents) == 2 and current.head_sha not in parents[1:]:
            return None
        return self.store.find_snapshot(
            pr=pr,
            head_sha=current.head_sha,
            base_sha=parents[0],
            created_before=current.merged_at,
        )

    def _merge_commit_parents(self, merge_commit_sha: str | None) -> tuple[str, ...] | None:
        if merge_commit_sha is None:
            return ()
        request = getattr(self.github, "request", None)
        if not callable(request):
            return None
        try:
            payload = request(
                "GET",
                f"repos/{self.settings.repository}/commits/{merge_commit_sha}",
            )
        except GitHubError:
            return ()
        try:
            data = _mapping(payload, "merge commit")
            raw_parents = data.get("parents")
            if not isinstance(raw_parents, Sequence) or isinstance(raw_parents, (str, bytes, bytearray)):
                return ()
            return tuple(
                _sha(
                    _mapping(item, "merge commit parent").get("sha"),
                    "merge commit parent sha",
                )
                for item in raw_parents
            )
        except (ValueError, TypeError):
            return ()

    def _pull_facts(self, pr: int) -> PullFacts:
        try:
            payload = self.github.pull(pr)
        except GitHubError as error:
            raise self._botify(error) from None
        try:
            data = _mapping(payload)
            return PullFacts(
                pr=_positive_int(data.get("number"), "pull request number"),
                state=_nonempty_str(data.get("state"), "pull request state"),
                merged=_bool(data.get("merged"), default=False),
                merged_at=_optional_str(data.get("merged_at")),
                merge_commit_sha=_optional_str(data.get("merge_commit_sha")),
                head_sha=_sha(_mapping(data.get("head"), "pull request head").get("sha"), "pull request head sha"),
                base_sha=_sha(_mapping(data.get("base"), "pull request base").get("sha"), "pull request base sha"),
                author_id=_positive_int(
                    _mapping(data.get("user"), "pull request user").get("id"), "pull request author id"
                ),
                author_login=_nonempty_str(
                    _mapping(data.get("user"), "pull request user").get("login"), "pull request author login"
                ),
                commit_count=_optional_positive_int(data.get("commits")),
            )
        except (ValueError, TypeError):
            raise BotError("GitHub response was invalid", code="github_response", status_code=502) from None

    def _assert_pull_current(self, snapshot: Snapshot, pull: PullFacts) -> None:
        if pull.state != "open":
            raise BotError("Pull request is no longer open", code="stale", status_code=409)
        if pull.author_id != snapshot.author_id:
            raise BotError("Pull request author changed", code="stale", status_code=409)
        if pull.head_sha != snapshot.head_sha or pull.base_sha != snapshot.base_sha:
            raise BotError("Pull request changed while processing", code="stale", status_code=409)

    def _require_actor(self, snapshot: Snapshot, actor_id: int) -> None:
        if actor_id != snapshot.author_id:
            raise BotError("Only the pull request author can continue", code="forbidden", status_code=403)

    def _require_binding_match(
        self,
        expected: Mapping[str, object],
        actual: Mapping[str, object],
    ) -> None:
        if set(actual) != set(_BINDING_KEYS):
            raise BotError("binding keys are invalid", code="invalid_binding", status_code=400)
        for key in _BINDING_KEYS:
            if actual.get(key) != expected.get(key):
                raise BotError("binding does not match the stored snapshot", code="stale", status_code=409)

    def _require_trusted_receipt(self, receipt: StoredReceipt, snapshot: Snapshot) -> None:
        if receipt.repo_id != self.settings.repository_id or snapshot.repo_id != self.settings.repository_id:
            raise BotError("repository binding mismatch", code="stale", status_code=409)
        if receipt.app_id != self.settings.app_id or receipt.installation_id != self.settings.installation_id:
            raise BotError("app binding mismatch", code="stale", status_code=409)
        if receipt.snapshot_id != snapshot.snapshot_id:
            raise BotError("snapshot binding mismatch", code="stale", status_code=409)

    def _hunk_for_anchor(self, snapshot: Snapshot, anchor: str) -> Hunk:
        for hunk in snapshot.diff.hunks:
            if hunk.anchor == anchor:
                return hunk
        raise BotError("snapshot hunk not found", code="invalid_state", status_code=500)

    def _purge_expired_jobs(self) -> int:
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if job.expires_at <= self.clock()
        ]
        for job_id in expired:
            job = self._jobs.pop(job_id)
            self._requests.pop((job.pr, job.actor_id, job.request_id), None)
        return len(expired)

    def _normalized_request_id(self, request_id: str) -> str:
        value = request_id.strip()
        if not value or len(value) > 200:
            raise BotError("request_id must be 1..200 characters", code="invalid_request", status_code=400)
        return value

    def _now_iso(self) -> str:
        return self._iso_from_timestamp(self.clock())

    def _iso_from_timestamp(self, value: float) -> str:
        return datetime.fromtimestamp(value, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _future_iso(self, attempt: int) -> str:
        delay = min(300, max(5, 5 * (2 ** max(attempt - 1, 0))))
        return self._iso_from_timestamp(self.clock() + delay)

    def _timestamp_from_iso(self, value: str) -> float:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()

    def _requeue_unverified_dispatches(self) -> str:
        now = self._now_iso()
        self.store.requeue_unverified_dispatches(
            now=now,
            sent_before=self._iso_from_timestamp(self.clock() - _VERIFIER_RETRY_AFTER_SECONDS),
            max_attempts=_MAX_VERIFIER_DISPATCH_ATTEMPTS,
        )
        return now

    def _pr_url(self, pr: int) -> str:
        return self.settings.base_url.rstrip("/") + f"/prs/{pr}"

    def _receipt_url(self, receipt_id: str) -> str:
        return self.settings.base_url.rstrip("/") + f"/receipts/{receipt_id}"

    def _start_comment_body(self, snapshot: Snapshot) -> str:
        if not self._can_publish_status():
            return (
                "Comprehension check required. localhost local service only. "
                f"Confirm head {snapshot.head_sha} manually."
            )
        return (
            "Comprehension check required before merge. "
            f"Open {self._pr_url(snapshot.pr)} for head {snapshot.head_sha}."
        )

    def _can_publish_status(self) -> bool:
        parsed = urlsplit(self.settings.base_url)
        host = parsed.hostname
        if self.settings.mode != "live" or host is None:
            return False
        if host == "localhost":
            return False
        try:
            return not ipaddress.ip_address(host).is_loopback
        except ValueError:
            return True

    def _sha256_json(self, payload: object) -> str:
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _safe_hint(self, anchor: str, hint: object) -> str:
        text = " ".join(str(hint or "").split())
        if not text:
            return f"inspect {anchor}"
        return text[:_MAX_HINT_LENGTH]

    def _active_job(self, job_id: str) -> MemoryJob | None:
        job = self._jobs.get(job_id)
        if job is None or job.expires_at <= self.clock():
            return None
        return job

    def _event_pull_is_current(self, snapshot: Snapshot) -> bool:
        try:
            self._assert_pull_current(snapshot, self._pull_facts(snapshot.pr))
        except BotError as error:
            if error.code == "stale":
                return False
            raise
        return True

    def _receipt_publication_payload(self, receipt: StoredReceipt) -> dict[str, object]:
        dispatch = self.store.load_verifier_dispatch(receipt.receipt_id)
        events = self.store.load_receipt_publications(receipt.receipt_id)
        publication_pending = 0
        publication_applied = 0
        publication_skipped = 0
        publication_failed = 0
        error_codes: set[str] = set()
        for event in events:
            if event.last_error_code:
                error_codes.add(event.last_error_code)
            if event.status == "pending":
                publication_pending += 1
                if event.attempts > 0 or event.last_error_code is not None:
                    publication_failed += 1
                continue
            if event.remote is not None and event.remote.get("skipped") is True:
                publication_skipped += 1
                continue
            publication_applied += 1
        payload: dict[str, object] = {
            "verified": receipt.verified,
            "verified_at": receipt.verified_at,
            "published": bool(
                receipt.verified
                and events
                and publication_pending == 0
                and publication_skipped == 0
                and publication_applied == len(events)
            ),
            "publication_total": len(events),
            "publication_applied": publication_applied,
            "publication_pending": publication_pending,
            "publication_skipped": publication_skipped,
            "publication_failed": publication_failed,
            **self._verification_payload(receipt, dispatch),
        }
        if error_codes:
            payload["publication_error_codes"] = sorted(error_codes)
        return payload

    def _verification_payload(
        self,
        receipt: StoredReceipt,
        dispatch: OutboxEvent | None,
    ) -> dict[str, object]:
        if receipt.verified:
            payload: dict[str, object] = {"verification_state": "verified"}
            if dispatch is not None:
                payload["verification_dispatch_attempts"] = dispatch.attempts
            return payload
        if dispatch is None:
            return {
                "verification_state": "error",
                "verification_dispatch_attempts": 0,
            }
        payload = {
            "verification_dispatch_attempts": dispatch.attempts,
        }
        if dispatch.last_error_code is not None:
            payload["verification_error_codes"] = [dispatch.last_error_code]
        if dispatch.status == "pending":
            if dispatch.attempts >= _MAX_VERIFIER_DISPATCH_ATTEMPTS:
                payload["verification_state"] = "error"
                return payload
            payload["verification_state"] = (
                "retrying" if dispatch.attempts > 0 or dispatch.last_error_code is not None else "waiting"
            )
            payload["verification_retry_at"] = dispatch.due_at
            return payload
        if dispatch.remote is not None and dispatch.remote.get("reason") == "retry_exhausted":
            payload["verification_state"] = "error"
            return payload
        retry_at = self._iso_from_timestamp(
            self._timestamp_from_iso(dispatch.updated_at) + _VERIFIER_RETRY_AFTER_SECONDS
        )
        if dispatch.attempts >= _MAX_VERIFIER_DISPATCH_ATTEMPTS and retry_at <= self._now_iso():
            payload["verification_state"] = "error"
            return payload
        payload["verification_state"] = "retrying" if dispatch.attempts > 1 else "waiting"
        if dispatch.attempts < _MAX_VERIFIER_DISPATCH_ATTEMPTS:
            payload["verification_retry_at"] = retry_at
        return payload

    def _publication_retry_message(self, error: BaseException) -> str:
        if isinstance(error, GitHubError):
            return "GitHub publication failed"
        safe_error = self._botify(error)
        if safe_error.code == "github_response":
            return "GitHub response was invalid"
        if safe_error.code == "github_error":
            return "GitHub request failed"
        if safe_error.code == "snapshot_error":
            return "Pull request snapshot refresh failed"
        return str(safe_error)

    def _botify(self, error: BaseException) -> BotError:
        if isinstance(error, BotError):
            return error
        if isinstance(error, GitHubError):
            return BotError("GitHub request failed", code="github_error", status_code=502)
        if isinstance(error, (SnapshotError, UnsupportedSnapshot)):
            return BotError("Pull request snapshot is unavailable", code="snapshot_error", status_code=409)
        if isinstance(error, ModelError):
            return BotError("Model processing failed; retry later", code="model_error", status_code=502)
        if isinstance(error, sqlite3.Error):
            return BotError("SQLite storage error", code="storage_error", status_code=500)
        if isinstance(error, OSError):
            return BotError("Local storage error", code="storage_error", status_code=500)
        if isinstance(error, (ValueError, TypeError)):
            return BotError("Service state is invalid", code="invalid_state", status_code=400)
        return BotError("Service error", code="service_error", status_code=500)


def _mapping(value: object, context: str = "mapping") -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be an object")
    return value


def _nonempty_str(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} is invalid")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return _nonempty_str(value, "string")


def _positive_int(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{context} is invalid")
    return value


def _optional_positive_int(value: object) -> int | None:
    if value is None:
        return None
    return _positive_int(value, "positive integer")


def _bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError("boolean is invalid")
    return value


def _sha(value: object, context: str) -> str:
    text = _nonempty_str(value, context)
    if len(text) != 40 or any(ch not in "0123456789abcdef" for ch in text):
        raise ValueError(f"{context} is invalid")
    return text
