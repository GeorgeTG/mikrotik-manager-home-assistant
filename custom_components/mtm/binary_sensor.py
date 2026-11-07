"""Read-only health indicator for managed router observations."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import MtmConfigEntry, MtmCoordinator
from .entity import DEVICE_STATES, MtmEntity, async_discover_devices

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MtmConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Discover health entities for current and newly authorized devices."""
    async_discover_devices(
        entry,
        async_add_entities,
        lambda coordinator, device_id: (MtmHealthySensor(coordinator, device_id),),
    )


class MtmHealthySensor(MtmEntity, BinarySensorEntity):
    """Report healthy only when the daemon's lifecycle observation is online."""

    _attr_name = "Healthy"
    _attr_translation_key = "healthy"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: MtmCoordinator, device_id: str) -> None:
        """Initialize the device health entity."""
        super().__init__(coordinator, device_id, "healthy")

    @property
    def is_on(self) -> bool | None:
        """Do not interpret degraded or an unknown state as a healthy router."""
        state = self.device.get("state")
        return state == "online" if state in DEVICE_STATES else None
