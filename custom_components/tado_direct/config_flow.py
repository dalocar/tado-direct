"""Config flow for Tado Direct integration."""

from __future__ import annotations

import asyncio
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
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_AUTH_CLIENT_ID,
    CONF_FALLBACK,
    CONF_REFRESH_TOKEN,
    CONST_OVERLAY_TADO_DEFAULT,
    CONST_OVERLAY_TADO_OPTIONS,
    DOMAIN,
)
from .coordinator import TadoDirectConfigEntry
from .tado_api import TadoAuthError, TadoDirectAPI

_LOGGER = logging.getLogger(__name__)


class TadoDirectConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tado Direct."""

    VERSION = 2
    login_task: asyncio.Task | None = None
    refresh_token: str | None = None
    tado: TadoDirectAPI | None = None
    _verification_url: str | None = None
    _user_code: str | None = None

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
        """Handle user step — device authorization flow."""

        if self.tado is None:
            _LOGGER.debug("Initiating device authorization")
            try:
                session = async_get_clientsession(self.hass)
                self.tado = TadoDirectAPI(session=session)
                result = await self.tado.start_device_authorization()
                self._verification_url = result["verification_uri_complete"]
                self._user_code = result["user_code"]
            except (TadoAuthError, aiohttp.ClientError):
                _LOGGER.exception("Error initiating device authorization")
                return self.async_abort(reason="cannot_connect")

        async def _wait_for_login() -> None:
            """Wait for the user to complete login."""
            assert self.tado is not None
            _LOGGER.debug("Waiting for device authorization")
            deadline = asyncio.get_event_loop().time() + 300
            try:
                while asyncio.get_event_loop().time() < deadline:
                    if await self.tado.check_device_authorization():
                        return
                    await asyncio.sleep(self.tado._device_poll_interval)
            except TadoAuthError as ex:
                _LOGGER.exception("Device authorization error")
                raise CannotConnect from ex
            raise CannotConnect("Device authorization timed out")

        if self.login_task is None:
            self.login_task = self.hass.async_create_task(_wait_for_login())

        if self.login_task.done():
            if self.login_task.exception():
                return self.async_show_progress_done(next_step_id="timeout")
            self.refresh_token = self.tado.get_refresh_token()
            return self.async_show_progress_done(next_step_id="finish_login")

        return self.async_show_progress(
            step_id="user",
            progress_action="wait_for_device",
            description_placeholders={
                "url": self._verification_url,
                "code": self._user_code,
            },
            progress_task=self.login_task,
        )

    async def async_step_finish_login(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the finalization of login."""
        _LOGGER.debug("Finalizing login")
        assert self.tado is not None
        tado_me = await self.tado.get_me()

        if "homes" not in tado_me or len(tado_me["homes"]) == 0:
            return self.async_abort(reason="no_homes")

        home = tado_me["homes"][0]
        unique_id = str(home["id"])
        name = home["name"]

        entry_data = {
            CONF_REFRESH_TOKEN: self.refresh_token,
            CONF_AUTH_CLIENT_ID: self.tado._auth_client_id,
        }

        if self.source != SOURCE_REAUTH:
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=name,
                data=entry_data,
            )

        self._abort_if_unique_id_mismatch(reason="reauth_account_mismatch")
        return self.async_update_reload_and_abort(
            self._get_reauth_entry(),
            data=entry_data,
        )

    async def async_step_timeout(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle timeout — allow retry."""
        if user_input is None:
            return self.async_show_form(step_id="timeout")
        self.login_task = None
        self.tado = None
        return await self.async_step_user()

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


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""
