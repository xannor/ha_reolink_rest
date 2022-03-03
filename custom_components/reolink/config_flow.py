"""Configuration flow"""
from __future__ import annotations

import logging
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_TIMEOUT,
    CONF_USERNAME,
)
import voluptuous as vol
from reolinkapi.rest import Client as ReolinkClient
from reolinkapi.const import DEFAULT_USERNAME, DEFAULT_PASSWORD, DEFAULT_TIMEOUT
from reolinkapi.rest.abilities import Abilities
from reolinkapi.rest.abilities.channel import LiveAbilitySupport
from reolinkapi.rest.const import StreamTypes as CameraStreamTypes
from reolinkapi.rest.system import DeviceInfo
from reolinkapi.exceptions import ReolinkError
from .const import (
    CONF_CHANNELS,
    CONF_PREFIX_CHANNEL,
    CONF_STREAM_TYPE,
    CONF_USE_HTTPS,
    CONF_USE_RTSP,
    DEFAULT_PORT,
    DEFAULT_PREFIX_CHANNEL,
    DEFAULT_USE_HTTPS,
    DEFAULT_USE_RTSP,
    DOMAIN,
    OutputStreamTypes,
)

_LOGGER = logging.getLogger(__name__)

OUTPUT_STREAM_TYPES = {e: e.name for e in OutputStreamTypes}


class ReolinkBaseConfigFlow:
    """Base Reolink options flow"""

    def __init__(self, data: dict | None = None) -> None:
        super().__init__()
        self._data = data or {}
        self._authenticated = False
        self._channels: dict[int, str] = None
        self._abilities: Abilities = None
        self._devinfo: DeviceInfo = None
        self._unique_id: str = None

    async def _update_client_data(self):
        self._unique_id = None
        self._abilities = None
        self._devinfo = None
        self._channels = None
        self._unique_id = None
        try:
            client = ReolinkClient()

            hostname = self._data.get(CONF_HOST, "")
            port = self._data.get(CONF_PORT, DEFAULT_PORT)
            use_https = self._data.get(CONF_USE_HTTPS, DEFAULT_USE_HTTPS)
            _timeout = self._data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
            await client.connect(hostname, port, _timeout, use_https=use_https)

            username = self._data.get(CONF_USERNAME, DEFAULT_USERNAME)
            password = self._data.get(CONF_PASSWORD, DEFAULT_PASSWORD)
            self._authenticated = await client.login(username, password)
            if not self._authenticated:
                return

            abil = await client.get_ability()
            if abil is None:
                return
            self._abilities = abil

            devinfo = (
                await client.get_device_info() if abil.device_info.supported else None
            )
            self._devinfo = devinfo
            if devinfo is not None and devinfo.channels > 1:
                if self._channels is None:
                    channels = await client.get_channel_status()
                    if not channels is None:
                        self._channels = {
                            status.channel: status.name for status in channels
                        }

                if CONF_CHANNELS not in self._data:
                    return

            link = (
                await client.get_local_link()
                if devinfo is None and abil.local_link.supported
                else None
            )
            self._unique_id = (
                devinfo.serial if devinfo is not None else link.mac_address
            )

        finally:
            await client.disconnect()

    @staticmethod
    def _normalize_host(user_input: dict):
        hostname: str = user_input.get(CONF_HOST)
        port: int = user_input.get(CONF_PORT)
        idx = hostname.find("://")
        if idx > -1:
            schema = hostname[0:idx].lower()
            hostname = hostname[idx + 3 :]
            idx = hostname.find(":")
            if idx > -1:
                _port = hostname[idx + 1 :]
                hostname = hostname[0:idx]
                idx = _port.find("/")
                if idx > -1:
                    _port = _port[0:idx]
                port = int(_port)
            if schema == "https" or port == 443:
                user_input[CONF_USE_HTTPS] = True
                if port is None or port == 443:
                    port = None
            elif schema == "http" or port == 80:
                user_input[CONF_USE_HTTPS] = False
                if port is None or port == 80:
                    port = None
            user_input[CONF_HOST] = hostname
            user_input[CONF_PORT] = port or None

    @staticmethod
    def _connect_schema(prior_input: dict) -> dict:
        return {
            vol.Required(CONF_HOST, default=prior_input.get(CONF_HOST)): str,
            vol.Optional(
                CONF_PORT,
                description={
                    "suggested_value": prior_input.get(CONF_PORT, DEFAULT_PORT)
                },
            ): cv.port,
            vol.Required(
                CONF_USE_HTTPS,
                default=prior_input.get(CONF_USE_HTTPS, DEFAULT_USE_HTTPS),
            ): bool,
        }

    @staticmethod
    def _login_schema(prior_input: dict) -> dict:
        return {
            vol.Required(
                CONF_USERNAME,
                default=prior_input.get(CONF_USERNAME, DEFAULT_USERNAME),
            ): str,
            vol.Optional(
                CONF_PASSWORD,
                description={
                    "suggested_value": prior_input.get(CONF_PASSWORD, DEFAULT_PASSWORD)
                },
            ): str,
        }

    @staticmethod
    def _stream_schema(prior_input: dict, live: LiveAbilitySupport) -> dict:
        schema = {}
        if live in (LiveAbilitySupport.MAIN_SUB, LiveAbilitySupport.MAIN_EXTERN_SUB):
            key = f"{CONF_STREAM_TYPE}_{CameraStreamTypes.MAIN.name.lower()}"
            schema[vol.Required(key, default=prior_input.get(key, []))] = vol.All(
                cv.ensure_list, [vol.In(OUTPUT_STREAM_TYPES)]
            )
            key = f"{CONF_STREAM_TYPE}_{CameraStreamTypes.SUB.name.lower()}"
            schema[vol.Required(key, default=prior_input.get(key, []))] = vol.All(
                cv.ensure_list, [vol.In(OUTPUT_STREAM_TYPES)]
            )
        if live == LiveAbilitySupport.MAIN_EXTERN_SUB:
            key = f"{CONF_STREAM_TYPE}_{CameraStreamTypes.EXT.name.lower()}"
            schema[vol.Required(key, default=prior_input.get(key, []))] = vol.All(
                cv.ensure_list, [vol.In(OUTPUT_STREAM_TYPES)]
            )

        return schema

    @staticmethod
    def _channels_schema(prior_input: dict, channels: dict) -> dict:
        return {
            vol.Required(
                CONF_PREFIX_CHANNEL,
                default=prior_input.get(CONF_PREFIX_CHANNEL, DEFAULT_PREFIX_CHANNEL),
            ): bool,
            vol.Required(
                CONF_CHANNELS,
                default=prior_input.get(CONF_CHANNELS, set(channels.keys())),
            ): cv.multi_select(channels),
        }


class ReolinkConfigFlow(
    ReolinkBaseConfigFlow, config_entries.ConfigFlow, domain=DOMAIN
):
    """Reolink configuration flow"""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()

    async def _update_client_data(self):
        await super()._update_client_data()
        if self._devinfo:
            placeholders: dict = self.context.setdefault("title_placeholders", {})
            placeholders["name"] = self._devinfo.name
            placeholders["type"] = self._devinfo.type

    async def async_step_user(self, user_input: dict[str, any] | None = None):
        """Initial user setup"""

        return await self.async_step_connect(user_input)

    async def _setup_entry(self):
        try:
            await self._update_client_data()
        except ReolinkError:
            return await self.async_step_connect(self._data, {"base": "cannot_connect"})
        if not self._authenticated:
            return await self.async_step_login(self._data, {"base", "invalid_auth"})
        if self._abilities is None:
            return await self.async_step_connect(self._data, {"base": "cannot_connect"})

        if self._channels and CONF_CHANNELS not in self._data:
            return await self.async_step_channels(
                self._data, {CONF_CHANNELS: "channel_required"}
            )

        if self._unique_id is not None:
            await self.async_set_unique_id(self._unique_id)
            self._abort_if_unique_id_configured()

        return self.async_create_entry(title=self._devinfo.name, data=self._data)

    async def async_step_connect(
        self, user_input: dict[str, any] | None = None, errors: dict = None
    ):
        """Initial connection"""

        if user_input is not None and errors is None:
            hostname: str | None = user_input.get(CONF_HOST)
            if hostname is not None:
                ReolinkBaseConfigFlow._normalize_host(user_input)

                self._data.update(user_input)
                return await self._setup_entry()

        return self.async_show_form(
            step_id="connect",
            description_placeholders={CONF_PORT: "Default"},
            data_schema=vol.Schema(
                ReolinkBaseConfigFlow._connect_schema(user_input or {})
            ),
            errors=errors,
        )

    async def async_step_login(
        self, user_input: dict[str, any] = None, errors: dict = None
    ):
        """Login information"""

        if user_input is not None and errors is None:
            self._data.update(user_input)
            return await self._setup_entry()

        return self.async_show_form(
            step_id="login",
            data_schema=vol.Schema(
                ReolinkBaseConfigFlow._login_schema(user_input or {})
            ),
            errors=errors,
        )

    async def async_step_channels(
        self, user_input: dict[str, any] = None, errors: dict = None
    ):
        """Channel Info"""

        if user_input is not None and errors is None:
            self._data.update(user_input)
            return await self._setup_entry()

        return self.async_show_form(
            step_id="channels",
            description_placeholders={
                "name": self._devinfo.name,
            },
            data_schema=vol.Schema(
                ReolinkBaseConfigFlow._channels_schema(user_input or {}, self._channels)
            ),
            errors=errors,
        )

    async def async_step_reauth(self, user_input: dict[str, any] = None):
        """re-authenticate"""
        return await self.async_step_login(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """get options flow"""
        return ReolinkOptionsFlow(config_entry)


class ReolinkOptionsFlow(ReolinkBaseConfigFlow, config_entries.OptionsFlow):
    """Reolink options flow"""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        super().__init__(config_entry.data.copy())
        self._config = config_entry

    async def async_step_init(self, user_input: dict[str, any] = None):
        """init"""

        await self._update_client_data()
        return await self.async_step_options(user_input)

    async def async_step_options(
        self, user_input: dict[str, any] = None, errors: dict = None
    ):
        """Manage options"""

        if user_input is not None:
            pass
            # return self.async_create_entry(title="", data=user_input)

        schema = ReolinkBaseConfigFlow._connect_schema(user_input or self._config.data)
        schema.update(
            ReolinkBaseConfigFlow._login_schema(user_input or self._config.data)
        )
        if self._channels is not None:
            schema.update(
                ReolinkBaseConfigFlow._channels_schema(
                    user_input or self._config.data, self._channels
                )
            )
        return self.async_show_form(
            step_id="options", data_schema=vol.Schema(schema), errors=errors
        )
