from __future__ import annotations

import json
import stat
from dataclasses import dataclass
from pathlib import Path

import pytest

from lasthuman.server import relay

HEAD_SHA = "b" * 40
BASE_SHA = "a" * 40
POLICY_VERSION = "1" * 64
SNAPSHOT_ID = "2" * 64
RECEIPT_ID = "receipt-5"
TOKEN_URL = "https://token.actions.githubusercontent.com/id"
OIDC_URL = TOKEN_URL + "?audience=hunhoon21%2Fthe-last-human"


class FakeResponse:
    def __init__(self, status_code: int, payload: object | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = (
            b""
            if payload is None
            else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("empty")
        return self._payload


@dataclass(frozen=True)
class RecordedCall:
    method: str
    url: str
    json_body: object | None


class FakeSession:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list[FakeResponse]] = {}
        self.calls: list[RecordedCall] = []

    def enqueue(self, method: str, url: str, *responses: FakeResponse) -> None:
        self.routes.setdefault((method, url), []).extend(responses)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: object | None,
        timeout: tuple[float, float],
        allow_redirects: bool,
    ) -> FakeResponse:
        assert headers
        assert timeout[0] > 0
        assert timeout[1] > 0
        assert allow_redirects is False
        self.calls.append(RecordedCall(method, url, json))
        try:
            return self.routes[(method, url)].pop(0)
        except (KeyError, IndexError):
            raise AssertionError(f"unexpected request: {method} {url}") from None


class FakeGitHub:
    def __init__(self, settings: relay.ActionSettings) -> None:
        self.settings = settings
        self.timeout: tuple[float, float] = (5, 30)
        self.verify_calls = 0
        self.pull_calls = 0
        self.status_payloads: list[dict[str, object]] = []
        self.pull_payload: dict[str, object] = {
            "number": 7,
            "state": "open",
            "user": {"id": 7, "login": "octocat"},
            "head": {"sha": HEAD_SHA},
            "base": {"sha": BASE_SHA},
        }

    def verify_repository(self) -> None:
        self.verify_calls += 1

    def pull(self, pr: int) -> dict[str, object]:
        self.pull_calls += 1
        assert pr == 7
        return dict(self.pull_payload)

    def request(self, method: str, path: str) -> dict[str, object]:
        assert method == "GET"
        assert path.startswith(
            "repos/hunhoon21/the-last-human/commits/"
            f"{HEAD_SHA}/status?per_page=100&page="
        )
        if not self.status_payloads:
            raise AssertionError("missing status response")
        return self.status_payloads.pop(0)


@dataclass(frozen=True)
class FakeSnapshot:
    author_id: int

    def binding(self) -> dict[str, object]:
        return binding()


class FakeSnapshotReader:
    def __init__(self, github: object, cache_dir: Path) -> None:
        del github, cache_dir

    def read(self, pr: int) -> FakeSnapshot:
        assert pr == 7
        return FakeSnapshot(author_id=7)


class FakeTimer:
    def __init__(self) -> None:
        self.value = 1_000.0

    def monotonic(self) -> float:
        return self.value

    def monotonic_ns(self) -> int:
        return int(self.value * 1_000_000_000)

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def binding() -> dict[str, object]:
    return {
        "repository_id": 1361123778,
        "pr": 7,
        "head_sha": HEAD_SHA,
        "base_sha": BASE_SHA,
        "policy_version": POLICY_VERSION,
        "snapshot_id": SNAPSHOT_ID,
        "score": 80,
        "triggered": True,
    }


def receipt() -> dict[str, object]:
    return {
        "receipt_id": RECEIPT_ID,
        "binding": binding(),
        "actor_id": 7,
        "question_version": "v1",
        "issued_at": "2026-09-08T00:00:00Z",
        "app_id": 101,
        "installation_id": 202,
    }


def publication(
    state: str,
    *,
    verified_at: str | None = None,
    status_id: int | None = None,
    error_code: str | None = None,
) -> dict[str, object]:
    return {
        "receipt": receipt(),
        "verified_at": verified_at,
        "gate": {
            "context": "comprehension-gate",
            "target_url": f"https://bot.example/receipts/{RECEIPT_ID}",
            "state": state,
            "status_id": status_id,
            "error_code": error_code,
        },
    }


def context(*, run_attempt: str = "1") -> relay.ActionContext:
    settings = relay.ActionSettings(
        repository="hunhoon21/the-last-human",
        repository_id=1361123778,
        owner_id=36983960,
        workflow="lasthuman-app.yml",
        workflow_ref="refs/heads/main",
        bot_url="https://bot.example",
        oidc_audience="hunhoon21/the-last-human",
        github_token="workflow-token",
    )
    return relay.ActionContext(
        settings=settings,
        event_name="workflow_dispatch",
        event_path=Path("unused.json"),
        token_request_url=TOKEN_URL,
        token_request_token="request-token",
        run_id="1001",
        run_attempt=run_attempt,
    )


def test_five_verification_stages_write_only_bounded_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(relay, "SnapshotReader", FakeSnapshotReader)
    action_context = context()
    github = FakeGitHub(action_context.settings)
    session = FakeSession()
    state_file = tmp_path / "tlh-verification" / "state.json"
    event = {"inputs": {"receipt_id": RECEIPT_ID}}
    publication_url = (
        f"https://bot.example/api/actions/receipts/{RECEIPT_ID}/publication"
    )
    verify_url = f"https://bot.example/api/actions/receipts/{RECEIPT_ID}/verify"

    session.enqueue(
        "GET",
        OIDC_URL,
        FakeResponse(200, {"value": "oidc-receipt"}),
        FakeResponse(200, {"value": "oidc-verify"}),
        FakeResponse(200, {"value": "oidc-publication"}),
    )
    session.enqueue(
        "GET",
        publication_url,
        FakeResponse(200, publication("waiting_verification")),
        FakeResponse(
            200,
            publication(
                "published",
                verified_at="2026-09-08T12:00:00Z",
                status_id=55,
            ),
        ),
    )
    session.enqueue(
        "POST",
        verify_url,
        FakeResponse(
            202,
            {"job_id": "job-verify", "url": "/api/actions/jobs/job-verify"},
        ),
    )
    session.enqueue(
        "GET",
        "https://bot.example/api/actions/jobs/job-verify",
        FakeResponse(
            200,
            {
                "job_id": "job-verify",
                "state": "completed",
                "result": {
                    "state": "verified",
                    "receipt_id": RECEIPT_ID,
                    "pr": 7,
                    "verified_at": "2026-09-08T12:00:00Z",
                },
            },
        ),
    )
    github.status_payloads.append(
        {
            "sha": HEAD_SHA,
            "statuses": [
                {
                    "id": 80,
                    "context": "unrelated",
                    "state": "success",
                    "target_url": "https://example.com/other",
                },
                {
                    "id": 55,
                    "context": "comprehension-gate",
                    "state": "success",
                    "target_url": f"https://bot.example/receipts/{RECEIPT_ID}",
                },
            ],
        }
    )

    for step in ("receipt", "snapshot", "compare", "verify", "publication"):
        relay._run_verification_step(
            action_context,
            github,
            session,
            event,
            tmp_path / "cache",
            step,
            state_file,
        )
        saved = json.loads(state_file.read_text(encoding="utf-8"))
        assert saved["stage"] == step

    state_blob = state_file.read_text(encoding="utf-8")
    final_state = json.loads(state_blob)
    assert stat.S_IMODE(state_file.stat().st_mode) == 0o600
    assert len(state_blob.encode("utf-8")) < 64 * 1024
    assert final_state["run_id"] == "1001"
    assert final_state["run_attempt"] == "1"
    assert final_state["publication"]["status_id"] == 55
    for forbidden in (
        "oidc-receipt",
        "oidc-verify",
        "oidc-publication",
        "workflow-token",
        "request-token",
        "successful_answers",
        "question_text",
        "raw_diff",
    ):
        assert forbidden not in state_blob
    assert github.pull_calls == 2
    assert [call.method for call in session.calls].count("POST") == 1


def test_receipt_capability_404_does_not_create_or_advance_state(
    tmp_path: Path,
) -> None:
    action_context = context()
    session = FakeSession()
    state_file = tmp_path / "new-state" / "state.json"
    session.enqueue("GET", OIDC_URL, FakeResponse(200, {"value": "oidc"}))
    session.enqueue(
        "GET",
        f"https://bot.example/api/actions/receipts/{RECEIPT_ID}/publication",
        FakeResponse(404, {"error": "not found"}),
    )

    with pytest.raises(relay.RelayError, match="not supported"):
        relay._verification_receipt_step(
            action_context,
            session,
            RECEIPT_ID,
            state_file,
        )

    assert not state_file.exists()
    assert not state_file.parent.exists()
    assert all(call.method == "GET" for call in session.calls)


@pytest.mark.parametrize("status_code", [401, 403])
def test_receipt_capability_rejects_oidc_denial_without_state(
    tmp_path: Path,
    status_code: int,
) -> None:
    action_context = context()
    session = FakeSession()
    state_file = tmp_path / "state.json"
    session.enqueue("GET", OIDC_URL, FakeResponse(200, {"value": "oidc"}))
    session.enqueue(
        "GET",
        f"https://bot.example/api/actions/receipts/{RECEIPT_ID}/publication",
        FakeResponse(status_code, {"error": "denied"}),
    )

    with pytest.raises(relay.RelayError, match=f"HTTP {status_code}"):
        relay._verification_receipt_step(
            action_context,
            session,
            RECEIPT_ID,
            state_file,
        )

    assert not state_file.exists()


def test_receipt_stage_replaces_prior_attempt_with_fresh_state(
    tmp_path: Path,
) -> None:
    previous_context = context(run_attempt="1")
    current_context = context(run_attempt="2")
    state_file = tmp_path / "state.json"
    old_state = relay._new_verification_state(
        previous_context,
        "publication",
        receipt(),
    )
    old_state["snapshot"] = {"binding": binding(), "author_id": 7}
    old_state["verified_at"] = "2026-09-08T12:00:00Z"
    old_state["publication"] = {
        "context": "comprehension-gate",
        "target_url": f"https://bot.example/receipts/{RECEIPT_ID}",
        "status_id": 55,
    }
    relay._write_verification_state(state_file, old_state)
    session = FakeSession()
    session.enqueue("GET", OIDC_URL, FakeResponse(200, {"value": "fresh-oidc"}))
    session.enqueue(
        "GET",
        f"https://bot.example/api/actions/receipts/{RECEIPT_ID}/publication",
        FakeResponse(200, publication("waiting_verification")),
    )

    relay._verification_receipt_step(
        current_context,
        session,
        RECEIPT_ID,
        state_file,
    )

    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["stage"] == "receipt"
    assert saved["run_attempt"] == "2"
    assert "snapshot" not in saved
    assert "verified_at" not in saved
    assert "publication" not in saved


def test_malformed_waiting_verification_does_not_create_state(tmp_path: Path) -> None:
    session = FakeSession()
    session.enqueue("GET", OIDC_URL, FakeResponse(200, {"value": "oidc"}))
    session.enqueue(
        "GET",
        f"https://bot.example/api/actions/receipts/{RECEIPT_ID}/publication",
        FakeResponse(200, publication("waiting_verification", error_code="unexpected_error")),
    )
    state_file = tmp_path / "state.json"

    with pytest.raises(relay.RelayError, match="verification state is invalid"):
        relay._verification_receipt_step(context(), session, RECEIPT_ID, state_file)

    assert not state_file.exists()


def test_compare_rejects_tampering_without_advancing_state(tmp_path: Path) -> None:
    action_context = context()
    state_file = tmp_path / "state.json"
    state = relay._new_verification_state(
        action_context,
        "snapshot",
        receipt(),
    )
    state["snapshot"] = {"binding": binding(), "author_id": 99}
    relay._write_verification_state(state_file, state)

    with pytest.raises(relay.RelayError, match="actor mismatch"):
        relay._verification_compare_step(
            action_context,
            state_file,
            state,
        )

    assert json.loads(state_file.read_text(encoding="utf-8"))["stage"] == "snapshot"


def test_state_rejects_cross_attempt_and_symlink(tmp_path: Path) -> None:
    first_context = context(run_attempt="1")
    state_file = tmp_path / "state.json"
    relay._write_verification_state(
        state_file,
        relay._new_verification_state(first_context, "receipt", receipt()),
    )

    with pytest.raises(relay.RelayError, match="context mismatch"):
        relay._load_verification_state(
            state_file,
            context(run_attempt="2"),
            RECEIPT_ID,
            expected_stage="receipt",
        )

    symlink = tmp_path / "state-link.json"
    symlink.symlink_to(state_file)
    with pytest.raises(relay.RelayError, match="unsafe"):
        relay._write_verification_state(
            symlink,
            relay._new_verification_state(first_context, "receipt", receipt()),
        )


@pytest.mark.parametrize("version", [True, "1", 1.0, None, 2])
def test_state_rejects_noninteger_or_unknown_schema_versions(version: object) -> None:
    action_context = context()
    state = relay._new_verification_state(action_context, "receipt", receipt())
    state["schema_version"] = version

    with pytest.raises(relay.RelayError, match="state version"):
        relay._validate_verification_state(state, action_context, RECEIPT_ID, "receipt")


def test_receipt_rejects_invalid_calendar_timestamp() -> None:
    metadata = receipt()
    metadata["issued_at"] = "2026-99-99T00:00:00Z"

    with pytest.raises(relay.RelayError, match="issued_at"):
        relay._require_receipt_metadata(metadata, RECEIPT_ID, context().settings)


def test_binding_comparison_does_not_coerce_pr_number() -> None:
    metadata = receipt()
    metadata["binding"]["pr"] = "7"
    snapshot = {"binding": binding(), "author_id": 7}

    with pytest.raises(relay.RelayError, match="binding mismatch"):
        relay._compare_receipt_metadata(metadata, snapshot, context().settings)


def test_latest_context_status_never_scans_past_newer_pending() -> None:
    action_context = context()
    github = FakeGitHub(action_context.settings)
    github.status_payloads.append(
        {
            "sha": HEAD_SHA,
            "statuses": [
                {
                    "id": 56,
                    "context": "comprehension-gate",
                    "state": "pending",
                    "target_url": "https://bot.example/prs/7",
                },
                {
                    "id": 55,
                    "context": "comprehension-gate",
                    "state": "success",
                    "target_url": f"https://bot.example/receipts/{RECEIPT_ID}",
                },
            ],
        }
    )

    selected = relay._latest_context_status(
        github,
        HEAD_SHA,
        "comprehension-gate",
        relay.time.monotonic() + 10,
    )

    assert selected is not None
    assert selected["id"] == 56
    assert selected["state"] == "pending"


def test_combined_status_sha_mismatch_is_terminal() -> None:
    action_context = context()
    github = FakeGitHub(action_context.settings)
    github.status_payloads.append({"sha": "c" * 40, "statuses": []})

    with pytest.raises(relay.RelayError, match="SHA mismatch"):
        relay._latest_context_status(
            github,
            HEAD_SHA,
            "comprehension-gate",
            relay.time.monotonic() + 10,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("state", "closed", "no longer open"),
        ("user", {"id": 99}, "author mismatch"),
        ("head", {"sha": "c" * 40}, "head changed"),
        ("base", {"sha": "d" * 40}, "base changed"),
    ],
)
def test_current_pull_identity_checks_are_fail_closed(
    field: str,
    value: object,
    message: str,
) -> None:
    action_context = context()
    github = FakeGitHub(action_context.settings)
    github.pull_payload[field] = value

    with pytest.raises(relay.RelayError, match=message):
        relay._require_current_pull(github, binding(), 7)


def test_wrong_remote_status_id_times_out_without_advancing_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action_context = context()
    github = FakeGitHub(action_context.settings)
    github.status_payloads.append(
        {
            "sha": HEAD_SHA,
            "statuses": [
                {
                    "id": 999,
                    "context": "comprehension-gate",
                    "state": "success",
                    "target_url": f"https://bot.example/receipts/{RECEIPT_ID}",
                }
            ],
        }
    )
    session = FakeSession()
    session.enqueue("GET", OIDC_URL, FakeResponse(200, {"value": "oidc"}))
    session.enqueue(
        "GET",
        f"https://bot.example/api/actions/receipts/{RECEIPT_ID}/publication",
        FakeResponse(
            200,
            publication(
                "published",
                verified_at="2026-09-08T12:00:00Z",
                status_id=55,
            ),
        ),
    )
    state_file = tmp_path / "state.json"
    verified_state = relay._new_verification_state(
        action_context,
        "verify",
        receipt(),
    )
    verified_state["snapshot"] = {"binding": binding(), "author_id": 7}
    verified_state["verified_at"] = "2026-09-08T12:00:00Z"
    relay._write_verification_state(state_file, verified_state)
    timer = FakeTimer()
    monkeypatch.setattr(relay.time, "monotonic", timer.monotonic)
    monkeypatch.setattr(relay.time, "monotonic_ns", timer.monotonic_ns)
    monkeypatch.setattr(relay.time, "sleep", timer.sleep)
    monkeypatch.setattr(relay, "_PUBLICATION_POLL_TIMEOUT_SECONDS", 1.0)

    with pytest.raises(relay.RelayError, match="timed out"):
        relay._verification_publication_step(
            action_context,
            github,
            session,
            state_file,
            verified_state,
        )

    assert json.loads(state_file.read_text(encoding="utf-8"))["stage"] == "verify"


@pytest.mark.parametrize(
    ("first_id", "first_target"),
    [
        (999, None),
        (55, None),
        (55, "https://bot.example/receipts/another-receipt"),
        (999, f"https://bot.example/receipts/{RECEIPT_ID}"),
    ],
)
def test_publication_waits_for_exact_status_after_nonmatching_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_id: int,
    first_target: str | None,
) -> None:
    action_context = context()
    github = FakeGitHub(action_context.settings)
    expected_status = {
        "id": 55,
        "context": "comprehension-gate",
        "state": "success",
        "target_url": f"https://bot.example/receipts/{RECEIPT_ID}",
    }
    github.status_payloads.extend([
        {"sha": HEAD_SHA, "statuses": [dict(expected_status, id=first_id, target_url=first_target)]},
        {"sha": HEAD_SHA, "statuses": [expected_status]},
    ])
    session = FakeSession()
    session.enqueue("GET", OIDC_URL, FakeResponse(200, {"value": "oidc"}))
    published = publication("published", verified_at="2026-09-08T12:00:00Z", status_id=55)
    session.enqueue(
        "GET",
        f"https://bot.example/api/actions/receipts/{RECEIPT_ID}/publication",
        FakeResponse(200, published),
        FakeResponse(200, published),
    )
    state = relay._new_verification_state(action_context, "verify", receipt())
    state["snapshot"] = {"binding": binding(), "author_id": 7}
    state["verified_at"] = "2026-09-08T12:00:00Z"
    state_file = tmp_path / "state.json"
    relay._write_verification_state(state_file, state)
    timer = FakeTimer()
    monkeypatch.setattr(relay.time, "monotonic", timer.monotonic)
    monkeypatch.setattr(relay.time, "monotonic_ns", timer.monotonic_ns)
    monkeypatch.setattr(relay.time, "sleep", timer.sleep)

    relay._verification_publication_step(action_context, github, session, state_file, state)

    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["stage"] == "publication"
    assert saved["publication"]["status_id"] == 55
    assert timer.value == 1002.0
    assert not github.status_payloads
    assert github.pull_calls == 3


def test_publication_view_rejects_malformed_and_terminal_states() -> None:
    action_context = context()
    malformed = publication(
        "published",
        verified_at="2026-09-08T12:00:00Z",
        status_id=55,
    )
    malformed["gate"]["target_url"] = "https://evil.example/receipt"
    with pytest.raises(relay.RelayError, match="target URL mismatch"):
        relay._require_publication_view(
            malformed,
            RECEIPT_ID,
            action_context.settings,
        )

    terminal = publication(
        "skipped",
        verified_at="2026-09-08T12:00:00Z",
        error_code="stale_snapshot",
    )
    _receipt, _verified_at, gate = relay._require_publication_view(
        terminal,
        RECEIPT_ID,
        action_context.settings,
    )
    assert gate["state"] == "skipped"


def test_main_routes_optional_stage_arguments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    called: dict[str, object] = {}

    def fake_run(**kwargs: object) -> None:
        called.update(kwargs)

    monkeypatch.setattr(relay, "run", fake_run)
    state_file = tmp_path / "state.json"

    assert (
        relay.main(
            [
                "--verification-step",
                "receipt",
                "--state-file",
                str(state_file),
            ]
        )
        == 0
    )
    assert called == {
        "verification_step": "receipt",
        "state_file": state_file,
    }
    called.clear()
    assert relay.main([]) == 0
    assert called == {}
    called.clear()
    monkeypatch.setattr(relay.sys, "argv", ["pytest", "--unrelated-test-option"])
    assert relay.main() == 0
    assert called == {}
