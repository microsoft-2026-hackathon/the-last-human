"""Configuration and cryptographic/grant boundaries, with real HTTP clients."""

from __future__ import annotations

from dataclasses import replace

import pytest
import requests

from registration_transport import CORE, Integration, integration
from lasthuman.server.config import ConfigurationError, GatewaySettings, Settings, load_settings
from lasthuman.server.events import DynamicOIDCVerifier, OIDCError
from lasthuman.server.github import GitHubClient, GitHubError
from lasthuman.server.registration import RepositoryContext

__all__ = ["integration"]


def test_first_event_defaults_isolate_paths_cookies_and_keep_fixed_compatibility(
    integration: Integration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = integration.settings
    assert (gateway.max_registered_repositories, gateway.positive_ttl_seconds,
            gateway.negative_ttl_seconds, gateway.max_model_calls) == (32, 60, 15, 4)
    first = gateway.tenant_settings(RepositoryContext(101, "acme/one", 77, 201, 1), prepare=False)
    second = gateway.tenant_settings(RepositoryContext(102, "acme/two", 77, 202, 1), prepare=False)
    assert first.database == gateway.state_root / "repos/101/lasthuman.sqlite"
    assert first.database != second.database
    assert first.public_base_url.endswith("/repos/101")
    assert first.session_cookie_name != second.session_cookie_name
    assert first.session_cookie_name != "lasthuman_sid_101"
    assert first.session_cookie_name == replace(first, tenant_generation=2).session_cookie_name
    assert first.session_cookie_name != replace(first, secret_key="another-secret-" * 3).session_cookie_name
    assert first.oauth_state_prefix == "r101." and second.oauth_state_prefix == "r102."
    assert first.oidc_audience == "acme/one" and second.oidc_audience == "acme/two"
    for name, value in {"TLH_REGISTRATION_MODE": "fixed", "TLH_REPOSITORY": "acme/one",
                        "TLH_REPOSITORY_ID": "101", "TLH_OWNER_ID": "77", "TLH_INSTALLATION_ID": "201",
                        "TLH_DATABASE": str(integration.root / "legacy.sqlite")}.items():
        monkeypatch.setenv(name, value)
    fixed = load_settings()
    assert isinstance(fixed, Settings)
    assert fixed.path_prefix == "" and fixed.tenant_generation is None
    assert fixed.session_cookie_name == "lasthuman_sid"
    assert fixed.database == integration.root / "legacy.sqlite"
    monkeypatch.setenv("TLH_REGISTRATION_MODE", "first-event")
    monkeypatch.delenv("TLH_REGISTRATION_DATABASE")
    monkeypatch.chdir(integration.root)
    current = load_settings()
    assert isinstance(current, GatewaySettings)
    assert current.database != fixed.database
    assert not fixed.database.exists()


@pytest.mark.parametrize("limit", [0, 5, 32, -1, True])
def test_global_model_budget_cannot_exceed_four(integration: Integration, limit: int) -> None:
    with pytest.raises(ConfigurationError):
        replace(integration.settings, max_model_calls=limit)


@pytest.mark.parametrize("limit", ["0", "5", "32", "-1", "true"])
def test_environment_model_budget_cannot_exceed_four(
    integration: Integration, monkeypatch: pytest.MonkeyPatch, limit: str,
) -> None:
    monkeypatch.setenv("TLH_MAX_MODEL_CALLS", limit)
    with pytest.raises(ConfigurationError, match="TLH_MAX_MODEL_CALLS"):
        load_settings()


@pytest.mark.parametrize("limit", [1, 4])
def test_supported_model_limits_load(integration: Integration, monkeypatch: pytest.MonkeyPatch, limit: int) -> None:
    monkeypatch.setenv("TLH_MAX_MODEL_CALLS", str(limit))
    settings = load_settings()
    assert isinstance(settings, GatewaySettings) and settings.max_model_calls == limit


@pytest.mark.parametrize("claims", [
    {"repository_id": True}, {"repository_owner_id": False}, {"aud": ["acme/one", "foreign/repo"]},
    {"ref": "refs/heads/feature"}, {"sub": "repo:acme/one:pull_request"},
    {"run_id": ""}, {"run_attempt": "0"},
])
def test_real_rsa_verifier_rejects_invalid_claim_contracts(integration: Integration, claims: dict[str, object]) -> None:
    token = integration.http.signed_token(101, claims=claims)
    with pytest.raises(OIDCError):
        integration.verifier.verify(token)
    assert not integration.http.calls and not integration.registry.list_registered()


def test_common_audience_requires_explicit_configuration(integration: Integration) -> None:
    token = integration.http.signed_token(101, claims={"aud": "shared-actions"})
    with pytest.raises(OIDCError):
        integration.verifier.verify(token)
    verifier = DynamicOIDCVerifier(replace(integration.settings, oidc_audience="shared-actions"))
    identity = verifier.verify(token)
    assert identity.repository_id == 101 and identity.repository == "acme/one"
    assert identity.audience == "shared-actions"


@pytest.mark.parametrize("defect", ["extra-repository", "issued-permissions", "expired"])
def test_tenant_github_client_independently_rejects_bad_issued_tokens(
    integration: Integration, defect: str,
) -> None:
    repo = integration.http.repositories[101]
    settings = integration.settings.tenant_settings(RepositoryContext(101, repo.name, 77, 201, 1))
    client = GitHubClient(settings, requests.Session())
    if defect == "extra-repository":
        repo.extra_scope = True
    elif defect == "issued-permissions":
        repo.token_permissions = {**CORE, "statuses": "read"}
    else:
        repo.token_expired = True
    with pytest.raises(GitHubError):
        client.installation_token()
    assert [call.method for call in integration.http.calls] == ["GET", "POST"]
    assert not repo.statuses and not repo.comments and not repo.dispatches


def test_cached_github_token_is_guarded_and_reverified_after_invalidation(integration: Integration) -> None:
    settings = integration.settings.tenant_settings(RepositoryContext(101, "acme/one", 77, 201, 1))
    guarded: list[bool] = []
    allowed = True

    def guard() -> None:
        guarded.append(allowed)
        if not allowed:
            raise GitHubError("Repository retired")

    client = GitHubClient(settings, requests.Session(), access_guard=guard)
    first = client.installation_token()
    calls = list(integration.http.calls)
    count = len(guarded)
    assert client.installation_token() == first
    assert integration.http.calls == calls and len(guarded) == count + 1
    allowed = False
    with pytest.raises(GitHubError, match="retired"):
        client.installation_token()
    assert integration.http.calls == calls
    allowed = True
    client.invalidate_tokens()
    assert client.installation_token() != first
    assert len(integration.http.calls) == len(calls) * 2
