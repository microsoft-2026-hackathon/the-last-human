from __future__ import annotations

import builtins
import http.client as http_client
import importlib
import io
import json
import os
import socket
import subprocess
import sys
import traceback
import urllib.error
import urllib.request
import urllib.response
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from email.message import Message
from types import ModuleType
from unittest.mock import Mock

import pytest

import lasthuman
from lasthuman import interview, model_auth
from lasthuman.model_auth import _verify_cli_account


TENANT = "11111111-1111-4111-8111-111111111111"
SUBSCRIPTION = "22222222-2222-4222-8222-222222222222"
SCOPE = "https://cognitiveservices.azure.com/.default"
ENDPOINT = (
    "https://demo.openai.azure.com/openai/deployments/gpt-4.1-mini/chat/completions"
    "?api-version=2024-10-21"
)
REPLY = b'{"choices":[{"message":{"content":"model reply"}}]}'
SECRET = "sensitive-value-that-must-not-leak"


class FakeAuthenticationError(Exception):
    pass


class FakeCredentialUnavailableError(FakeAuthenticationError):
    pass


@dataclass
class FakeSdk:
    credential: Mock
    bearer_provider: Mock
    provider: Mock
    account_check: Mock


@pytest.fixture(autouse=True)
def sdk(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeSdk]:
    for name in tuple(os.environ):
        if name.startswith(("LASTHUMAN_", "AZURE_")) or name == "GITHUB_TOKEN":
            monkeypatch.delenv(name)
    model_auth._token_provider.cache_clear()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Unexpected real network or Azure CLI access")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(urllib.request.HTTPHandler, "http_open", forbidden)
    monkeypatch.setattr(urllib.request.HTTPSHandler, "https_open", forbidden)
    fake = FakeSdk(Mock(return_value=object()), Mock(), Mock(return_value="cli-token"), Mock())
    fake.bearer_provider.return_value = fake.provider
    monkeypatch.setattr(model_auth, "_verify_cli_account", fake.account_check)
    modules = {name: ModuleType(name) for name in (
        "azure", "azure.core", "azure.core.exceptions", "azure.identity",
    )}
    for name in ("azure", "azure.core"):
        monkeypatch.setattr(modules[name], "__path__", [], raising=False)
    monkeypatch.setattr(
        modules["azure.core.exceptions"], "ClientAuthenticationError",
        FakeAuthenticationError, raising=False,
    )
    monkeypatch.setattr(
        modules["azure.identity"], "AzureCliCredential", fake.credential, raising=False,
    )
    monkeypatch.setattr(
        modules["azure.identity"], "get_bearer_token_provider",
        fake.bearer_provider, raising=False,
    )
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    yield fake
    model_auth._token_provider.cache_clear()


@pytest.fixture
def azure_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "LASTHUMAN_AUTH_MODE": "azure-cli",
        "LASTHUMAN_PROVIDER": "azure",
        "LASTHUMAN_ENDPOINT": ENDPOINT,
        "LASTHUMAN_MODEL": "gpt-4.1-mini",
        "AZURE_TENANT_ID": TENANT,
        "AZURE_SUBSCRIPTION_ID": SUBSCRIPTION,
        "AZURE_OPENAI_SCOPE": SCOPE,
    }.items():
        monkeypatch.setenv(name, value)


@dataclass
class FakeHttp:
    requests: list[urllib.request.Request]
    open: Mock
    build: Mock


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    requests: list[urllib.request.Request] = []

    def send(request: urllib.request.Request, *, timeout: float) -> io.BytesIO:
        requests.append(request)
        return io.BytesIO(REPLY)

    open_request = Mock(side_effect=send)
    opener = Mock()
    opener.open = open_request
    build = Mock(return_value=opener)
    monkeypatch.setattr(urllib.request, "urlopen", open_request)
    monkeypatch.setattr(urllib.request, "build_opener", build)
    return FakeHttp(requests, open_request, build)


def assert_sanitized(error: pytest.ExceptionInfo[interview.ModelError]) -> None:
    assert SECRET not in "".join(traceback.format_exception(error.value))
    assert error.value.__cause__ is None


def test_cli_provider_reused_and_called_for_each_token(
    monkeypatch: pytest.MonkeyPatch, azure_env: None, sdk: FakeSdk, http: FakeHttp,
) -> None:
    for name in ("LASTHUMAN_API_KEY", "LASTHUMAN_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.setenv(name, SECRET)
    sdk.provider.side_effect = ["first-cli-token", "renewed-cli-token"]
    for _ in range(2):
        assert interview.call_model("question", token=SECRET, timeout=17) == "model reply"
    sdk.credential.assert_called_once_with(subscription=SUBSCRIPTION)
    sdk.bearer_provider.assert_called_once_with(sdk.credential.return_value, SCOPE)
    assert sdk.provider.call_count == 2
    assert sdk.account_check.call_count == 2
    assert [r.get_header("Authorization") for r in http.requests] == [
        "Bearer first-cli-token", "Bearer renewed-cli-token",
    ]
    for request in http.requests:
        assert request.get_header("Api-key") is None
        assert SECRET not in repr(request.header_items())
        assert request.full_url == ENDPOINT
        assert request.get_method() == "POST"
        assert json.loads(request.data) == {
            "model": "gpt-4.1-mini",
            "temperature": 0.2,
            "messages": [{"role": "user", "content": "question"}],
        }
    assert http.open.call_args.kwargs == {"timeout": 17}
    assert isinstance(http.build.call_args.args[0], model_auth.AzureCliNoRedirectHandler)


@pytest.mark.parametrize(("name", "value"), [
    ("AZURE_TENANT_ID", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    ("AZURE_SUBSCRIPTION_ID", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    ("AZURE_OPENAI_SCOPE", "https://ai.azure.com/.default"),
])
def test_provider_cache_binds_identity_and_scope(
    monkeypatch: pytest.MonkeyPatch, azure_env: None, sdk: FakeSdk,
    name: str, value: str,
) -> None:
    original = os.environ[name]
    model_auth.azure_cli_token(ENDPOINT, "azure")
    monkeypatch.setenv(name, value)
    model_auth.azure_cli_token(ENDPOINT, "azure")
    monkeypatch.setenv(name, original)
    model_auth.azure_cli_token(ENDPOINT, "azure")
    assert sdk.credential.call_count == 2
    assert sdk.bearer_provider.call_count == 2
    assert sdk.provider.call_count == 3
    expected_tenant = value if name == "AZURE_TENANT_ID" else TENANT
    expected_subscription = value if name == "AZURE_SUBSCRIPTION_ID" else SUBSCRIPTION
    assert sdk.credential.call_args.kwargs == {
        "subscription": expected_subscription,
    }
    checked = sdk.account_check.call_args_list[1].args[0]
    assert checked.tenant_id == expected_tenant
    assert checked.subscription_id == expected_subscription
    assert sdk.bearer_provider.call_args.args[1] == (
        value if name == "AZURE_OPENAI_SCOPE" else SCOPE
    )


def test_concurrent_requests_share_sdk_provider(azure_env: None, sdk: FakeSdk) -> None:
    with ThreadPoolExecutor(max_workers=4) as workers:
        tokens = list(workers.map(
            lambda _: model_auth.azure_cli_token(ENDPOINT, "azure"), range(12),
        ))
    assert tokens == ["cli-token"] * 12
    assert sdk.credential.call_count == sdk.bearer_provider.call_count == 1
    assert sdk.provider.call_count == 12
    assert sdk.account_check.call_count == 12


def test_cli_verifies_tenant_with_subscription_selected(
    monkeypatch: pytest.MonkeyPatch, azure_env: None, sdk: FakeSdk, http: FakeHttp,
) -> None:
    monkeypatch.setattr(model_auth, "_verify_cli_account", _verify_cli_account)
    query = Mock(return_value=subprocess.CompletedProcess([], 0, TENANT + "\n", ""))
    monkeypatch.setattr(subprocess, "run", query)
    assert interview.call_model("question") == "model reply"
    query.assert_called_once_with(
        [
            "az", "account", "show", "--subscription", SUBSCRIPTION,
            "--query", "tenantId", "--output", "tsv", "--only-show-errors",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    sdk.credential.assert_called_once_with(subscription=SUBSCRIPTION)
    assert len(http.requests) == 1


@pytest.mark.parametrize(("returncode", "stdout"), [
    (1, SECRET),
    (0, SECRET),
    (0, ""),
    (0, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
])
def test_invalid_account_binding_stops_token_and_model_requests(
    monkeypatch: pytest.MonkeyPatch, azure_env: None, sdk: FakeSdk, http: FakeHttp,
    returncode: int, stdout: str,
) -> None:
    monkeypatch.setattr(model_auth, "_verify_cli_account", _verify_cli_account)
    monkeypatch.setattr(
        subprocess, "run",
        Mock(return_value=subprocess.CompletedProcess([], returncode, stdout, SECRET)),
    )
    with pytest.raises(interview.ModelError) as error:
        interview.call_model("question")
    assert_sanitized(error)
    sdk.provider.assert_not_called()
    http.open.assert_not_called()


@pytest.mark.parametrize("failure", [
    FileNotFoundError(SECRET),
    subprocess.TimeoutExpired(["az", SECRET], 10),
    UnicodeError(SECRET),
])
def test_account_query_failures_are_sanitized(
    monkeypatch: pytest.MonkeyPatch, azure_env: None, sdk: FakeSdk, http: FakeHttp,
    failure: Exception,
) -> None:
    monkeypatch.setattr(model_auth, "_verify_cli_account", _verify_cli_account)
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=failure))
    with pytest.raises(interview.ModelError) as error:
        interview.call_model("question")
    assert_sanitized(error)
    sdk.provider.assert_not_called()
    http.open.assert_not_called()


def test_cached_provider_rechecks_account_binding(
    azure_env: None, sdk: FakeSdk, http: FakeHttp,
) -> None:
    assert interview.call_model("question") == "model reply"
    sdk.account_check.side_effect = model_auth.ModelAuthError("Azure account binding changed.")
    with pytest.raises(interview.ModelError, match="binding changed"):
        interview.call_model("question")
    assert sdk.provider.call_count == 1
    assert len(http.requests) == 1


@pytest.mark.parametrize("mode", [None, "", "default"])
@pytest.mark.parametrize("provider", ["azure", "openai"])
@pytest.mark.parametrize(("api_key", "argument", "manual", "github", "expected"), [
    ("api-key", "argument-token", "manual-token", "github-token", "argument-token"),
    ("api-key", None, "manual-token", "github-token", "api-key"),
    (None, "argument-token", "manual-token", "github-token", "argument-token"),
    (None, None, "manual-token", "github-token", "manual-token"),
    (None, None, None, "github-token", "github-token"),
    ("api-key", "", None, None, "api-key"),
])
def test_default_auth_and_loopback_compatibility(
    monkeypatch: pytest.MonkeyPatch, sdk: FakeSdk, http: FakeHttp,
    mode: str | None, provider: str, api_key: str | None, argument: str | None,
    manual: str | None, github: str | None, expected: str,
) -> None:
    monkeypatch.setenv("LASTHUMAN_PROVIDER", provider)
    endpoint = "http://127.0.0.1:9999/chat/completions"
    monkeypatch.setenv("LASTHUMAN_ENDPOINT", endpoint)
    for name, value in (
        ("LASTHUMAN_AUTH_MODE", mode), ("LASTHUMAN_API_KEY", api_key),
        ("LASTHUMAN_TOKEN", manual), ("GITHUB_TOKEN", github),
    ):
        if value is not None:
            monkeypatch.setenv(name, value)
    assert interview.call_model("question", token=argument) == "model reply"
    request = http.requests[0]
    if provider == "azure" and api_key:
        assert request.get_header("Api-key") == api_key
        assert request.get_header("Authorization") is None
    else:
        assert request.get_header("Authorization") == f"Bearer {expected}"
        assert request.get_header("Api-key") is None
    assert request.full_url == endpoint
    http.build.assert_not_called()
    sdk.credential.assert_not_called()


def test_default_missing_credentials(http: FakeHttp, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LASTHUMAN_ENDPOINT", "http://localhost:9999/chat/completions")
    with pytest.raises(interview.ModelError):
        interview.call_model("question")
    http.open.assert_not_called()


@pytest.mark.parametrize("mode", [SECRET, "Azure-CLI", " ", "managed-identity"])
def test_unknown_mode_is_sanitized(
    monkeypatch: pytest.MonkeyPatch, sdk: FakeSdk, http: FakeHttp, mode: str,
) -> None:
    monkeypatch.setenv("LASTHUMAN_AUTH_MODE", mode)
    with pytest.raises(interview.ModelError, match="LASTHUMAN_AUTH_MODE") as error:
        interview.call_model("question")
    assert_sanitized(error)
    sdk.credential.assert_not_called()
    http.open.assert_not_called()


@pytest.mark.parametrize("provider", ["openai", "none", SECRET])
@pytest.mark.parametrize("explicit_endpoint", [True, False])
def test_cli_rejects_other_providers_before_endpoint_resolution(
    monkeypatch: pytest.MonkeyPatch, azure_env: None, sdk: FakeSdk, http: FakeHttp,
    provider: str, explicit_endpoint: bool,
) -> None:
    monkeypatch.setenv("LASTHUMAN_PROVIDER", provider)
    if not explicit_endpoint:
        monkeypatch.delenv("LASTHUMAN_ENDPOINT")
    with pytest.raises(interview.ModelError, match="LASTHUMAN_PROVIDER=azure") as error:
        interview.call_model("question", token=SECRET)
    assert_sanitized(error)
    sdk.credential.assert_not_called()
    http.open.assert_not_called()


@pytest.mark.parametrize("name", ["AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID"])
@pytest.mark.parametrize("value", [None, "", "*", "common", "organizations", SECRET, TENANT + "\n"])
def test_cli_requires_explicit_uuid_identity(
    monkeypatch: pytest.MonkeyPatch, azure_env: None, sdk: FakeSdk, http: FakeHttp,
    name: str, value: str | None,
) -> None:
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    with pytest.raises(interview.ModelError, match=name) as error:
        interview.call_model("question", token=SECRET)
    assert_sanitized(error)
    sdk.credential.assert_not_called()
    http.open.assert_not_called()


@pytest.mark.parametrize("value", [None, "", SECRET, "https://management.azure.com/.default", "*"])
def test_cli_requires_explicit_supported_scope(
    monkeypatch: pytest.MonkeyPatch, azure_env: None, sdk: FakeSdk, http: FakeHttp,
    value: str | None,
) -> None:
    if value is None:
        monkeypatch.delenv("AZURE_OPENAI_SCOPE")
    else:
        monkeypatch.setenv("AZURE_OPENAI_SCOPE", value)
    with pytest.raises(interview.ModelError, match="AZURE_OPENAI_SCOPE") as error:
        interview.call_model("question")
    assert_sanitized(error)
    sdk.credential.assert_not_called()
    http.open.assert_not_called()


@pytest.mark.parametrize("endpoint", [
    "http://demo.openai.azure.com/openai/v1/chat/completions",
    "https://openai.azure.com/openai/v1/chat/completions",
    "https://notopenai.azure.com/openai/v1/chat/completions",
    "https://demo.openai.azure.com.attacker.example/openai/v1/chat/completions",
    "https://attacker.example/openai/v1/chat/completions",
    "https://demo.openai.azure.cn/openai/v1/chat/completions",
    "https://127.0.0.1/openai/v1/chat/completions",
    "https://demo.openai.azure.com:444/openai/v1/chat/completions",
    "https://demo.openai.azure.com:/openai/v1/chat/completions",
    "https://demo.openai.azure.com:invalid/openai/v1/chat/completions",
    "https://demo.openai.azure.com:65536/openai/v1/chat/completions",
    f"https://user:{SECRET}@demo.openai.azure.com/openai/v1/chat/completions",
    "https://@demo.openai.azure.com/openai/v1/chat/completions",
    ENDPOINT + "#" + SECRET,
    ENDPOINT + "#",
    " " + ENDPOINT,
    ENDPOINT + "\r\n" + SECRET,
    "https://demo.openai.azure.com./openai/v1/chat/completions",
    "https://bad..openai.azure.com/openai/v1/chat/completions",
    "https://-bad.openai.azure.com/openai/v1/chat/completions",
    "https://bad_.openai.azure.com/openai/v1/chat/completions",
    "https://demo.openai.azure.com/openai/v1/responses",
    "https://demo.openai.azure.com/chat/completions",
    "https://demo.openai.azure.com/openai/v1/chat/completions/../responses",
    "https://demo.openai.azure.com/openai/deployments/../chat/completions",
    "https://demo.openai.azure.com/openai/v1/chat%2fcompletions",
    "https://demo.openai.azure.com\\@attacker.example/openai/v1/chat/completions",
    "https://[broken/openai/v1/chat/completions",
    "//demo.openai.azure.com/openai/v1/chat/completions",
])
def test_cli_rejects_endpoints_before_credential_acquisition(
    monkeypatch: pytest.MonkeyPatch, azure_env: None, sdk: FakeSdk, http: FakeHttp,
    endpoint: str,
) -> None:
    monkeypatch.setenv("LASTHUMAN_ENDPOINT", endpoint)
    with pytest.raises(interview.ModelError, match="HTTPS Public Azure") as error:
        interview.call_model("question")
    assert_sanitized(error)
    sdk.credential.assert_not_called()
    http.open.assert_not_called()


@pytest.mark.parametrize("host", [
    "demo.openai.azure.com", "demo.services.ai.azure.com", "demo.cognitiveservices.azure.com",
    "DEMO.OPENAI.AZURE.COM:443",
])
@pytest.mark.parametrize("path", [
    "/openai/v1/chat/completions", "/openai/deployments/gpt-4.1-mini/chat/completions",
])
def test_cli_accepts_public_azure_chat_routes(
    monkeypatch: pytest.MonkeyPatch, azure_env: None, http: FakeHttp, host: str, path: str,
) -> None:
    endpoint = f"https://{host}{path}?api-version=2024-10-21"
    monkeypatch.setenv("LASTHUMAN_ENDPOINT", endpoint)
    assert interview.call_model("question") == "model reply"
    assert http.requests[0].full_url == endpoint


def test_cli_preserves_generated_deployment_endpoint(
    monkeypatch: pytest.MonkeyPatch, azure_env: None, http: FakeHttp,
) -> None:
    monkeypatch.delenv("LASTHUMAN_ENDPOINT")
    monkeypatch.delenv("LASTHUMAN_PROVIDER")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://demo.openai.azure.com/")
    assert interview.call_model("question") == "model reply"
    assert http.requests[0].full_url == ENDPOINT


@pytest.mark.parametrize("dependency", ["azure.identity", "azure.core.exceptions"])
def test_missing_optional_dependency_is_sanitized(
    monkeypatch: pytest.MonkeyPatch, azure_env: None, http: FakeHttp, dependency: str,
) -> None:
    original_import = builtins.__import__

    def import_without_sdk(name: str, *args: object, **kwargs: object) -> object:
        if name == dependency:
            raise ImportError(SECRET)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_sdk)
    with pytest.raises(interview.ModelError, match=r"lasthuman\[bot\]") as error:
        interview.call_model("question")
    assert_sanitized(error)
    http.open.assert_not_called()


def test_fresh_base_import_and_cli_help_need_no_azure_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = ("lasthuman.model_auth", "lasthuman.interview", "lasthuman.cli")
    for name in names:
        importlib.import_module(name)
    original_import = builtins.__import__

    def import_without_azure(name: str, *args: object, **kwargs: object) -> object:
        if name == "azure" or name.startswith("azure."):
            raise AssertionError("Base import attempted to load the optional Azure SDK")
        return original_import(name, *args, **kwargs)

    with monkeypatch.context() as isolated:
        for name in names:
            isolated.delitem(sys.modules, name)
            isolated.delattr(lasthuman, name.rsplit(".", 1)[1])
        isolated.setattr(builtins, "__import__", import_without_azure)
        cli = importlib.import_module("lasthuman.cli")
        isolated.setattr(cli, "_utf8_stdio", lambda: None)
        with pytest.raises(SystemExit) as result:
            cli.main(["--help"])
        assert result.value.code == 0


@pytest.mark.parametrize("error_type", [FakeAuthenticationError, FakeCredentialUnavailableError])
def test_sdk_and_cli_errors_never_leak_or_fall_back(
    monkeypatch: pytest.MonkeyPatch, azure_env: None, sdk: FakeSdk, http: FakeHttp,
    error_type: type[FakeAuthenticationError],
) -> None:
    for name in ("LASTHUMAN_API_KEY", "LASTHUMAN_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.setenv(name, SECRET)
    sdk.provider.side_effect = error_type(SECRET)
    with pytest.raises(interview.ModelError, match="Azure CLI authentication failed") as error:
        interview.call_model("question", token=SECRET)
    assert_sanitized(error)
    http.open.assert_not_called()


def test_refresh_failure_is_fail_closed_and_provider_can_recover(
    azure_env: None, sdk: FakeSdk, http: FakeHttp,
) -> None:
    sdk.provider.side_effect = [
        "first-token", FakeAuthenticationError(SECRET), "renewed-token",
    ]
    assert interview.call_model("question") == "model reply"
    with pytest.raises(interview.ModelError) as error:
        interview.call_model("question")
    assert_sanitized(error)
    assert len(http.requests) == 1
    assert interview.call_model("question") == "model reply"
    assert sdk.credential.call_count == sdk.bearer_provider.call_count == 1
    assert [r.get_header("Authorization") for r in http.requests] == [
        "Bearer first-token", "Bearer renewed-token",
    ]


@pytest.mark.parametrize("token", [
    None, "", " ", "\t", "token\r\ninjected", "token\x00", "token\x7f",
    "token\x85", "token\u2028", "토큰", "two tokens", 42, b"bytes", ["token"],
])
def test_invalid_sdk_tokens_fail_before_http(
    azure_env: None, sdk: FakeSdk, http: FakeHttp, token: object,
) -> None:
    sdk.provider.return_value = token
    with pytest.raises(interview.ModelError, match="invalid bearer token") as error:
        interview.call_model("question")
    assert_sanitized(error)
    http.open.assert_not_called()


@pytest.mark.parametrize("status", [301, 400, 401, 403, 404, 429, 500])
def test_cli_http_errors_do_not_read_or_leak_response_bodies(
    azure_env: None, http: FakeHttp, status: int,
) -> None:
    body = Mock(wraps=io.BytesIO(SECRET.encode()))
    http.open.side_effect = urllib.error.HTTPError(
        ENDPOINT + "&secret=" + SECRET, status, SECRET, Message(), body,
    )
    with pytest.raises(interview.ModelError, match=f"HTTP {status}") as error:
        interview.call_model("question")
    assert_sanitized(error)
    body.read.assert_not_called()
    body.close.assert_called_once()


@pytest.mark.parametrize("failure", [
    urllib.error.URLError(SECRET), OSError(SECRET), http_client.BadStatusLine(SECRET),
])
def test_cli_transport_errors_are_sanitized(
    azure_env: None, http: FakeHttp, failure: Exception,
) -> None:
    http.open.side_effect = failure
    with pytest.raises(interview.ModelError, match="Azure model") as error:
        interview.call_model("question")
    assert_sanitized(error)


@pytest.mark.parametrize("body", [
    b"\xff", b"not-json", b"{}", b'{"choices":[]}',
    b'{"choices":[{"message":{"content":null}}]}',
    b'{"choices":[{"message":{"content":""}}]}',
])
def test_cli_keeps_response_validation(
    azure_env: None, http: FakeHttp, body: bytes,
) -> None:
    http.open.side_effect = lambda *args, **kwargs: io.BytesIO(body)
    with pytest.raises(interview.ModelError):
        interview.call_model("question")


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("location", [
    "https://attacker.example/" + SECRET,
    "http://demo.openai.azure.com/" + SECRET,
    "https://other.services.ai.azure.com/openai/v1/chat/completions",
    "/openai/v1/chat/completions",
])
def test_cli_redirects_never_forward_tokens(
    monkeypatch: pytest.MonkeyPatch, azure_env: None, status: int, location: str,
) -> None:
    sent: list[urllib.request.Request] = []

    def redirect(
        handler: urllib.request.HTTPSHandler, request: urllib.request.Request,
    ) -> urllib.response.addinfourl:
        sent.append(request)
        headers = Message()
        headers["Location"] = location
        response = urllib.response.addinfourl(
            io.BytesIO(SECRET.encode()), headers, request.full_url, status,
        )
        response.msg = "redirect"
        return response

    monkeypatch.setattr(urllib.request.HTTPSHandler, "https_open", redirect)
    with pytest.raises(interview.ModelError, match=f"HTTP {status}.*Redirects") as error:
        interview.call_model("question")
    assert_sanitized(error)
    assert len(sent) == 1
    assert sent[0].full_url == ENDPOINT
    assert sent[0].get_header("Authorization") == "Bearer cli-token"
