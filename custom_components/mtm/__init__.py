"""Read-only MikroTik Manager integration."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session

from .api import MtmClient
from .const import CONF_SERVER_URL, PLATFORMS
from .coordinator import MtmConfigEntry, MtmCoordinator
from .oauth import async_get_implementation, async_revoke_entry


async def async_setup_entry(hass: HomeAssistant, entry: MtmConfigEntry) -> bool:
    """Load a connection using its own OAuth grant and authorized inventory."""
    implementation = await async_get_implementation(hass, entry)
    oauth_session = OAuth2Session(hass, entry, implementation)
    client = MtmClient(
        async_get_clientsession(hass), oauth_session, entry.data[CONF_SERVER_URL]
    )
    coordinator = MtmCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MtmConfigEntry) -> bool:
    """Unload entities; the grant survives reloads and Home Assistant restarts."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: MtmConfigEntry) -> None:
    """Revoke this connection when the user removes the integration."""
    await async_revoke_entry(hass, entry)
