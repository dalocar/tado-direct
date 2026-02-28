"""Module for Tado Direct child lock switch entity."""

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import TadoDirectConfigEntry, TadoDirectDataUpdateCoordinator
from .entity import TadoDirectZoneEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TadoDirectConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Tado Direct switch platform."""

    tado = entry.runtime_data
    entities: list[TadoDirectChildLockSwitchEntity] = []
    for zone in tado.zones:
        zone_child_lock_supported = (
            len(zone["devices"]) > 0 and "childLockEnabled" in zone["devices"][0]
        )

        if not zone_child_lock_supported:
            continue

        entities.append(
            TadoDirectChildLockSwitchEntity(
                tado, zone["name"], zone["id"], zone["devices"][0]
            )
        )
    async_add_entities(entities, True)


class TadoDirectChildLockSwitchEntity(TadoDirectZoneEntity, SwitchEntity):
    """Representation of a Tado Direct child lock switch entity."""

    _attr_translation_key = "child_lock"

    def __init__(
        self,
        coordinator: TadoDirectDataUpdateCoordinator,
        zone_name: str,
        zone_id: int,
        device_info: dict[str, Any],
    ) -> None:
        """Initialize the Tado Direct child lock switch entity."""
        super().__init__(zone_name, coordinator.home_id, zone_id, coordinator)

        self._device_info = device_info
        self._device_id = self._device_info["shortSerialNo"]
        self._attr_unique_id = f"{zone_id} {coordinator.home_id} child-lock"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        await self.coordinator.set_child_lock(self._device_id, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        await self.coordinator.set_child_lock(self._device_id, False)
        await self.coordinator.async_request_refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._async_update_callback()
        super()._handle_coordinator_update()

    @callback
    def _async_update_callback(self) -> None:
        """Handle update callbacks."""
        try:
            self._device_info = self.coordinator.data["device"][self._device_id]
        except KeyError:
            _LOGGER.error(
                "Could not update child lock info for device %s in zone %s",
                self._device_id,
                self.zone_name,
            )
        else:
            self._attr_is_on = self._device_info.get("childLockEnabled", False) is True
