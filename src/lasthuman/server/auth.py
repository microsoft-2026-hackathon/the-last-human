"""Server-side GitHub OAuth and memory-backed sessions."""

from __future__ import annotations

import hmac
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from threading import RLock
from urllib.parse import urlsplit

import requests
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.requests_client import OAuth2Session
from authlib.oauth2.base import OAuth2Error

from .config import Settings
from .github import GitHubClient, GitHubError, JsonObject

SESSION_COOKIE_NAME = "lasthuman_sid"
_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_DASHBOARD_PATH = "/dashboard"
_SESSION_LIMIT = 1000
_STATE_LIMIT = 1000
_OAUTH_TTL_SECONDS = 600.0


class AuthError(RuntimeError):
    """Sanitized browser authentication failure."""

    def __init__(self, message: str, *, code: str, status_code: int) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class AuthorizedSession:
    sid: str = field(repr=False)
    actor_id: int
    actor_login: str
    access_token: str = field(repr=False)
    csrf_token: str = field(repr=False)
    expires_at: float
    pull: JsonObject | None = None


@dataclass
class _SessionRecord:
    sid: str = field(repr=False)
    csrf_token: str = field(repr=False)
    created_at: float
    expires_at: float
    actor_id: int | None = None
    actor_login: str | None = None
    access_token: str | None = field(default=None, repr=False)
    touched_at: float = 0.0


@dataclass(frozen=True)
class _PendingState:
    sid: str = field(repr=False)
    code_verifier: str = field(repr=False)
    next_path: str
    expires_at: float


class AuthlibGitHubOAuth:
    """Small Authlib adapter with a fixed GitHub OAuth flow."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.redirect_uri = settings.base_url.rstrip("/") + "/auth/github/callback"

    def create_authorization_url(self, *, state: str, code_verifier: str) -> str:
        session = OAuth2Session(
            client_id=self.settings.client_id,
            redirect_uri=self.redirect_uri,
            code_challenge_method="S256",
        )
        authorization_url, returned_state = session.create_authorization_url(
            _AUTHORIZE_URL,
            state=state,
            code_verifier=code_verifier,
        )
        if returned_state != state:
            raise AuthError(
                "GitHub login could not be started",
                code="oauth_start_failed",
                status_code=502,
            )
        return authorization_url

    def fetch_token(self, *, code: str, code_verifier: str) -> Mapping[str, object]:
        session = OAuth2Session(
            client_id=self.settings.client_id,
            client_secret=self.settings.client_secret,
            redirect_uri=self.redirect_uri,
            token_endpoint_auth_method="client_secret_post",
        )
        return dict(
            session.fetch_token(
                _TOKEN_URL,
                code=code,
                code_verifier=code_verifier,
                headers={"Accept": "application/json"},
                timeout=(5, 30),
                allow_redirects=False,
            )
        )


class MemorySessions:
    """Bounded in-memory sessions and one-time OAuth state."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        session_ttl_seconds: float = 1800.0,
        state_ttl_seconds: float = _OAUTH_TTL_SECONDS,
        max_sessions: int = _SESSION_LIMIT,
        max_states: int = _STATE_LIMIT,
    ) -> None:
        self.clock = clock
        self.session_ttl_seconds = session_ttl_seconds
        self.state_ttl_seconds = state_ttl_seconds
        self.max_sessions = max_sessions
        self.max_states = max_states
        self._lock = RLock()
        self._sessions: dict[str, _SessionRecord] = {}
        self._states: dict[str, _PendingState] = {}

    def ensure_prelogin(self, sid: str | None) -> str:
        now = self.clock()
        with self._lock:
            self._purge_locked(now)
            if sid is not None:
                existing = self._sessions.get(sid)
                if existing is not None and existing.expires_at > now:
                    return existing.sid
            record = _SessionRecord(
                sid=self._new_token(),
                csrf_token=self._new_token(),
                created_at=now,
                expires_at=now + self.session_ttl_seconds,
                touched_at=now,
            )
            self._sessions[record.sid] = record
            self._trim_sessions_locked()
            return record.sid

    def register_state(self, sid: str, *, next_path: str, code_verifier: str) -> str:
        now = self.clock()
        state = self._new_token()
        with self._lock:
            self._purge_locked(now)
            self._states[state] = _PendingState(
                sid=sid,
                code_verifier=code_verifier,
                next_path=next_path,
                expires_at=now + self.state_ttl_seconds,
            )
            self._trim_states_locked()
        return state

    def consume_state(self, state: str, *, sid: str | None) -> _PendingState:
        now = self.clock()
        with self._lock:
            self._purge_locked(now)
            pending = self._states.get(state)
            if pending is None:
                raise AuthError(
                    "GitHub login state is invalid",
                    code="invalid_state",
                    status_code=400,
                )
            if sid is None or sid != pending.sid:
                raise AuthError(
                    "GitHub login state is invalid",
                    code="invalid_state",
                    status_code=400,
                )
            self._states.pop(state, None)
            return pending

    def create_authenticated(
        self,
        pending_sid: str,
        *,
        actor_id: int,
        actor_login: str,
        access_token: str,
        access_token_expires_at: float,
    ) -> AuthorizedSession:
        now = self.clock()
        expires_at = min(now + self.session_ttl_seconds, access_token_expires_at)
        record = _SessionRecord(
            sid=self._new_token(),
            csrf_token=self._new_token(),
            created_at=now,
            expires_at=expires_at,
            actor_id=actor_id,
            actor_login=actor_login,
            access_token=access_token,
            touched_at=now,
        )
        with self._lock:
            self._purge_locked(now)
            self._sessions.pop(pending_sid, None)
            self._sessions[record.sid] = record
            self._trim_sessions_locked()
        return AuthorizedSession(
            sid=record.sid,
            actor_id=actor_id,
            actor_login=actor_login,
            access_token=access_token,
            csrf_token=record.csrf_token,
            expires_at=record.expires_at,
        )

    def get(self, sid: str | None) -> AuthorizedSession | None:
        if sid is None:
            return None
        now = self.clock()
        with self._lock:
            self._purge_locked(now)
            record = self._sessions.get(sid)
            if record is None or record.expires_at <= now:
                return None
            if (
                record.actor_id is None
                or record.actor_login is None
                or record.access_token is None
            ):
                return None
            record.touched_at = now
            return AuthorizedSession(
                sid=record.sid,
                actor_id=record.actor_id,
                actor_login=record.actor_login,
                access_token=record.access_token,
                csrf_token=record.csrf_token,
                expires_at=record.expires_at,
            )

    def csrf_token(self, sid: str | None) -> str | None:
        if sid is None:
            return None
        now = self.clock()
        with self._lock:
            self._purge_locked(now)
            record = self._sessions.get(sid)
            if record is None or record.expires_at <= now:
                return None
            return record.csrf_token

    def validate_csrf(self, sid: str | None, presented: str | None) -> None:
        actual = self.csrf_token(sid)
        if actual is None or not isinstance(presented, str):
            raise AuthError(
                "CSRF token is invalid",
                code="csrf_invalid",
                status_code=403,
            )
        if not hmac.compare_digest(actual, presented):
            raise AuthError(
                "CSRF token is invalid",
                code="csrf_invalid",
                status_code=403,
            )

    def destroy(self, sid: str | None) -> None:
        if sid is None:
            return
        with self._lock:
            self._sessions.pop(sid, None)

    def purge_expired(self) -> int:
        with self._lock:
            return self._purge_locked(self.clock())

    def cookie_settings(self, settings: Settings) -> dict[str, object]:
        max_age = int(settings.session_ttl.total_seconds())
        secure = urlsplit(settings.base_url).scheme.lower() == "https"
        return {
            "key": SESSION_COOKIE_NAME,
            "path": "/",
            "httponly": True,
            "samesite": "Lax",
            "secure": secure,
            "max_age": max_age,
        }

    def _purge_locked(self, now: float) -> int:
        removed = 0
        expired_sessions = [
            sid for sid, record in self._sessions.items() if record.expires_at <= now
        ]
        for sid in expired_sessions:
            self._sessions.pop(sid, None)
            removed += 1
        expired_states = [
            state for state, pending in self._states.items() if pending.expires_at <= now
        ]
        for state in expired_states:
            self._states.pop(state, None)
            removed += 1
        return removed

    def _trim_sessions_locked(self) -> None:
        if len(self._sessions) <= self.max_sessions:
            return
        victims = sorted(
            self._sessions.values(),
            key=lambda item: (item.touched_at, item.created_at, item.sid),
        )
        for record in victims[: len(self._sessions) - self.max_sessions]:
            self._sessions.pop(record.sid, None)

    def _trim_states_locked(self) -> None:
        if len(self._states) <= self.max_states:
            return
        victims = sorted(
            self._states.items(),
            key=lambda item: (item[1].expires_at, item[0]),
        )
        for state, _pending in victims[: len(self._states) - self.max_states]:
            self._states.pop(state, None)

    def _new_token(self) -> str:
        return secrets.token_urlsafe(32)


class AuthManager:
    """HTTP-focused GitHub login and live identity verification."""

    def __init__(
        self,
        settings: Settings,
        github: GitHubClient,
        *,
        oauth: object | None = None,
        sessions: MemorySessions | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.github = github
        self.clock = clock
        self.oauth = oauth if oauth is not None else AuthlibGitHubOAuth(settings)
        self.sessions = (
            sessions
            if sessions is not None
            else MemorySessions(
                clock=clock,
                session_ttl_seconds=settings.session_ttl.total_seconds(),
            )
        )

    def begin(self, sid: str | None, next_path: str | None) -> tuple[str, str]:
        safe_next = normalize_next_path(next_path)
        prelogin_sid = self.sessions.ensure_prelogin(sid)
        code_verifier = secrets.token_urlsafe(48)
        state = self.sessions.register_state(
            prelogin_sid,
            next_path=safe_next,
            code_verifier=code_verifier,
        )
        authorization_url = self._oauth_create_authorization_url(
            state=state,
            code_verifier=code_verifier,
        )
        return prelogin_sid, authorization_url

    def callback(
        self,
        sid: str | None,
        *,
        state: str | None,
        code: str | None,
    ) -> tuple[AuthorizedSession, str]:
        if not state or not code:
            raise AuthError(
                "GitHub login callback is invalid",
                code="invalid_callback",
                status_code=400,
            )
        pending = self.sessions.consume_state(state, sid=sid)
        token = self._oauth_fetch_token(code=code, code_verifier=pending.code_verifier)
        access_token = _require_access_token(token)
        access_token_expires_at = _token_expiry(token, now=self.clock())
        actor = self._validate_live_login(access_token)
        session = self.sessions.create_authenticated(
            pending.sid,
            actor_id=actor["id"],
            actor_login=actor["login"],
            access_token=access_token,
            access_token_expires_at=access_token_expires_at,
        )
        return session, pending.next_path

    def authorize(
        self,
        sid: str | None,
        *,
        pr: int | None = None,
        require_author: bool = False,
    ) -> AuthorizedSession:
        session = self.sessions.get(sid)
        if session is None:
            raise AuthError("Sign in required", code="login_required", status_code=401)
        try:
            actor = self._validate_live_login(session.access_token)
        except AuthError:
            self.sessions.destroy(session.sid)
            raise
        if actor["id"] != session.actor_id:
            self.sessions.destroy(session.sid)
            raise AuthError("Sign in required", code="login_required", status_code=401)
        pull: JsonObject | None = None
        if pr is not None:
            try:
                payload = self.github.pull(pr, user_token=session.access_token)
            except GitHubError:
                self.sessions.destroy(session.sid)
                raise AuthError("Sign in required", code="login_required", status_code=401) from None
            pull = payload
            if require_author:
                user = payload.get("user")
                if not isinstance(user, dict):
                    raise AuthError(
                        "GitHub pull request is invalid",
                        code="github_response",
                        status_code=502,
                    )
                author_id = user.get("id")
                if isinstance(author_id, bool) or not isinstance(author_id, int):
                    raise AuthError(
                        "GitHub pull request is invalid",
                        code="github_response",
                        status_code=502,
                    )
                if author_id != session.actor_id:
                    raise AuthError(
                        "Only the pull request author can continue",
                        code="forbidden",
                        status_code=403,
                    )
        return AuthorizedSession(
            sid=session.sid,
            actor_id=session.actor_id,
            actor_login=actor["login"],
            access_token=session.access_token,
            csrf_token=session.csrf_token,
            expires_at=session.expires_at,
            pull=pull,
        )

    def validate_csrf(self, sid: str | None, presented: str | None) -> None:
        self.sessions.validate_csrf(sid, presented)

    def logout(self, sid: str | None) -> None:
        self.sessions.destroy(sid)

    def purge_expired(self) -> int:
        return self.sessions.purge_expired()

    def _validate_live_login(self, access_token: str) -> dict[str, object]:
        try:
            user = self.github.user(access_token)
            self.github.repository_info(user_token=access_token)
        except GitHubError:
            raise AuthError("Sign in required", code="login_required", status_code=401) from None
        actor_id = user.get("id")
        actor_login = user.get("login")
        if isinstance(actor_id, bool) or not isinstance(actor_id, int) or actor_id <= 0:
            raise AuthError("GitHub user is invalid", code="github_response", status_code=502)
        if not isinstance(actor_login, str) or not actor_login:
            raise AuthError("GitHub user is invalid", code="github_response", status_code=502)
        return {"id": actor_id, "login": actor_login}

    def _oauth_create_authorization_url(self, *, state: str, code_verifier: str) -> str:
        creator = getattr(self.oauth, "create_authorization_url", None)
        if not callable(creator):
            raise AuthError("GitHub login could not be started", code="oauth_start_failed", status_code=502)
        url = creator(state=state, code_verifier=code_verifier)
        if not isinstance(url, str) or not url:
            raise AuthError("GitHub login could not be started", code="oauth_start_failed", status_code=502)
        return url

    def _oauth_fetch_token(self, *, code: str, code_verifier: str) -> Mapping[str, object]:
        fetcher = getattr(self.oauth, "fetch_token", None)
        if not callable(fetcher):
            raise AuthError("GitHub login failed", code="oauth_failed", status_code=401)
        try:
            token = fetcher(code=code, code_verifier=code_verifier)
        except (
            GitHubError,
            LookupError,
            OAuthError,
            OAuth2Error,
            requests.RequestException,
            ValueError,
            TypeError,
        ):
            raise AuthError("GitHub login failed", code="oauth_failed", status_code=401) from None
        if not isinstance(token, Mapping):
            raise AuthError("GitHub login failed", code="oauth_failed", status_code=401)
        return token


def normalize_next_path(next_path: str | None) -> str:
    raw = (next_path or "").strip()
    if not raw:
        return _DASHBOARD_PATH
    parsed = urlsplit(raw)
    if parsed.scheme or parsed.netloc or raw.startswith("//"):
        raise AuthError("next path is invalid", code="invalid_next", status_code=400)
    if parsed.query or parsed.fragment:
        raise AuthError("next path is invalid", code="invalid_next", status_code=400)
    if parsed.path == _DASHBOARD_PATH:
        return parsed.path
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) == 2 and parts[0] == "prs" and parts[1].isdigit() and int(parts[1]) > 0:
        return parsed.path
    raise AuthError("next path is invalid", code="invalid_next", status_code=400)


def _require_access_token(token: Mapping[str, object]) -> str:
    access_token = token.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise AuthError("GitHub login failed", code="oauth_failed", status_code=401)
    return access_token


def _token_expiry(token: Mapping[str, object], *, now: float) -> float:
    expires_at = token.get("expires_at")
    if isinstance(expires_at, (int, float)) and not isinstance(expires_at, bool):
        if float(expires_at) > now:
            return float(expires_at)
        raise AuthError("GitHub login failed", code="oauth_failed", status_code=401)
    if isinstance(expires_at, str):
        try:
            numeric = float(expires_at)
        except ValueError:
            numeric = 0.0
        if numeric > now:
            return numeric
        raise AuthError("GitHub login failed", code="oauth_failed", status_code=401)
    expires_in = token.get("expires_in")
    if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool):
        if float(expires_in) > 0:
            return now + float(expires_in)
        raise AuthError("GitHub login failed", code="oauth_failed", status_code=401)
    if isinstance(expires_in, str) and expires_in.isdigit():
        numeric_in = float(expires_in)
        if numeric_in > 0:
            return now + numeric_in
    raise AuthError("GitHub login failed", code="oauth_failed", status_code=401)
