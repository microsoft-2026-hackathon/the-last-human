"""GitHub App runtime configuration."""

from __future__ import annotations

import ipaddress
import hashlib
import hmac
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import ClassVar, Literal, cast
from urllib.parse import urlsplit

from .registration import RegistrationOperationalError, RepositoryContext
from .runtime_lock import (
    RUNTIME_LOCK_NAME, StatePathError, pending_import_journals,
    prepare_private_directory, reject_path_aliases, validate_state_path,
)

Mode = Literal["development", "live"]
RegistrationMode = Literal["fixed", "first-event"]

_DEFAULT_DATABASE = Path(".work/lasthuman.sqlite3")
_DEFAULT_REGISTRATION_DATABASE = Path(".work/lasthuman-registry.sqlite3")
_DEFAULT_STATE_ROOT = Path(".work/lasthuman")
_DEFAULT_WORKFLOW = "lasthuman-app.yml"
_DEFAULT_WORKFLOW_REF = "refs/heads/main"
_DEVELOPMENT_CONTEXT = "last-human/human-verified-dev"
_LIVE_CONTEXT = "last-human/human-verified"
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_WORKFLOW_RE = re.compile(r"^[A-Za-z0-9._-]+\.ya?ml$")
_WORKFLOW_REF_RE = re.compile(r"^refs/heads/[A-Za-z0-9._/-]+$")
_DEFAULT_PRESENTATION_NAME = "The Last Human"
_DEFAULT_PRESENTATION_LOCALE = "ko"
_DEFAULT_PRESENTATION_MAX_CHARS = 6000
_DEFAULT_PRESENTATION_REASON_LIMIT = 3
_DEFAULT_PRESENTATION_DETAIL_LIMIT = 10
_DEFAULT_PRESENTATION_PATHS_PER_GROUP = 2
_DISPLAY_NAME_MAX_CHARS = 100
_PRESENTATION_MAX_CHARS_RANGE = (1000, 100_000)
_QUESTION_COUNT_RANGE = (1, 5)
_PRESENTATION_LIMIT_RANGE = (1, 1000)


class ConfigurationError(ValueError):
    """Fail-fast configuration error."""


@dataclass(frozen=True)
class Settings:
    app_id: int
    client_id: str
    client_secret: str = field(repr=False)
    private_key_file: Path = field(repr=False)
    installation_id: int
    repository: str
    repository_id: int
    owner_id: int
    base_url: str
    secret_key: str = field(repr=False)
    database: Path
    mode: Mode
    status_context: str
    workflow: str
    workflow_ref: str
    oidc_audience: str
    question_count: int = 3
    checks_enabled: bool = False
    check_name: str = _DEFAULT_PRESENTATION_NAME
    presentation_name: str = _DEFAULT_PRESENTATION_NAME
    presentation_locale: Literal["ko", "en"] = "ko"
    presentation_max_chars: int = _DEFAULT_PRESENTATION_MAX_CHARS
    presentation_reason_limit: int = _DEFAULT_PRESENTATION_REASON_LIMIT
    presentation_detail_limit: int = _DEFAULT_PRESENTATION_DETAIL_LIMIT
    presentation_paths_per_group: int = _DEFAULT_PRESENTATION_PATHS_PER_GROUP
    #: 대시보드에 집계 단계에서 더하는 데모 이력. 없으면 실측만 보여준다.
    demo_seed: Path | None = None
    path_prefix: str = ""
    tenant_generation: int | None = None

    session_ttl: ClassVar[timedelta] = timedelta(minutes=30)

    def __post_init__(self) -> None:
        if self.path_prefix:
            expected = f"/repos/{_context_positive_int(self.repository_id, 'repository_id')}"
            if self.path_prefix != expected:
                raise ConfigurationError("path_prefix must be empty or match /repos/<repository_id>")
        if self.tenant_generation is not None:
            _context_positive_int(self.tenant_generation, "generation")

    @property
    def public_base_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.path_prefix}"

    @property
    def session_cookie_name(self) -> str:
        if not self.path_prefix:
            return "lasthuman_sid"
        namespace = f"{self.app_id}:{self.repository_id}".encode("ascii")
        digest = hmac.new(self.secret_key.encode("utf-8"), namespace, hashlib.sha256).hexdigest()[:24]
        return f"lasthuman_sid_{digest}"

    @property
    def oauth_state_prefix(self) -> str:
        return f"r{self.repository_id}." if self.path_prefix else ""

    @classmethod
    def from_env(cls) -> Settings:
        return cast(Settings, _settings_from_env(cls, registration_mode="fixed"))


@dataclass(frozen=True)
class GatewaySettings:
    app_id: int
    client_id: str
    client_secret: str = field(repr=False)
    private_key_file: Path = field(repr=False)
    base_url: str
    secret_key: str = field(repr=False)
    database: Path
    state_root: Path
    mode: Mode
    registration_mode: Literal["first-event"]
    status_context: str
    workflow: str
    workflow_ref: str
    oidc_audience: str
    question_count: int = 3
    checks_enabled: bool = False
    check_name: str = _DEFAULT_PRESENTATION_NAME
    presentation_name: str = _DEFAULT_PRESENTATION_NAME
    presentation_locale: Literal["ko", "en"] = "ko"
    presentation_max_chars: int = _DEFAULT_PRESENTATION_MAX_CHARS
    presentation_reason_limit: int = _DEFAULT_PRESENTATION_REASON_LIMIT
    presentation_detail_limit: int = _DEFAULT_PRESENTATION_DETAIL_LIMIT
    presentation_paths_per_group: int = _DEFAULT_PRESENTATION_PATHS_PER_GROUP
    allowed_owner_ids: frozenset[int] = field(default_factory=frozenset)
    max_registered_repositories: int = 32
    positive_ttl_seconds: int = 60
    negative_ttl_seconds: int = 15
    max_model_calls: int = 4

    def __post_init__(self) -> None:
        if self.registration_mode != "first-event" or self.workflow_ref != _DEFAULT_WORKFLOW_REF:
            raise ConfigurationError("first-event registration requires the trusted main workflow")
        for name, value, bounds in (
            ("max_registered_repositories", self.max_registered_repositories, (1, 256)),
            ("positive_ttl_seconds", self.positive_ttl_seconds, (5, 3600)),
            ("negative_ttl_seconds", self.negative_ttl_seconds, (1, 300)),
            ("max_model_calls", self.max_model_calls, (1, 4)),
        ):
            _context_positive_int(value, name)
            if not bounds[0] <= value <= bounds[1]:
                raise ConfigurationError(f"{name} must be between {bounds[0]} and {bounds[1]}")
        for owner_id in self.allowed_owner_ids:
            _context_positive_int(owner_id, "owner_id")
        try:
            self.validate_runtime_paths(allow_pending_import=True)
        except RegistrationOperationalError as error:
            raise ConfigurationError(str(error)) from error

    @classmethod
    def from_env(cls) -> GatewaySettings:
        return cast(GatewaySettings, _settings_from_env(cls, registration_mode="first-event"))

    def validate_runtime_paths(
        self, repository_id: int | None = None, *, allow_pending_import: bool = False,
    ) -> None:
        try:
            validate_state_path(self.state_root, directory=True)
            validate_state_path(self.database)
            lock_path = self.state_root / RUNTIME_LOCK_NAME
            validate_state_path(lock_path)
            reject_path_aliases(self.database, lock_path)
            registry = self.database.absolute()
            repos = self.state_root.absolute() / "repos"
            if (
                registry == self.state_root.absolute() or registry.is_relative_to(repos)
                or registry.name == RUNTIME_LOCK_NAME or registry.name.startswith(".lasthuman-import-")
            ):
                raise StatePathError("registration database must be separate from reserved runtime and tenant paths")
            for suffix in ("-journal", "-wal", "-shm"):
                validate_state_path(Path(f"{self.database}{suffix}"))
            validate_state_path(repos, directory=True)
            if not allow_pending_import and pending_import_journals(self.state_root):
                raise StatePathError("pending legacy import; retry import-legacy without --dry-run before startup")
            if repository_id is not None:
                repository_id = _context_positive_int(repository_id, "repository_id")
                tenant_root = repos / str(repository_id)
                validate_state_path(tenant_root, directory=True)
                database = tenant_root / "lasthuman.sqlite"
                validate_state_path(database)
                reject_path_aliases(self.database, lock_path, database)
                for suffix in ("-journal", "-wal", "-shm"):
                    validate_state_path(Path(f"{database}{suffix}"))
                for name in ("cache", "snapshot-cache", "runtime"):
                    cache = tenant_root / name
                    validate_state_path(cache, directory=True)
                    if not cache.exists():
                        continue
                    for root, directories, files in os.walk(cache, followlinks=False, onerror=_raise_walk_error):
                        for directory in directories:
                            validate_state_path(Path(root) / directory, directory=True)
                        for filename in files:
                            validate_state_path(Path(root) / filename)
        except (OSError, StatePathError) as error:
            raise RegistrationOperationalError(f"unsafe first-event storage paths: {error}") from error

    def tenant_settings(self, context: RepositoryContext, *, prepare: bool = True) -> Settings:
        repository_id = _context_positive_int(context.repository_id, "repository_id")
        if not isinstance(context.repository, str) or not _REPOSITORY_RE.fullmatch(context.repository):
            raise ConfigurationError("repository context repository must be an owner/repository name")
        for name, value in (
            ("owner_id", context.owner_id), ("installation_id", context.installation_id),
            ("generation", context.generation),
        ):
            _context_positive_int(value, name)
        tenant_root = self.tenant_root(context)
        if prepare:
            try:
                for directory in (
                    self.state_root, self.state_root / "repos", tenant_root,
                    tenant_root / "cache", tenant_root / "snapshot-cache", tenant_root / "runtime",
                ):
                    prepare_private_directory(directory)
                self.validate_runtime_paths(repository_id)
            except (OSError, StatePathError) as error:
                raise RegistrationOperationalError("tenant storage could not be initialized safely") from error
        return Settings(
            app_id=self.app_id, client_id=self.client_id, client_secret=self.client_secret,
            private_key_file=self.private_key_file, installation_id=context.installation_id,
            repository=context.repository, repository_id=repository_id, owner_id=context.owner_id,
            base_url=self.base_url, secret_key=self.secret_key, database=tenant_root / "lasthuman.sqlite",
            mode=self.mode, status_context=self.status_context, workflow=self.workflow, workflow_ref=self.workflow_ref,
            oidc_audience=self.oidc_audience or context.repository, question_count=self.question_count,
            checks_enabled=self.checks_enabled, check_name=self.check_name,
            presentation_name=self.presentation_name, presentation_locale=self.presentation_locale,
            presentation_max_chars=self.presentation_max_chars,
            presentation_reason_limit=self.presentation_reason_limit,
            presentation_detail_limit=self.presentation_detail_limit,
            presentation_paths_per_group=self.presentation_paths_per_group,
            path_prefix=f"/repos/{repository_id}", tenant_generation=context.generation,
        )

    def tenant_root(self, context: RepositoryContext) -> Path:
        repository_id = _context_positive_int(context.repository_id, "repository_id")
        self.validate_runtime_paths(repository_id)
        return self.state_root / "repos" / str(repository_id)

    def tenant_cache_dir(self, context: RepositoryContext) -> Path:
        return self.tenant_root(context) / "cache"

    def tenant_runtime_dir(self, context: RepositoryContext) -> Path:
        return self.tenant_root(context) / "runtime"


def load_settings() -> Settings | GatewaySettings:
    mode = os.environ.get("TLH_REGISTRATION_MODE", "fixed").strip()
    if mode == "fixed":
        return Settings.from_env()
    if mode == "first-event":
        return GatewaySettings.from_env()
    raise ConfigurationError("TLH_REGISTRATION_MODE must be fixed or first-event")


def _raise_walk_error(error: OSError) -> None:
    raise error


def _context_positive_int(value: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < (1 << 63):
        raise ConfigurationError(f"repository context {field_name} must be a positive integer")
    return value


def _parse_owner_allowlist(raw: str | None) -> frozenset[int]:
    if not raw or not raw.strip():
        return frozenset()
    result: set[int] = set()
    for item in raw.split(","):
        token = item.strip()
        if not re.fullmatch(r"[1-9][0-9]*", token):
            raise ConfigurationError("TLH_REGISTRATION_OWNER_ALLOWLIST must contain positive numeric owner IDs")
        result.add(int(token))
    return frozenset(result)


def _settings_from_env(
    cls: type[Settings] | type[GatewaySettings], *, registration_mode: RegistrationMode,
) -> Settings | GatewaySettings:
    mode = _parse_mode(os.environ.get("TLH_MODE", "development"))
    client_id = _require_env("TLH_CLIENT_ID")
    if not _CLIENT_ID_RE.fullmatch(client_id):
        raise ConfigurationError("TLH_CLIENT_ID must contain only GitHub client ID characters")
    repository = ""
    scoped: dict[str, object]
    if registration_mode == "fixed":
        repository = _require_env("TLH_REPOSITORY")
        if not _REPOSITORY_RE.fullmatch(repository):
            raise ConfigurationError("TLH_REPOSITORY must be an owner/repository name on GitHub.com")
        scoped = {
            "repository": repository,
            "installation_id": _require_positive_int("TLH_INSTALLATION_ID"),
            "repository_id": _require_positive_int("TLH_REPOSITORY_ID"),
            "owner_id": _require_positive_int("TLH_OWNER_ID"),
            "database": _normalize_path(os.environ.get("TLH_DATABASE", str(_DEFAULT_DATABASE))),
            "demo_seed": _optional_path(os.environ.get("TLH_DEMO_SEED")),
        }
    else:
        if os.environ.get("TLH_DEMO_SEED", "").strip():
            raise ConfigurationError("TLH_DEMO_SEED is fixed-mode only")
        scoped = {
            "registration_mode": "first-event",
            "database": _normalize_path(
                os.environ.get("TLH_REGISTRATION_DATABASE", "").strip() or str(_DEFAULT_REGISTRATION_DATABASE)
            ),
            "state_root": _normalize_path(os.environ.get("TLH_STATE_ROOT", "").strip() or str(_DEFAULT_STATE_ROOT)),
            "allowed_owner_ids": _parse_owner_allowlist(os.environ.get("TLH_REGISTRATION_OWNER_ALLOWLIST")),
        }
        for name, env, default, bounds in (
            ("max_registered_repositories", "TLH_MAX_REGISTERED_REPOSITORIES", 32, (1, 256)),
            ("positive_ttl_seconds", "TLH_REGISTRATION_POSITIVE_TTL_SECONDS", 60, (5, 3600)),
            ("negative_ttl_seconds", "TLH_REGISTRATION_NEGATIVE_TTL_SECONDS", 15, (1, 300)),
            ("max_model_calls", "TLH_MAX_MODEL_CALLS", 4, (1, 4)),
        ):
            scoped[name] = _require_bounded_int(env, os.environ.get(env), default=default, bounds=bounds)
    workflow = os.environ.get("TLH_WORKFLOW", _DEFAULT_WORKFLOW).strip()
    if not _WORKFLOW_RE.fullmatch(workflow):
        raise ConfigurationError("TLH_WORKFLOW must be a workflow file name")
    workflow_ref = os.environ.get("TLH_WORKFLOW_REF", _DEFAULT_WORKFLOW_REF).strip()
    if not _WORKFLOW_REF_RE.fullmatch(workflow_ref) or "/../" in workflow_ref:
        raise ConfigurationError("TLH_WORKFLOW_REF must be a trusted refs/heads/* value")
    if mode == "live" and workflow_ref != _DEFAULT_WORKFLOW_REF:
        raise ConfigurationError("live mode requires TLH_WORKFLOW_REF to stay on refs/heads/main")
    if registration_mode == "first-event" and workflow_ref != _DEFAULT_WORKFLOW_REF:
        raise ConfigurationError("first-event registration requires TLH_WORKFLOW_REF to stay on refs/heads/main")
    status_context = os.environ.get(
        "TLH_STATUS_CONTEXT", _DEVELOPMENT_CONTEXT if mode == "development" else _LIVE_CONTEXT,
    ).strip()
    if not status_context:
        raise ConfigurationError("TLH_STATUS_CONTEXT must not be empty")
    if mode == "development" and status_context == _LIVE_CONTEXT:
        raise ConfigurationError("development mode must use a non-production status context")
    if mode == "live" and status_context.endswith("-dev"):
        raise ConfigurationError("live mode must use a live status context")
    check_name = _validate_display_name(os.environ.get("TLH_CHECK_NAME", _DEFAULT_PRESENTATION_NAME), "TLH_CHECK_NAME")
    if check_name.casefold() == status_context.casefold():
        raise ConfigurationError("TLH_CHECK_NAME must differ from TLH_STATUS_CONTEXT")
    private_key_file = _normalize_path(_require_env("TLH_PRIVATE_KEY_FILE"))
    if private_key_file.is_symlink():
        raise ConfigurationError("TLH_PRIVATE_KEY_FILE must not be a symlink")
    if not private_key_file.exists() or not private_key_file.is_file():
        raise ConfigurationError("TLH_PRIVATE_KEY_FILE must point to an existing file")
    if private_key_file.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigurationError("TLH_PRIVATE_KEY_FILE permissions are too open")
    return cls(
        app_id=_require_positive_int("TLH_APP_ID"), client_id=client_id,
        client_secret=_require_secret("TLH_CLIENT_SECRET"), private_key_file=private_key_file,
        base_url=_validate_base_url(_require_env("TLH_BASE_URL"), mode), secret_key=_require_secret("TLH_SECRET_KEY"),
        mode=mode, status_context=status_context, workflow=workflow, workflow_ref=workflow_ref,
        oidc_audience=os.environ.get("TLH_OIDC_AUDIENCE", repository).strip() or repository,
        question_count=_require_bounded_int(
            "TLH_QUESTION_COUNT", os.environ.get("TLH_QUESTION_COUNT"), default=3, bounds=_QUESTION_COUNT_RANGE,
        ),
        checks_enabled=_parse_strict_bool(os.environ.get("TLH_CHECK_RUNS", "false"), "TLH_CHECK_RUNS"),
        check_name=check_name,
        presentation_name=_validate_display_name(
            os.environ.get("TLH_PRESENTATION_NAME", _DEFAULT_PRESENTATION_NAME), "TLH_PRESENTATION_NAME",
        ),
        presentation_locale=_parse_locale(os.environ.get("TLH_PRESENTATION_LOCALE", _DEFAULT_PRESENTATION_LOCALE)),
        presentation_max_chars=_require_bounded_int(
            "TLH_PRESENTATION_MAX_CHARS", os.environ.get("TLH_PRESENTATION_MAX_CHARS"),
            default=_DEFAULT_PRESENTATION_MAX_CHARS, bounds=_PRESENTATION_MAX_CHARS_RANGE,
        ),
        presentation_reason_limit=_require_bounded_int(
            "TLH_PRESENTATION_REASON_LIMIT", os.environ.get("TLH_PRESENTATION_REASON_LIMIT"),
            default=_DEFAULT_PRESENTATION_REASON_LIMIT, bounds=_PRESENTATION_LIMIT_RANGE,
        ),
        presentation_detail_limit=_require_bounded_int(
            "TLH_PRESENTATION_DETAIL_LIMIT", os.environ.get("TLH_PRESENTATION_DETAIL_LIMIT"),
            default=_DEFAULT_PRESENTATION_DETAIL_LIMIT, bounds=_PRESENTATION_LIMIT_RANGE,
        ),
        presentation_paths_per_group=_require_bounded_int(
            "TLH_PRESENTATION_PATHS_PER_GROUP", os.environ.get("TLH_PRESENTATION_PATHS_PER_GROUP"),
            default=_DEFAULT_PRESENTATION_PATHS_PER_GROUP, bounds=_PRESENTATION_LIMIT_RANGE,
        ),
        **scoped,
    )


def _parse_mode(raw: str) -> Mode:
    mode = raw.strip()
    if mode not in {"development", "live"}:
        raise ConfigurationError("TLH_MODE must be development or live")
    return mode


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"{name} is required")
    return value


def _require_positive_int(name: str) -> int:
    raw = _require_env(name)
    if not raw.isdigit():
        raise ConfigurationError(f"{name} must be a positive integer")
    value = int(raw)
    if value <= 0:
        raise ConfigurationError(f"{name} must be a positive integer")
    return value


def _require_secret(name: str) -> str:
    value = _require_env(name)
    if len(value) < 32:
        raise ConfigurationError(f"{name} must be at least 32 characters long")
    return value


def _parse_strict_bool(raw: str, name: str) -> bool:
    value = raw.strip()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ConfigurationError(f"{name} must be exactly true or false")


def _optional_path(raw: str | None) -> Path | None:
    value = (raw or "").strip()
    if not value:
        return None
    path = _normalize_path(value)
    if not path.is_file():
        raise ConfigurationError("TLH_DEMO_SEED must point to an existing file")
    return path


def _parse_locale(raw: str) -> Literal["ko", "en"]:
    value = raw.strip()
    if value in {"ko", "en"}:
        return value
    raise ConfigurationError("TLH_PRESENTATION_LOCALE must be ko or en")


def _validate_display_name(raw: str, name: str) -> str:
    value = raw.strip()
    if not value:
        raise ConfigurationError(f"{name} must not be empty")
    if len(value) > _DISPLAY_NAME_MAX_CHARS:
        raise ConfigurationError(f"{name} must be at most {_DISPLAY_NAME_MAX_CHARS} characters")
    if any(character in value for character in "\r\n"):
        raise ConfigurationError(f"{name} must be a single line")
    return value


def _require_bounded_int(
    name: str,
    raw: str | None,
    *,
    default: int,
    bounds: tuple[int, int],
) -> int:
    if raw is None:
        return default
    value_text = raw.strip()
    if not value_text.isdigit():
        raise ConfigurationError(f"{name} must be an integer")
    value = int(value_text)
    lower, upper = bounds
    if value < lower or value > upper:
        raise ConfigurationError(f"{name} must be between {lower} and {upper}")
    return value


def _normalize_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _validate_base_url(raw: str, mode: Mode) -> str:
    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.netloc:
        raise ConfigurationError("TLH_BASE_URL must be an absolute origin")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConfigurationError("TLH_BASE_URL must not include a path, query, or fragment")
    if parsed.username or parsed.password:
        raise ConfigurationError("TLH_BASE_URL must not embed credentials")

    scheme = parsed.scheme.lower()
    host = parsed.hostname
    if host is None:
        raise ConfigurationError("TLH_BASE_URL must include a host")
    if mode == "development":
        if scheme not in {"http", "https"}:
            raise ConfigurationError("development TLH_BASE_URL must use http or https")
        if not _is_loopback(host):
            raise ConfigurationError("development TLH_BASE_URL must use a loopback host")
    elif scheme != "https":
        raise ConfigurationError("live TLH_BASE_URL must use https")

    normalized_host = f"[{host}]" if ":" in host else host
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{scheme}://{normalized_host}{port}"


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
