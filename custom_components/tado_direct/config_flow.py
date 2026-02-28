"""Config flow for Tado Direct integration."""

from __future__ import annotations

import logging
import secrets
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
    tado: TadoDirectAPI | None = None
    refresh_token: str | None = None
    _code_verifier: str | None = None
    _state: str | None = None
    _auth_url: str | None = None

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
        """Handle user step — Authorization Code + PKCE flow."""
        errors: dict[str, str] = {}

        if user_input is not None:
            redirect_url = user_input.get("redirect_url", "")
            try:
                code, state = TadoDirectAPI.parse_authorization_response(
                    redirect_url
                )

                if state != self._state:
                    errors["redirect_url"] = "invalid_auth"
                else:
                    session = async_get_clientsession(self.hass)
                    self.tado = TadoDirectAPI(session=session)
                    await self.tado.exchange_authorization_code(
                        code, self._code_verifier
                    )
                    self.refresh_token = self.tado.get_refresh_token()
                    return await self.async_step_finish_login()

            except TadoAuthError:
                _LOGGER.exception("Authentication failed")
                errors["redirect_url"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Error processing redirect URL")
                errors["redirect_url"] = "invalid_auth"

        # Generate PKCE pair and auth URL on first visit
        if self._code_verifier is None:
            self._code_verifier, code_challenge = (
                TadoDirectAPI.generate_pkce_pair()
            )
            self._state = secrets.token_urlsafe(16)
            self._auth_url = TadoDirectAPI.get_authorization_url(
                code_challenge, self._state
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required("redirect_url"): str}
            ),
            description_placeholders={"auth_url": self._auth_url},
            errors=errors,
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
