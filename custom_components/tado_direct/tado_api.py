"""Direct API client for Tado, replacing python-tado library.

Uses the Tado Android app's OAuth2 credentials to call the API directly,
bypassing 3rd-party rate limits imposed on the python-tado library.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

# OAuth2 Configuration
OAUTH_BASE_URL = "https://login.tado.com"
API_BASE_URL = "https://my.tado.com/api/v2"
HOPS_API_BASE_URL = "https://hops.tado.com"

# Tado webapp client (first-party, may support device code grant)
WEBAPP_CLIENT_ID = "af44f89e-ae86-4ebe-905f-6bf759cf6473"
WEBAPP_SCOPE = "home.user offline_access"

# PyTado device authorization client (fallback, known to work)
PYTADO_CLIENT_ID = "1bb50063-6b0c-4d11-bd99-387f4a91cc46"
PYTADO_SCOPE = "offline_access"

DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"

# Token refresh buffer (refresh 30s before expiry)
TOKEN_REFRESH_BUFFER = 30


class TadoApiError(Exception):
    """Base exception for Tado API errors."""


class TadoAuthError(TadoApiError):
    """Authentication error."""


class TadoConnectionError(TadoApiError):
    """Connection error."""


class TadoZone:
    """Wraps raw zone state JSON and exposes the same attribute interface as PyTado.TadoZone.

    This class provides property accessors that the HA entities expect,
    translating from the raw API JSON structure.
    """

    def __init__(
        self, data: dict[str, Any], default_overlay: dict[str, Any] | None = None
    ) -> None:
        """Initialize with raw zone state JSON and optional default overlay data."""
        self._data = data
        self._default_overlay = default_overlay or {}

    @property
    def current_temp(self) -> float | None:
        """Return current temperature in celsius."""
        sensor = self._data.get("sensorDataPoints", {})
        inside = sensor.get("insideTemperature")
        if inside:
            return inside.get("celsius")
        return None

    @property
    def current_temp_timestamp(self) -> str | None:
        """Return current temperature timestamp."""
        sensor = self._data.get("sensorDataPoints", {})
        inside = sensor.get("insideTemperature")
        if inside:
            return inside.get("timestamp")
        return None

    @property
    def current_humidity(self) -> float | None:
        """Return current humidity percentage."""
        sensor = self._data.get("sensorDataPoints", {})
        humidity = sensor.get("humidity")
        if humidity:
            return humidity.get("percentage")
        return None

    @property
    def current_humidity_timestamp(self) -> str | None:
        """Return current humidity timestamp."""
        sensor = self._data.get("sensorDataPoints", {})
        humidity = sensor.get("humidity")
        if humidity:
            return humidity.get("timestamp")
        return None

    @property
    def target_temp(self) -> float | None:
        """Return target temperature."""
        setting = self._data.get("setting", {})
        temp = setting.get("temperature")
        if temp:
            return temp.get("celsius")
        # Check overlay setting
        overlay = self._data.get("overlay")
        if overlay:
            overlay_temp = overlay.get("setting", {}).get("temperature")
            if overlay_temp:
                return overlay_temp.get("celsius")
        return None

    @property
    def current_hvac_mode(self) -> str:
        """Return current HVAC mode string.

        Returns one of: HEAT, COOL, AUTO, DRY, FAN, OFF, SMART_SCHEDULE
        """
        setting = self._data.get("setting", {})
        power = setting.get("power", "OFF")
        overlay = self._data.get("overlay")

        if power == "ON":
            if overlay:
                overlay_setting = overlay.get("setting", {})
                zone_type = overlay_setting.get("type", "")
                if zone_type in ("HEATING", "HOT_WATER"):
                    return "HEAT"
                mode = overlay_setting.get("mode")
                return mode if mode else "OFF"
            return "SMART_SCHEDULE"
        return "OFF"

    @property
    def current_hvac_action(self) -> str:
        """Return current HVAC action string.

        Returns one of: HEAT, COOL, DRY, FAN, IDLE, OFF, HOT_WATER
        """
        setting = self._data.get("setting", {})
        power = setting.get("power", "OFF")
        activity = self._data.get("activityDataPoints", {})

        if power == "ON":
            heating = activity.get("heatingPower")
            if heating and heating.get("percentage", 0) > 0:
                return "HEAT"
            ac = activity.get("acPower")
            if ac and ac.get("value") == "ON":
                mode = setting.get("mode")
                mode_map = {
                    "COOL": "COOL",
                    "HEAT": "HEAT",
                    "DRY": "DRY",
                    "FAN": "FAN",
                }
                return mode_map.get(mode, "COOL")
            return "IDLE"
        return "OFF"

    @property
    def current_fan_speed(self) -> str | None:
        """Return current fan speed (legacy)."""
        return self._data.get("setting", {}).get("fanSpeed")

    @property
    def current_fan_level(self) -> str | None:
        """Return current fan level."""
        setting = self._data.get("setting", {})
        fan_level = setting.get("fanLevel")
        if fan_level:
            return fan_level
        # Convert legacy fan speed to fan level
        fan_speed = setting.get("fanSpeed")
        if fan_speed:
            speed_to_level = {
                "LOW": "LEVEL1",
                "MIDDLE": "LEVEL2",
                "HIGH": "LEVEL3",
                "AUTO": "AUTO",
            }
            return speed_to_level.get(fan_speed)
        return None

    @property
    def current_swing_mode(self) -> str:
        """Return current swing mode (legacy ON/OFF)."""
        return self._data.get("setting", {}).get("swing", "OFF")

    @property
    def current_vertical_swing_mode(self) -> str | None:
        """Return current vertical swing mode."""
        return self._data.get("setting", {}).get("verticalSwing")

    @property
    def current_horizontal_swing_mode(self) -> str | None:
        """Return current horizontal swing mode."""
        return self._data.get("setting", {}).get("horizontalSwing")

    @property
    def overlay_active(self) -> bool:
        """Return whether an overlay is active."""
        return self._data.get("overlay") is not None

    @property
    def overlay_termination_type(self) -> str | None:
        """Return overlay termination type."""
        overlay = self._data.get("overlay")
        if overlay and overlay.get("termination"):
            return (
                overlay["termination"].get("typeSkillBasedApp")
                or overlay["termination"].get("type")
            )
        return None

    @property
    def open_window(self) -> bool:
        """Return whether an open window is active."""
        return self._data.get("openWindow") is not None

    @property
    def open_window_detected(self) -> bool:
        """Return whether an open window was detected."""
        return self._data.get("openWindowDetected", False)

    @property
    def open_window_attr(self) -> dict[str, Any]:
        """Return open window attributes."""
        ow = self._data.get("openWindow")
        if ow:
            return {
                "detected_time": ow.get("detectedTime"),
                "duration_in_seconds": ow.get("durationInSeconds"),
                "expiry": ow.get("expiry"),
                "remaining_time_in_seconds": ow.get("remainingTimeInSeconds"),
            }
        return {}

    @property
    def preparation(self) -> bool:
        """Return whether the zone is in preparation (early start)."""
        return self._data.get("preparation") is not None

    @property
    def is_away(self) -> bool:
        """Return whether the zone is in away mode."""
        return self._data.get("tadoMode") == "AWAY"

    @property
    def power(self) -> str:
        """Return power state (ON/OFF)."""
        return self._data.get("setting", {}).get("power", "OFF")

    @property
    def link(self) -> str:
        """Return link state (ONLINE/OFFLINE)."""
        return self._data.get("link", {}).get("state", "OFFLINE")

    @property
    def available(self) -> bool:
        """Return whether the zone is available (link online)."""
        return self.link == "ONLINE"

    @property
    def tado_mode(self) -> str | None:
        """Return tado mode (HOME/AWAY)."""
        return self._data.get("tadoMode")

    @property
    def heating_power_percentage(self) -> float | None:
        """Return heating power percentage."""
        activity = self._data.get("activityDataPoints", {})
        heating = activity.get("heatingPower")
        if heating:
            return heating.get("percentage")
        return None

    @property
    def heating_power_timestamp(self) -> str | None:
        """Return heating power timestamp."""
        activity = self._data.get("activityDataPoints", {})
        heating = activity.get("heatingPower")
        if heating:
            return heating.get("timestamp")
        return None

    @property
    def ac_power(self) -> str | None:
        """Return AC power state (ON/OFF)."""
        activity = self._data.get("activityDataPoints", {})
        ac = activity.get("acPower")
        if ac:
            return ac.get("value")
        return None

    @property
    def ac_power_timestamp(self) -> str | None:
        """Return AC power timestamp."""
        activity = self._data.get("activityDataPoints", {})
        ac = activity.get("acPower")
        if ac:
            return ac.get("timestamp")
        return None

    @property
    def default_overlay_termination_type(self) -> str | None:
        """Return default overlay termination type from zone settings."""
        tc = self._default_overlay.get("terminationCondition", {})
        return tc.get("type")

    @property
    def default_overlay_termination_duration(self) -> int | None:
        """Return default overlay termination duration in seconds."""
        tc = self._default_overlay.get("terminationCondition", {})
        return tc.get("durationInSeconds") or tc.get("remainingTimeInSeconds")


class TadoDirectAPI:
    """Async API client for Tado using the Android app's OAuth2 credentials."""

    def __init__(
        self,
        session: aiohttp.ClientSession | None = None,
        refresh_token: str | None = None,
    ) -> None:
        """Initialize the API client."""
        self._session = session
        self._owns_session = session is None
        self._refresh_token = refresh_token
        self._access_token: str | None = None
        self._token_expiry: float = 0
        self._home_id: int | None = None
        self._auto_geofencing_supported: bool = False
        self._is_tado_x: bool = False

        # Device authorization flow state
        self._device_code: str | None = None
        self._user_code: str | None = None
        self._verification_uri: str | None = None
        self._device_poll_interval: int = 5
        self._auth_client_id: str = WEBAPP_CLIENT_ID  # set during device auth

    @property
    def refresh_token(self) -> str | None:
        """Return current refresh token."""
        return self._refresh_token

    @property
    def home_id(self) -> int | None:
        """Return cached home ID."""
        return self._home_id

    @property
    def is_tado_x(self) -> bool:
        """Return whether this home uses Tado X (hops API)."""
        return self._is_tado_x

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure we have an active session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        """Close the session if we own it."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    # --- Device Authorization Flow ---

    async def _try_device_authorize(
        self, client_id: str, scope: str
    ) -> dict[str, Any] | None:
        """Try device authorization with a specific client. Returns result or None."""
        session = await self._ensure_session()
        data = {"client_id": client_id, "scope": scope}

        async with session.post(
            f"{OAUTH_BASE_URL}/oauth2/device_authorize", data=data
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            text = await resp.text()
            _LOGGER.debug(
                "Device auth with client %s failed (%s): %s",
                client_id, resp.status, text,
            )
            return None

    async def start_device_authorization(self) -> dict[str, str]:
        """Initiate the OAuth2 device authorization flow.

        Tries the Tado webapp client first (first-party), falls back to
        the PyTado device client.

        Returns dict with 'verification_uri_complete' and 'user_code'.
        """
        # Try webapp client first
        result = await self._try_device_authorize(WEBAPP_CLIENT_ID, WEBAPP_SCOPE)
        if result:
            self._auth_client_id = WEBAPP_CLIENT_ID
            _LOGGER.info("Using Tado webapp client for device authorization")
        else:
            # Fallback to PyTado client
            result = await self._try_device_authorize(PYTADO_CLIENT_ID, PYTADO_SCOPE)
            if result:
                self._auth_client_id = PYTADO_CLIENT_ID
                _LOGGER.info("Using PyTado client for device authorization")
            else:
                raise TadoAuthError("Device authorization failed with all clients")

        self._device_code = result["device_code"]
        self._user_code = result["user_code"]
        self._verification_uri = result.get(
            "verification_uri_complete", result.get("verification_uri", "")
        )
        self._device_poll_interval = result.get("interval", 5)

        return {
            "verification_uri_complete": self._verification_uri,
            "user_code": self._user_code,
        }

    async def check_device_authorization(self) -> bool:
        """Poll for device authorization completion.

        Returns True if authorized, False if still pending.
        """
        if not self._device_code:
            raise TadoAuthError("Device authorization not started")

        session = await self._ensure_session()
        data = {
            "client_id": self._auth_client_id,
            "device_code": self._device_code,
            "grant_type": DEVICE_GRANT_TYPE,
        }

        async with session.post(
            f"{OAUTH_BASE_URL}/oauth2/token",
            data=data,
        ) as resp:
            result = await resp.json()

            if resp.status == 200 and "access_token" in result:
                self._access_token = result["access_token"]
                self._refresh_token = result["refresh_token"]
                self._token_expiry = time.time() + result.get("expires_in", 600)
                return True

            error = result.get("error", "")
            if error in ("authorization_pending", "slow_down"):
                if error == "slow_down":
                    self._device_poll_interval += 1
                return False

            raise TadoAuthError(
                f"Device authorization failed: {result.get('error_description', error)}"
            )

    def get_refresh_token(self) -> str | None:
        """Return current refresh token."""
        return self._refresh_token

    # --- Token Management ---

    async def _refresh_access_token(self) -> None:
        """Refresh the access token using the refresh token."""
        if not self._refresh_token:
            raise TadoAuthError("No refresh token available")

        session = await self._ensure_session()
        _LOGGER.debug(
            "Refreshing token with client_id=%s, refresh_token=%s...",
            self._auth_client_id,
            self._refresh_token[:20] if self._refresh_token else "None",
        )
        data = {
            "client_id": self._auth_client_id,
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
        }

        async with session.post(
            f"{OAUTH_BASE_URL}/oauth2/token", data=data
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise TadoAuthError(f"Token refresh failed ({resp.status}): {text}")
            result = await resp.json()

        self._access_token = result["access_token"]
        self._refresh_token = result["refresh_token"]
        self._token_expiry = time.time() + result.get("expires_in", 600)
        _LOGGER.debug("Token refreshed successfully")

    async def _ensure_token(self) -> str:
        """Ensure we have a valid access token, refreshing if needed."""
        if (
            not self._access_token
            or time.time() >= self._token_expiry - TOKEN_REFRESH_BUFFER
        ):
            await self._refresh_access_token()
        return self._access_token

    # --- HTTP Request Helpers ---

    async def _request(
        self,
        method: str,
        url: str,
        json_data: dict | None = None,
        retry_on_401: bool = True,
        _rate_limit_attempt: int = 0,
    ) -> Any:
        """Make an authenticated API request."""
        session = await self._ensure_session()
        token = await self._ensure_token()

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            async with session.request(
                method, url, headers=headers, json=json_data
            ) as resp:
                if resp.status == 401 and retry_on_401:
                    _LOGGER.debug("Got 401, refreshing token and retrying")
                    self._access_token = None
                    return await self._request(
                        method, url, json_data, retry_on_401=False
                    )

                if resp.status == 429 and _rate_limit_attempt < 3:
                    retry_after = int(resp.headers.get("Retry-After", 1))
                    delay = max(retry_after, 2 ** _rate_limit_attempt)
                    _LOGGER.debug(
                        "Rate limited (429), retrying in %ss (attempt %s/3)",
                        delay,
                        _rate_limit_attempt + 1,
                    )
                    await asyncio.sleep(delay)
                    return await self._request(
                        method, url, json_data,
                        retry_on_401=retry_on_401,
                        _rate_limit_attempt=_rate_limit_attempt + 1,
                    )

                if resp.status == 204:
                    return {}

                if resp.status >= 400:
                    text = await resp.text()
                    raise TadoApiError(
                        f"API request failed ({resp.status}): {text}"
                    )

                return await resp.json()
        except aiohttp.ClientError as err:
            raise TadoConnectionError(f"Connection error: {err}") from err

    def _home_url(self, path: str = "") -> str:
        """Build a URL for the v2 home API."""
        if self._home_id is None:
            raise TadoApiError("Home ID not set. Call get_me() first.")
        return f"{API_BASE_URL}/homes/{self._home_id}/{path}" if path else f"{API_BASE_URL}/homes/{self._home_id}"

    def _hops_home_url(self, path: str = "") -> str:
        """Build a URL for the hops (Tado X) home API."""
        if self._home_id is None:
            raise TadoApiError("Home ID not set. Call get_me() first.")
        return f"{HOPS_API_BASE_URL}/homes/{self._home_id}/{path}" if path else f"{HOPS_API_BASE_URL}/homes/{self._home_id}"

    # --- API Methods ---

    async def get_me(self) -> dict[str, Any]:
        """Get current user profile including homes."""
        result = await self._request("GET", f"{API_BASE_URL}/me")
        # Cache the home ID from the first home
        if "homes" in result and result["homes"]:
            self._home_id = result["homes"][0]["id"]
        return result

    async def get_zones(self) -> list[dict[str, Any]]:
        """Get all zones for the home."""
        return await self._request("GET", self._home_url("zones"))

    async def get_devices(self) -> list[dict[str, Any]]:
        """Get all devices for the home."""
        return await self._request("GET", self._home_url("devices"))

    async def get_zone_states(self) -> dict[str, Any]:
        """Get all zone states at once.

        Returns raw JSON: {"zoneStates": {"1": {...}, "2": {...}, ...}}
        """
        return await self._request("GET", self._home_url("zoneStates"))

    async def get_zone_state(self, zone_id: int) -> dict[str, Any]:
        """Get current state of a single zone (raw JSON)."""
        return await self._request(
            "GET", self._home_url(f"zones/{zone_id}/state")
        )

    async def get_capabilities(self, zone_id: int | str) -> dict[str, Any]:
        """Get capabilities for a zone."""
        return await self._request(
            "GET", self._home_url(f"zones/{zone_id}/capabilities")
        )

    async def get_zone_overlay_default(self, zone_id: int) -> dict[str, Any]:
        """Get default overlay settings for a zone."""
        return await self._request(
            "GET", self._home_url(f"zones/{zone_id}/defaultOverlay")
        )

    async def get_weather(self) -> dict[str, Any]:
        """Get weather data for the home."""
        return await self._request("GET", self._home_url("weather"))

    async def get_home_state(self) -> dict[str, Any]:
        """Get current home state (presence, geofencing)."""
        result = await self._request("GET", self._home_url("state"))
        # Update auto geofencing support flag
        self._auto_geofencing_supported = "presenceLocked" in result
        return result

    async def get_device_info(
        self, device_id: str, key: str
    ) -> dict[str, Any]:
        """Get device info (e.g. temperatureOffset)."""
        return await self._request(
            "GET", f"{API_BASE_URL}/devices/{device_id}/{key}"
        )

    async def get_auto_geofencing_supported(self) -> bool:
        """Return whether auto geofencing is supported."""
        return self._auto_geofencing_supported

    async def set_zone_overlay(
        self,
        zone_id: int,
        overlay_mode: str,
        temperature: float | None = None,
        duration: int | None = None,
        device_type: str = "HEATING",
        power: str = "ON",
        mode: str | None = None,
        fan_speed: str | None = None,
        swing: str | None = None,
        fan_level: str | None = None,
        vertical_swing: str | None = None,
        horizontal_swing: str | None = None,
    ) -> dict[str, Any]:
        """Set a zone overlay (manual control)."""
        payload: dict[str, Any] = {
            "setting": {"type": device_type, "power": power},
            "termination": {"typeSkillBasedApp": overlay_mode},
        }

        if temperature is not None:
            payload["setting"]["temperature"] = {"celsius": temperature}

        if mode is not None:
            payload["setting"]["mode"] = mode

        if fan_speed is not None:
            payload["setting"]["fanSpeed"] = fan_speed
        elif fan_level is not None:
            payload["setting"]["fanLevel"] = fan_level

        if swing is not None:
            payload["setting"]["swing"] = swing
        else:
            if vertical_swing is not None:
                payload["setting"]["verticalSwing"] = vertical_swing
            if horizontal_swing is not None:
                payload["setting"]["horizontalSwing"] = horizontal_swing

        if duration is not None:
            payload["termination"]["durationInSeconds"] = duration

        return await self._request(
            "PUT",
            self._home_url(f"zones/{zone_id}/overlay"),
            json_data=payload,
        )

    async def reset_zone_overlay(self, zone_id: int) -> dict[str, Any]:
        """Delete zone overlay (resume schedule)."""
        return await self._request(
            "DELETE", self._home_url(f"zones/{zone_id}/overlay")
        )

    async def set_home(self) -> dict[str, Any]:
        """Set home presence to HOME."""
        return await self._request(
            "PUT",
            self._home_url("presenceLock"),
            json_data={"homePresence": "HOME"},
        )

    async def set_away(self) -> dict[str, Any]:
        """Set home presence to AWAY."""
        return await self._request(
            "PUT",
            self._home_url("presenceLock"),
            json_data={"homePresence": "AWAY"},
        )

    async def set_auto(self) -> dict[str, Any]:
        """Set home presence to AUTO (delete presence lock)."""
        return await self._request(
            "DELETE", self._home_url("presenceLock")
        )

    async def set_temp_offset(
        self, device_id: str, offset: float, measure: str = "celsius"
    ) -> dict[str, Any]:
        """Set temperature offset on a device."""
        return await self._request(
            "PUT",
            f"{API_BASE_URL}/devices/{device_id}/temperatureOffset",
            json_data={measure: offset},
        )

    async def set_child_lock(
        self, device_id: str, enabled: bool
    ) -> dict[str, Any]:
        """Set child lock on a device."""
        return await self._request(
            "PUT",
            f"{API_BASE_URL}/devices/{device_id}/childLock",
            json_data={"childLockEnabled": enabled},
        )

    async def set_eiq_meter_readings(
        self, date: str, reading: int
    ) -> dict[str, Any]:
        """Send meter reading to Tado Energy IQ."""
        return await self._request(
            "POST",
            self._home_url("meterReadings"),
            json_data={"date": date, "reading": reading},
        )

    # --- Tado X (hops) API Methods ---

    async def detect_tado_x(self) -> bool:
        """Detect if this home uses Tado X (hops API).

        Tries to fetch rooms from the hops API. If rooms exist, this is a
        Tado X home. Result is cached in _is_tado_x.
        """
        try:
            rooms = await self._request("GET", self._hops_home_url("rooms"))
            if rooms and isinstance(rooms, list) and len(rooms) > 0:
                self._is_tado_x = True
                _LOGGER.info("Tado X detected for home %s", self._home_id)
                return True
        except TadoApiError:
            pass
        _LOGGER.debug("Home %s is not Tado X (using v2 API)", self._home_id)
        return False

    async def get_rooms(self) -> list[dict[str, Any]]:
        """Get all rooms from hops API (Tado X)."""
        return await self._request("GET", self._hops_home_url("rooms"))

    async def get_actionable_devices(self) -> list[dict[str, Any]]:
        """Get devices from hops API (Tado X)."""
        return await self._request("GET", self._hops_home_url("actionableDevices"))

    async def set_room_overlay(
        self,
        room_id: int,
        power: str = "ON",
        temperature: float | None = None,
        termination_type: str = "MANUAL",
        duration: int | None = None,
    ) -> dict[str, Any]:
        """Set manual control on a Tado X room."""
        payload: dict[str, Any] = {
            "setting": {"power": power},
            "termination": {"type": termination_type},
        }
        if temperature is not None:
            payload["setting"]["temperature"] = {"value": temperature}
        if duration is not None:
            payload["termination"]["durationInSeconds"] = duration
        return await self._request(
            "POST",
            self._hops_home_url(f"rooms/{room_id}/manualControl"),
            json_data=payload,
        )

    async def reset_room_overlay(self, room_id: int) -> dict[str, Any]:
        """Resume schedule for a Tado X room."""
        return await self._request(
            "POST",
            self._hops_home_url(f"rooms/{room_id}/resumeSchedule"),
            json_data={},
        )

    @staticmethod
    def normalize_hops_room(room: dict) -> dict:
        """Convert a hops room response to v2-compatible zone state format.

        This normalization allows all existing entity code (climate.py, sensor.py,
        binary_sensor.py, etc.) to work unchanged with Tado X data.
        The webapp does the same mapping in mapApiRoomToRoom().
        """
        setting = dict(room.get("setting", {}))
        setting_temp = setting.get("temperature")

        # Hops API doesn't include "power" or "type" in setting.
        # Derive them: if temperature exists → power ON, otherwise OFF.
        if "power" not in setting:
            setting["power"] = "ON" if setting_temp else "OFF"
        if "type" not in setting:
            setting["type"] = "HEATING"

        state: dict[str, Any] = {
            "setting": setting,
            "link": {
                "state": "ONLINE"
                if room.get("connection", {}).get("state") == "CONNECTED"
                else "OFFLINE"
            },
            "sensorDataPoints": dict(room.get("sensorDataPoints", {})),
            "activityDataPoints": {
                "heatingPower": room.get("heatingPower", {"percentage": 0}),
            },
            "tadoMode": "AWAY" if room.get("awayMode") else "HOME",
        }

        # Normalize temperature fields: value → celsius
        if setting_temp and "value" in setting_temp and "celsius" not in setting_temp:
            setting_temp["celsius"] = setting_temp["value"]

        inside = state["sensorDataPoints"].get("insideTemperature")
        if inside and "celsius" not in inside and "value" in inside:
            inside["celsius"] = inside["value"]

        # Build overlay from manualControlTermination or boostMode
        mct = room.get("manualControlTermination")
        boost = room.get("boostMode")
        if mct:
            state["overlay"] = {
                "setting": state["setting"],
                "termination": {
                    "typeSkillBasedApp": mct.get("type"),
                    "durationInSeconds": mct.get("durationInSeconds"),
                },
            }
        elif boost:
            state["overlay"] = {
                "setting": {
                    "power": "ON",
                    "temperature": state["setting"].get("temperature"),
                },
                "termination": {
                    "typeSkillBasedApp": boost.get("type"),
                    "durationInSeconds": boost.get("durationInSeconds"),
                },
            }

        # Open window
        ow = room.get("openWindow")
        if ow:
            if ow.get("activated"):
                state["openWindow"] = {
                    "remainingTimeInSeconds": ow.get("expiryInSeconds"),
                }
            else:
                state["openWindowDetected"] = True

        # Preparation (early start / preheating)
        away_mode = room.get("awayMode")
        if away_mode and isinstance(away_mode, dict) and away_mode.get("preheating"):
            state["preparation"] = True

        return state
