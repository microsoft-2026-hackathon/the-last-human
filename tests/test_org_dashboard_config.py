from __future__ import annotations

import pytest

from test_scoped_demo_seed import gateway_config
from lasthuman.server.config import ConfigurationError, GatewaySettings, load_settings
from lasthuman.server.registration import RepositoryContext

__all__ = ["gateway_config"]


@pytest.mark.parametrize("mode", ["fixed", "first-event"])
def test_organization_demo_is_explicit_and_does_not_create_state(
    gateway_config: GatewaySettings, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    monkeypatch.setenv("TLH_REGISTRATION_MODE", mode)
    if mode == "fixed":
        for key, value in {
            "TLH_REPOSITORY": "acme/the-last-human", "TLH_REPOSITORY_ID": "101",
            "TLH_OWNER_ID": "77", "TLH_INSTALLATION_ID": "201",
        }.items():
            monkeypatch.setenv(key, value)
    assert load_settings().org_demo_enabled is False
    monkeypatch.setenv("TLH_ORG_DEMO_ENABLED", "true")
    settings = load_settings()
    assert settings.org_demo_enabled is True
    if isinstance(settings, GatewaySettings):
        context = RepositoryContext(101, "acme/the-last-human", 77, 201, 1)
        assert settings.tenant_settings(context, prepare=False).org_demo_enabled is True
    assert not gateway_config.database.exists()
    assert not gateway_config.state_root.exists()
    monkeypatch.setenv("TLH_ORG_DEMO_ENABLED", "invalid")
    with pytest.raises(ConfigurationError, match="TLH_ORG_DEMO_ENABLED"):
        load_settings()
