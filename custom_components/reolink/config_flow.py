"""Configuration flow"""

import logging
from typing import Optional
from homeassistant import config_entries
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
from reolinkapi.rest.system import DeviceInfo
from .const import (
    CONF_CHANNELS,
    CONF_PREFIX_CHANNEL,
    CONF_USE_HTTPS,
    DEFAULT_PORT,
    DEFAULT_PREFIX_CHANNEL,
    DEFAULT_USE_HTTPS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class ReolinkConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Reolink configuration flow"""

    def __init__(self) -> None:
        super().__init__()
        self._client = ReolinkClient()
        self._data = {}
        self._connection_id: int = 0
        self._channels: dict[int, str] = None
        self._abilities: Abilities = None
        self._devinfo: DeviceInfo = None

    async def async_step_user(self, user_input: Optional[dict[str, any]] = None):
        """Initial user setup"""

        return await self.async_step_connect(user_input)

    async def _setup_entry(self):
        hostname = self._data.get(CONF_HOST, "")
        port = self._data.get(CONF_PORT, DEFAULT_PORT)
        use_https = self._data.get(CONF_USE_HTTPS, DEFAULT_USE_HTTPS)
        _timeout = self._data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)

        await self._client.connect(hostname, port, _timeout, use_https=use_https)
        if self._connection_id != self._client.connection_id:
            self._channels = None
            self._connection_id = self._client.connection_id
        username = self._data.get(CONF_USERNAME, DEFAULT_USERNAME)
        password = self._data.get(CONF_PASSWORD, DEFAULT_PASSWORD)
        if not await self._client.login(username, password):
            return await self.async_step_login(
                self._data, {CONF_USERNAME, "please provide valid credentials"}
            )

        abil = await self._client.get_ability()
        if abil is None:
            return await self.async_step_connect(
                self._data, {CONF_HOST, "Could not get device abilities"}
            )
        self._abilities = abil

        devinfo = (
            await self._client.get_device_info() if abil.device_info.supported else None
        )
        self._devinfo = devinfo
        if (
            devinfo is not None
            and devinfo.channels > 1
            and CONF_CHANNELS not in self._data
        ):
            return await self.async_step_channels(
                self._data, {CONF_CHANNELS: "Please confirm the channels to monitor"}
            )

        link = (
            await self._client.get_local_link()
            if devinfo is None and abil.local_link.supported
            else None
        )
        if devinfo is not None or link is not None:
            await self.async_set_unique_id(
                devinfo.serial if devinfo is not None else link.mac_address
            )
            self._abort_if_unique_id_configured()

        return self.async_create_entry(title=devinfo.name, data=self._data)

    async def async_step_connect(
        self, user_input: Optional[dict[str, any]] = None, errors: dict = None
    ):
        """Initial connection"""

        if user_input is not None and errors is None:
            hostname: Optional[str] = user_input.get(CONF_HOST)
            port = None
            if hostname is not None:
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

                self._data.update(user_input)
                return await self._setup_entry()

        prior_input = user_input or {}

        return self.async_show_form(
            step_id="connect",
            description_placeholders={CONF_PORT: "Default"},
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=prior_input.get(CONF_HOST)): str,
                    vol.Optional(
                        CONF_PORT,
                        description={
                            "suggested_value": prior_input.get(CONF_PORT, DEFAULT_PORT)
                        },
                    ): cv.positive_int,
                    vol.Required(
                        CONF_USE_HTTPS,
                        default=prior_input.get(CONF_USE_HTTPS, DEFAULT_USE_HTTPS),
                    ): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_login(
        self, user_input: Optional[dict[str, any]] = None, errors: dict = None
    ):
        """Login information"""

        if user_input is not None and errors is None:
            self._data.update(user_input)
            return await self._setup_entry()

        prior_input = user_input or {}

        return self.async_show_form(
            step_id="login",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=prior_input.get(CONF_USERNAME, DEFAULT_USERNAME),
                    ): str,
                    vol.Optional(
                        CONF_PASSWORD,
                        description={
                            "suggested_value": prior_input.get(
                                CONF_PASSWORD, DEFAULT_PASSWORD
                            )
                        },
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_channels(
        self, user_input: Optional[dict[str, any]] = None, errors: dict = None
    ):
        """Channel Info"""

        if self._channels is None:
            channels = await self._client.get_channel_status()
            if not channels is None:
                self._channels = {status.channel: status.name for status in channels}
            else:
                self._channels = {}
                if errors is None:
                    errors = {}
                errors[CONF_CHANNELS] = "Could not load channels"

        if user_input is not None and errors is None:
            self._data.update(user_input)
            return await self._setup_entry()

        prior_input = user_input or {}
        if CONF_CHANNELS not in prior_input:
            prior_input[CONF_CHANNELS] = list(self._channels.keys())

        return self.async_show_form(
            step_id="channels",
            description_placeholders={
                "name": self._devinfo.name,
            },
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PREFIX_CHANNEL,
                        default=prior_input.get(
                            CONF_PREFIX_CHANNEL, DEFAULT_PREFIX_CHANNEL
                        ),
                    ): bool,
                    vol.Required(
                        CONF_CHANNELS,
                        default=prior_input.get(
                            CONF_CHANNELS, list(self._channels.keys())
                        ),
                    ): cv.multi_select(self._channels),
                }
            ),
            errors=errors,
        )

    async def async_finish_flow(self, _result):
        """cleanp"""
        await self._client.disconnect()
