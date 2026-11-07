"""Read-only device state and inventory diagnostics."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import MtmConfigEntry, MtmCoordinator
from .entity import DEVICE_STATES, MtmEntity, async_discover_devices

PARALLEL_UPDATES = 0

SENSORS = (
    SensorEntityDescription(
        key="state",
        name="State",
        translation_key="state",
        device_class=SensorDeviceClass.ENUM,
        options=list(DEVICE_STATES),
        icon="mdi:router-network",
    ),
    SensorEntityDescription(
        key="last_handshake_age_sec",
        name="Last handshake age",
        translation_key="last_handshake_age",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:timer-outline",
    ),
    SensorEntityDescription(
        key="ros_version",
        name="RouterOS version",
        translation_key="routeros_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:information-outline",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MtmConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Discover sensors for current and newly authorized devices."""
    async_discover_devices(
        entry,
        async_add_entities,
        lambda coordinator, device_id: (
            MtmSensor(coordinator, device_id, description) for description in SENSORS
        ),
    )


class MtmSensor(MtmEntity, SensorEntity):
    """One non-mutating inventory field."""

    def __init__(
        self,
        coordinator: MtmCoordinator,
        device_id: str,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize a sensor from its field description."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> str | int | None:
        """Keep unavailable observations unknown rather than inventing zeroes."""
        value = self.device.get(self.entity_description.key)
        if self.entity_description.key == "last_handshake_age_sec":
            return value if type(value) is int and value >= 0 else None
        if self.entity_description.key == "state":
            return value if value in DEVICE_STATES else None
        return value if isinstance(value, str) else None
