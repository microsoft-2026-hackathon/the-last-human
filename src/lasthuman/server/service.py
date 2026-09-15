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
from typing import cast
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
from lasthuman.server.coverage import ZoneCounts, finalize, load_seed
from lasthuman.server.github import GitHubError
from lasthuman.server.presentation import (
    PresentationPhase,
    PresentationView,
    RenderedPresentation,
    presentation_revision,
    reason_lines,
    render_presentation,
)
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
_MAX_PRESENTATION_ATTEMPTS = 3


def _safe_publication_error_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = "".join(
        character.lower()
        if character.isascii() and (character.isalnum() or character in {"-", "_"})
        else "-"
        for character in value[:64]
    ).strip("-")
    return normalized or None


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
    closed_at: str | None
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
    feedback: list[dict[str, object]] = field(default_factory=list)
    #: 보류 회차에서 이미 통과한 문항. 화면이 Accepted 로 잠근다.
    accepted: list[str] = field(default_factory=list)
    receipt_id: str | None = None
    question_version: str | None = None
    verified_at: str | None = None
    future: Future[None] | None = None


@dataclass(frozen=True)
class PresentationCheckCancelTarget:
    snapshot_id: str
    head_sha: str
    external_id: str
    check_run_id: int | None = None


_EVIDENCE_RADIUS = 4
_EVIDENCE_MAX_LINES = 40


def _evidence_centers(snapshot: Snapshot, path: str, lines: Sequence[str]) -> list[int]:
    """근거 파일에서 보여줄 중심 줄들 — 피호출자 정의와 상수를 쓰는 본문 줄, 그 파일의 상수 선언, 없으면 hunk 시작."""
    centers: list[int] = []
    stripped = [line.strip() for line in lines]
    for callee in snapshot.structure.callees:
        if callee.defined_in != path:
            continue
        centers.append(callee.line)
        # 발췌 줄(상수를 실제로 쓰는 줄)이 정의보다 멀리 있으면 그 줄도 보여야 "무엇을 하는지"가 보인다.
        for excerpt in callee.excerpt:
            if excerpt.strip() in stripped:
                centers.append(stripped.index(excerpt.strip()) + 1)
        for constant in callee.constants:
            name = constant.split("=", 1)[0].strip()
            for index, line in enumerate(lines, start=1):
                if line.startswith(f"{name} ") or line.startswith(f"{name}=") or line.startswith(f"{name}:"):
                    centers.append(index)
                    break
    if not centers:
        for hunk in snapshot.risk.top_hunks:
            if hunk.file == path:
                centers.append(hunk.new_start)
                break
    return centers or [1]


def excerpt_segments(
    lines: Sequence[str],
    centers: Sequence[int],
    *,
    radius: int = _EVIDENCE_RADIUS,
    max_lines: int = _EVIDENCE_MAX_LINES,
) -> list[dict[str, object]]:
    """중심 줄들 주변을 잘라 겹치는 구간은 합친다. 파일 순서대로, 총 줄 수 상한 안에서."""
    total = len(lines)
    windows: list[tuple[int, int]] = []
    for center in sorted(set(centers)):
        start = max(1, center - radius)
        end = min(total, center + radius)
        if start > end:
            continue
        if windows and start <= windows[-1][1] + 1:
            windows[-1] = (windows[-1][0], max(windows[-1][1], end))
        else:
            windows.append((start, end))
    segments: list[dict[str, object]] = []
    budget = max_lines
    for start, end in windows:
        if budget <= 0:
            break
        end = min(end, start + budget - 1)
        segments.append({"start": start, "lines": list(lines[start - 1:end])})
        budget -= end - start + 1
    return segments


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

    def sync(
        self,
        pr: int,
        *,
        expected_binding: Mapping[str, object] | None = None,
        regenerate: bool = False,
    ) -> dict[str, object]:
        """PR 을 다시 읽어 저장한다.

        regenerate 는 운영자가 결함 있는 질문 세트를 버리고 같은 스냅샷에서 다시 뽑을 때 쓴다.
        이미 답변 영수증이 있는 스냅샷의 질문은 바꾸지 않는다 — 영수증이 가리키는 질문이 사라진다.
        """
        with self._lock:
            pull = self._pull_facts(pr)
            if pull.state != "open":
                if pull.merged:
                    return self.sync_merged(pr, pull=pull)
                self._queue_closed_projection(pr, pull=pull, now=self._now_iso())
                return {"state": "closed", "pr": pr, "measured": False}

            snapshot = self._read_snapshot(pr)
            if expected_binding is not None:
                self._require_binding_match(snapshot.binding(), expected_binding)

            existing = self.store.load_snapshot(snapshot.snapshot_id)
            now = self._now_iso()
            if not snapshot.risk.triggered:
                record = self.store.save_snapshot(snapshot, (), "neutral", now=now)
                self._queue_current_projection(record, expected_binding=expected_binding, now=now)
                return {
                    "state": "neutral",
                    "snapshot_id": record.snapshot.snapshot_id,
                    "question_version": record.question_version,
                    "question_count": record.question_count,
                }

            if regenerate and existing is not None and self.store.load_receipts_for_snapshot(snapshot.snapshot_id):
                raise BotError("Questions with receipts cannot be regenerated", code="invalid_state", status_code=409)
            reuse = existing is not None and existing.question_count == self.settings.question_count and not regenerate
            if reuse:
                record = self.store.save_snapshot(
                    snapshot,
                    [item.question for item in existing.questions],
                    "pending",
                    now=now,
                )
                self.store.clear_snapshot_preparation_error(snapshot.snapshot_id, now=now)
            else:
                record = self.store.save_snapshot(
                    snapshot,
                    (),
                    "pending",
                    now=now,
                )
                self._queue_current_projection(record, expected_binding=expected_binding, now=now)
                try:
                    questions = self._validated_questions(snapshot)
                except BotError as error:
                    error_now = self._now_iso()
                    self.store.mark_snapshot_preparation_error(
                        snapshot.snapshot_id,
                        code=error.code,
                        message=str(error),
                        now=error_now,
                    )
                    self._queue_current_projection(record, expected_binding=expected_binding, now=error_now)
                    raise
                self._assert_pull_current(snapshot, self._pull_facts(pr))
                record = self.store.save_snapshot(snapshot, questions, "pending", now=now)
                self.store.clear_snapshot_preparation_error(snapshot.snapshot_id, now=now)

            self._queue_current_projection(record, expected_binding=expected_binding, now=now)

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
                "reasons": list(reason_lines(record.snapshot, self.settings.presentation_locale)),
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

    def receipt_publication(self, receipt_id: str) -> dict[str, object]:
        with self._lock:
            receipt = self._receipt_or_404(receipt_id)
            snapshot = self._snapshot_or_404(receipt.snapshot_id)
            self._require_trusted_receipt(receipt, snapshot.snapshot)
            receipt_facts = (
                receipt.pr,
                receipt.repo.casefold(),
                receipt.head_sha,
                receipt.base_sha,
                receipt.policy_version,
                receipt.actor_id,
            )
            snapshot_facts = (
                snapshot.snapshot.pr,
                snapshot.snapshot.repo.casefold(),
                snapshot.snapshot.head_sha,
                snapshot.snapshot.base_sha,
                snapshot.snapshot.policy_version,
                snapshot.snapshot.author_id,
            )
            if receipt_facts != snapshot_facts:
                raise BotError(
                    "receipt binding mismatch",
                    code="stale",
                    status_code=409,
                )
            current = self.store.load_current_snapshot(receipt.pr)
            if (
                current is None
                or current.snapshot.snapshot_id != receipt.snapshot_id
                or current.question_version != receipt.question_version
            ):
                raise BotError(
                    "receipt is not current",
                    code="stale",
                    status_code=409,
                )

            target_url = self._receipt_url(receipt.receipt_id)
            gate: dict[str, object] = {
                "context": self.settings.status_context,
                "target_url": target_url,
                "state": "waiting_verification",
                "status_id": None,
                "error_code": None,
            }
            if receipt.verified:
                event_id = self._status_event_id(
                    "success-status",
                    receipt.receipt_id,
                    target_url,
                )
                event = next(
                    (
                        candidate
                        for candidate in self.store.load_receipt_publications(
                            receipt.receipt_id
                        )
                        if candidate.event_id == event_id
                    ),
                    None,
                )
                gate = self._receipt_gate_payload(
                    receipt,
                    snapshot,
                    target_url,
                    event,
                )
            return {
                "receipt": {
                    "receipt_id": receipt.receipt_id,
                    "binding": snapshot.snapshot.binding(),
                    "actor_id": receipt.actor_id,
                    "question_version": receipt.question_version,
                    "issued_at": receipt.created_at,
                    "app_id": receipt.app_id,
                    "installation_id": receipt.installation_id,
                },
                "verified_at": receipt.verified_at,
                "gate": gate,
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
                if self._is_presentation_event(event) and event.attempts >= _MAX_PRESENTATION_ATTEMPTS:
                    self.store.mark_publication_terminal(
                        event.event_id,
                        now=now,
                        error_code=event.last_error_code or "presentation_retry_exhausted",
                        error_message=event.last_error or "Presentation delivery retry limit reached.",
                        remote={"skipped": True, "reason": "retry_exhausted"},
                    )
                    processed += 1
                    continue
                try:
                    outcome = self._deliver_event(event)
                except (SnapshotError, json.JSONDecodeError):
                    self.store.mark_publication_retry(
                        event.event_id,
                        now=now,
                        due_at=self._future_iso(event.attempts + 1),
                        error_code="stored_snapshot_invalid",
                        error_message="Stored snapshot could not be restored.",
                    )
                    continue
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
                    if self._is_presentation_event(event) and event.attempts + 1 >= _MAX_PRESENTATION_ATTEMPTS:
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
                        if self._is_presentation_event(event) and event.attempts + 1 >= _MAX_PRESENTATION_ATTEMPTS:
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
                self._queue_closed_projection(pr, pull=current, now=self._now_iso())
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
            self._queue_closed_projection(pr, pull=current, now=self._now_iso())
            return {
                "state": "merged",
                "pr": pr,
                "measured": measured,
                "snapshot_id": snapshot_id,
                "merged_at": current.merged_at,
            }

    def dashboard(self, *, days: int = 30) -> dict[str, object]:
        """구역 단위 이해 커버리지. 사람 이름은 CODEOWNERS 담당 외에 내보내지 않는다."""
        with self._lock:
            if days <= 0:
                raise BotError("days must be positive", code="invalid_request", status_code=400)
            since = self._iso_from_timestamp(self.clock() - days * 86400)
            merges = self.store.load_merges_since(since=since)
            totals = {"merged": 0, "gated": 0, "attested": 0, "forced": 0, "waiting": 0}
            measured_total = 0
            unmeasured_total = 0
            live: dict[str, ZoneCounts] = {}
            zone_answerers: dict[str, set[int]] = {}
            all_zones: list[str] = []
            latest_base_sha: str | None = None
            latest_merged_at = ""
            for merge in merges:
                totals["merged"] += 1
                if not merge.measured or not merge.snapshot_id:
                    unmeasured_total += 1
                    continue
                snapshot = self.store.load_snapshot(merge.snapshot_id)
                if snapshot is None:
                    unmeasured_total += 1
                    continue
                measured_total += 1
                if (merge.merged_at or "") >= latest_merged_at:
                    latest_merged_at = merge.merged_at or ""
                    latest_base_sha = snapshot.snapshot.base_sha
                    all_zones = list(snapshot.snapshot.zones)
                touched = {
                    zone
                    for file_change in snapshot.snapshot.diff.files
                    if (zone := zone_of(file_change.file, snapshot.snapshot.zones))
                }
                for zone in touched:
                    row = live.setdefault(zone, ZoneCounts(zone=zone))
                    row.merged += 1
                    if merge.pr not in row.prs:
                        row.prs.append(merge.pr)
                if not snapshot.snapshot.risk.triggered:
                    continue
                totals["gated"] += 1
                for zone in touched:
                    live[zone].gated += 1
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
                    totals["attested"] += 1
                else:
                    # 게이트가 걸렸는데 유효한 인증 없이 머지됐다 — 우회. 누가가 아니라 어디에.
                    totals["forced"] += 1
                    for zone in touched:
                        live[zone].forced += 1
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
                    live[zone].attested += 1
            for zone, actors in zone_answerers.items():
                live[zone].answerers = len(actors)

            # 대기: 현재 snapshot 이 pending 이고 머지되지 않은 PR. 머지된 것과 섞지 않는다.
            pending = self.store.load_pending_unmerged_prs()
            totals["waiting"] = len(pending)
            if not all_zones:
                for _pr, snapshot_id in pending[-1:]:
                    current = self.store.load_snapshot(snapshot_id)
                    if current is not None:
                        all_zones = list(current.snapshot.zones)
                        latest_base_sha = current.snapshot.base_sha

            owners: dict[str, str] = {}
            zone_owners = getattr(self.reader, "zone_owners", None)
            if latest_base_sha and callable(zone_owners):
                try:
                    owners = dict(zone_owners(latest_base_sha))
                except Exception:  # pylint: disable=broad-exception-caught
                    # 담당 열은 보조 정보다. 읽기 실패로 대시보드를 막지 않는다.
                    owners = {}

            seed = load_seed(self.settings.demo_seed) if self.settings.demo_seed else None
            payload = finalize(
                live,
                all_zones=all_zones,
                owners=owners,
                totals=totals,
                seed=seed,
                min_sample=MIN_SAMPLE,
            )
            payload.update(
                {
                    "repo": self.settings.repository,
                    "generated_at": self._now_iso(),
                    "window_days": days,
                    "min_sample": MIN_SAMPLE,
                    "measured_total": measured_total,
                    "unmeasured_total": unmeasured_total,
                }
            )
            return payload

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
                "publication": self._receipt_publication_payload(receipt),
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
        feedback: list[dict[str, object]] = []
        successful: list[ReceiptAnswer] = []
        for payload in answers:
            with self._lock:
                if self._active_job(job_id) is None:
                    return
            question = by_id[str(payload["id"])]
            text = str(payload["text"])
            choice = payload.get("choice")
            if not text.strip() or (question.question.choices and choice is None):
                blank: dict[str, object] = {
                    "id": question.id,
                    "hint": f"inspect {question.question.anchor}",
                }
                evidence = self._evidence_for(record, question.question)
                if evidence is not None:
                    blank["evidence"] = evidence
                feedback.append(blank)
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
                item: dict[str, object] = {
                    "id": question.id,
                    "hint": self._safe_hint(question.question.anchor, graded.hint),
                }
                # 보류일 때만 근거 파일을 열어 준다. 정답이 아니라 어디를 볼지다.
                evidence = self._evidence_for(record, question.question)
                if evidence is not None:
                    item["evidence"] = evidence
                feedback.append(item)
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
                job.accepted = [answer.question_id for answer in successful]
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
            self._queue_current_projection(record, now=self._now_iso())
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

    def _queue_current_projection(
        self,
        record: StoredSnapshot,
        *,
        expected_binding: Mapping[str, object] | None = None,
        phase_override: PresentationPhase | None = None,
        closed_pull: PullFacts | None = None,
        now: str,
    ) -> None:
        for publication in self._projection_publications(
            record,
            expected_binding=expected_binding,
            phase_override=phase_override,
            closed_pull=closed_pull,
        ):
            self.store.queue_publication(publication, now=now)

    def _queue_closed_projection(self, pr: int, *, pull: PullFacts, now: str) -> None:
        record = self.store.load_current_snapshot(pr)
        if record is None:
            return
        phase, _ = self._closed_presentation_projection(record, pull)
        self._queue_current_projection(record, phase_override=phase, closed_pull=pull, now=now)

    def _projection_publications(
        self,
        record: StoredSnapshot,
        *,
        expected_binding: Mapping[str, object] | None = None,
        phase_override: PresentationPhase | None = None,
        closed_pull: PullFacts | None = None,
    ) -> tuple[PublicationRequest, ...]:
        phase, receipt = self._presentation_projection(record, phase_override=phase_override)
        snapshot = record.snapshot
        publications: list[PublicationRequest] = []
        publications.extend(self._superseded_check_publications(snapshot, phase))
        presentation_payload: dict[str, object] = {"phase": phase}
        if closed_pull is not None:
            presentation_payload["closed"] = self._closed_publication_payload(closed_pull)

        card_kind = "start_comment" if phase in {"preparing", "awaiting_author", "error"} else "presentation_card"
        card_receipt_id = receipt.receipt_id if receipt is not None else None
        if phase == "verified" and receipt is not None:
            card_kind = "success_comment"
        publications.append(
            PublicationRequest(
                event_id=self._presentation_card_event_id(snapshot, phase, card_receipt_id),
                kind=card_kind,
                pr=snapshot.pr,
                snapshot_id=snapshot.snapshot_id,
                receipt_id=card_receipt_id,
                payload=presentation_payload,
                requeue_failed_sent=True,
            )
        )

        if phase not in {"neutral", "closed"} and self._can_publish_status():
            publications.append(
                PublicationRequest(
                    event_id=self._status_event_id("pending-status", snapshot.snapshot_id, self._pr_url(snapshot.pr)),
                    kind="pending_status",
                    pr=snapshot.pr,
                    snapshot_id=snapshot.snapshot_id,
                    payload={
                        "description": "Awaiting author explanation",
                        "target_url": self._pr_url(snapshot.pr),
                    },
                )
            )
        if phase == "neutral" and expected_binding is not None and self._can_publish_status():
            publications.append(
                PublicationRequest(
                    event_id=self._status_event_id("neutral-status", snapshot.snapshot_id, self._pr_url(snapshot.pr)),
                    kind="neutral_status",
                    pr=snapshot.pr,
                    snapshot_id=snapshot.snapshot_id,
                    payload={
                        "description": "Not required — below risk threshold",
                        "target_url": self._pr_url(snapshot.pr),
                    },
                )
            )
        if phase == "verified" and receipt is not None and self._can_publish_status():
            publications.append(
                PublicationRequest(
                    event_id=self._status_event_id(
                        "success-status", receipt.receipt_id, self._receipt_url(receipt.receipt_id)
                    ),
                    kind="success_status",
                    pr=snapshot.pr,
                    snapshot_id=snapshot.snapshot_id,
                    receipt_id=receipt.receipt_id,
                    payload={
                        "description": "Human-verified",
                        "target_url": self._receipt_url(receipt.receipt_id),
                    },
                )
            )
        if self._can_publish_checks():
            publications.append(
                PublicationRequest(
                    event_id=self._presentation_check_event_id(snapshot, phase, card_receipt_id),
                    kind="presentation_check",
                    pr=snapshot.pr,
                    snapshot_id=snapshot.snapshot_id,
                    receipt_id=card_receipt_id,
                    payload=presentation_payload,
                    requeue_failed_sent=True,
                )
            )
        return tuple(publications)

    def _superseded_check_publications(
        self,
        snapshot: Snapshot,
        phase: PresentationPhase,
    ) -> tuple[PublicationRequest, ...]:
        if not self._can_publish_checks():
            return ()
        cancel_phase: PresentationPhase = "closed" if phase == "closed" else "superseded"
        targets: dict[str, PresentationCheckCancelTarget] = {}
        active = self.store.load_presentation_check_run(snapshot.pr)
        if (
            active is not None
            and active.snapshot_id != snapshot.snapshot_id
            and not (active.status == "completed" and active.conclusion is not None)
            and self.store.load_snapshot(active.snapshot_id) is not None
        ):
            targets[active.external_id] = PresentationCheckCancelTarget(
                snapshot_id=active.snapshot_id,
                head_sha=active.head_sha,
                external_id=active.external_id,
                check_run_id=active.check_run_id,
            )
        for event in self.store.load_unresolved_presentation_check_publications(
            pr=snapshot.pr,
            excluding_snapshot_id=snapshot.snapshot_id,
        ):
            target = self._presentation_check_cancel_target_from_intent(event)
            if target is None:
                continue
            existing = targets.get(target.external_id)
            if existing is None or (existing.check_run_id is None and target.check_run_id is not None):
                targets[target.external_id] = target
        return tuple(
            self._presentation_check_cancel_publication(
                snapshot.pr,
                current_snapshot_id=snapshot.snapshot_id,
                phase=cancel_phase,
                target=target,
            )
            for target in targets.values()
        )

    def _deliver_event(self, event: OutboxEvent) -> Mapping[str, object]:
        if event.kind == "verifier_dispatch":
            receipt = self.store.load_receipt(event.receipt_id or "")
            if receipt is None or receipt.verified:
                return {"skipped": True, "reason": "already_verified"}
            self.github.dispatch_verification(receipt.receipt_id)
            return {"receipt_id": receipt.receipt_id}

        if event.kind in {"start_comment", "success_comment", "presentation_card"}:
            return self._deliver_presentation_card(event)

        if event.kind == "presentation_check":
            return self._deliver_presentation_check(event)

        if event.kind == "presentation_check_cancel":
            return self._deliver_presentation_check_cancel(event)

        if event.kind in {"pending_status", "neutral_status"}:
            snapshot = self.store.load_snapshot(event.snapshot_id or "")
            if snapshot is None:
                return {"skipped": True, "reason": "missing_snapshot"}
            current = self.store.load_current_snapshot(snapshot.snapshot.pr)
            if current is None or current.snapshot.snapshot_id != snapshot.snapshot.snapshot_id:
                return {"skipped": True, "reason": "stale_snapshot"}
            if not self._event_pull_is_current(snapshot.snapshot):
                return {"skipped": True, "reason": "stale_snapshot"}
            if not self._snapshot_binding_is_current(snapshot.snapshot):
                return {"skipped": True, "reason": "stale_snapshot"}
            if self._current_verified_receipt(current.snapshot.pr) is not None:
                return {"skipped": True, "reason": "already_verified"}
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

        if event.kind == "success_status":
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
                if not self._verified_receipt_matches_closed_pull(receipt, snapshot, current):
                    return {"skipped": True, "reason": "stale_snapshot"}
            else:
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
            if not self._can_publish_status():
                return {"skipped": True, "reason": "local_only"}
            return self.github.set_status(
                snapshot.snapshot.head_sha,
                "success",
                str(event.payload["description"]),
                str(event.payload["target_url"]),
            )

        raise BotError("unknown publication kind", code="invalid_state", status_code=500)

    def _deliver_presentation_card(self, event: OutboxEvent) -> Mapping[str, object]:
        record = self._presentation_record_for_event(event)
        if record is None:
            return {"skipped": True, "reason": "missing_snapshot"}
        requested_phase = self._event_phase(event)
        if self._uses_closed_publication_context(event):
            current = self._closed_publication_pull(event, record)
            if current is None:
                return {"skipped": True, "reason": "stale_snapshot"}
            if not self._presentation_event_is_current(event, record):
                return {"skipped": True, "reason": "stale_snapshot"}
            phase, receipt = self._closed_delivery_projection(record, current, requested_phase)
            if phase is None:
                return {"skipped": True, "reason": "stale_snapshot"}
        else:
            if not self._presentation_event_is_current(event, record):
                return {"skipped": True, "reason": "stale_snapshot"}
            phase, receipt = self._presentation_projection(record)
            if not self._event_pull_is_current(record.snapshot):
                current = self._closed_publication_pull(event, record)
                if not self._verified_projection_matches_closed_pull(record, phase, receipt, current):
                    return {"skipped": True, "reason": "stale_snapshot"}
            else:
                if not self._snapshot_binding_is_current(record.snapshot):
                    return {"skipped": True, "reason": "stale_snapshot"}
        if phase in {"neutral", "closed"} and self.github.find_pr_card(record.snapshot.pr) is None:
            return {"skipped": True, "reason": "no_existing_card"}
        rendered = self._render_presentation(
            record,
            phase,
            None if receipt is None else receipt.receipt_id,
        )
        outcome = dict(self.github.ensure_pr_card(record.snapshot.pr, rendered.body))
        outcome["phase"] = phase
        outcome["channel"] = "card"
        return outcome

    def _deliver_presentation_check(self, event: OutboxEvent) -> Mapping[str, object]:
        if not self._can_publish_checks():
            return {"skipped": True, "reason": "local_only"}
        record = self._presentation_record_for_event(event)
        if record is None:
            return {"skipped": True, "reason": "missing_snapshot"}
        requested_phase = self._event_phase(event)
        if self._uses_closed_publication_context(event):
            current = self._closed_publication_pull(event, record)
            if current is None:
                return {"skipped": True, "reason": "stale_snapshot"}
            if not self._presentation_event_is_current(event, record):
                return {"skipped": True, "reason": "stale_snapshot"}
            phase, receipt = self._closed_delivery_projection(record, current, requested_phase)
            if phase is None:
                return {"skipped": True, "reason": "stale_snapshot"}
        else:
            if not self._presentation_event_is_current(event, record):
                return {"skipped": True, "reason": "stale_snapshot"}
            phase, receipt = self._presentation_projection(record)
            if not self._event_pull_is_current(record.snapshot):
                current = self._closed_publication_pull(event, record)
                if not self._verified_projection_matches_closed_pull(record, phase, receipt, current):
                    return {"skipped": True, "reason": "stale_snapshot"}
            else:
                if not self._snapshot_binding_is_current(record.snapshot):
                    return {"skipped": True, "reason": "stale_snapshot"}
        rendered = self._render_presentation(
            record,
            phase,
            None if receipt is None else receipt.receipt_id,
        )
        external_id = self._presentation_check_external_id(record.snapshot)
        outcome = dict(
            self.github.ensure_check_run(
                record.snapshot.head_sha,
                external_id,
                status=rendered.check_status,
                conclusion=rendered.check_conclusion,
                title=rendered.title,
                summary=rendered.summary,
                details_url=rendered.details_url,
            )
        )
        self.store.save_presentation_check_run(
            pr=record.snapshot.pr,
            snapshot_id=record.snapshot.snapshot_id,
            head_sha=record.snapshot.head_sha,
            external_id=external_id,
            check_run_id=_positive_int(outcome.get("id"), "check run id"),
            status=str(outcome.get("status", rendered.check_status)),
            conclusion=_optional_str(outcome.get("conclusion")),
            now=self._now_iso(),
        )
        outcome["phase"] = phase
        outcome["channel"] = "check"
        return outcome

    def _deliver_presentation_check_cancel(self, event: OutboxEvent) -> Mapping[str, object]:
        if not self._can_publish_checks():
            return {"skipped": True, "reason": "local_only"}
        record = self.store.load_snapshot(event.snapshot_id or "")
        if record is None:
            return {"skipped": True, "reason": "missing_snapshot"}
        phase = self._event_phase(event)
        if phase not in {"closed", "superseded"}:
            phase = "superseded"
        sha = _sha(event.payload.get("head_sha"), "check run head sha")
        external_id = _nonempty_str(event.payload.get("external_id"), "check run external id")
        current = self.store.load_current_snapshot(event.pr)
        if (
            current is not None
            and self._presentation_check_external_id(current.snapshot) == external_id
        ):
            return {
                "skipped": True,
                "reason": "current_snapshot",
                "head_sha": sha,
                "external_id": external_id,
                "phase": phase,
                "channel": "check",
                "cancelled": False,
            }
        rendered = self._render_presentation(record, phase)
        existing = self.github.find_check_run(sha, external_id)
        if existing is None:
            return {
                "skipped": True,
                "reason": "not_published",
                "published": False,
                "head_sha": sha,
                "external_id": external_id,
                "phase": phase,
                "channel": "check",
                "cancelled": False,
            }
        if existing.get("status") == "completed" and existing.get("conclusion") is not None:
            outcome = dict(existing)
            outcome["phase"] = phase
            outcome["channel"] = "check"
            outcome["cancelled"] = existing.get("conclusion") == "cancelled"
            if existing.get("conclusion") == "success":
                outcome["preserved"] = True
            return outcome
        outcome = dict(
            self.github.cancel_check_run(
                _positive_int(existing.get("id"), "check run id"),
                sha=sha,
                external_id=external_id,
                title=rendered.title,
                summary=rendered.summary,
                details_url=rendered.details_url,
            )
        )
        outcome["phase"] = phase
        outcome["channel"] = "check"
        outcome["cancelled"] = True
        return outcome

    def _presentation_check_cancel_target_from_intent(
        self,
        event: OutboxEvent,
    ) -> PresentationCheckCancelTarget | None:
        if event.snapshot_id is None:
            return None
        record = self.store.load_snapshot(event.snapshot_id)
        if record is None:
            return None
        external_id = self._presentation_check_external_id(record.snapshot)
        check_run_id = self._presentation_check_run_id_from_remote(
            event.remote,
            head_sha=record.snapshot.head_sha,
            external_id=external_id,
        )
        return PresentationCheckCancelTarget(
            snapshot_id=record.snapshot.snapshot_id,
            head_sha=record.snapshot.head_sha,
            external_id=external_id,
            check_run_id=check_run_id,
        )

    def _presentation_check_cancel_publication(
        self,
        pr: int,
        *,
        current_snapshot_id: str,
        phase: PresentationPhase,
        target: PresentationCheckCancelTarget,
    ) -> PublicationRequest:
        payload: dict[str, object] = {
            "phase": phase,
            "head_sha": target.head_sha,
            "external_id": target.external_id,
        }
        if target.check_run_id is not None:
            payload["check_run_id"] = target.check_run_id
        return PublicationRequest(
            event_id=(
                "presentation-check-cancel:"
                f"{target.external_id}:{current_snapshot_id}:{phase}"
            ),
            kind="presentation_check_cancel",
            pr=pr,
            snapshot_id=target.snapshot_id,
            payload=payload,
            requeue_failed_sent=True,
        )

    def _presentation_check_run_id_from_remote(
        self,
        remote: Mapping[str, object] | None,
        *,
        head_sha: str,
        external_id: str,
    ) -> int | None:
        if remote is None:
            return None
        try:
            if remote.get("head_sha") != head_sha or remote.get("external_id") != external_id:
                return None
            return _positive_int(remote.get("id"), "check run id")
        except ValueError:
            return None

    def _presentation_record_for_event(self, event: OutboxEvent) -> StoredSnapshot | None:
        if event.snapshot_id is None:
            return self.store.load_current_snapshot(event.pr)
        return self.store.load_snapshot(event.snapshot_id)

    def _presentation_event_is_current(self, event: OutboxEvent, record: StoredSnapshot) -> bool:
        current = self.store.load_current_snapshot(record.snapshot.pr)
        if current is None:
            return False
        if current.snapshot.snapshot_id != record.snapshot.snapshot_id:
            return False
        if event.receipt_id is None:
            return True
        receipt = self.store.load_receipt(event.receipt_id)
        if receipt is None:
            return False
        return (
            receipt.snapshot_id == record.snapshot.snapshot_id
            and receipt.question_version == current.question_version
        )

    def _uses_closed_publication_context(self, event: OutboxEvent) -> bool:
        return self._event_phase(event) == "closed" or event.payload.get("closed") is not None

    def _closed_publication_pull(self, event: OutboxEvent, record: StoredSnapshot) -> PullFacts | None:
        current = self._pull_facts(record.snapshot.pr)
        if current.state != "closed":
            return None
        raw_closed = event.payload.get("closed")
        if raw_closed is None:
            return current if self._closed_pull_matches_record(record, current) else None
        closed = _mapping(raw_closed, "closed publication")
        if _nonempty_str(closed.get("state"), "closed publication state") != "closed":
            return None
        captured_head_sha = _sha(closed.get("head_sha"), "closed publication head sha")
        captured_base_sha = _sha(closed.get("base_sha"), "closed publication base sha")
        captured_author_id = _positive_int(closed.get("author_id"), "closed publication author id")
        captured_closed_at = _optional_str(closed.get("closed_at"))
        if (
            current.merged != _bool(closed.get("merged"), default=False)
            or current.head_sha != captured_head_sha
            or (not current.merged and current.base_sha != captured_base_sha)
            or current.author_id != captured_author_id
        ):
            return None
        if captured_closed_at is not None and current.closed_at != captured_closed_at:
            return None
        if (
            record.snapshot.head_sha != captured_head_sha
            or record.snapshot.author_id != captured_author_id
            or (not current.merged and record.snapshot.base_sha != captured_base_sha)
        ):
            return None
        return current

    def _closed_pull_matches_record(self, record: StoredSnapshot, pull: PullFacts) -> bool:
        if pull.state != "closed":
            return False
        return pull.head_sha == record.snapshot.head_sha and pull.author_id == record.snapshot.author_id

    def _verified_projection_matches_closed_pull(
        self,
        record: StoredSnapshot,
        phase: PresentationPhase,
        receipt: StoredReceipt | None,
        current: PullFacts | None,
    ) -> bool:
        return (
            phase == "verified"
            and receipt is not None
            and current is not None
            and self._verified_receipt_matches_closed_pull(receipt, record, current)
        )

    def _verified_receipt_matches_closed_pull(
        self,
        receipt: StoredReceipt,
        record: StoredSnapshot,
        current: PullFacts,
    ) -> bool:
        return (
            receipt.verified
            and current.state == "closed"
            and current.head_sha == receipt.head_sha
            and current.head_sha == record.snapshot.head_sha
            and current.author_id == record.snapshot.author_id
        )

    def _snapshot_binding_is_current(self, snapshot: Snapshot) -> bool:
        fresh = self._read_snapshot(snapshot.pr)
        try:
            self._require_binding_match(snapshot.binding(), fresh.binding())
        except BotError as error:
            if error.code == "stale":
                return False
            raise
        return True

    def _event_phase(self, event: OutboxEvent) -> PresentationPhase | None:
        raw = event.payload.get("phase")
        if raw in {
            "preparing",
            "awaiting_author",
            "verifying",
            "verified",
            "neutral",
            "closed",
            "superseded",
            "error",
        }:
            return cast(PresentationPhase, raw)
        return None

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
            payload["accepted"] = list(job.accepted)
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

    def _current_receipt_for_record(self, record: StoredSnapshot) -> StoredReceipt | None:
        return self.store.load_receipt_by_key(
            record.snapshot.snapshot_id,
            record.question_version,
            record.snapshot.author_id,
        )

    def _presentation_projection(
        self,
        record: StoredSnapshot,
        *,
        phase_override: PresentationPhase | None = None,
    ) -> tuple[PresentationPhase, StoredReceipt | None]:
        if phase_override is not None:
            return phase_override, self._current_receipt_for_record(record)
        snapshot = record.snapshot
        if record.state == "neutral" or not snapshot.risk.triggered:
            return "neutral", None
        if record.question_count != self.settings.question_count:
            if self.store.load_snapshot_preparation_error(snapshot.snapshot_id) is not None:
                return "error", None
            return "preparing", None
        receipt = self._current_receipt_for_record(record)
        if receipt is None:
            return "awaiting_author", None
        if receipt.verified:
            return "verified", receipt
        return "verifying", receipt

    def _closed_delivery_projection(
        self,
        record: StoredSnapshot,
        current: PullFacts,
        requested_phase: PresentationPhase | None,
    ) -> tuple[PresentationPhase | None, StoredReceipt | None]:
        if requested_phase == "verified":
            phase, receipt = self._presentation_projection(record)
            if phase == "verified" and receipt is not None and receipt.head_sha == current.head_sha:
                return phase, receipt
            return None, None
        if requested_phase == "closed":
            return self._closed_presentation_projection(record, current)
        return None, None

    def _closed_presentation_projection(
        self,
        record: StoredSnapshot,
        pull: PullFacts,
    ) -> tuple[PresentationPhase, StoredReceipt | None]:
        receipt = self._current_receipt_for_record(record)
        if (
            receipt is not None
            and receipt.verified
            and receipt.head_sha == record.snapshot.head_sha
            and receipt.head_sha == pull.head_sha
        ):
            return "verified", receipt
        return "closed", receipt

    def _closed_publication_payload(self, pull: PullFacts) -> dict[str, object]:
        return {
            "state": pull.state,
            "merged": pull.merged,
            "closed_at": pull.closed_at,
            "head_sha": pull.head_sha,
            "base_sha": pull.base_sha,
            "author_id": pull.author_id,
        }

    def _presentation_card_event_id(
        self,
        snapshot: Snapshot,
        phase: PresentationPhase,
        receipt_id: str | None,
    ) -> str:
        subject = receipt_id or snapshot.snapshot_id
        revision = presentation_revision(self.settings)
        return f"presentation-card:v2:{snapshot.pr}:{snapshot.snapshot_id}:{phase}:{subject}:{revision}"

    def _presentation_check_event_id(
        self,
        snapshot: Snapshot,
        phase: PresentationPhase,
        receipt_id: str | None,
    ) -> str:
        subject = receipt_id or snapshot.snapshot_id
        revision = presentation_revision(self.settings)
        return f"presentation-check:v2:{snapshot.pr}:{snapshot.snapshot_id}:{phase}:{subject}:{revision}"

    def _status_event_id(self, kind: str, subject: str, target_url: str) -> str:
        destination = self._sha256_json({"context": self.settings.status_context, "target_url": target_url})
        return f"{kind}:{subject}:{destination}"

    def _presentation_check_external_id(self, snapshot: Snapshot) -> str:
        return f"pr-{snapshot.pr}-snapshot-{snapshot.snapshot_id}"

    def _question_anchors(self, record: StoredSnapshot) -> tuple[str, ...]:
        return tuple(item.question.anchor for item in record.questions)

    def _render_presentation(
        self,
        record: StoredSnapshot,
        phase: PresentationPhase,
        receipt_id: str | None = None,
    ) -> RenderedPresentation:
        return render_presentation(
            PresentationView(
                snapshot=record.snapshot,
                phase=phase,
                question_anchors=self._question_anchors(record),
                receipt_id=receipt_id,
            ),
            self.settings,
        )

    def _success_publications(
        self,
        snapshot: Snapshot,
        receipt_id: str,
    ) -> tuple[PublicationRequest, ...]:
        publications: list[PublicationRequest] = [
            PublicationRequest(
                event_id=self._presentation_card_event_id(snapshot, "verified", receipt_id),
                kind="success_comment",
                pr=snapshot.pr,
                snapshot_id=snapshot.snapshot_id,
                receipt_id=receipt_id,
                payload={"phase": "verified"},
                requeue_failed_sent=True,
            )
        ]
        if self._can_publish_status():
            publications.append(
                PublicationRequest(
                    event_id=self._status_event_id("success-status", receipt_id, self._receipt_url(receipt_id)),
                    kind="success_status",
                    pr=snapshot.pr,
                    snapshot_id=snapshot.snapshot_id,
                    receipt_id=receipt_id,
                    payload={
                        "description": "Human-verified",
                        "target_url": self._receipt_url(receipt_id),
                    },
                )
            )
        if self._can_publish_checks():
            publications.append(
                PublicationRequest(
                    event_id=self._presentation_check_event_id(snapshot, "verified", receipt_id),
                    kind="presentation_check",
                    pr=snapshot.pr,
                    snapshot_id=snapshot.snapshot_id,
                    receipt_id=receipt_id,
                    payload={"phase": "verified"},
                    requeue_failed_sent=True,
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
                closed_at=_optional_str(data.get("closed_at")),
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

    def _can_publish_checks(self) -> bool:
        return self.settings.checks_enabled and self._can_publish_status()

    def _sha256_json(self, payload: object) -> str:
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _evidence_for(self, record: StoredSnapshot, question: Question) -> dict[str, object] | None:
        """보류된 문항의 근거 파일 발췌. 읽기에 실패하면 None — 보류를 막지 않는다.

        피호출자 정의 주변과, 그 파일의 상수 선언 줄 주변을 함께 보여준다.
        "전송 계층이 이미 3번 재시도한다"는 사실은 정의가 아니라 상수 줄에 있다.
        """
        path = question.evidence_path
        if not path:
            return None
        reader = getattr(self.reader, "read_file", None)
        if not callable(reader):
            return None
        try:
            lines = reader(record.snapshot.head_sha, path)
        except Exception:  # pylint: disable=broad-exception-caught
            return None
        if not lines:
            return None
        segments = excerpt_segments(lines, _evidence_centers(record.snapshot, path, lines))
        if not segments:
            return None
        return {"path": path, "segments": segments}

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
        presentation_events = self.store.load_presentation_publications(
            pr=receipt.pr,
            snapshot_id=receipt.snapshot_id,
            receipt_id=receipt.receipt_id,
        )
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
        presentation_payload = self._presentation_publication_payload(presentation_events)
        if presentation_payload:
            payload["presentation"] = presentation_payload
        return payload

    def _receipt_gate_payload(
        self,
        receipt: StoredReceipt,
        snapshot: StoredSnapshot,
        target_url: str,
        event: OutboxEvent | None,
    ) -> dict[str, object]:
        gate: dict[str, object] = {
            "context": self.settings.status_context,
            "target_url": target_url,
            "state": "missing",
            "status_id": None,
            "error_code": "publication_missing",
        }
        if event is None:
            return gate
        if (
            event.kind != "success_status"
            or event.pr != receipt.pr
            or event.snapshot_id != snapshot.snapshot.snapshot_id
            or event.receipt_id != receipt.receipt_id
            or event.payload.get("target_url") != target_url
        ):
            gate["state"] = "invalid"
            gate["error_code"] = "publication_invalid"
            return gate
        if event.status == "pending":
            gate["state"] = "waiting_publication"
            gate["error_code"] = _safe_publication_error_code(event.last_error_code)
            return gate
        if event.status != "sent":
            gate["state"] = "invalid"
            gate["error_code"] = "publication_invalid"
            return gate
        remote = event.remote
        if not isinstance(remote, dict):
            gate["state"] = "unavailable" if event.last_error_code else "invalid"
            gate["error_code"] = (
                _safe_publication_error_code(event.last_error_code)
                or "publication_invalid"
            )
            return gate
        if remote.get("skipped") is True:
            gate["state"] = "skipped"
            gate["error_code"] = (
                _safe_publication_error_code(remote.get("reason"))
                or _safe_publication_error_code(event.last_error_code)
                or "publication_skipped"
            )
            return gate
        if event.last_error_code is not None:
            gate["state"] = "unavailable"
            gate["error_code"] = (
                _safe_publication_error_code(event.last_error_code)
                or "publication_unavailable"
            )
            return gate
        status_id = remote.get("id")
        valid_status_id = isinstance(status_id, int) and not isinstance(status_id, bool) and status_id > 0
        remote_facts = (remote.get("context"), remote.get("target_url"), remote.get("state"))
        if not valid_status_id or remote_facts != (self.settings.status_context, target_url, "success"):
            gate["state"] = "invalid"
            gate["error_code"] = (
                _safe_publication_error_code(event.last_error_code)
                or "publication_invalid"
            )
            return gate
        gate["state"] = "published"
        gate["status_id"] = status_id
        gate["error_code"] = None
        return gate

    def _presentation_publication_payload(
        self,
        events: Sequence[OutboxEvent],
    ) -> dict[str, object]:
        channels: dict[str, dict[str, object]] = {}
        for event in events:
            if event.kind in {"pending_status", "neutral_status"}:
                continue
            channel = "check" if event.kind in {"presentation_check", "presentation_check_cancel"} else "card"
            current = channels.setdefault(
                channel,
                {
                    "pending": 0,
                    "applied": 0,
                    "skipped": 0,
                    "failed": 0,
                    "error_codes": set(),
                },
            )
            if event.last_error_code:
                current["error_codes"].add(event.last_error_code)
            if event.status == "pending":
                current["pending"] = int(current["pending"]) + 1
                if event.attempts > 0 or event.last_error_code is not None:
                    current["failed"] = int(current["failed"]) + 1
                continue
            if event.remote is not None and event.remote.get("skipped") is True:
                current["skipped"] = int(current["skipped"]) + 1
                if event.remote.get("reason") == "retry_exhausted":
                    current["failed"] = int(current["failed"]) + 1
                continue
            current["applied"] = int(current["applied"]) + 1
        payload: dict[str, object] = {}
        for channel, values in channels.items():
            channel_payload = {
                "pending": values["pending"],
                "applied": values["applied"],
                "skipped": values["skipped"],
                "failed": values["failed"],
            }
            codes = sorted(str(item) for item in values["error_codes"])
            if codes:
                channel_payload["error_codes"] = codes
            payload[channel] = channel_payload
        return payload

    def _is_presentation_event(self, event: OutboxEvent) -> bool:
        return event.kind in {
            "presentation_card",
            "presentation_check",
            "presentation_check_cancel",
            "start_comment",
            "success_comment",
        }

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
