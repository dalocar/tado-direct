"""Provides diagnostics for Tado Direct."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import TadoDirectConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: TadoDirectConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a Tado Direct config entry."""

    return {"data": config_entry.runtime_data.data}
