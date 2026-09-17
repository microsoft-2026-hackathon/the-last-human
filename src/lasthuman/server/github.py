"""GitHub App API adapter."""

from __future__ import annotations

import re
import shlex
from base64 import b64decode
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Final, Literal, cast
from urllib.parse import quote, unquote, urlsplit

import jwt
import requests
import yaml

from lasthuman.config import parse_config
from .config import GatewaySettings, Settings
from .registration import RegistrationDeniedError, RegistrationOperationalError, RepositoryInstallation

JsonObject = dict[str, object]
JsonData = JsonObject | list[object]
StatusState = Literal["pending", "success", "error"]
CheckStatus = Literal["in_progress", "completed"]
CheckConclusion = Literal["success", "neutral", "cancelled", "action_required"]

_API_ORIGIN: Final = "https://api.github.com"
_ACCEPT: Final = "application/vnd.github+json"
_TIMEOUT: Final[tuple[int, int]] = (5, 30)
_MAX_RESPONSE_BYTES: Final = 2 * 1024 * 1024
_PER_PAGE: Final = 100
_MAX_PAGES: Final = 10
_TOKEN_REFRESH_SKEW: Final = timedelta(seconds=60)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_WORKFLOW_STATES = {"pending", "success", "error"}
_CHECK_STATUSES = {"in_progress", "completed"}
_COMPLETED_CHECK_CONCLUSIONS = {"success", "neutral", "cancelled", "action_required"}
_CHECK_EXTERNAL_ID_MAX = 512
_CHECK_OUTPUT_TEXT_MAX = 65_535
_PR_CARD_MARKER_PREFIX = "lasthuman:pr-card:"
_MARKER_RESERVATION_CHARS = 128
_REQUIRED_CORE_PERMISSIONS: Final[dict[str, str]] = {
    "contents": "read", "pull_requests": "write", "statuses": "write", "actions": "write",
}


class GitHubError(RuntimeError):
    """Sanitized GitHub API error."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        detail = message if status_code is None else f"{message} (HTTP {status_code})"
        super().__init__(detail)


class GitHubUncertainResultError(GitHubError):
    """A write may have completed, but the caller cannot prove it."""


class GitHubClient:
    """Single-repository GitHub App client."""

    api_origin: str = _API_ORIGIN
    timeout: tuple[int, int] = _TIMEOUT

    def __init__(
        self, settings: Settings, session: requests.Session | None = None,
        *, access_guard: Callable[[], None] | None = None,
    ) -> None:
        self.settings = settings
        self._session = requests.Session() if session is None else session
        self._access_guard = access_guard
        self._repo_path = f"repos/{settings.repository}"
        self._verification_lock = Lock()
        self._token_lock = Lock()
        self._check_token_lock = Lock()
        self._repository_verified = False
        self._repository_verification_expiry: datetime | None = None
        self._installation_token: str | None = None
        self._installation_token_expiry: datetime | None = None
        self._check_installation_token: str | None = None
        self._check_installation_token_expiry: datetime | None = None

    def request(
        self,
        method: str,
        relative_api_path: str,
        token_override: str | None = None,
        json: JsonObject | None = None,
    ) -> JsonData:
        self._guard_access()
        if method.upper() != "GET":
            raise GitHubError(
                "GitHub public request is read-only; use scoped methods for writes"
            )
        normalized_path = _normalize_relative_api_path(relative_api_path)
        token = token_override if token_override is not None else self.installation_token()
        return self._request_data(method, normalized_path, token=token, json=json)

    def repository_info(self, user_token: str | None = None) -> JsonObject:
        payload = self.request("GET", self._repo_path, token_override=user_token)
        repository = _require_object(payload, "repository response")
        return self._validate_repository(repository)

    def pull(self, pr: int, user_token: str | None = None) -> JsonObject:
        _validate_positive_number(pr, "pull request number")
        payload = self.request("GET", f"{self._repo_path}/pulls/{pr}", token_override=user_token)
        pull_request = _require_object(payload, "pull request response")
        number = _require_int(pull_request.get("number"), "number", "pull request")
        if number != pr:
            raise GitHubError("GitHub pull request number mismatch")
        base = _require_object(pull_request.get("base"), "pull request base")
        base_repo = _require_object(base.get("repo"), "pull request base repository")
        self._validate_repository(base_repo)
        head = _require_object(pull_request.get("head"), "pull request head")
        _require_sha(head.get("sha"), "pull request head sha")
        return pull_request

    def files(self, pr: int) -> list[JsonObject]:
        _validate_positive_number(pr, "pull request number")
        collected: list[JsonObject] = []
        for page in range(1, _MAX_PAGES + 1):
            payload = self.request(
                "GET",
                f"{self._repo_path}/pulls/{pr}/files?per_page={_PER_PAGE}&page={page}",
            )
            page_items = _require_list(payload, "pull request files response")
            collected.extend(_require_object(item, "pull request file") for item in page_items)
            if len(page_items) < _PER_PAGE:
                return collected
        raise GitHubError("GitHub pull request files exceeded the pagination cap")

    def pulls_with_head(self, sha: str) -> list[JsonObject]:
        normalized_sha = _require_sha(sha, "pull request head sha")
        matches: list[JsonObject] = []
        for page in range(1, _MAX_PAGES + 1):
            payload = self.request(
                "GET",
                f"{self._repo_path}/pulls?state=open&per_page={_PER_PAGE}&page={page}",
            )
            page_items = _require_list(payload, "open pull requests response")
            for item in page_items:
                pull_request = _require_object(item, "open pull request")
                head = _require_object(pull_request.get("head"), "open pull request head")
                if _require_sha(head.get("sha"), "open pull request head sha") == normalized_sha:
                    matches.append(pull_request)
            if len(page_items) < _PER_PAGE:
                return matches
        raise GitHubError("GitHub open pull request scan exceeded the pagination cap")

    def installation_token(self) -> str:
        self._guard_access()
        now = datetime.now(timezone.utc)
        with self._token_lock:
            token = self._installation_token
            expiry = self._installation_token_expiry
        if (
            token is not None
            and expiry is not None
            and now + _TOKEN_REFRESH_SKEW < expiry
            and self._repository_verification_is_fresh(now)
        ):
            return token

        self.verify_repository()

        with self._token_lock:
            token = self._installation_token
            expiry = self._installation_token_expiry
        if token is None or expiry is None or expiry <= datetime.now(timezone.utc):
            raise GitHubError("GitHub installation token is unavailable")
        return token

    def _check_token(self) -> str:
        self._guard_access()
        self._ensure_checks_enabled()
        now = datetime.now(timezone.utc)
        with self._check_token_lock:
            token = self._check_installation_token
            expiry = self._check_installation_token_expiry
        if (
            token is not None
            and expiry is not None
            and now + _TOKEN_REFRESH_SKEW < expiry
            and self._repository_verification_is_fresh(now)
        ):
            return token

        self.verify_repository()

        now = datetime.now(timezone.utc)
        with self._check_token_lock:
            token = self._check_installation_token
            expiry = self._check_installation_token_expiry
            if (
                token is not None
                and expiry is not None
                and now + _TOKEN_REFRESH_SKEW < expiry
                and self._repository_verification_is_fresh(now)
            ):
                return token

            app_token = self._app_jwt()
            payload = self._request_object(
                "POST",
                f"app/installations/{self.settings.installation_id}/access_tokens",
                token=app_token,
                json={
                    "repository_ids": [self.settings.repository_id],
                    "permissions": {"checks": "write"},
                },
                expected_statuses=(201,),
            )
            self._validate_issued_token(payload, {"checks": "write"})
            token = _require_str(payload.get("token"), "token", "installation token response")
            expiry = _parse_github_timestamp(
                _require_str(payload.get("expires_at"), "expires_at", "installation token response")
            )
            if expiry <= now:
                raise GitHubError("GitHub installation token is already expired")
            self._check_installation_token = token
            self._check_installation_token_expiry = expiry
            return token

    def user(self, access_token: str) -> JsonObject:
        if not access_token.strip():
            raise GitHubError("GitHub user access token must not be empty")
        payload = self._request_object("GET", "user", token=access_token)
        _require_int(payload.get("id"), "id", "user")
        _require_str(payload.get("login"), "login", "user")
        return payload

    def verify_repository(self) -> None:
        self._guard_access()
        now = datetime.now(timezone.utc)
        if self._repository_verification_is_fresh(now):
            return
        with self._verification_lock:
            now = datetime.now(timezone.utc)
            if self._repository_verification_is_fresh(now):
                return
            self._repository_verified = False
            self._repository_verification_expiry = None
            app_token = self._app_jwt()
            installation = self._request_object(
                "GET",
                f"{self._repo_path}/installation",
                token=app_token,
            )
            installation_id = _require_int(
                installation.get("id"), "id", "repository installation"
            )
            if installation_id != self.settings.installation_id:
                raise GitHubError("GitHub installation id mismatch")
            account = _require_object(installation.get("account"), "repository installation account")
            owner_id = _require_int(account.get("id"), "id", "repository installation account")
            if owner_id != self.settings.owner_id:
                raise GitHubError("GitHub installation owner mismatch")
            app_id = _require_int(installation.get("app_id"), "app_id", "repository installation")
            if app_id != self.settings.app_id:
                raise GitHubError("GitHub app id mismatch")
            if self.settings.tenant_generation is not None and installation.get("suspended_at") is not None:
                raise GitHubError("GitHub App installation is suspended", status_code=403)
            installation_token = self._refresh_installation_token(app_token, now=now)
            repository = self._request_object("GET", self._repo_path, token=installation_token)
            self._validate_repository(repository)
            self._repository_verified = True
            self._repository_verification_expiry = self._installation_token_expiry

    def _guard_access(self) -> None:
        if self._access_guard is not None:
            self._access_guard()

    def invalidate_tokens(self) -> None:
        with self._verification_lock:
            self._repository_verified = False
            self._repository_verification_expiry = None
            with self._token_lock:
                self._installation_token = None
                self._installation_token_expiry = None
            with self._check_token_lock:
                self._check_installation_token = None
                self._check_installation_token_expiry = None

    def ensure_comment(self, pr: int, body: str, publication_id: str) -> JsonObject:
        _validate_positive_number(pr, "pull request number")
        marker = _publication_marker(publication_id)
        existing = self._find_comment(pr, marker)
        if existing is not None:
            return self._comment_result(existing)

        try:
            created = self._request_object(
                "POST",
                f"{self._repo_path}/issues/{pr}/comments",
                json={"body": _append_marker(body, marker)},
                expected_statuses=(201,),
                allow_transport_errors=True,
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            try:
                reconciled = self._find_comment(pr, marker)
            except GitHubError:
                raise GitHubUncertainResultError(
                    "GitHub comment publication outcome is uncertain"
                ) from None
            if reconciled is not None:
                return self._comment_result(reconciled)
            raise GitHubUncertainResultError(
                "GitHub comment publication outcome is uncertain"
            ) from None

        self._validate_comment_attribution(created, marker)
        return self._comment_result(created)

    def find_pr_card(self, pr: int) -> JsonObject | None:
        _validate_positive_number(pr, "pull request number")
        marker = _pr_card_marker(pr)
        existing = self._find_unique_pr_card(pr, marker)
        if existing is None:
            return None
        return self._pr_card_result(existing)

    def ensure_pr_card(self, pr: int, body: str) -> JsonObject:
        _validate_positive_number(pr, "pull request number")
        marker = _pr_card_marker(pr)
        desired_body = _append_marker_with_budget(
            body,
            marker,
            self.settings.presentation_max_chars,
        )
        existing = self._find_unique_pr_card(pr, marker)
        if existing is not None:
            self._validate_comment_attribution(existing, marker)
            existing_id = _require_positive_int(
                existing.get("id"),
                "id",
                "issue comment",
            )
            if _require_str(existing.get("body"), "body", "issue comment") == desired_body:
                return self._pr_card_result(existing)
            try:
                updated = self._request_object(
                    "PATCH",
                    f"{self._repo_path}/issues/comments/{existing_id}",
                    json={"body": desired_body},
                    expected_statuses=(200,),
                    allow_transport_errors=True,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                return self._reconcile_pr_card_after_uncertain_write(pr, marker, desired_body)
            self._validate_comment_attribution(updated, marker)
            if _require_str(updated.get("body"), "body", "issue comment") != desired_body:
                raise GitHubError("GitHub PR card update result mismatch")
            return self._pr_card_result(updated)

        try:
            created = self._request_object(
                "POST",
                f"{self._repo_path}/issues/{pr}/comments",
                json={"body": desired_body},
                expected_statuses=(201,),
                allow_transport_errors=True,
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            return self._reconcile_pr_card_after_uncertain_write(pr, marker, desired_body)

        self._validate_comment_attribution(created, marker)
        if _require_str(created.get("body"), "body", "issue comment") != desired_body:
            raise GitHubError("GitHub PR card creation result mismatch")
        return self._pr_card_result(created)

    def find_check_run(self, sha: str, external_id: str) -> JsonObject | None:
        self._ensure_checks_enabled()
        normalized_sha = _require_sha(sha, "check run head sha")
        normalized_external_id = _require_external_id(external_id)
        existing = self._find_unique_check_run(normalized_sha, normalized_external_id)
        if existing is None:
            return None
        self._validate_check_run_identity(existing, normalized_sha, normalized_external_id)
        return self._check_run_result(existing)

    def ensure_check_run(
        self,
        sha: str,
        external_id: str,
        *,
        status: CheckStatus,
        conclusion: CheckConclusion | None,
        title: str,
        summary: str,
        details_url: str,
    ) -> JsonObject:
        self._ensure_checks_enabled()
        request = _validate_check_run_request(
            sha,
            external_id,
            status=status,
            conclusion=conclusion,
            title=title,
            summary=summary,
            details_url=details_url,
        )
        self._validate_runtime_url(request["details_url"], "check details URL")

        existing = self._find_unique_check_run(request["sha"], request["external_id"])
        if existing is not None:
            self._validate_check_run_identity(existing, request["sha"], request["external_id"])
            if self._check_run_matches_request(existing, request):
                return self._check_run_result(existing)
            existing_id = _require_positive_int(
                existing.get("id"),
                "id",
                "check run",
            )
            return self._patch_check_run(existing_id, request, expected_id=existing_id)

        try:
            created = self._request_check_object(
                "POST",
                f"{self._repo_path}/check-runs",
                json=_check_run_create_payload(self.settings.check_name, request),
                expected_statuses=(201,),
                allow_transport_errors=True,
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            return self._reconcile_check_run_after_uncertain_write(request)

        self._validate_check_run_identity(created, request["sha"], request["external_id"])
        if not self._check_run_matches_request(created, request):
            raise GitHubError("GitHub check run creation result mismatch")
        return self._check_run_result(created)

    def cancel_check_run(
        self,
        check_run_id: int,
        *,
        sha: str,
        external_id: str,
        title: str,
        summary: str,
        details_url: str,
    ) -> JsonObject:
        self._ensure_checks_enabled()
        _validate_positive_number(check_run_id, "check run id")
        request = _validate_check_run_request(
            sha,
            external_id,
            status="completed",
            conclusion="cancelled",
            title=title,
            summary=summary,
            details_url=details_url,
        )
        self._validate_runtime_url(request["details_url"], "check details URL")
        existing = self._request_check_object(
            "GET",
            f"{self._repo_path}/check-runs/{check_run_id}",
        )
        self._validate_check_run_identity(existing, request["sha"], request["external_id"])
        if self._check_run_matches_request(existing, request):
            return self._check_run_result(existing)
        return self._patch_check_run(check_run_id, request, expected_id=check_run_id)

    def set_status(
        self,
        sha: str,
        state: StatusState,
        description: str,
        target_url: str,
    ) -> JsonObject:
        normalized_sha = _require_sha(sha, "commit sha")
        if state not in _WORKFLOW_STATES:
            raise GitHubError("GitHub status state must be pending, success, or error")
        if not description.strip():
            raise GitHubError("GitHub status description must not be empty")
        self._validate_target_url(target_url)

        return self._request_object(
            "POST",
            f"{self._repo_path}/statuses/{normalized_sha}",
            json={
                "state": state,
                "description": description,
                "context": self.settings.status_context,
                "target_url": target_url,
            },
            expected_statuses=(201,),
        )

    def dispatch_verification(self, receipt_id: str) -> None:
        marker = receipt_id.strip()
        if not marker:
            raise GitHubError("GitHub receipt_id must not be empty")
        self._request_no_content(
            "POST",
            f"{self._repo_path}/actions/workflows/{self.settings.workflow}/dispatches",
            json={
                "ref": self.settings.workflow_ref,
                "inputs": {"receipt_id": marker},
            },
            expected_statuses=(204,),
        )

    def _app_jwt(self) -> str:
        now = int(datetime.now(timezone.utc).timestamp())
        payload = {
            "iss": self.settings.client_id,
            "iat": now - 60,
            "exp": now + 540,
        }
        try:
            private_key = self.settings.private_key_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise GitHubError("GitHub App private RSA key could not be read") from None
        try:
            return cast(
                str,
                jwt.encode(payload, private_key, algorithm="RS256"),
            )
        except (TypeError, ValueError, jwt.PyJWTError):
            raise GitHubError("GitHub App private RSA key is invalid") from None

    def _repository_verification_is_fresh(self, now: datetime) -> bool:
        return (
            self._repository_verified
            and self._repository_verification_expiry is not None
            and now + _TOKEN_REFRESH_SKEW < self._repository_verification_expiry
        )

    def _refresh_installation_token(self, app_token: str, *, now: datetime) -> str:
        with self._token_lock:
            if (
                self._installation_token is not None
                and self._installation_token_expiry is not None
                and now + _TOKEN_REFRESH_SKEW < self._installation_token_expiry
            ):
                return self._installation_token

            payload = self._request_object(
                "POST",
                f"app/installations/{self.settings.installation_id}/access_tokens",
                token=app_token,
                json={
                    "repository_ids": [self.settings.repository_id],
                    "permissions": {
                        "contents": "read",
                        "pull_requests": "write",
                        "statuses": "write",
                        "actions": "write",
                    },
                },
                expected_statuses=(201,),
            )
            self._validate_issued_token(payload, _REQUIRED_CORE_PERMISSIONS)
            token = _require_str(payload.get("token"), "token", "installation token response")
            expiry = _parse_github_timestamp(
                _require_str(payload.get("expires_at"), "expires_at", "installation token response")
            )
            if expiry <= now:
                raise GitHubError("GitHub installation token is already expired")
            self._installation_token = token
            self._installation_token_expiry = expiry
            self._repository_verified = False
            self._repository_verification_expiry = None
            return token

    def _find_comment(self, pr: int, marker: str) -> JsonObject | None:
        for page in range(1, _MAX_PAGES + 1):
            payload = self.request(
                "GET",
                f"{self._repo_path}/issues/{pr}/comments?per_page={_PER_PAGE}&page={page}",
            )
            comments = _require_list(payload, "issue comments response")
            for comment in comments:
                comment_object = _require_object(comment, "issue comment")
                if self._comment_has_attribution(comment_object, marker):
                    return comment_object
            if len(comments) < _PER_PAGE:
                return None
        raise GitHubError("GitHub issue comment scan exceeded the pagination cap")

    def _find_unique_pr_card(self, pr: int, marker: str) -> JsonObject | None:
        matches = self._find_comment_matches(pr, marker)
        if not matches:
            return None
        if len(matches) > 1:
            raise GitHubError("GitHub PR card identity is ambiguous")
        return matches[0]

    def _find_comment_matches(self, pr: int, marker: str) -> list[JsonObject]:
        matches: list[JsonObject] = []
        for page in range(1, _MAX_PAGES + 1):
            payload = self.request(
                "GET",
                f"{self._repo_path}/issues/{pr}/comments?per_page={_PER_PAGE}&page={page}",
            )
            comments = _require_list(payload, "issue comments response")
            for comment in comments:
                comment_object = _require_object(comment, "issue comment")
                if self._comment_has_attribution(comment_object, marker):
                    matches.append(comment_object)
            if len(comments) < _PER_PAGE:
                return matches
        raise GitHubError("GitHub issue comment scan exceeded the pagination cap")

    def _reconcile_pr_card_after_uncertain_write(
        self,
        pr: int,
        marker: str,
        desired_body: str,
    ) -> JsonObject:
        try:
            existing = self._find_unique_pr_card(pr, marker)
        except GitHubError:
            raise GitHubUncertainResultError(
                "GitHub PR card publication outcome is uncertain"
            ) from None
        try:
            if (
                existing is not None
                and _require_str(existing.get("body"), "body", "issue comment") == desired_body
            ):
                return self._pr_card_result(existing)
        except GitHubError:
            raise GitHubUncertainResultError(
                "GitHub PR card publication outcome is uncertain"
            ) from None
        raise GitHubUncertainResultError(
            "GitHub PR card publication outcome is uncertain"
        ) from None

    def _ensure_checks_enabled(self) -> None:
        if not self.settings.checks_enabled:
            raise GitHubError("GitHub Check Runs are disabled by configuration")

    def _find_unique_check_run(self, sha: str, external_id: str) -> JsonObject | None:
        matches = self._find_check_runs(sha, external_id)
        if not matches:
            return None
        if len(matches) > 1:
            raise GitHubError("GitHub check run identity is ambiguous")
        return matches[0]

    def _find_check_runs(self, sha: str, external_id: str) -> list[JsonObject]:
        matches: list[JsonObject] = []
        encoded_name = quote(self.settings.check_name, safe="")
        for page in range(1, _MAX_PAGES + 1):
            payload = self._request_check_object(
                "GET",
                (
                    f"{self._repo_path}/commits/{sha}/check-runs?"
                    f"check_name={encoded_name}&filter=all&per_page={_PER_PAGE}&page={page}"
                ),
            )
            runs = _require_list(payload.get("check_runs"), "check runs response")
            for item in runs:
                check_run = _require_object(item, "check run")
                if self._check_run_has_identity(check_run, sha, external_id):
                    matches.append(check_run)
            if len(runs) < _PER_PAGE:
                return matches
        raise GitHubError("GitHub check run scan exceeded the pagination cap")

    def _patch_check_run(
        self,
        check_run_id: int,
        request: JsonObject,
        *,
        expected_id: int,
    ) -> JsonObject:
        try:
            updated = self._request_check_object(
                "PATCH",
                f"{self._repo_path}/check-runs/{check_run_id}",
                json=_check_run_update_payload(request),
                expected_statuses=(200,),
                allow_transport_errors=True,
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            return self._reconcile_check_run_after_uncertain_write(
                request,
                expected_id=expected_id,
            )
        self._validate_check_run_identity(updated, request["sha"], request["external_id"])
        if _require_positive_int(updated.get("id"), "id", "check run") != expected_id:
            raise GitHubError("GitHub check run update result id mismatch")
        if not self._check_run_matches_request(updated, request):
            raise GitHubError("GitHub check run update result mismatch")
        return self._check_run_result(updated)

    def _reconcile_check_run_after_uncertain_write(
        self,
        request: JsonObject,
        *,
        expected_id: int | None = None,
    ) -> JsonObject:
        try:
            matches = self._find_check_runs(request["sha"], request["external_id"])
        except GitHubError:
            raise GitHubUncertainResultError(
                "GitHub check run publication outcome is uncertain"
            ) from None
        try:
            if (
                len(matches) == 1
                and (
                    expected_id is None
                    or _require_positive_int(
                        matches[0].get("id"),
                        "id",
                        "check run",
                    ) == expected_id
                )
                and self._check_run_matches_request(matches[0], request)
            ):
                return self._check_run_result(matches[0])
        except GitHubError:
            raise GitHubUncertainResultError(
                "GitHub check run publication outcome is uncertain"
            ) from None
        raise GitHubUncertainResultError(
            "GitHub check run publication outcome is uncertain"
        ) from None

    def _latest_status_for_context(self, statuses: list[object]) -> JsonObject | None:
        for item in statuses:
            status = _require_object(item, "commit status")
            if _require_str(status.get("context"), "context", "commit status") == self.settings.status_context:
                return status
        return None

    def _validate_repository(self, repository: JsonObject) -> JsonObject:
        repository_id = _require_int(repository.get("id"), "id", "repository")
        if repository_id != self.settings.repository_id:
            raise GitHubError("GitHub repository id mismatch")
        full_name = _require_str(repository.get("full_name"), "full_name", "repository")
        if full_name.casefold() != self.settings.repository.casefold():
            raise GitHubError("GitHub repository name mismatch")
        owner = _require_object(repository.get("owner"), "repository owner")
        owner_id = _require_int(owner.get("id"), "id", "repository owner")
        if owner_id != self.settings.owner_id:
            raise GitHubError("GitHub repository owner mismatch")
        return repository

    def _validate_comment_attribution(self, comment: JsonObject, marker: str) -> None:
        if not self._comment_has_attribution(comment, marker):
            raise GitHubError("GitHub comment attribution mismatch")

    def _comment_has_attribution(self, comment: JsonObject, marker: str) -> bool:
        try:
            body = _require_str(comment.get("body"), "body", "issue comment")
            if _hidden_marker(marker) not in body:
                return False
            user = _require_object(comment.get("user"), "issue comment user")
            if _require_str(user.get("type"), "type", "issue comment user") != "Bot":
                return False
            app = _require_object(
                comment.get("performed_via_github_app"),
                "issue comment app attribution",
            )
            return (
                _require_int(app.get("id"), "id", "issue comment app attribution")
                == self.settings.app_id
            )
        except GitHubError:
            return False

    def _comment_result(self, comment: JsonObject) -> JsonObject:
        return {
            "id": _require_int(comment.get("id"), "id", "issue comment"),
            "html_url": _require_str(comment.get("html_url"), "html_url", "issue comment"),
        }

    def _pr_card_result(self, comment: JsonObject) -> JsonObject:
        return {
            "id": _require_positive_int(comment.get("id"), "id", "issue comment"),
            "html_url": _require_str(comment.get("html_url"), "html_url", "issue comment"),
            "body": _require_str(comment.get("body"), "body", "issue comment"),
        }

    def _check_run_has_identity(
        self,
        check_run: JsonObject,
        sha: object,
        external_id: object,
    ) -> bool:
        try:
            self._validate_check_run_identity(check_run, sha, external_id)
            return True
        except GitHubError:
            return False

    def _validate_check_run_identity(
        self,
        check_run: JsonObject,
        sha: object,
        external_id: object,
    ) -> None:
        _require_positive_int(check_run.get("id"), "id", "check run")
        if _require_str(check_run.get("name"), "name", "check run") != self.settings.check_name:
            raise GitHubError("GitHub check run name mismatch")
        if _require_sha(check_run.get("head_sha"), "check run head sha") != sha:
            raise GitHubError("GitHub check run sha mismatch")
        if _require_str(check_run.get("external_id"), "external_id", "check run") != external_id:
            raise GitHubError("GitHub check run external identity mismatch")
        app = _require_object(check_run.get("app"), "check run app attribution")
        if _require_int(app.get("id"), "id", "check run app attribution") != self.settings.app_id:
            raise GitHubError("GitHub check run app attribution mismatch")

    def _check_run_matches_request(self, check_run: JsonObject, request: JsonObject) -> bool:
        output = _require_object(check_run.get("output"), "check run output")
        return (
            _require_str(check_run.get("status"), "status", "check run") == request["status"]
            and _string_or_none(check_run.get("conclusion")) == request["conclusion"]
            and _string_or_none(check_run.get("details_url")) == request["details_url"]
            and _string_or_none(output.get("title")) == request["title"]
            and _string_or_none(output.get("summary")) == request["summary"]
        )

    def _check_run_result(self, check_run: JsonObject) -> JsonObject:
        output = _optional_object(check_run.get("output"), "check run output")
        return {
            "id": _require_positive_int(check_run.get("id"), "id", "check run"),
            "html_url": _string_or_none(check_run.get("html_url")),
            "name": _require_str(check_run.get("name"), "name", "check run"),
            "head_sha": _require_sha(check_run.get("head_sha"), "check run head sha"),
            "external_id": _require_str(check_run.get("external_id"), "external_id", "check run"),
            "status": _require_str(check_run.get("status"), "status", "check run"),
            "conclusion": _string_or_none(check_run.get("conclusion")),
            "details_url": _string_or_none(check_run.get("details_url")),
            "app_id": _require_int(
                _require_object(check_run.get("app"), "check run app attribution").get("id"),
                "id",
                "check run app attribution",
            ),
            "title": _string_or_none(output.get("title")) if output is not None else None,
            "summary": _string_or_none(output.get("summary")) if output is not None else None,
        }

    def _validate_issued_token(self, payload: JsonObject, required: Mapping[str, str]) -> None:
        if self.settings.tenant_generation is None:
            return
        repositories = _require_list(payload.get("repositories"), "installation token repositories")
        if len(repositories) != 1:
            raise GitHubError("GitHub installation token repository scope is not exact")
        repository = _require_object(repositories[0], "installation token repository")
        if (
            repository.get("id") != self.settings.repository_id
            or isinstance(repository.get("id"), bool)
            or _require_str(repository.get("full_name"), "full_name", "installation token repository").casefold()
            != self.settings.repository.casefold()
        ):
            raise GitHubError("GitHub installation token repository scope mismatch")
        permissions = _require_object(payload.get("permissions"), "installation token permissions")
        if any(not _permission_covers(permissions.get(name), level) for name, level in required.items()):
            raise GitHubError("GitHub installation token permissions mismatch")

    def _validate_target_url(self, target_url: str) -> None:
        self._validate_runtime_url(target_url, "status target URL")

    def _validate_runtime_url(self, target_url: str, label: str) -> None:
        parsed = urlsplit(target_url)
        if not parsed.scheme or not parsed.netloc:
            raise GitHubError(f"GitHub {label} must be absolute")
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise GitHubError(f"GitHub {label} must not contain secrets")
        if _origin_tuple(parsed) != _origin_tuple(urlsplit(self.settings.base_url)):
            raise GitHubError(f"GitHub {label} must stay under the configured base_url")
        decoded_path = unquote(parsed.path)
        if "\\" in decoded_path or any(segment == ".." for segment in decoded_path.split("/")):
            raise GitHubError(f"GitHub {label} must not include parent traversal")
        if self.settings.path_prefix and not re.fullmatch(
            re.escape(self.settings.path_prefix) + r"/(?:prs/[1-9][0-9]*|receipts/[A-Za-z0-9._-]{1,128})",
            parsed.path,
        ):
            raise GitHubError(f"GitHub {label} must stay within this repository")

    def _request_object(
        self,
        method: str,
        relative_api_path: str,
        *,
        token: str | None = None,
        json: JsonObject | None = None,
        expected_statuses: tuple[int, ...] = (200,),
        allow_transport_errors: bool = False,
    ) -> JsonObject:
        payload = self._request_data(
            method,
            relative_api_path,
            token=token if token is not None else self.installation_token(),
            json=json,
            expected_statuses=expected_statuses,
            allow_transport_errors=allow_transport_errors,
        )
        return _require_object(payload, f"{method.upper()} {relative_api_path} response")

    def _request_check_object(
        self,
        method: str,
        relative_api_path: str,
        *,
        json: JsonObject | None = None,
        expected_statuses: tuple[int, ...] = (200,),
        allow_transport_errors: bool = False,
    ) -> JsonObject:
        payload = self._request_data(
            method,
            relative_api_path,
            token=self._check_token(),
            json=json,
            expected_statuses=expected_statuses,
            allow_transport_errors=allow_transport_errors,
        )
        return _require_object(payload, f"{method.upper()} {relative_api_path} response")

    def _request_no_content(
        self,
        method: str,
        relative_api_path: str,
        *,
        json: JsonObject | None = None,
        expected_statuses: tuple[int, ...] = (204,),
    ) -> None:
        self._request_data(
            method,
            relative_api_path,
            token=self.installation_token(),
            json=json,
            expected_statuses=expected_statuses,
            expect_json=False,
        )

    def _request_data(
        self,
        method: str,
        relative_api_path: str,
        *,
        token: str,
        json: JsonObject | None = None,
        expected_statuses: tuple[int, ...] = (200,),
        expect_json: bool = True,
        allow_transport_errors: bool = False,
    ) -> JsonData:
        self._guard_access()
        path = _normalize_relative_api_path(relative_api_path)
        url = f"{self.api_origin}/{path}"
        authorization_header = "Bearer " + token
        headers = {"Accept": _ACCEPT, "Authorization": authorization_header}
        try:
            response = self._session.request(
                method.upper(),
                url,
                headers=headers,
                json=json,
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.exceptions.Timeout:
            if allow_transport_errors:
                raise
            raise GitHubError(f"GitHub {method.upper()} {path} timed out") from None
        except requests.exceptions.ConnectionError:
            if allow_transport_errors:
                raise
            raise GitHubError(f"GitHub {method.upper()} {path} connection failed") from None
        except requests.exceptions.RequestException:
            raise GitHubError(f"GitHub {method.upper()} {path} request failed") from None

        if response.status_code not in expected_statuses:
            raise GitHubError(
                f"GitHub {method.upper()} {path} failed",
                status_code=response.status_code,
            )
        _validate_response_size(response, method.upper(), path)
        if not expect_json:
            return {}
        if not response.content:
            raise GitHubError(
                f"GitHub {method.upper()} {path} returned an empty response",
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError:
            raise GitHubError(
                f"GitHub {method.upper()} {path} returned invalid JSON",
                status_code=response.status_code,
            ) from None
        if not isinstance(payload, (dict, list)):
            raise GitHubError(
                f"GitHub {method.upper()} {path} returned invalid JSON",
                status_code=response.status_code,
            )
        return cast(JsonData, payload)


class GitHubInstallationDiscovery:
    """Independently prove App installation, exact grants and trusted-main opt-in."""

    api_origin: str = _API_ORIGIN
    timeout: tuple[int, int] = _TIMEOUT

    def __init__(self, common_settings: GatewaySettings, session: requests.Session | None = None) -> None:
        self.settings = common_settings
        self._session = requests.Session() if session is None else session

    def discover(
        self, repository: str, repository_id: int, owner_id: int, *, verify_opt_in: bool = True,
    ) -> RepositoryInstallation:
        try:
            return self._discover(repository, repository_id, owner_id, verify_opt_in=verify_opt_in)
        except GitHubError as error:
            raise RegistrationOperationalError("GitHub discovery response was invalid") from error

    def _discover(
        self, repository: str, repository_id: int, owner_id: int, *, verify_opt_in: bool,
    ) -> RepositoryInstallation:
        repository_name = _validate_repository_name(repository)
        _validate_positive_number(repository_id, "repository id")
        _validate_positive_number(owner_id, "repository owner id")
        app_token = self._app_jwt()
        installation = self._request_object("GET", f"repos/{repository_name}/installation", token=app_token)
        installation_id = _require_positive_int(installation.get("id"), "id", "repository installation")
        if _require_positive_int(installation.get("app_id"), "app_id", "installation") != self.settings.app_id:
            raise RegistrationDeniedError("GitHub App installation mismatch")
        if installation.get("suspended_at") is not None:
            raise RegistrationDeniedError("GitHub App installation is suspended")
        account = _require_object(installation.get("account"), "installation account")
        if _require_positive_int(account.get("id"), "id", "installation account") != owner_id:
            raise RegistrationDeniedError("GitHub installation owner mismatch")
        if installation.get("permissions") is not None:
            self._require_core_permissions(installation)
        token_payload = self._request_object(
            "POST", f"app/installations/{installation_id}/access_tokens", token=app_token,
            json={"repository_ids": [repository_id], "permissions": dict(_REQUIRED_CORE_PERMISSIONS)},
            expected_statuses=(201,),
        )
        self._require_core_permissions(token_payload)
        self._require_exact_repository_scope(token_payload, repository_name, repository_id)
        token = _require_str(token_payload.get("token"), "token", "installation token response")
        expiry = _parse_github_timestamp(
            _require_str(token_payload.get("expires_at"), "expires_at", "installation token response")
        )
        if expiry <= datetime.now(timezone.utc):
            raise RegistrationOperationalError("GitHub installation token is already expired")
        metadata = self._request_object("GET", f"repos/{repository_name}", token=token)
        self._validate_repository(metadata, repository_name, repository_id, owner_id)
        if verify_opt_in:
            self._verify_opt_in(repository_name, token)
        return RepositoryInstallation(repository_id, repository_name, owner_id, installation_id)

    def _verify_opt_in(self, repository: str, token: str) -> None:
        branch = self.settings.workflow_ref.removeprefix("refs/heads/")
        config_text = self._read_contents_text(repository, ".lasthuman.yml", branch, token)
        try:
            parse_config(config_text)
        except (AttributeError, TypeError, ValueError, yaml.YAMLError):
            raise RegistrationDeniedError("trusted .lasthuman.yml is invalid") from None
        workflow = self._read_contents_text(
            repository, f".github/workflows/{self.settings.workflow}", branch, token,
        )
        self._validate_relay_workflow(workflow)

    def _read_contents_text(self, repository: str, path: str, ref: str, token: str) -> str:
        payload = self._request_object(
            "GET", f"repos/{repository}/contents/{quote(path, safe='/')}?ref={quote(ref, safe='')}", token=token,
        )
        if payload.get("type") != "file" or payload.get("encoding") != "base64":
            raise RegistrationDeniedError("trusted repository opt-in file is invalid")
        content = _require_str(payload.get("content"), "content", "repository content")
        try:
            return b64decode("".join(content.split()), validate=True).decode("utf-8")
        except (ValueError, UnicodeError):
            raise RegistrationDeniedError("trusted repository opt-in file is invalid") from None

    def _validate_relay_workflow(self, text: str) -> None:
        try:
            workflow = yaml.safe_load(text)
        except yaml.YAMLError:
            raise RegistrationDeniedError("trusted relay workflow is invalid") from None
        if not isinstance(workflow, dict):
            raise RegistrationDeniedError("trusted relay workflow is invalid")
        triggers = workflow.get("on", workflow.get(True))
        if not (
            triggers == "pull_request_target"
            or isinstance(triggers, (dict, list)) and "pull_request_target" in triggers
        ):
            raise RegistrationDeniedError("trusted relay workflow must opt in to pull_request_target")
        jobs = workflow.get("jobs")
        if not isinstance(jobs, dict):
            raise RegistrationDeniedError("trusted relay workflow must define jobs")
        for job in jobs.values():
            if not isinstance(job, dict) or not _job_runs_relay(job):
                continue
            permissions = job.get("permissions", workflow.get("permissions"))
            if isinstance(permissions, dict) and permissions.get("id-token") == "write":
                return
        raise RegistrationDeniedError("trusted relay workflow must run the Last Human relay with id-token: write")

    def _require_core_permissions(self, payload: Mapping[str, object]) -> None:
        permissions = _require_object(payload.get("permissions"), "installation token permissions")
        for name, required in _REQUIRED_CORE_PERMISSIONS.items():
            if not _permission_covers(permissions.get(name), required):
                raise RegistrationDeniedError(f"GitHub installation token is missing {name} permission")

    def _require_exact_repository_scope(
        self, payload: Mapping[str, object], repository_name: str, repository_id: int,
    ) -> None:
        repositories = _require_list(payload.get("repositories"), "installation token repositories")
        if len(repositories) != 1:
            raise RegistrationDeniedError("GitHub installation token repository scope is not exact")
        repository = _require_object(repositories[0], "installation token repository")
        if _require_positive_int(repository.get("id"), "id", "installation token repository") != repository_id:
            raise RegistrationDeniedError("GitHub installation token repository scope mismatch")
        full_name = _require_str(repository.get("full_name"), "full_name", "installation token repository")
        if full_name.casefold() != repository_name.casefold():
            raise RegistrationDeniedError("GitHub installation token repository name mismatch")

    def _validate_repository(self, repository: JsonObject, name: str, repo_id: int, owner_id: int) -> None:
        if _require_positive_int(repository.get("id"), "id", "repository") != repo_id:
            raise RegistrationDeniedError("GitHub repository id mismatch")
        if _require_str(repository.get("full_name"), "full_name", "repository").casefold() != name.casefold():
            raise RegistrationDeniedError("GitHub repository name mismatch")
        owner = _require_object(repository.get("owner"), "repository owner")
        if _require_positive_int(owner.get("id"), "id", "repository owner") != owner_id:
            raise RegistrationDeniedError("GitHub repository owner mismatch")

    def _app_jwt(self) -> str:
        now = int(datetime.now(timezone.utc).timestamp())
        try:
            key = self.settings.private_key_file.read_text(encoding="utf-8")
            return cast(str, jwt.encode(
                {"iss": self.settings.client_id, "iat": now - 60, "exp": now + 540}, key, algorithm="RS256",
            ))
        except (OSError, UnicodeError, TypeError, ValueError, jwt.PyJWTError):
            raise RegistrationOperationalError("GitHub App private RSA key is unavailable or invalid") from None

    def _request_object(
        self, method: str, relative_api_path: str, *, token: str,
        json: JsonObject | None = None, expected_statuses: tuple[int, ...] = (200,),
    ) -> JsonObject:
        path = _normalize_relative_api_path(relative_api_path)
        try:
            response = self._session.request(
                method.upper(), f"{self.api_origin}/{path}",
                headers={"Accept": _ACCEPT, "Authorization": "Bearer " + token},
                json=json, timeout=self.timeout, allow_redirects=False,
            )
        except requests.exceptions.RequestException as error:
            raise RegistrationOperationalError(f"GitHub discovery {method.upper()} {path} failed") from error
        if response.status_code not in expected_statuses:
            if _is_operational_discovery_status(response):
                raise RegistrationOperationalError(f"GitHub discovery {method.upper()} {path} failed")
            raise RegistrationDeniedError(f"GitHub discovery {method.upper()} {path} denied")
        _validate_response_size(response, method.upper(), path)
        if not response.content:
            raise RegistrationOperationalError("GitHub discovery returned an empty response")
        try:
            payload = response.json()
        except ValueError:
            raise RegistrationOperationalError("GitHub discovery returned invalid JSON") from None
        return _require_object(payload, "discovery response")


def _validate_repository_name(repository: str) -> str:
    if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", repository):
        raise RegistrationDeniedError("GitHub repository name is invalid")
    return repository


def _permission_covers(actual: object, required: str) -> bool:
    levels = {"none": 0, "read": 1, "write": 2}
    return isinstance(actual, str) and levels.get(actual, -1) >= levels[required]


def _job_runs_relay(job: JsonObject) -> bool:
    steps = job.get("steps")
    if not isinstance(steps, list):
        return False
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("run"), str):
            continue
        # Check executable command position, not comments, echo arguments or quoted examples.
        for line in step["run"].replace("\\\n", " ").splitlines():
            try:
                lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|")
                lexer.whitespace_split = True
                tokens = list(lexer)
            except ValueError:
                continue
            command: list[str] = []
            for token in [*tokens, ";"]:
                if token and all(character in ";&|" for character in token):
                    offset = 2 if command[:2] == ["uv", "run"] else 0
                    executable = command[offset:offset + 3]
                    if (
                        len(executable) == 3
                        and re.fullmatch(r"python(?:3(?:\.\d+)?)?", executable[0])
                        and executable[1:] == ["-m", "lasthuman.server.relay"]
                    ):
                        return True
                    command = []
                else:
                    command.append(token)
    return False


def _is_operational_discovery_status(response: requests.Response) -> bool:
    if response.status_code in {401, 429} or response.status_code >= 500:
        return True
    if response.status_code != 403:
        return False
    retry_after = response.headers.get("Retry-After") or response.headers.get("retry-after")
    remaining = response.headers.get("X-RateLimit-Remaining") or response.headers.get("x-ratelimit-remaining")
    if retry_after or remaining == "0":
        return True
    return "rate limit" in response.text.lower()


def _publication_marker(publication_id: str) -> str:
    marker = publication_id.strip()
    if not marker:
        raise GitHubError("GitHub publication id must not be empty")
    if re.search(r"\s", marker):
        raise GitHubError("GitHub publication id must not contain whitespace")
    return f"lasthuman:publication:{marker}"


def _pr_card_marker(pr: int) -> str:
    _validate_positive_number(pr, "pull request number")
    marker = f"{_PR_CARD_MARKER_PREFIX}{pr}"
    if len(_hidden_marker(marker)) > _MARKER_RESERVATION_CHARS:
        raise GitHubError("GitHub PR card marker exceeds reserved presentation budget")
    return marker


def _append_marker(body: str, marker: str) -> str:
    comment = body.rstrip()
    hidden_marker = _hidden_marker(marker)
    if hidden_marker in comment:
        return comment
    if comment:
        return f"{comment}\n\n{hidden_marker}"
    return hidden_marker


def _append_marker_with_budget(body: str, marker: str, max_chars: int) -> str:
    if len(_hidden_marker(marker)) > _MARKER_RESERVATION_CHARS:
        raise GitHubError("GitHub PR card marker exceeds reserved presentation budget")
    comment = _append_marker(body, marker)
    if len(comment) > max_chars:
        raise GitHubError("GitHub PR card body exceeds presentation character budget")
    return comment


def _hidden_marker(marker: str) -> str:
    return f"<!-- {marker} -->"


def _validate_check_run_request(
    sha: str,
    external_id: str,
    *,
    status: CheckStatus,
    conclusion: CheckConclusion | None,
    title: str,
    summary: str,
    details_url: str,
) -> JsonObject:
    normalized_sha = _require_sha(sha, "check run head sha")
    normalized_external_id = _require_external_id(external_id)
    if status not in _CHECK_STATUSES:
        raise GitHubError("GitHub check run status must be in_progress or completed")
    if status == "in_progress":
        if conclusion is not None:
            raise GitHubError("GitHub in_progress check run conclusion must be absent")
    elif conclusion not in _COMPLETED_CHECK_CONCLUSIONS:
        raise GitHubError(
            "GitHub completed check run conclusion must be success, neutral, "
            "cancelled, or action_required"
        )
    return {
        "sha": normalized_sha,
        "external_id": normalized_external_id,
        "status": status,
        "conclusion": conclusion,
        "title": _require_bounded_text(title, "title", "check run output"),
        "summary": _require_bounded_text(summary, "summary", "check run output"),
        "details_url": _require_str(details_url, "details_url", "check run"),
    }


def _check_run_create_payload(name: str, request: JsonObject) -> JsonObject:
    payload = _check_run_update_payload(request)
    payload.update(
        {
            "name": name,
            "head_sha": request["sha"],
            "external_id": request["external_id"],
        }
    )
    return payload


def _check_run_update_payload(request: JsonObject) -> JsonObject:
    payload: JsonObject = {
        "status": request["status"],
        "details_url": request["details_url"],
        "output": {
            "title": request["title"],
            "summary": request["summary"],
        },
    }
    if request["status"] == "completed":
        payload["conclusion"] = request["conclusion"]
    return payload


def _require_external_id(value: object) -> str:
    external_id = _require_str(value, "external_id", "check run")
    if len(external_id) > _CHECK_EXTERNAL_ID_MAX:
        raise GitHubError("GitHub check run external identity is too long")
    if re.search(r"\s", external_id):
        raise GitHubError("GitHub check run external identity must not contain whitespace")
    return external_id


def _require_bounded_text(value: object, field_name: str, context: str) -> str:
    text = _require_str(value, field_name, context)
    if len(text) > _CHECK_OUTPUT_TEXT_MAX:
        raise GitHubError(f"GitHub {context} {field_name} is too long")
    return text


def _normalize_relative_api_path(relative_api_path: str) -> str:
    parsed = urlsplit(relative_api_path)
    if parsed.username or parsed.password:
        raise GitHubError("GitHub relative API path must not embed credentials")
    if parsed.scheme or parsed.netloc or relative_api_path.startswith("//"):
        raise GitHubError("GitHub relative API path must stay under the GitHub API origin")
    if parsed.fragment:
        raise GitHubError("GitHub relative API path must not include fragments")
    canonical_path = parsed.path.lstrip("/")
    decoded_path = unquote(canonical_path)
    if not decoded_path:
        raise GitHubError("GitHub relative API path must not be empty")
    if "\\" in decoded_path or any(segment == ".." for segment in decoded_path.split("/")):
        raise GitHubError("GitHub relative API path must not include parent traversal")
    return f"{canonical_path}?{parsed.query}" if parsed.query else canonical_path


def _validate_response_size(response: requests.Response, method: str, path: str) -> None:
    header_value = response.headers.get("Content-Length")
    if header_value is not None:
        try:
            content_length = int(header_value)
        except ValueError:
            raise GitHubError(
                f"GitHub {method} {path} returned an invalid Content-Length"
            ) from None
        if content_length > _MAX_RESPONSE_BYTES:
            raise GitHubError(f"GitHub {method} {path} exceeded the 2 MiB response limit")
    if len(response.content) > _MAX_RESPONSE_BYTES:
        raise GitHubError(f"GitHub {method} {path} exceeded the 2 MiB response limit")


def _parse_github_timestamp(raw: str) -> datetime:
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise GitHubError("GitHub returned an invalid expires_at timestamp") from None


def _origin_tuple(parsed) -> tuple[str, str, int | None]:
    host = parsed.hostname
    if host is None:
        raise GitHubError("GitHub status target URL must include a host")
    return (parsed.scheme.lower(), host.lower(), parsed.port)


def _validate_positive_number(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GitHubError(f"GitHub {name} must be positive")


def _require_object(value: object, context: str) -> JsonObject:
    if not isinstance(value, dict):
        raise GitHubError(f"GitHub {context} must be a JSON object")
    return cast(JsonObject, value)


def _optional_object(value: object, context: str) -> JsonObject | None:
    if value is None:
        return None
    return _require_object(value, context)


def _require_list(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise GitHubError(f"GitHub {context} must be a JSON array")
    return value


def _require_str(value: object, field_name: str, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise GitHubError(f"GitHub {context} missing {field_name}")
    return value


def _require_int(value: object, field_name: str, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GitHubError(f"GitHub {context} missing {field_name}")
    return value


def _require_positive_int(value: object, field_name: str, context: str) -> int:
    integer = _require_int(value, field_name, context)
    if integer <= 0:
        raise GitHubError(f"GitHub {context} {field_name} must be positive")
    return integer


def _require_sha(value: object, context: str) -> str:
    sha = _require_str(value, "sha", context)
    if not _SHA_RE.fullmatch(sha):
        raise GitHubError("GitHub commit sha must be a 40-character hexadecimal string")
    return sha


def _string_or_none(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None
