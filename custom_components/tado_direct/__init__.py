"""Support for Tado Direct - using Tado app credentials."""

from datetime import timedelta
import logging

import aiohttp

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_AUTH_CLIENT_ID,
    CONF_FALLBACK,
    CONF_REFRESH_TOKEN,
    CONST_OVERLAY_MANUAL,
    CONST_OVERLAY_TADO_DEFAULT,
    CONST_OVERLAY_TADO_MODE,
    CONST_OVERLAY_TADO_OPTIONS,
    DOMAIN,
    TADO_BRIDGE_MODELS,
)
from .coordinator import TadoDirectConfigEntry, TadoDirectDataUpdateCoordinator
from .services import async_setup_services
from .tado_api import TadoApiError, TadoAuthError, TadoDirectAPI

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
]

MIN_TIME_BETWEEN_UPDATES = timedelta(minutes=4)
SCAN_INTERVAL = timedelta(minutes=5)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Tado Direct."""

    async_setup_services(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: TadoDirectConfigEntry
) -> bool:
    """Set up Tado Direct from a config entry."""
    if CONF_REFRESH_TOKEN not in entry.data:
        raise ConfigEntryAuthFailed

    _async_import_options_from_data_if_missing(hass, entry)

    _LOGGER.debug("Setting up Tado Direct connection")

    session = async_get_clientsession(hass)
    stored_refresh_token = entry.data[CONF_REFRESH_TOKEN]
    auth_client_id = entry.data.get(CONF_AUTH_CLIENT_ID)

    _LOGGER.debug(
        "Setting up with client_id=%s, refresh_token=%s...",
        auth_client_id,
        stored_refresh_token[:20] if stored_refresh_token else "None",
    )

    tado = TadoDirectAPI(
        session=session,
        refresh_token=stored_refresh_token,
    )
    # Restore the auth client ID used during setup (for token refresh)
    if auth_client_id:
        tado._auth_client_id = auth_client_id

    try:
        # Validate the refresh token by attempting to get user info
        await tado.get_me()
    except TadoAuthError as err:
        raise ConfigEntryAuthFailed(
            f"Invalid Tado credentials: {err}"
        ) from err
    except (TadoApiError, aiohttp.ClientError) as err:
        raise ConfigEntryNotReady(
            f"Error during Tado Direct setup: {err}"
        ) from err

    _LOGGER.debug("Tado Direct connection established")

    # Persist updated refresh token if it was rotated during auth
    new_refresh_token = tado.get_refresh_token()
    if new_refresh_token and new_refresh_token != stored_refresh_token:
        _LOGGER.debug("Refresh token was rotated, updating config entry")
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_REFRESH_TOKEN: new_refresh_token},
        )

    coordinator = TadoDirectDataUpdateCoordinator(hass, entry, tado)
    await coordinator.async_config_entry_first_refresh()

    # Pre-register the bridge device to ensure it exists before other devices reference it
    device_registry = dr.async_get(hass)
    for device in coordinator.data["device"].values():
        if device["deviceType"] in TADO_BRIDGE_MODELS:
            _LOGGER.debug("Pre-registering Tado bridge: %s", device["shortSerialNo"])
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, device["shortSerialNo"])},
                manufacturer="Tado",
                model=device["deviceType"],
                name=device["serialNo"],
                sw_version=device["currentFwVersion"],
                configuration_url=f"https://app.tado.com/en/main/settings/rooms-and-devices/device/{device['serialNo']}",
            )

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


@callback
def _async_import_options_from_data_if_missing(
    hass: HomeAssistant, entry: TadoDirectConfigEntry
):
    options = dict(entry.options)
    if CONF_FALLBACK not in options:
        options[CONF_FALLBACK] = entry.data.get(
            CONF_FALLBACK, CONST_OVERLAY_TADO_DEFAULT
        )
        hass.config_entries.async_update_entry(entry, options=options)

    if options[CONF_FALLBACK] not in CONST_OVERLAY_TADO_OPTIONS:
        if options[CONF_FALLBACK]:
            options[CONF_FALLBACK] = CONST_OVERLAY_TADO_MODE
        else:
            options[CONF_FALLBACK] = CONST_OVERLAY_MANUAL
        hass.config_entries.async_update_entry(entry, options=options)


async def async_unload_entry(hass: HomeAssistant, entry: TadoDirectConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
