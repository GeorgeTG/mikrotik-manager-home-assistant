"""Poll the complete inventory visible through one user's OAuth grant."""

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    MtmAuthenticationError,
    MtmClient,
    MtmConnectionError,
    MtmPermissionError,
)
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

type DeviceInventory = dict[str, dict[str, Any]]
type MtmConfigEntry = ConfigEntry[MtmCoordinator]


class MtmCoordinator(DataUpdateCoordinator[DeviceInventory]):
    """Replace snapshots so access changes cannot leave live device values behind."""

    def __init__(
        self, hass: HomeAssistant, entry: MtmConfigEntry, client: MtmClient
    ) -> None:
        """Initialize the shared polling coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client
        self.data = {}

    async def _async_update_data(self) -> DeviceInventory:
        """Fetch a fresh, completely paginated authorization-scoped inventory."""
        try:
            return await self.client.async_get_devices()
        except MtmAuthenticationError as err:
            self.data = {}
            raise ConfigEntryAuthFailed("Reconnect MikroTik Manager") from err
        except MtmPermissionError as err:
            self.data = {}
            raise UpdateFailed("Device access is no longer available") from err
        except MtmConnectionError as err:
            raise UpdateFailed("Cannot update MikroTik Manager devices") from err
