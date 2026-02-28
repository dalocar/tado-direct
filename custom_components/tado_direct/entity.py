"""Base class for Tado Direct entity."""

import logging

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_NAME, DOMAIN, TADO_BRIDGE_MODELS, TADO_HOME, TADO_ZONE
from .coordinator import TadoDirectDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


class TadoDirectCoordinatorEntity(CoordinatorEntity[TadoDirectDataUpdateCoordinator]):
    """Base class for Tado Direct entity."""

    _attr_has_entity_name = True


class TadoDirectDeviceEntity(TadoDirectCoordinatorEntity):
    """Base implementation for Tado Direct device."""

    def __init__(
        self, device_info: dict[str, str], coordinator: TadoDirectDataUpdateCoordinator
    ) -> None:
        """Initialize a Tado device."""
        super().__init__(coordinator)
        self._device_info = device_info
        self.device_name = device_info["serialNo"]
        self.device_id = device_info["shortSerialNo"]
        via_device: tuple[str, str] | None = None
        if device_info["deviceType"] not in TADO_BRIDGE_MODELS:
            for device in coordinator.data["device"].values():
                if device["deviceType"] in TADO_BRIDGE_MODELS:
                    via_device = (DOMAIN, device["shortSerialNo"])
                    break

        self._attr_device_info = DeviceInfo(
            configuration_url=f"https://app.tado.com/en/main/settings/rooms-and-devices/device/{self.device_name}",
            identifiers={(DOMAIN, self.device_id)},
            name=self.device_name,
            manufacturer=DEFAULT_NAME,
            sw_version=device_info["currentFwVersion"],
            model=device_info["deviceType"],
        )
        if via_device:
            self._attr_device_info["via_device"] = via_device


class TadoDirectHomeEntity(TadoDirectCoordinatorEntity):
    """Base implementation for Tado Direct home."""

    def __init__(self, coordinator: TadoDirectDataUpdateCoordinator) -> None:
        """Initialize a Tado home."""
        super().__init__(coordinator)
        self.home_name = coordinator.home_name
        self.home_id = coordinator.home_id
        self._attr_device_info = DeviceInfo(
            configuration_url="https://app.tado.com",
            identifiers={(DOMAIN, str(coordinator.home_id))},
            manufacturer=DEFAULT_NAME,
            model=TADO_HOME,
            name=coordinator.home_name,
        )


class TadoDirectZoneEntity(TadoDirectCoordinatorEntity):
    """Base implementation for Tado Direct zone."""

    def __init__(
        self,
        zone_name: str,
        home_id: int,
        zone_id: int,
        coordinator: TadoDirectDataUpdateCoordinator,
    ) -> None:
        """Initialize a Tado zone."""
        super().__init__(coordinator)
        self.zone_name = zone_name
        self.zone_id = zone_id
        self._attr_device_info = DeviceInfo(
            configuration_url=(f"https://app.tado.com/en/main/home/zoneV2/{zone_id}"),
            identifiers={(DOMAIN, f"{home_id}_{zone_id}")},
            name=zone_name,
            manufacturer=DEFAULT_NAME,
            model=TADO_ZONE,
            suggested_area=zone_name,
        )
