"""Trusted GitHub Actions OIDC verification and event decoding."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

import jwt

from .config import ConfigurationError, GatewaySettings, Settings
from .github import JsonObject

_ACTIONS_ISSUER = "https://token.actions.githubusercontent.com"
_ACTIONS_JWKS_URL = f"{_ACTIONS_ISSUER}/.well-known/jwks"
_TRUSTED_WORKFLOW_REF = "refs/heads/main"
_ALLOWED_EVENT_NAMES = frozenset({"pull_request_target", "workflow_dispatch"})
_ALLOWED_PR_ACTIONS = frozenset(
    {"opened", "synchronize", "reopened", "edited", "labeled", "unlabeled", "closed"}
)
_OPEN_EVENT_KEYS = frozenset({"repository_id", "pr", "action", "binding"})
_CLOSED_EVENT_KEYS = frozenset({"repository_id", "pr", "action", "head_sha"})
_BINDING_KEYS = frozenset(
    {
        "repository_id",
        "pr",
        "head_sha",
        "base_sha",
        "policy_version",
        "snapshot_id",
        "score",
        "triggered",
    }
)
_NUMERIC_RE = re.compile(r"^[1-9][0-9]*$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_SQLITE_ID = (1 << 63) - 1


class OIDCError(RuntimeError):
    """Sanitized GitHub Actions OIDC failure."""

    status_code = 401


class EventError(RuntimeError):
    """Sanitized GitHub Actions payload failure."""

    status_code = 400


@dataclass(frozen=True)
class ActionsIdentity:
    event_name: str
    run_id: str
    run_attempt: str
    jti: str
    workflow_ref: str
    repository: str = ""
    repository_id: int | None = None
    owner_id: int | None = None
    sub: str = ""
    audience: str = ""


@dataclass(frozen=True)
class VerifiedActionsIdentity:
    event_name: str
    run_id: str
    run_attempt: str
    jti: str
    workflow_ref: str
    repository: str
    repository_id: int
    owner_id: int
    sub: str
    audience: str


@dataclass(frozen=True)
class ActionsEvent:
    pr: int
    action: str
    expected_binding: JsonObject


class OIDCVerifier:
    """Verify GitHub Actions OIDC tokens against the trusted workflow."""

    def __init__(self, settings: Settings, jwks_client: object | None = None) -> None:
        if settings.workflow_ref != _TRUSTED_WORKFLOW_REF:
            raise ConfigurationError(
                "GitHub Actions OIDC requires TLH_WORKFLOW_REF to stay on refs/heads/main"
            )
        self.settings = settings
        self._jwks_client = (
            jwt.PyJWKClient(_ACTIONS_JWKS_URL) if jwks_client is None else jwks_client
        )

    def verify(self, token: str) -> ActionsIdentity:
        if not isinstance(token, str):
            raise OIDCError("GitHub Actions OIDC token is invalid")
        candidate = token.strip()
        if not candidate:
            raise OIDCError("GitHub Actions OIDC token is invalid")
        try:
            signing_key = cast(
                object,
                self._jwks_client.get_signing_key_from_jwt(candidate),
            )
            claims = cast(
                Mapping[str, object],
                jwt.decode(
                    candidate,
                    cast(object, signing_key).key,
                    algorithms=["RS256"],
                    audience=self.settings.oidc_audience,
                    issuer=_ACTIONS_ISSUER,
                    leeway=30,
                    options={
                        "require": ["exp", "iat", "nbf", "iss", "aud", "sub", "jti"]
                    },
                ),
            )
        except (AttributeError, TypeError, ValueError, jwt.PyJWTError):
            raise OIDCError("GitHub Actions OIDC token is invalid") from None
        try:
            return ActionsIdentity(
                event_name=_require_event_name(claims.get("event_name")),
                run_id=_require_numeric_string(claims.get("run_id"), "run_id"),
                run_attempt=_require_numeric_string(claims.get("run_attempt"), "run_attempt"),
                jti=_require_nonempty_string(claims.get("jti"), "jti"),
                workflow_ref=_require_workflow_source(claims, self.settings),
            )
        except OIDCError:
            raise
        except Exception:
            raise OIDCError("GitHub Actions source is untrusted") from None


class DynamicOIDCVerifier:
    """Tenant claims become authority only after pinned signature and time checks."""

    def __init__(self, settings: GatewaySettings, jwks_client: object | None = None) -> None:
        if settings.workflow_ref != _TRUSTED_WORKFLOW_REF:
            raise ConfigurationError("dynamic GitHub Actions OIDC requires the trusted main workflow")
        self.settings = settings
        self._jwks_client = jwt.PyJWKClient(_ACTIONS_JWKS_URL) if jwks_client is None else jwks_client

    def verify(self, token: str) -> VerifiedActionsIdentity:
        if not isinstance(token, str) or not token.strip():
            raise OIDCError("GitHub Actions OIDC token is invalid")
        candidate = token.strip()
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(candidate)
            claims = cast(Mapping[str, object], jwt.decode(
                candidate, signing_key.key, algorithms=["RS256"], issuer=_ACTIONS_ISSUER, leeway=30,
                options={
                    "verify_aud": False,
                    "require": ["exp", "iat", "nbf", "iss", "aud", "sub", "jti"],
                },
            ))
        except (AttributeError, TypeError, ValueError, jwt.PyJWTError):
            raise OIDCError("GitHub Actions OIDC token is invalid") from None
        for field in ("exp", "iat", "nbf"):
            value = claims[field]
            if isinstance(value, bool) or not isinstance(value, int):
                raise OIDCError("GitHub Actions OIDC token is invalid")
        if claims["exp"] <= claims["iat"] or claims["exp"] <= claims["nbf"]:
            raise OIDCError("GitHub Actions OIDC token is invalid")
        repository = _require_nonempty_string(claims.get("repository"), "repository")
        if not _REPOSITORY_RE.fullmatch(repository):
            raise OIDCError("GitHub Actions source is untrusted")
        repository_id = _require_claim_positive_int(claims.get("repository_id"), "repository_id")
        owner_id = _require_claim_positive_int(claims.get("repository_owner_id"), "repository_owner_id")
        expected = f"{repository}/.github/workflows/{self.settings.workflow}@{self.settings.workflow_ref}"
        workflow_ref = _require_nonempty_string(claims.get("workflow_ref"), "workflow_ref")
        if workflow_ref != expected or claims.get("ref") != self.settings.workflow_ref:
            raise OIDCError("GitHub Actions source is untrusted")
        audience = _require_signed_audience(
            claims.get("aud"), common_audience=self.settings.oidc_audience, repository=repository,
        )
        sub = claims.get("sub")
        if not isinstance(sub, str) or sub not in expected_actions_subjects(
            repository, repository_id, owner_id, self.settings.workflow_ref,
        ):
            raise OIDCError("GitHub Actions source is untrusted")
        return VerifiedActionsIdentity(
            event_name=_require_event_name(claims.get("event_name")),
            run_id=_require_numeric_string(claims.get("run_id"), "run_id"),
            run_attempt=_require_numeric_string(claims.get("run_attempt"), "run_attempt"),
            jti=_require_nonempty_string(claims.get("jti"), "jti"), workflow_ref=workflow_ref,
            repository=repository, repository_id=repository_id, owner_id=owner_id, sub=sub, audience=audience,
        )


def expected_actions_subjects(
    repository: str, repository_id: int, owner_id: int, workflow_ref: str,
) -> tuple[str, str]:
    """Exact GitHub default subjects for already-validated repository claims."""
    owner, name = repository.split("/", 1)
    return (
        f"repo:{repository}:ref:{workflow_ref}",
        f"repo:{owner}@{owner_id}/{name}@{repository_id}:ref:{workflow_ref}",
    )


def _require_signed_audience(value: object, *, common_audience: str, repository: str) -> str:
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    trusted = {repository}
    if common_audience:
        trusted.add(common_audience)
    if not isinstance(value, str) or value not in trusted:
        raise OIDCError("GitHub Actions source is untrusted")
    return value


def decode_event(
    payload: Mapping[str, object],
    identity: ActionsIdentity,
    settings: Settings,
) -> ActionsEvent:
    expected_workflow = (
        f"{settings.repository}/.github/workflows/{settings.workflow}@"
        f"{settings.workflow_ref}"
    )
    if identity.workflow_ref != expected_workflow:
        raise EventError("workflow source is untrusted")
    if identity.event_name != "pull_request_target":
        raise EventError("workflow_dispatch must use the receipt verification route")
    return _decode_repository_event(payload, settings.repository_id)


def decode_event_for_identity(
    payload: Mapping[str, object], identity: ActionsIdentity | VerifiedActionsIdentity,
) -> ActionsEvent:
    if identity.event_name != "pull_request_target":
        raise EventError("only pull_request_target can register a repository")
    repository_id = _require_positive_int(identity.repository_id, "verified repository identity")
    return _decode_repository_event(payload, repository_id)


def _decode_repository_event(payload: Mapping[str, object], trusted_repository_id: int) -> ActionsEvent:
    data = _require_object(payload, "event payload")
    action = _require_action(data.get("action"))
    repository_id = _require_positive_int(data.get("repository_id"), "repository_id")
    if repository_id != trusted_repository_id:
        raise EventError("repository binding mismatch")
    pr = _require_positive_int(data.get("pr"), "pr")
    if action == "closed":
        if frozenset(data) != _CLOSED_EVENT_KEYS:
            raise EventError("closed event payload fields are invalid")
        return ActionsEvent(
            pr=pr,
            action=action,
            expected_binding={
                "repository_id": repository_id,
                "pr": pr,
                "head_sha": _require_sha(data.get("head_sha"), "head_sha"),
            },
        )
    if frozenset(data) != _OPEN_EVENT_KEYS:
        raise EventError("event payload fields are invalid")
    binding = _require_binding(data.get("binding"), repository_id, pr)
    return ActionsEvent(pr=pr, action=action, expected_binding=dict(binding))


def _require_workflow_source(
    claims: Mapping[str, object],
    settings: Settings,
) -> str:
    repository = _require_nonempty_string(claims.get("repository"), "repository")
    if repository.casefold() != settings.repository.casefold():
        raise OIDCError("GitHub Actions source is untrusted")
    repository_id = _require_claim_positive_int(claims.get("repository_id"), "repository_id")
    if repository_id != settings.repository_id:
        raise OIDCError("GitHub Actions source is untrusted")
    owner_id = _require_claim_positive_int(
        claims.get("repository_owner_id"),
        "repository_owner_id",
    )
    if owner_id != settings.owner_id:
        raise OIDCError("GitHub Actions source is untrusted")
    workflow_ref = _require_nonempty_string(claims.get("workflow_ref"), "workflow_ref")
    expected = f"{settings.repository}/.github/workflows/{settings.workflow}@{settings.workflow_ref}"
    if workflow_ref != expected:
        raise OIDCError("GitHub Actions source is untrusted")
    ref = _require_nonempty_string(claims.get("ref"), "ref")
    if ref != settings.workflow_ref:
        raise OIDCError("GitHub Actions source is untrusted")
    return workflow_ref


def _require_object(value: object, context: str) -> JsonObject:
    if not isinstance(value, dict):
        raise EventError(f"{context} must be a JSON object")
    return cast(JsonObject, value)


def _require_binding(value: object, repository_id: int, pr: int) -> JsonObject:
    binding = _require_object(value, "binding")
    if frozenset(binding) != _BINDING_KEYS:
        raise EventError("binding keys are invalid")
    if _require_positive_int(binding.get("repository_id"), "binding.repository_id") != repository_id:
        raise EventError("repository binding mismatch")
    if _require_positive_int(binding.get("pr"), "binding.pr") != pr:
        raise EventError("pull request binding mismatch")
    _require_sha(binding.get("head_sha"), "binding.head_sha")
    _require_sha(binding.get("base_sha"), "binding.base_sha")
    _require_hex64(binding.get("policy_version"), "binding.policy_version")
    _require_hex64(binding.get("snapshot_id"), "binding.snapshot_id")
    _require_nonnegative_int(binding.get("score"), "binding.score")
    if not isinstance(binding.get("triggered"), bool):
        raise EventError("binding.triggered must be a boolean")
    return binding


def _require_event_name(value: object) -> str:
    event_name = _require_nonempty_string(value, "event_name")
    if event_name not in _ALLOWED_EVENT_NAMES:
        raise OIDCError("GitHub Actions source is untrusted")
    return event_name


def _require_action(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise EventError("action must be a non-empty string")
    action = value
    if action not in _ALLOWED_PR_ACTIONS:
        raise EventError("action is invalid")
    return action


def _require_numeric_string(value: object, field_name: str) -> str:
    raw = _require_nonempty_string(value, field_name)
    if not _NUMERIC_RE.fullmatch(raw):
        raise OIDCError("GitHub Actions source is untrusted")
    return raw


def _require_claim_positive_int(value: object, _field_name: str) -> int:
    if isinstance(value, bool):
        raise OIDCError("GitHub Actions source is untrusted")
    if isinstance(value, int):
        if 0 < value <= _MAX_SQLITE_ID:
            return value
        raise OIDCError("GitHub Actions source is untrusted")
    if isinstance(value, str) and _NUMERIC_RE.fullmatch(value):
        if len(value) <= 19 and int(value) <= _MAX_SQLITE_ID:
            return int(value)
    raise OIDCError("GitHub Actions source is untrusted")


def _require_positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= _MAX_SQLITE_ID:
        raise EventError(f"{field_name} must be a positive integer")
    return value


def _require_nonnegative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EventError(f"{field_name} must be a non-negative integer")
    return value


def _require_nonempty_string(value: object, _field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise OIDCError("GitHub Actions source is untrusted")
    return value


def _require_sha(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise EventError(f"{field_name} must be a 40-character hexadecimal string")
    return value


def _require_hex64(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _HEX64_RE.fullmatch(value):
        raise EventError(f"{field_name} must be a 64-character hexadecimal string")
    return value
