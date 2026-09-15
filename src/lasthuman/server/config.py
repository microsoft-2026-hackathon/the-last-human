"""GitHub App runtime configuration."""

from __future__ import annotations

import ipaddress
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import ClassVar, Literal
from urllib.parse import urlsplit

Mode = Literal["development", "live"]

_DEFAULT_DATABASE = Path(".work/lasthuman.sqlite3")
_DEFAULT_WORKFLOW = "lasthuman-app.yml"
_DEFAULT_WORKFLOW_REF = "refs/heads/main"
_DEVELOPMENT_CONTEXT = "comprehension-gate-dev"
_LIVE_CONTEXT = "comprehension-gate"
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_WORKFLOW_RE = re.compile(r"^[A-Za-z0-9._-]+\.ya?ml$")
_WORKFLOW_REF_RE = re.compile(r"^refs/heads/[A-Za-z0-9._/-]+$")


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

    session_ttl: ClassVar[timedelta] = timedelta(minutes=30)

    @classmethod
    def from_env(cls) -> Settings:
        mode = _parse_mode(os.environ.get("TLH_MODE", "development"))
        client_id = _require_env("TLH_CLIENT_ID")
        if not _CLIENT_ID_RE.fullmatch(client_id):
            raise ConfigurationError("TLH_CLIENT_ID must contain only GitHub client ID characters")

        repository = _require_env("TLH_REPOSITORY")
        if not _REPOSITORY_RE.fullmatch(repository):
            raise ConfigurationError("TLH_REPOSITORY must be an owner/repository name on GitHub.com")

        workflow = os.environ.get("TLH_WORKFLOW", _DEFAULT_WORKFLOW).strip()
        if not _WORKFLOW_RE.fullmatch(workflow):
            raise ConfigurationError("TLH_WORKFLOW must be a workflow file name")

        workflow_ref = os.environ.get("TLH_WORKFLOW_REF", _DEFAULT_WORKFLOW_REF).strip()
        if not _WORKFLOW_REF_RE.fullmatch(workflow_ref) or "/../" in workflow_ref:
            raise ConfigurationError("TLH_WORKFLOW_REF must be a trusted refs/heads/* value")
        if mode == "live" and workflow_ref != _DEFAULT_WORKFLOW_REF:
            raise ConfigurationError("live mode requires TLH_WORKFLOW_REF to stay on refs/heads/main")

        status_context = os.environ.get(
            "TLH_STATUS_CONTEXT",
            _DEVELOPMENT_CONTEXT if mode == "development" else _LIVE_CONTEXT,
        ).strip()
        if not status_context:
            raise ConfigurationError("TLH_STATUS_CONTEXT must not be empty")
        if mode == "development" and status_context == _LIVE_CONTEXT:
            raise ConfigurationError(
                "development mode must use a non-production status context"
            )
        if mode == "live" and status_context.endswith("-dev"):
            raise ConfigurationError("live mode must use a live status context")

        private_key_file = _normalize_path(_require_env("TLH_PRIVATE_KEY_FILE"))
        if private_key_file.is_symlink():
            raise ConfigurationError("TLH_PRIVATE_KEY_FILE must not be a symlink")
        if not private_key_file.exists() or not private_key_file.is_file():
            raise ConfigurationError("TLH_PRIVATE_KEY_FILE must point to an existing file")
        mode_bits = private_key_file.stat().st_mode
        if mode_bits & (stat.S_IRWXG | stat.S_IRWXO):
            raise ConfigurationError("TLH_PRIVATE_KEY_FILE permissions are too open")

        return cls(
            app_id=_require_positive_int("TLH_APP_ID"),
            client_id=client_id,
            client_secret=_require_secret("TLH_CLIENT_SECRET"),
            private_key_file=private_key_file,
            installation_id=_require_positive_int("TLH_INSTALLATION_ID"),
            repository=repository,
            repository_id=_require_positive_int("TLH_REPOSITORY_ID"),
            owner_id=_require_positive_int("TLH_OWNER_ID"),
            base_url=_validate_base_url(_require_env("TLH_BASE_URL"), mode),
            secret_key=_require_secret("TLH_SECRET_KEY"),
            database=_normalize_path(os.environ.get("TLH_DATABASE", str(_DEFAULT_DATABASE))),
            mode=mode,
            status_context=status_context,
            workflow=workflow,
            workflow_ref=workflow_ref,
            oidc_audience=os.environ.get("TLH_OIDC_AUDIENCE", repository).strip() or repository,
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
