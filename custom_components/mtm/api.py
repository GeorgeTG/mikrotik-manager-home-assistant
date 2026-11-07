"""Bounded read-only MCP client for MikroTik Manager.

OAuth credentials belong exclusively to /mcp. Do not send them to the REST API.
Each inventory refresh owns and closes one short-lived MCP session.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp

from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session
from homeassistant.exceptions import ConfigEntryAuthFailed

PROTOCOL_VERSION = "2025-11-25"
MAX_RESPONSE_BYTES = 256 * 1024
MAX_DEVICES = 10_000
MAX_PAGES = 1_000
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)
INVENTORY_TIMEOUT_SECONDS = 120


class MtmConnectionError(Exception):
    """The server could not provide a complete, trustworthy inventory."""


class MtmAuthenticationError(Exception):
    """The connection needs browser authorization again."""


class MtmPermissionError(Exception):
    """The account or application cannot read the inventory."""


class _SessionExpired(Exception):
    """The server discarded the short-lived MCP session."""


class MtmClient:
    """Read the current user's inventory without retaining privileged fields."""

    def __init__(
        self, session: aiohttp.ClientSession, oauth: OAuth2Session, server_url: str
    ) -> None:
        self._http = session
        self._oauth = oauth
        self._url = f"{server_url}/mcp"
        self._lock = asyncio.Lock()
        self._session_id: str | None = None
        self._last_headers: dict[str, str] = {}
        self._request_id = 0

    async def _headers(self) -> dict[str, str]:
        """Refresh through HA's serialized session before every request."""
        try:
            await self._oauth.async_ensure_token_valid()
        except ConfigEntryAuthFailed as error:
            raise MtmAuthenticationError("Reconnect to mtm") from error
        token = self._oauth.token
        scopes = token.get("scope", "")
        if not isinstance(scopes, str):
            raise MtmAuthenticationError("Authorize read-only access in mtm")
        granted = set(scopes.split())
        if "mtm:read" not in granted or "mtm:write" in granted:
            raise MtmAuthenticationError("Authorize read-only access in mtm")
        access_token = token.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise MtmAuthenticationError("Reconnect to mtm")
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        self._last_headers = headers
        return headers

    async def _message(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._request_id += 1
        request_id = self._request_id
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        notification = method.startswith("notifications/")
        if not notification:
            body["id"] = request_id
        if params is not None:
            body["params"] = params
        async with self._http.post(
            self._url,
            json=body,
            headers=await self._headers(),
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
        ) as response:
            if response.status == 401:
                raise MtmAuthenticationError("Reconnect to mtm")
            if response.status == 403:
                raise MtmPermissionError("Allow device_list and devices.read in mtm")
            if response.status == 404 and self._session_id:
                if await response.content.read(128) == b"Not Found: Session not found":
                    raise _SessionExpired
                raise MtmPermissionError("The mtm inventory is no longer available")
            if response.status not in (200, 202):
                raise MtmConnectionError("The mtm server did not accept the request")
            if method == "initialize":
                self._session_id = response.headers.get("Mcp-Session-Id")
            if notification:
                return None
            payload = await self._read_response(response, request_id)
        if payload.get("jsonrpc") != "2.0" or payload.get("id") != request_id:
            raise MtmConnectionError("Unexpected MCP response")
        if "error" in payload:
            raise MtmConnectionError("The mtm server refused the MCP request")
        if "result" not in payload:
            raise MtmConnectionError("Incomplete MCP response")
        return payload["result"]

    @staticmethod
    async def _read_response(
        response: aiohttp.ClientResponse, request_id: int
    ) -> dict[str, Any]:
        """Read JSON or a bounded SSE response, allowing protocol notifications."""
        if response.content_type == "application/json":
            raw = bytearray()
            async for chunk in response.content.iter_chunked(8192):
                raw.extend(chunk)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise MtmConnectionError("MCP response exceeded the size limit")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise MtmConnectionError("Unexpected MCP response")
            return value
        if response.content_type != "text/event-stream":
            raise MtmConnectionError("Unexpected MCP content type")
        size = 0
        data: list[str] = []
        async for raw_line in response.content:
            size += len(raw_line)
            if size > MAX_RESPONSE_BYTES:
                raise MtmConnectionError("MCP response exceeded the size limit")
            line = raw_line.decode("utf-8").rstrip("\r\n")
            if line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
            elif not line and data:
                value = json.loads("\n".join(data))
                data = []
                if isinstance(value, dict) and value.get("id") == request_id:
                    return value
        raise MtmConnectionError("MCP response ended before the result")

    async def _close_session(self) -> None:
        if not self._session_id:
            return
        try:
            async with self._http.delete(
                self._url,
                headers={**self._last_headers, "Mcp-Session-Id": self._session_id},
                timeout=aiohttp.ClientTimeout(total=5),
                allow_redirects=False,
            ):
                pass
        except (aiohttp.ClientError, TimeoutError, MtmAuthenticationError):
            # Cleanup is best effort. Never replace the useful inventory error.
            pass
        finally:
            self._session_id = None

    async def _inventory(self) -> dict[str, dict[str, Any]]:
        result = await self._message(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mtm-home-assistant", "version": "0.1.0"},
            },
        )
        if not isinstance(result, dict) or result.get("protocolVersion") != PROTOCOL_VERSION:
            raise MtmConnectionError("Unsupported MCP protocol version")
        await self._message("notifications/initialized")
        devices: dict[str, dict[str, Any]] = {}
        offset = 0
        for _ in range(MAX_PAGES):
            result = await self._message(
                "tools/call",
                {"name": "device_list", "arguments": {"limit": 45, "offset": offset}},
            )
            if not isinstance(result, dict):
                raise MtmConnectionError("Unexpected device list")
            if result.get("isError"):
                texts = [
                    item.get("text", "")
                    for item in result.get("content", [])
                    if isinstance(item, dict) and item.get("type") == "text"
                ]
                # Inspect only the stable refusal code, never display the server body.
                codes = {text.split(":", 1)[0] for text in texts if isinstance(text, str)}
                if "unauthorized" in codes:
                    raise MtmAuthenticationError("Reconnect to mtm")
                if codes & {"forbidden", "insufficient_scope", "tool_not_allowed"}:
                    raise MtmPermissionError("Allow device_list and devices.read in mtm")
                raise MtmConnectionError("The mtm server could not read devices")
            page = result.get("structuredContent")
            if not isinstance(page, dict) or not isinstance(page.get("devices"), list):
                raise MtmConnectionError("Incomplete device list")
            rows = page["devices"]
            if type(page.get("count")) is not int or page["count"] != len(rows):
                raise MtmConnectionError("Inconsistent device page")
            for row in rows:
                if not isinstance(row, dict):
                    raise MtmConnectionError("Unexpected device row")
                device_id = row.get("id")
                if not isinstance(device_id, str) or not device_id or device_id in devices:
                    raise MtmConnectionError("Inconsistent device inventory")
                # Discard unneeded address/topology data instead of caching whole responses.
                devices[device_id] = {
                    key: row.get(key)
                    for key in (
                        "id", "name", "slug", "state", "board", "ros_version",
                        "last_handshake_age_sec",
                    )
                }
            if len(devices) > MAX_DEVICES:
                raise MtmConnectionError("Inventory exceeded the supported device limit")
            if "next_offset" not in page or type(page.get("truncated")) is not bool:
                raise MtmConnectionError("Incomplete pagination metadata")
            next_offset = page["next_offset"]
            if next_offset is None:
                if page["truncated"]:
                    raise MtmConnectionError("The mtm server returned an incomplete inventory")
                return devices
            if type(next_offset) is not int or next_offset != offset + len(rows) or not rows:
                raise MtmConnectionError("Inventory pagination did not advance")
            offset = next_offset
        raise MtmConnectionError("Inventory exceeded the supported page limit")

    async def async_get_devices(self) -> dict[str, dict[str, Any]]:
        """Return a complete replacement snapshot, never a partial successful page."""
        async with self._lock:
            try:
                async with asyncio.timeout(INVENTORY_TIMEOUT_SECONDS):
                    for attempt in range(2):
                        try:
                            return await self._inventory()
                        except _SessionExpired:
                            if attempt:
                                raise MtmConnectionError("The mtm session expired during inventory") from None
                        finally:
                            await self._close_session()
            except (aiohttp.ClientError, TimeoutError, ValueError, UnicodeError) as error:
                raise MtmConnectionError("Unable to read the mtm inventory") from error
        raise MtmConnectionError("Unable to read the mtm inventory")
