"""GitHub App API adapter."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Final, Literal, cast
from urllib.parse import unquote, urlsplit

import jwt
import requests

from .config import Settings

JsonObject = dict[str, object]
JsonData = JsonObject | list[object]
StatusState = Literal["pending", "success", "error"]

_API_ORIGIN: Final = "https://api.github.com"
_ACCEPT: Final = "application/vnd.github+json"
_TIMEOUT: Final[tuple[int, int]] = (5, 30)
_MAX_RESPONSE_BYTES: Final = 2 * 1024 * 1024
_PER_PAGE: Final = 100
_MAX_PAGES: Final = 10
_TOKEN_REFRESH_SKEW: Final = timedelta(seconds=60)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_WORKFLOW_STATES = {"pending", "success", "error"}


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

    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        self.settings = settings
        self._session = requests.Session() if session is None else session
        self._repo_path = f"repos/{settings.repository}"
        self._verification_lock = Lock()
        self._token_lock = Lock()
        self._repository_verified = False
        self._repository_verification_expiry: datetime | None = None
        self._installation_token: str | None = None
        self._installation_token_expiry: datetime | None = None

    def request(
        self,
        method: str,
        relative_api_path: str,
        token_override: str | None = None,
        json: JsonObject | None = None,
    ) -> JsonData:
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

    def user(self, access_token: str) -> JsonObject:
        if not access_token.strip():
            raise GitHubError("GitHub user access token must not be empty")
        payload = self._request_object("GET", "user", token=access_token)
        _require_int(payload.get("id"), "id", "user")
        _require_str(payload.get("login"), "login", "user")
        return payload

    def verify_repository(self) -> None:
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
            installation_token = self._refresh_installation_token(app_token, now=now)
            repository = self._request_object("GET", self._repo_path, token=installation_token)
            self._validate_repository(repository)
            self._repository_verified = True
            self._repository_verification_expiry = self._installation_token_expiry

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

    def _validate_target_url(self, target_url: str) -> None:
        parsed = urlsplit(target_url)
        if not parsed.scheme or not parsed.netloc:
            raise GitHubError("GitHub status target URL must be absolute")
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise GitHubError("GitHub status target URL must not contain secrets")
        if _origin_tuple(parsed) != _origin_tuple(urlsplit(self.settings.base_url)):
            raise GitHubError("GitHub status target URL must stay under the configured base_url")
        decoded_path = unquote(parsed.path)
        if "\\" in decoded_path or any(segment == ".." for segment in decoded_path.split("/")):
            raise GitHubError("GitHub status target URL must not include parent traversal")

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


def _publication_marker(publication_id: str) -> str:
    marker = publication_id.strip()
    if not marker:
        raise GitHubError("GitHub publication id must not be empty")
    if re.search(r"\s", marker):
        raise GitHubError("GitHub publication id must not contain whitespace")
    return f"lasthuman:publication:{marker}"


def _append_marker(body: str, marker: str) -> str:
    comment = body.rstrip()
    hidden_marker = _hidden_marker(marker)
    if hidden_marker in comment:
        return comment
    if comment:
        return f"{comment}\n\n{hidden_marker}"
    return hidden_marker


def _hidden_marker(marker: str) -> str:
    return f"<!-- {marker} -->"


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


def _require_sha(value: object, context: str) -> str:
    sha = _require_str(value, "sha", context)
    if not _SHA_RE.fullmatch(sha):
        raise GitHubError("GitHub commit sha must be a 40-character hexadecimal string")
    return sha


def _string_or_none(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None
