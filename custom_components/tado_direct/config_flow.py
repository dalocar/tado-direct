"""Config flow for Tado Direct integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_FALLBACK,
    CONF_REFRESH_TOKEN,
    CONF_USE_LEGACY_AUTH,
    CONST_OVERLAY_TADO_DEFAULT,
    CONST_OVERLAY_TADO_OPTIONS,
    DOMAIN,
)
from .coordinator import TadoDirectConfigEntry
from .tado_api import TadoAuthError, TadoDirectAPI

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class TadoDirectConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tado Direct."""

    VERSION = 2

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauth on credential failure."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prepare reauth."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")

        return await self.async_step_user()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle user step — email/password login."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                session = async_get_clientsession(self.hass)
                tado = TadoDirectAPI(session=session)
                await tado.login_with_password(
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )

                tado_me = await tado.get_me()

                if "homes" not in tado_me or len(tado_me["homes"]) == 0:
                    return self.async_abort(reason="no_homes")

                home = tado_me["homes"][0]
                unique_id = str(home["id"])
                name = home["name"]
                refresh_token = tado.get_refresh_token()
                use_legacy = tado._use_legacy_auth

                if self.source == SOURCE_REAUTH:
                    self._abort_if_unique_id_mismatch(
                        reason="reauth_account_mismatch"
                    )
                    return self.async_update_reload_and_abort(
                        self._get_reauth_entry(),
                        data={
                            CONF_REFRESH_TOKEN: refresh_token,
                            CONF_USE_LEGACY_AUTH: use_legacy,
                        },
                    )

                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=name,
                    data={
                        CONF_REFRESH_TOKEN: refresh_token,
                        CONF_USE_LEGACY_AUTH: use_legacy,
                    },
                )

            except TadoAuthError:
                _LOGGER.exception("Authentication failed")
                errors["base"] = "invalid_auth"
            except aiohttp.ClientError:
                _LOGGER.exception("Connection error")
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=USER_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: TadoDirectConfigEntry,
    ) -> OptionsFlowHandler:
        """Get the options flow for this handler."""
        return OptionsFlowHandler()


class OptionsFlowHandler(OptionsFlow):
    """Handle an option flow for Tado Direct."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle options flow."""
        if user_input:
            result = self.async_create_entry(data=user_input)
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)
            return result

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_FALLBACK,
                    default=self.config_entry.options.get(
                        CONF_FALLBACK, CONST_OVERLAY_TADO_DEFAULT
                    ),
                ): vol.In(CONST_OVERLAY_TADO_OPTIONS),
            }
        )
        return self.async_show_form(step_id="init", data_schema=data_schema)
