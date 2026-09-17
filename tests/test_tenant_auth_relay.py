"""Independent browser requests, real Authlib PKCE, and scoped relay contracts."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from registration_transport import Integration, integration
from test_app_presentation_render import RenderSettings, _snapshot
from test_verification_steps import RECEIPT_ID, context, receipt
from lasthuman.server import relay
from lasthuman.server.presentation import PresentationView, presentation_revision, render_presentation

__all__ = ["integration"]


def test_oauth_state_sid_namespace_replay_rotation_and_root_callback(integration: Integration) -> None:
    for repo_id in (101, 102):
        integration.open_event(repo_id)
    browser = integration.client()
    first = browser.get("/repos/101/auth/github", query_string={"next": "/prs/1"})
    other = browser.get("/repos/102/auth/github", query_string={"next": "/prs/1"})
    assert first.status_code == other.status_code == 302
    state = parse_qs(urlsplit(first.location).query)["state"][0]
    code = integration.http.oauth_code(first.location, allowed={101})
    one = integration.manager.resolve(101).settings.session_cookie_name
    two = integration.manager.resolve(102).settings.session_cookie_name
    assert one != two
    before = browser.get_cookie(one, domain="bot.example")
    untouched = browser.get_cookie(two, domain="bot.example")
    assert before is not None and untouched is not None and before.value != untouched.value
    assert integration.client().get("/auth/github/callback",
                                    query_string={"state": state, "code": code}).status_code == 400
    assert browser.get("/auth/github/callback",
                       query_string={"state": state.replace("r101.", "r102.", 1), "code": code}).status_code == 400
    callback = browser.get("/auth/github/callback", query_string={"state": state, "code": code})
    assert callback.status_code == 302 and callback.location == "/repos/101/prs/1"
    rotated = browser.get_cookie(one, domain="bot.example")
    assert rotated is not None and rotated.value != before.value
    assert browser.get_cookie(two, domain="bot.example") == untouched
    assert browser.get("/auth/github/callback", query_string={"state": state, "code": code}).status_code == 400
    external = browser.get("/repos/101/auth/github", query_string={"next": "https://evil.example/steal"})
    assert external.status_code == 400 and "Location" not in external.headers
    assert integration.client().get("/dashboard").status_code == 400
    assert integration.client().get("/prs/1").status_code == 400
    denied = integration.client()
    login = denied.get("/repos/102/auth/github")
    denied_state = parse_qs(urlsplit(login.location).query)["state"][0]
    denied_code = integration.http.oauth_code(login.location, actor=99, allowed={101})
    result = denied.get("/auth/github/callback", query_string={"state": denied_state, "code": denied_code})
    assert result.status_code == 401
    assert denied.get("/repos/102/dashboard").status_code == 302


def test_presentation_scoped_url_affects_revision_without_changing_fixed_shape() -> None:
    view = PresentationView(_snapshot(pr=7), "awaiting_author")
    fixed = RenderSettings(base_url="https://bot.example")
    tenant = RenderSettings(base_url="https://bot.example", path_prefix="/repos/303")
    assert render_presentation(view, fixed).details_url == "https://bot.example/prs/7"
    assert render_presentation(view, tenant).details_url == "https://bot.example/repos/303/prs/7"
    assert presentation_revision(fixed) != presentation_revision(tenant)
    with pytest.raises(ValueError, match="path_prefix"):
        render_presentation(view, RenderSettings(path_prefix="/repos/303/../404"))


@pytest.mark.parametrize("path", ["/repos/999", "/repos/1361123778/../999", ""])
def test_relay_rejects_foreign_and_traversal_repository_path(path: str) -> None:
    payload = {"repository_path": path, "receipt": receipt(), "verified_at": None,
               "gate": {"context": "last-human/human-verified", "state": "waiting_verification",
                        "target_url": f"https://bot.example/repos/1361123778/receipts/{RECEIPT_ID}",
                        "status_id": None, "error_code": None}}
    with pytest.raises(relay.RelayError, match="repository path"):
        relay._require_publication_view(payload, RECEIPT_ID, context().settings)


def test_relay_rejects_root_publication_when_receipt_is_scoped() -> None:
    payload = {"repository_path": "/repos/1361123778", "receipt": receipt(), "verified_at": None,
               "gate": {"context": "last-human/human-verified", "state": "waiting_verification",
                        "target_url": f"https://bot.example/receipts/{RECEIPT_ID}",
                        "status_id": None, "error_code": None}}
    with pytest.raises(relay.RelayError, match="target URL mismatch"):
        relay._require_publication_view(payload, RECEIPT_ID, context().settings)
