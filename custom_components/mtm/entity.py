"""Common identity, metadata and discovery for authorized devices."""

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MtmConfigEntry, MtmCoordinator

DEVICE_STATES = (
    "created",
    "enrolling",
    "online",
    "degraded",
    "offline",
    "retired",
    "enroll_failed",
)


@callback
def async_discover_devices(
    entry: MtmConfigEntry,
    async_add_entities: AddEntitiesCallback,
    create_entities: Callable[[MtmCoordinator, str], Iterable[Entity]],
) -> None:
    """Add newly visible devices while retaining unavailable registry/history entries."""
    coordinator = entry.runtime_data
    discovered: set[str] = set()

    @callback
    def discover() -> None:
        """Create each platform's entities once per device during this load."""
        if not coordinator.last_update_success:
            return
        new_ids = coordinator.data.keys() - discovered
        if not new_ids:
            return
        entities = [
            entity
            for device_id in sorted(new_ids)
            for entity in create_entities(coordinator, device_id)
        ]
        discovered.update(new_ids)
        async_add_entities(entities)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class MtmEntity(CoordinatorEntity[MtmCoordinator]):
    """A device entity isolated to the connected server/account grant."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: MtmCoordinator, device_id: str, key: str
    ) -> None:
        """Bind the entity to the stable config entry and device UUID."""
        super().__init__(coordinator, context=device_id)
        self.device_id = device_id
        entry = coordinator.config_entry
        assert entry is not None
        self._device_identifier = f"{entry.unique_id or entry.entry_id}:{device_id}"
        self._attr_unique_id = f"{self._device_identifier}:{key}"

    @property
    def device(self) -> dict[str, Any]:
        """Return only the current authorized row, never a retained stale row."""
        return self.coordinator.data.get(self.device_id, {})

    @property
    def available(self) -> bool:
        """Withdraw live entities when their device disappears or polling fails."""
        return super().available and self.device_id in self.coordinator.data

    @property
    def device_info(self) -> DeviceInfo:
        """Expose useful hardware metadata without network or policy internals."""
        row = self.device
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_identifier)},
            manufacturer="MikroTik",
            name=row.get("name"),
            model=row.get("board"),
            sw_version=row.get("ros_version"),
        )
