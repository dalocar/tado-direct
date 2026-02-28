"""Config flow for Tado Direct integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
import voluptuous as vol
from yarl import URL

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
        """Handle user step."""

        if self.tado is None:
            _LOGGER.debug("Initiating device activation")
            try:
                session = async_get_clientsession(self.hass)
                self.tado = TadoDirectAPI(session=session)
                result = await self.tado.start_device_authorization()
                self._verification_url = result["verification_uri_complete"]
                self._user_code = result["user_code"]
            except (TadoAuthError, aiohttp.ClientError):
                _LOGGER.exception("Error while initiating Tado Direct")
                return self.async_abort(reason="cannot_connect")

        tado_device_url = self._verification_url
        user_code = self._user_code

        async def _wait_for_login() -> None:
            """Wait for the user to login."""
            assert self.tado is not None
            _LOGGER.debug("Waiting for device activation")
            try:
                await self.tado.wait_for_device_authorization(timeout=300)
            except Exception as ex:
                _LOGGER.exception("Error while waiting for device activation")
                raise CannotConnect from ex

            if self.tado.device_activation_status() != "COMPLETED":
                raise CannotConnect

        _LOGGER.debug("Checking login task")
        if self.login_task is None:
            _LOGGER.debug("Creating task for device activation")
            self.login_task = self.hass.async_create_task(_wait_for_login())

        if self.login_task.done():
            _LOGGER.debug("Login task is done, checking results")
            if self.login_task.exception():
                return self.async_show_progress_done(next_step_id="timeout")
            self.refresh_token = self.tado.get_refresh_token()
            return self.async_show_progress_done(next_step_id="finish_login")

        return self.async_show_progress(
            step_id="user",
            progress_action="wait_for_device",
            description_placeholders={
                "url": tado_device_url,
                "code": user_code,
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

        if self.source != SOURCE_REAUTH:
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=name,
                data={CONF_REFRESH_TOKEN: self.refresh_token},
            )

        self._abort_if_unique_id_mismatch(reason="reauth_account_mismatch")
        return self.async_update_reload_and_abort(
            self._get_reauth_entry(),
            data={CONF_REFRESH_TOKEN: self.refresh_token},
        )

    async def async_step_timeout(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle issues that need transition await from progress step."""
        if user_input is None:
            return self.async_show_form(
                step_id="timeout",
            )
        del self.login_task
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
