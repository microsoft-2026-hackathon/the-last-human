"""Opt-in, process-local Azure CLI credentials for model requests."""

from __future__ import annotations

import os
import re
import subprocess
import threading
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from email.message import Message
from functools import lru_cache
from typing import BinaryIO, Literal
from urllib.parse import urlsplit
from uuid import UUID


class ModelAuthError(RuntimeError):
    """A safe-to-display model authentication/configuration failure."""


_SCOPES = frozenset({
    "https://cognitiveservices.azure.com/.default",
    "https://ai.azure.com/.default",
})
_HOST_SUFFIXES = (
    ".openai.azure.com",
    ".services.ai.azure.com",
    ".cognitiveservices.azure.com",
)
_CREDENTIAL_HELP = (
    "Azure CLI authentication failed. Check that Azure CLI is installed and "
    "run az login for the configured tenant/subscription, then retry."
)
_provider_lock = threading.Lock()


def auth_mode() -> Literal["default", "azure-cli"]:
    mode = os.environ.get("LASTHUMAN_AUTH_MODE", "")
    if mode in ("", "default"):
        return "default"
    if mode != "azure-cli":
        raise ModelAuthError("Unsupported LASTHUMAN_AUTH_MODE. Use default or azure-cli.")
    if os.environ.get("LASTHUMAN_PROVIDER", "azure").lower() != "azure":
        raise ModelAuthError("Azure CLI authentication requires LASTHUMAN_PROVIDER=azure.")
    return "azure-cli"


@dataclass(frozen=True)
class _AzureCliConfig:
    tenant_id: str
    subscription_id: str
    scope: str


def _uuid_setting(name: str) -> str:
    value = os.environ.get(name, "")
    try:
        parsed = UUID(value)
    except ValueError:
        raise ModelAuthError(f"{name} must be an explicit UUID.") from None
    if str(parsed) != value.lower():
        raise ModelAuthError(f"{name} must be an explicit UUID.")
    return str(parsed)


def _config() -> _AzureCliConfig:
    tenant_id = _uuid_setting("AZURE_TENANT_ID")
    subscription_id = _uuid_setting("AZURE_SUBSCRIPTION_ID")
    scope = os.environ.get("AZURE_OPENAI_SCOPE", "")
    if scope not in _SCOPES:
        raise ModelAuthError(
            "AZURE_OPENAI_SCOPE must match the deployment sample: "
            "https://cognitiveservices.azure.com/.default or https://ai.azure.com/.default."
        )
    return _AzureCliConfig(tenant_id, subscription_id, scope)


def _validate_endpoint(endpoint: str, provider: str) -> None:
    error = (
        "Azure CLI authentication requires an HTTPS Public Azure inference endpoint "
        "on port 443 with an /openai/deployments/<deployment>/chat/completions or "
        "/openai/v1/chat/completions path, without userinfo or a fragment."
    )
    if provider != "azure" or any(
        ord(char) <= 32 or ord(char) >= 127 or char in "\\#" for char in endpoint
    ):
        raise ModelAuthError(error)
    try:
        url = urlsplit(endpoint)
        host = url.hostname or ""
        valid = (
            url.scheme == "https"
            and url.username is None
            and url.password is None
            and url.port in (None, 443)
            and re.fullmatch(r"[A-Za-z0-9.-]+(?::443)?", url.netloc) is not None
            and len(host) <= 253
            and host.endswith(_HOST_SUFFIXES)
            and all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in host.split(".")
            )
            and re.fullmatch(
                r"/openai/(?:v1|deployments/[A-Za-z0-9][A-Za-z0-9._-]*)/chat/completions",
                url.path,
            ) is not None
        )
    except ValueError:
        raise ModelAuthError(error) from None
    if not valid:
        raise ModelAuthError(error)


def _verify_cli_account(config: _AzureCliConfig) -> None:
    try:
        result = subprocess.run(
            [
                "az", "account", "show", "--subscription", config.subscription_id,
                "--query", "tenantId", "--output", "tsv", "--only-show-errors",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        raise ModelAuthError(_CREDENTIAL_HELP) from None
    if result.returncode != 0:
        raise ModelAuthError(_CREDENTIAL_HELP)
    try:
        tenant_id = str(UUID(result.stdout.strip()))
    except ValueError:
        raise ModelAuthError("Azure CLI returned invalid subscription metadata.") from None
    if tenant_id != config.tenant_id:
        raise ModelAuthError(
            "The configured Azure subscription belongs to a different tenant. "
            "Check AZURE_TENANT_ID and AZURE_SUBSCRIPTION_ID."
        )


@lru_cache(maxsize=8)
def _token_provider(config: _AzureCliConfig) -> Callable[[], str]:
    try:
        from azure.core.exceptions import ClientAuthenticationError
        from azure.identity import AzureCliCredential, get_bearer_token_provider
    except ImportError:
        raise ModelAuthError(
            "Azure CLI authentication requires the optional bot dependencies: lasthuman[bot]."
        ) from None

    # Azure CLI rejects simultaneous tenant/subscription token selectors.
    credential = AzureCliCredential(subscription=config.subscription_id)
    provider = get_bearer_token_provider(credential, config.scope)

    def get_token() -> str:
        _verify_cli_account(config)
        try:
            token = provider()
        except ClientAuthenticationError:
            raise ModelAuthError(_CREDENTIAL_HELP) from None
        if not isinstance(token, str) or not token or any(
            ord(char) < 33 or ord(char) > 126 for char in token
        ):
            raise ModelAuthError("Azure CLI returned an invalid bearer token. Sign in again.")
        return token

    return get_token


def azure_cli_token(endpoint: str, provider: str) -> str:
    _validate_endpoint(endpoint, provider)
    config = _config()
    # Serialize SDK cache access/refresh as well as first-time provider creation.
    with _provider_lock:
        return _token_provider(config)()


class AzureCliNoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: urllib.request.Request, fp: BinaryIO, code: int, msg: str,
        headers: Message, newurl: str,
    ) -> None:
        return None
