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
from reolinkapi.rest.const import StreamTypes as CameraStreamTypes
from reolinkapi.rest.typings.abilities import Abilities
from reolinkapi.rest.typings.abilities.channel import LiveAbilityVers
from reolinkapi.rest.typings.system import DeviceInfo
from reolinkapi.exceptions import ReolinkError
from .const import (
    CONF_CHANNELS,
    CONF_PREFIX_CHANNEL,
    CONF_USE_HTTPS,
    DEFAULT_PORT,
    DEFAULT_PREFIX_CHANNEL,
    DEFAULT_STREAM_TYPE,
    DEFAULT_USE_HTTPS,
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
        self._connection_id: int = 0
        self._auth_id: int = 0

    async def _update_client_data(self):

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
                self._auth_id = 0
                return

            if (
                self._connection_id == client.connection_id
                and self._auth_id == client.authentication_id
            ):
                return

            if CONF_USERNAME not in self._data:
                self._data[CONF_USERNAME] = username
                self._data[CONF_PASSWORD] = password

            commands = []
            self._connection_id = client.connection_id
            abil = self._abilities
            if self._auth_id != client.authentication_id:
                abil = self._abilities = await client.get_ability()
            else:
                commands.append(ReolinkClient.create_get_ability())

            if self._abilities is None:
                return
            self._auth_id = client.authentication_id

            if self._abilities["p2p"]["ver"]:
                commands.append(ReolinkClient.create_get_p2p())

            if self._abilities["localLink"]["ver"]:
                commands.append(ReolinkClient.create_get_local_link())

            if self._abilities["devInfo"]["ver"]:
                commands.append(ReolinkClient.create_get_device_info())
            if self._devinfo is not None and self._devinfo["channelNum"] > 1:
                commands.append(ReolinkClient.create_get_channel_status())

            responses = await client.batch(commands)
            self._abilities = next(ReolinkClient.get_ability_responses(responses), abil)

            if self._abilities is None:
                return
            p2p = next(ReolinkClient.get_p2p_responses(responses), None)
            link = next(ReolinkClient.get_local_link_responses(responses), None)
            self._devinfo = next(ReolinkClient.get_device_info_responses(responses))
            channels = next(ReolinkClient.get_channel_status_responses(responses), None)
            if (
                self._devinfo is not None
                and self._devinfo["channelNum"] > 1
                and channels is None
            ):
                channels = await client.get_channel_status()
            elif channels is not None:
                channels = channels["status"]
            self._channels = (
                {channel["channel"]: channel["name"] for channel in channels}
                if channels is not None
                else None
            )

            self._unique_id = (
                f'reolink-uid-{p2p["uid"]}'
                if p2p is not None
                else f'reolink-device-{self._devinfo["type"]}-{self._devinfo["serial"]}'
                if self._devinfo is not None
                else f'reolink-mac-{link["mac"]}'
                if link is not None
                else None
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

    @staticmethod
    def _channel_schema(
        live: LiveAbilityVers,
        supported_output_types: dict[OutputStreamTypes, str],
        prior_input: dict,
    ) -> dict:
        def _create_schema(stream: CameraStreamTypes):
            _key = f"{stream.name.lower()}_type"
            return (
                vol.Required(
                    _key, default=prior_input.get(_key, DEFAULT_STREAM_TYPE[stream])
                ),
                vol.In(supported_output_types),
            )

        schema = []

        if live in (LiveAbilityVers.MAIN_SUB, LiveAbilityVers.MAIN_EXTERN_SUB):
            schema.append(_create_schema(CameraStreamTypes.MAIN))
            schema.append(_create_schema(CameraStreamTypes.SUB))
        if live == LiveAbilityVers.MAIN_EXTERN_SUB:
            schema.append(_create_schema(CameraStreamTypes.EXT))

        return {_k: _v for _k, _v in schema}


class ReolinkConfigFlow(
    ReolinkBaseConfigFlow, config_entries.ConfigFlow, domain=DOMAIN
):
    """Reolink configuration flow"""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()

    async def _update_client_data(self):
        await super()._update_client_data()
        if self._devinfo is not None:
            placeholders: dict = self.context.setdefault("title_placeholders", {})
            placeholders["name"] = self._devinfo["name"]
            placeholders["type"] = self._devinfo["type"]

    async def async_step_user(self, user_input: dict[str, any] | None = None):
        """Initial user setup"""

        return await self.async_step_connect(user_input)

    async def _setup_entry(self):
        try:
            await self._update_client_data()
        except ReolinkError:
            return await self.async_step_connect(self._data, {"base": "cannot_connect"})
        if not self._authenticated:
            return await self.async_step_login(self._data, {"base": "invalid_auth"})
        if self._abilities is None:
            return await self.async_step_connect(self._data, {"base": "cannot_connect"})

        if self._channels and CONF_CHANNELS not in self._data:
            return await self.async_step_channels(
                self._data, {CONF_CHANNELS: "channel_required"}
            )

        if self._unique_id is not None:
            await self.async_set_unique_id(self._unique_id)
            self._abort_if_unique_id_configured()

        title = self._devinfo["name"]

        return self.async_create_entry(title=title, data=self._data)

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
        """Channels Info"""

        if user_input is not None and errors is None:
            self._data.update(user_input)
            return await self._setup_entry()

        return self.async_show_form(
            step_id="channels",
            description_placeholders={
                "name": self._devinfo["name"],
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


CONF_MENU_CHOICE = "menu_choice"


class ReolinkOptionsFlow(ReolinkBaseConfigFlow, config_entries.OptionsFlow):
    """Reolink options flow"""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        super().__init__(config_entry.data.copy())
        self._options = (
            config_entry.options.copy() if config_entry.options is not None else {}
        )

    async def async_step_init(self, user_input: dict[str, any] = None):
        """init"""
        await self._update_client_data()
        return await self.async_step_menu(user_input)

    async def _setup_entry(self):
        await self._update_client_data()
        return await self.async_step_menu()

    async def async_step_menu(self, user_input: dict[str, any] = None):
        """Menu"""

        if user_input is not None:
            choice = user_input.get(CONF_MENU_CHOICE, "done")
            if choice == "done":
                return self.async_create_entry(title="", data=self._data)
            if choice == "options":
                return await self.async_step_options(self._options, {})
            if choice == "channels":
                return await self.async_step_channels(self._data, {})
            if choice[0:8] == "channel_":
                self.context["channel_id"] = int(choice[8:])
                return await self.async_step_channel(self._options, {})

        if self._channels is None:
            return await self.async_step_options(self._options, {})

        choices = [("options", "General Settings"), ("channels", "Select Channels")]

        choices.extend(
            (
                (f"channel_{key}", f"Configure ({name})")
                for key, name in self._channels.items()
            )
        )

        choices.append(("done", "Save"))
        choices = {_k: _v for _k, _v in choices}

        return self.async_show_form(
            step_id="menu",
            data_schema=vol.Schema(
                {vol.Required(CONF_MENU_CHOICE, default="done"): vol.In(choices)}
            ),
        )

    async def async_step_channels(
        self, user_input: dict[str, any] = None, errors: dict = None
    ):
        """Channels Info"""

        if user_input is not None and errors is None:
            self._data.update(user_input)
            return await self._setup_entry()

        return self.async_show_form(
            step_id="channels",
            description_placeholders={
                "name": self._devinfo["name"],
            },
            data_schema=vol.Schema(
                ReolinkBaseConfigFlow._channels_schema(user_input or {}, self._channels)
            ),
            errors=errors,
        )

    def _update_channel_options(self, channel_id: int, user_input: dict[str, any]):
        def _key_pair(stream: CameraStreamTypes):
            user_key = f"{stream.name.lower()}_type"
            return (user_key, f"channel_{channel_id}_{user_key}")

        def _update_type(stream: CameraStreamTypes):
            keys = _key_pair(stream)
            self._options[keys[1]] = user_input.get(
                keys[0], DEFAULT_STREAM_TYPE[stream]
            )

        _update_type(CameraStreamTypes.MAIN)
        _update_type(CameraStreamTypes.SUB)
        _update_type(CameraStreamTypes.EXT)

    def _get_channel_options(self, channel_id: int):
        def _key_pair(stream: CameraStreamTypes):
            user_key = f"{stream.name.lower()}_type"
            return (user_key, f"channel_{channel_id}_{user_key}")

        user_input = {}

        def _update_option(stream: CameraStreamTypes):
            keys = _key_pair(stream)
            if keys[1] in self._options:
                user_input[keys[0]] = self._options[keys[1]]

        live = self._abilities["abilityChn"][channel_id]["live"]["ver"]
        if live in (LiveAbilityVers.MAIN_SUB, LiveAbilityVers.MAIN_EXTERN_SUB):
            _update_option(CameraStreamTypes.MAIN)
            _update_option(CameraStreamTypes.SUB)
        if live == LiveAbilityVers.MAIN_EXTERN_SUB:
            _update_option(CameraStreamTypes.EXT)

        return user_input

    def _get_output_streams(self):
        output_types = list()
        if self._abilities["rtsp"]["ver"]:
            output_types.append(OutputStreamTypes.RTSP)
        if self._abilities["rtmp"]["ver"]:
            output_types.append(OutputStreamTypes.RTMP)
        output_types.append(OutputStreamTypes.MJPEG)
        return {_type: _type.name for _type in output_types}

    async def async_step_channel(
        self, user_input: dict[str, any] = None, errors: dict = None
    ):
        """Channel Options"""

        channel_id: int = self.context.get("channel_id", 0)
        if user_input is not None and errors is None:
            self._update_channel_options(channel_id, user_input)
            self.context.pop("channel_id", None)
            return await self._setup_entry()

        if user_input is None:
            user_input = self._get_channel_options(channel_id)
        output_types = self._get_output_streams()
        live = self._abilities["abilityChn"][channel_id]["live"]["ver"]

        name = self._channels[channel_id] if self._channels is not None else "Stream"

        return self.async_show_form(
            step_id="channel",
            description_placeholders={"name": self._devinfo["name"], "channel": name},
            data_schema=vol.Schema(
                ReolinkBaseConfigFlow._channel_schema(live, output_types, user_input)
            ),
            errors=errors,
        )

    async def async_step_options(
        self, user_input: dict[str, any] = None, errors: dict = None
    ):
        """ "General Options"""

        if user_input is not None and errors is None:
            if self._channels is None:
                self._update_channel_options(0, user_input)
                return self.async_create_entry(title="", data=self._options)

            return await self._setup_entry()

        schema = {}
        if self._channels is None:
            if user_input is None:
                user_input = self._get_channel_options(0)
            output_types = self._get_output_streams()
            live = self._abilities["abilityChn"][0]["live"]["ver"]
            schema.update(
                ReolinkBaseConfigFlow._channel_schema(live, output_types, user_input)
            )

        return self.async_show_form(
            step_id="options",
            description_placeholders={"name": self._devinfo["name"]},
            data_schema=vol.Schema(schema),
            errors=errors,
        )
