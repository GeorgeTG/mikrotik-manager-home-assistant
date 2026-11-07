"""Server URL and browser OAuth setup for MikroTik Manager."""

from collections.abc import Mapping
import logging
from typing import Any

from aiohttp import ClientError
import probatio

from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlowResult
from homeassistant.helpers.config_entry_oauth2_flow import (
    AbstractOAuth2FlowHandler,
    async_get_redirect_uri,
)

from .const import CONF_CLIENT_ID, CONF_SERVER_URL, DOMAIN
from .oauth import (
    MtmOAuth2Implementation,
    OAuthMetadataError,
    ReadOnlyTokenError,
    async_register_client,
    async_revoke,
    async_validate_server,
    normalize_server_url,
)

_LOGGER = logging.getLogger(__name__)


class MtmConfigFlow(AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Link one mtm account using its own dynamically registered public client."""

    DOMAIN = DOMAIN
    VERSION = 1

    def __init__(self) -> None:
        """Keep the chosen origin, registration and PKCE verifier flow-local."""
        super().__init__()
        self._server_url = ""
        self._client_id = ""

    @property
    def logger(self) -> logging.Logger:
        """Return the integration logger."""
        return _LOGGER

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask only for the server URL, then open browser authorization."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._server_url = normalize_server_url(user_input[CONF_SERVER_URL])
            except (OAuthMetadataError, AttributeError):
                errors[CONF_SERVER_URL] = "invalid_url"
            else:
                try:
                    await async_validate_server(self.hass, self._server_url)
                    redirect_uri = async_get_redirect_uri(self.hass)
                    self._client_id = await async_register_client(
                        self.hass, self._server_url, redirect_uri
                    )
                except OAuthMetadataError:
                    errors["base"] = "unsupported_server"
                except (ClientError, TimeoutError, ValueError):
                    errors["base"] = "cannot_connect"
                else:
                    self.flow_impl = MtmOAuth2Implementation(
                        self.hass, self._server_url, self._client_id
                    )
                    return await self.async_step_auth()
        return self.async_show_form(
            step_id="user",
            data_schema=probatio.Schema({probatio.Required(CONF_SERVER_URL): str}),
            errors=errors,
        )

    async def async_step_creation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Give a useful refusal if consent granted broader access than requested."""
        try:
            return await super().async_step_creation(user_input)
        except ReadOnlyTokenError:
            return self.async_abort(reason="read_only_required")

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Persist only this origin's public registration and the OAuth token."""
        entry_data = {
            **data,
            CONF_SERVER_URL: self._server_url,
            CONF_CLIENT_ID: self._client_id,
        }
        if self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            if self._server_url != entry.data[CONF_SERVER_URL]:
                return self.async_abort(reason="unique_id_mismatch")
            # The original registration identifies the HA connection. Keep its
            # identity (and entities) stable when Disconnect deleted that client.
            await async_revoke(self.hass, entry)
            return self.async_update_reload_and_abort(
                entry, data_updates=entry_data
            )
        await self.async_set_unique_id(f"{self._server_url}|{self._client_id}")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"MikroTik Manager ({self._server_url})", data=entry_data
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Return to the same server when the user must authorize again."""
        self._server_url = normalize_server_url(entry_data[CONF_SERVER_URL])
        self._client_id = entry_data[CONF_CLIENT_ID]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Require a browser login again and update the original config entry."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        try:
            await async_validate_server(self.hass, self._server_url)
            redirect_uri = async_get_redirect_uri(self.hass)
            # mtm Disconnect removes the client registration as well as tokens.
            # A new public client recovers that case and binds the current callback.
            self._client_id = await async_register_client(
                self.hass, self._server_url, redirect_uri
            )
        except (ClientError, TimeoutError, ValueError):
            return self.async_show_form(
                step_id="reauth_confirm", errors={"base": "cannot_connect"}
            )
        self.flow_impl = MtmOAuth2Implementation(
            self.hass, self._server_url, self._client_id
        )
        return await self.async_step_auth()
