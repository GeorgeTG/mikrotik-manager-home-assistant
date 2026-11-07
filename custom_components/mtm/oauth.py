"""Public-client OAuth with PKCE for one explicitly selected mtm server."""

import asyncio
from collections.abc import Mapping
import json
from typing import Any

from aiohttp import ClientError, ClientResponseError
from yarl import URL

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    OAuth2TokenRequestConnectionError,
)
from homeassistant.helpers import oauth2
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import (
    LocalOAuth2ImplementationWithPkce,
)

from .const import CONF_CLIENT_ID, CONF_SERVER_URL, DOMAIN, OAUTH_SCOPE, REQUEST_TIMEOUT

MAX_OAUTH_RESPONSE_BYTES = 256 * 1024


class OAuthMetadataError(ValueError):
    """The selected origin did not advertise the supported mtm OAuth contract."""


class ReadOnlyTokenError(ConfigEntryAuthFailed):
    """The server did not issue a usable, exclusively read-only token."""


async def _async_request_json(
    hass: HomeAssistant,
    method: str,
    url: str,
    *,
    expected_status: int = 200,
    json_data: Mapping[str, Any] | None = None,
    form_data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read bounded JSON without logging response bodies or following redirects."""
    session = async_get_clientsession(hass)
    request = session.get if method == "GET" else session.post
    kwargs: dict[str, Any] = {"allow_redirects": False}
    if json_data is not None:
        kwargs["json"] = dict(json_data)
    if form_data is not None:
        kwargs["data"] = dict(form_data)
    async with asyncio.timeout(REQUEST_TIMEOUT):
        async with request(url, **kwargs) as response:
            if response.status != expected_status:
                # In particular, never include an OAuth error_description or a
                # response body in an exception that HA may subsequently log.
                raise ClientResponseError(
                    request_info=response.request_info,
                    history=(),
                    status=response.status,
                    message="The mtm OAuth request was refused",
                )
            body = bytearray()
            async for chunk in response.content.iter_chunked(16 * 1024):
                if len(body) + len(chunk) > MAX_OAUTH_RESPONSE_BYTES:
                    raise OAuthMetadataError("The mtm OAuth response is too large")
                body.extend(chunk)
    try:
        value = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise OAuthMetadataError("Invalid mtm OAuth JSON response") from None
    if not isinstance(value, dict):
        raise OAuthMetadataError("The mtm OAuth response must be an object")
    return value


async def async_token_request(
    hass: HomeAssistant, token_url: str, data: Mapping[str, Any]
) -> dict[str, Any]:
    """Preserve HA's typed OAuth failures while bounding and redacting bodies."""
    try:
        return await _async_request_json(hass, "POST", token_url, form_data=data)
    except ClientError as err:
        oauth2.raise_mapped_token_error(err, DOMAIN)
    except ValueError as err:
        raise OAuth2TokenRequestConnectionError(domain=DOMAIN) from err


def normalize_server_url(value: str) -> str:
    """Accept an HTTPS origin without credentials, paths or hidden URL components."""
    try:
        if not isinstance(value, str):
            raise ValueError
        raw = value.strip()
        if len(raw) > 2048 or any(char.isspace() or ord(char) < 32 for char in raw):
            raise ValueError
        url = URL(raw)
        if (
            url.scheme != "https"
            or not url.host
            or url.user is not None
            or url.password is not None
            or url.path not in ("", "/")
            or url.query_string
            or url.fragment
            or "?" in value
            or "#" in value
        ):
            raise ValueError
        # Accessing the port also validates its range and spelling.
        _ = url.port
        return str(url.origin())
    except (TypeError, ValueError) as err:
        raise OAuthMetadataError("Enter the HTTPS origin of the mtm server") from err


def validate_read_only_token(token: Mapping[str, Any]) -> None:
    """Refuse absent scopes, broader grants and unusable token responses."""
    scope = token.get("scope")
    if not isinstance(scope, str) or set(scope.split()) != {"mtm:read"}:
        raise ReadOnlyTokenError("Authorize read-only access to MikroTik Manager")
    if (
        not isinstance(token.get("access_token"), str)
        or not token["access_token"]
        or not isinstance(token.get("refresh_token"), str)
        or not token["refresh_token"]
        or str(token.get("token_type", "")).lower() != "bearer"
        or isinstance(token.get("expires_in"), bool)
    ):
        raise ReadOnlyTokenError("The server returned an unusable OAuth token")
    try:
        if int(token["expires_in"]) <= 0:
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError) as err:
        raise ReadOnlyTokenError("The server returned an unusable OAuth lifetime") from err


async def async_validate_server(hass: HomeAssistant, server_url: str) -> None:
    """Validate discovery without following endpoints to another origin."""
    metadata = await _async_request_json(
        hass, "GET", f"{server_url}/.well-known/oauth-authorization-server"
    )
    expected = {
        "issuer": server_url,
        "authorization_endpoint": f"{server_url}/oauth/authorize",
        "token_endpoint": f"{server_url}/oauth/token",
        "registration_endpoint": f"{server_url}/oauth/register",
        "revocation_endpoint": f"{server_url}/oauth/revoke",
    }
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise OAuthMetadataError("OAuth endpoints must belong to the selected mtm origin")
    required = {
        "code_challenge_methods_supported": {"S256"},
        "token_endpoint_auth_methods_supported": {"none"},
        "grant_types_supported": {"authorization_code", "refresh_token"},
        "response_types_supported": {"code"},
        "scopes_supported": {"mtm:read", "offline_access"},
    }
    for key, values in required.items():
        actual = metadata.get(key)
        if (
            not isinstance(actual, list)
            or not all(isinstance(item, str) for item in actual)
            or not values.issubset(actual)
        ):
            raise OAuthMetadataError("The server does not support read-only PKCE authorization")


async def async_register_client(
    hass: HomeAssistant, server_url: str, redirect_uri: str
) -> str:
    """Register a public client for this flow's exact Home Assistant callback."""
    registration = {
        "client_name": "Home Assistant",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    data = await _async_request_json(
        hass,
        "POST",
        f"{server_url}/oauth/register",
        expected_status=201,
        json_data=registration,
    )
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("client_id"), str)
        or not data["client_id"]
        or data.get("token_endpoint_auth_method") != "none"
        or data.get("redirect_uris") != [redirect_uri]
        or "client_secret" in data
    ):
        raise OAuthMetadataError("The server did not register the expected public client")
    return data["client_id"]


class MtmOAuth2Implementation(LocalOAuth2ImplementationWithPkce):
    """An independent PKCE flow and a read-only, audience-bound token client."""

    def __init__(self, hass: HomeAssistant, server_url: str, client_id: str) -> None:
        """Build only fixed endpoints at the previously selected HTTPS origin."""
        self.server_url = normalize_server_url(server_url)
        self.resource = f"{self.server_url}/mcp"
        super().__init__(
            hass,
            f"{self.server_url}|{client_id}",
            client_id,
            f"{self.server_url}/oauth/authorize",
            f"{self.server_url}/oauth/token",
        )

    @property
    def name(self) -> str:
        """Return the selected service name."""
        return "MikroTik Manager"

    @property
    def service_domain(self) -> str:
        """Use the integration domain in Home Assistant's token error messages."""
        return DOMAIN

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        """Request read-only access and preserve the helper's PKCE challenge."""
        return super().extra_authorize_data | {
            "scope": OAUTH_SCOPE,
            "resource": self.resource,
        }

    async def _token_request(self, data: dict[str, Any]) -> dict[str, Any]:
        """Bind every exchange/refresh to MCP and reject broadened token scopes."""
        request = {**data, "client_id": self.client_id, "resource": self.resource}
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                token = await async_token_request(self.hass, self.token_url, request)
        except (ValueError, TypeError) as err:
            raise OAuth2TokenRequestConnectionError(domain=DOMAIN) from err
        if not isinstance(token, dict):
            raise OAuth2TokenRequestConnectionError(domain=DOMAIN)
        try:
            validate_read_only_token(token)
        except ReadOnlyTokenError:
            # An operator may select a wider grant on the server's consent page.
            # Never persist it or leave that unused broader grant connected.
            await self.async_revoke_token(token)
            raise
        return token

    async def async_revoke_token(self, token: Mapping[str, Any]) -> None:
        """Best-effort family revocation, without renewing or logging credentials."""
        presented = token.get("refresh_token") or token.get("access_token")
        if not isinstance(presented, str) or not presented:
            return
        session = async_get_clientsession(self.hass)
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with session.post(
                    f"{self.server_url}/oauth/revoke",
                    data={"token": presented, "client_id": self.client_id},
                    allow_redirects=False,
                ) as response:
                    # Removal must still finish when the server is offline. The
                    # account's Connections page can revoke the grant separately.
                    response.release()
        except (ClientError, TimeoutError):
            return


async def async_get_implementation(
    hass: HomeAssistant, entry: ConfigEntry
) -> MtmOAuth2Implementation:
    """Reconstruct an entry's public client after a Home Assistant restart."""
    return MtmOAuth2Implementation(
        hass, entry.data[CONF_SERVER_URL], entry.data[CONF_CLIENT_ID]
    )


async def async_revoke(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Revoke this connection only when its config entry is removed."""
    implementation = await async_get_implementation(hass, entry)
    await implementation.async_revoke_token(entry.data.get("token", {}))


async_revoke_entry = async_revoke
