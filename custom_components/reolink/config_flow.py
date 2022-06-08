"""Configuration flow"""
from __future__ import annotations

import logging
from typing import TypedDict, cast
from urllib.parse import urlparse
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_TIMEOUT,
    CONF_USERNAME,
    CONF_SCAN_INTERVAL,
)
import voluptuous as vol
from reolinkapi.const import DEFAULT_USERNAME, DEFAULT_PASSWORD, DEFAULT_TIMEOUT
from reolinkapi.const import StreamTypes as CameraStreamTypes
from reolinkapi.typings.abilities import Abilities
from reolinkapi.typings.abilities.channel import (
    LiveAbilityVers,
    EncodingTypeAbilityVers,
)
from reolinkapi.typings.system import DeviceInfo
from reolinkapi.typings.network import ChannelStatus
from reolinkapi.typings.discovery import Device as DiscoveredDevice
from reolinkapi.helpers.abilities.ability import NO_ABILITY
from reolinkapi.exceptions import ReolinkError
from reolinkapi import helpers as clientHelpers

from reolinkrestapi import Client as ReolinkClient
from reolinkrestapi.parts.connection import Encryption

from .const import (
    CONF_CHANNELS,
    CONF_MOTION_INTERVAL,
    CONF_PREFIX_CHANNEL,
    CONF_USE_HTTPS,
    DEFAULT_MOTION_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_PREFIX_CHANNEL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STREAM_TYPE,
    DEFAULT_USE_HTTPS,
    DOMAIN,
    OutputStreamTypes,
)
from .typings import component

_LOGGER = logging.getLogger(__name__)

OUTPUT_STREAM_TYPES = {e: e.name for e in OutputStreamTypes}


def _create_unique_id(
    *,
    uuid: str | None = None,
    device_type: str | None = None,
    serial: str | None = None,
    mac: str | None = None,
):
    return (
        f"reolink-uid-{uuid}"
        if uuid is not None
        else f"reolink-device-{device_type}-{serial}"
        if device_type is not None and serial is not None
        else f"reolink-mac-{mac}"
        if mac is not None
        else None
    )


class _DeviceData(TypedDict, total=False):
    unique_id: str
    channels: dict[int, str]
    connection_id: int
    authentication_id: int
    abilities: Abilities
    devinfo: DeviceInfo


def _simple_channels(channels: list[ChannelStatus]):
    return (
        {channel["channel"]: channel["name"] for channel in channels}
        if channels is not None
        else None
    )


async def _update_from_device(config_data: dict[str, any], **kwargs):
    try:
        client = ReolinkClient()
        hostname = config_data.get(CONF_HOST, "")
        if hostname == "":
            return
        port = config_data.get(CONF_PORT, DEFAULT_PORT)
        _timeout = config_data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
        encryption = Encryption.NONE
        if config_data.get(CONF_USE_HTTPS, DEFAULT_USE_HTTPS):
            encryption = Encryption.HTTPS
        # elif self._data.get(CONF_USE_AES, DEFAULT_USE_AES):
        #    encryption = Encryption.AES
        await client.connect(
            hostname,
            port,
            _timeout,
            encryption=encryption,
        )

        username = config_data.get(CONF_USERNAME, DEFAULT_USERNAME)
        password = config_data.get(CONF_PASSWORD, DEFAULT_PASSWORD)
        if not await client.login(username, password):
            return False

        if CONF_USERNAME not in config_data:
            config_data[CONF_USERNAME] = username
            config_data[CONF_PASSWORD] = password

        prior_data: _DeviceData = kwargs
        commands = []

        if prior_data.get("authentication_id", 0) != client.authentication_id:
            abilities = await client.get_ability()
        else:
            abilities = prior_data.get("abilities", None)
            commands.append(clientHelpers.system.create_get_ability())

        if abilities is None:
            return

        if abilities["p2p"]["ver"]:
            commands.append(clientHelpers.network.create_get_p2p())

        if abilities["localLink"]["ver"]:
            commands.append(clientHelpers.network.create_get_local_link())

        if abilities["devInfo"]["ver"]:
            commands.append(clientHelpers.system.create_get_device_info())
        devinfo: DeviceInfo = prior_data.get("devinfo", None)
        if devinfo is not None and devinfo["channelNum"] > 1:
            commands.append(clientHelpers.network.create_get_channel_status())

        responses = await client.batch(commands)
        abilities = next(
            clientHelpers.system.get_ability_responses(responses), abilities
        )

        if abilities is None:
            return
        p2p = next(clientHelpers.network.get_p2p_responses(responses), None)
        link = next(clientHelpers.network.get_local_link_responses(responses), None)
        devinfo = next(clientHelpers.system.get_devinfo_responses(responses))
        channels = next(
            clientHelpers.network.get_channel_status_responses(responses), None
        )
        if devinfo is not None and devinfo["channelNum"] > 1 and channels is None:
            channels = await client.get_channel_status()
        elif channels is not None:
            channels = channels["status"]

        return _DeviceData(
            unique_id=_create_unique_id(
                uuid=p2p["uid"] if p2p is not None else None,
                device_type=devinfo["type"] if devinfo is not None else None,
                serial=devinfo["serial"] if devinfo is not None else None,
                mac=link["mac"] if link is not None else None,
            ),
            channels=_simple_channels(channels),
            connection_id=client.connection_id,
            authentication_id=client.authentication_id,
            abilities=abilities,
            devinfo=devinfo,
        )

    finally:
        await client.disconnect()


def _normalize_host(user_input: dict):
    hostname: str = user_input.get(CONF_HOST)
    port: int = user_input.get(CONF_PORT)
    parsed = urlparse(hostname)
    if parsed.scheme != "":
        scheme = parsed.scheme
        hostname = parsed.hostname
        port = parsed.port
        if parsed.username:
            user_input[CONF_USERNAME] = parsed.username
        if parsed.password:
            user_input[CONF_PASSWORD] = parsed.password
        if scheme == "https" or port == 443:
            user_input[CONF_USE_HTTPS] = True
        elif scheme == "http" or port == 80:
            user_input[CONF_USE_HTTPS] = False
    if user_input.get(CONF_USE_HTTPS):
        if port == 443:
            port = None
    elif port == 80:
        port = None
    user_input[CONF_HOST] = hostname
    user_input[CONF_PORT] = port


def _connect_schema(prior_input: dict) -> dict:
    return {
        vol.Required(CONF_HOST, default=prior_input.get(CONF_HOST)): str,
        vol.Optional(
            CONF_PORT,
            description={"suggested_value": prior_input.get(CONF_PORT, DEFAULT_PORT)},
        ): cv.port,
        vol.Required(
            CONF_USE_HTTPS,
            default=prior_input.get(CONF_USE_HTTPS, DEFAULT_USE_HTTPS),
        ): bool,
    }


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


def _options_schema(prior_input: dict):
    return {
        vol.Required(
            CONF_SCAN_INTERVAL,
            default=prior_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        ): cv.positive_int,
        vol.Required(
            CONF_MOTION_INTERVAL,
            default=prior_input.get(CONF_MOTION_INTERVAL, DEFAULT_MOTION_INTERVAL),
        ): cv.positive_int,
    }


def _channel_schema(
    live: LiveAbilityVers,
    main: EncodingTypeAbilityVers,
    supported_output_types: dict[OutputStreamTypes, str],
    prior_input: dict,
) -> dict:
    def _create_schema(stream: CameraStreamTypes):
        _key = f"{stream.name.lower()}_type"
        out_types = supported_output_types
        def_type = DEFAULT_STREAM_TYPE[stream]
        if stream == CameraStreamTypes.MAIN and main == EncodingTypeAbilityVers.H265:
            out_types = supported_output_types.copy()
            out_types.pop(OutputStreamTypes.RTMP, None)
        if def_type not in out_types:
            def_type = next(iter(out_types.keys()))
        return (
            vol.Required(_key, default=prior_input.get(_key, def_type)),
            vol.In(out_types),
        )

    schema = []

    if live in (LiveAbilityVers.MAIN_SUB, LiveAbilityVers.MAIN_EXTERN_SUB):
        schema.append(_create_schema(CameraStreamTypes.MAIN))
        schema.append(_create_schema(CameraStreamTypes.SUB))
    if live == LiveAbilityVers.MAIN_EXTERN_SUB:
        schema.append(_create_schema(CameraStreamTypes.EXT))

    return dict(schema)


class ReolinkConfigFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Reolink configuration flow"""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._data = {}
        self._device: _DeviceData = {}

    async def async_step_discovery(
        self, discovery_info: config_entries.DiscoveryInfoType
    ):
        device: DiscoveredDevice = discovery_info
        placeholders: dict = self.context.setdefault("title_placeholders", {})
        placeholders["name"] = device["name"]
        existing = await self.async_set_unique_id(
            _create_unique_id(uuid=device["uuid"], mac=device["mac"])
        )
        if existing is not None:
            return self.async_abort(reason="already_configured")

        user_data = {CONF_HOST: device["ip"]}

        return await self._update_data(user_data)

    async def async_step_user(self, user_input: dict[str, any] | None = None):
        return await self.async_step_connect(user_input)

    async def _update_data(self, config_data: dict[str, any]):
        if config_data is not None:
            self._data.update(config_data)

        try:
            data = await _update_from_device(self._data, **self._device)
        except ReolinkError:
            return await self.async_step_connect(self._data, {"base": "cannot_connect"})
        if data is False:
            return await self.async_step_login(self._data, {"base": "invalid_auth"})
        if data is None or data.get("abilities", None) is None:
            return await self.async_step_connect(self._data, {"base": "cannot_connect"})
        self._device.update(data)

        if data.get("channels", None) is not None and CONF_CHANNELS not in self._data:
            return await self.async_step_channels(
                self._data, {CONF_CHANNELS: "channel_required"}
            )

        if self.unique_id is None:
            entry = await self.async_set_unique_id(self._device.get("unique_id", None))
            if entry is not None:
                return self.async_abort(reason="already_configured")

        title = self._device["devinfo"]["name"]

        return self.async_create_entry(title=title, data=self._data)

    async def async_step_connect(
        self, user_input: dict[str, any] | None = None, errors: dict = None
    ):
        """Initial connection"""

        if user_input is not None and errors is None:
            hostname: str | None = user_input.get(CONF_HOST)
            if hostname is not None:
                _normalize_host(user_input)

                return await self._update_data(user_input)

        return self.async_show_form(
            step_id="connect",
            description_placeholders={CONF_PORT: "Default"},
            data_schema=vol.Schema(_connect_schema(user_input or self._data or {})),
            errors=errors,
        )

    async def async_step_reauth(self, user_input: dict[str, any] | None = None):
        """Handle configuration re-auth"""
        return await self.async_step_login(user_input)

    async def async_step_login(
        self, user_input: dict[str, any] = None, errors: dict = None
    ):
        """Login information"""

        if user_input is not None and errors is None:
            return await self._update_data(user_input)

        return self.async_show_form(
            step_id="login",
            data_schema=vol.Schema(_login_schema(user_input or {})),
            errors=errors,
        )

    async def async_step_channels(
        self, user_input: dict[str, any] = None, errors: dict = None
    ):
        """Channels Info"""

        if user_input is not None and errors is None:
            return await self._update_data(user_input)

        return self.async_show_form(
            step_id="channels",
            description_placeholders={
                "name": self._device["devinfo"]["name"],
            },
            data_schema=vol.Schema(
                _channels_schema(user_input or {}, self._device["channels"])
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return ReolinkOptionsFlowHandler(config_entry)


CONF_MENU_CHOICE = "menu_choice"


class ReolinkOptionsFlowHandler(config_entries.OptionsFlow):
    """Reolink options flow"""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        super().__init__()
        self._entry = config_entry
        self._data = {}
        self._device: _DeviceData = {}

    async def async_step_init(self, user_input: dict[str, any] = None):
        """init"""

        if user_input is not None:
            choice = user_input.get(CONF_MENU_CHOICE, "done")
            if choice == "done":
                return self.async_create_entry(title="", data=self._data)
            if choice == "options":
                return await self.async_step_options(self._data, {})
            if choice == "channels":
                return await self.async_step_channels(self._data, {})
            if choice[0:8] == "channel_":
                self.context["channel_id"] = int(choice[8:])
                return await self.async_step_channel(self._data, {})

        if self._device.get("connection_id", 0) == 0:
            domain_data = cast(component.HassDomainData, self.hass.data)[DOMAIN]
            entry_data = domain_data[self._entry.entry_id]
            client = entry_data["client"]
            device_data = entry_data["coordinator"].data
            self._device["connection_id"] = client.connection_id
            self._device["authentication_id"] = client.authentication_id
            self._device["abilities"] = device_data.abilities
            self._device["devinfo"] = device_data.device_info
            self._device["unique_id"] = self._entry.entry_id
            self._device["channels"] = _simple_channels(device_data.channels)

        choices = [("options", "General Settings")]

        if self._device.get("channels", None) is not None:
            choices.extend(
                (
                    (f"channel_{key}", f"Configure ({name})")
                    for key, name in self._device["channels"].items()
                )
            )
        else:
            choices.append(("channel_0", "Configure Streams"))

        choices.append(("done", "Save"))
        choices = {_k: _v for _k, _v in choices}

        return self.async_show_form(
            step_id="init",
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
            return await self.async_step_init()

        return self.async_show_form(
            step_id="channels",
            description_placeholders={
                "name": self._device["devinfo"]["name"],
            },
            data_schema=vol.Schema(
                _channels_schema(user_input or {}, self._device["channels"])
            ),
            errors=errors,
        )

    def _update_channel_options(self, channel_id: int, user_input: dict[str, any]):
        def _key_pair(stream: CameraStreamTypes):
            user_key = f"{stream.name.lower()}_type"
            return (user_key, f"channel_{channel_id}_{user_key}")

        def _update_type(stream: CameraStreamTypes):
            keys = _key_pair(stream)
            self._data[keys[1]] = user_input.get(keys[0], DEFAULT_STREAM_TYPE[stream])

        _update_type(CameraStreamTypes.MAIN)
        _update_type(CameraStreamTypes.SUB)
        _update_type(CameraStreamTypes.EXT)

    def _get_channel_options(self, channel_id: int, user_input: dict):
        def _key_pair(stream: CameraStreamTypes):
            user_key = f"{stream.name.lower()}_type"
            return (user_key, f"channel_{channel_id}_{user_key}")

        channel_input = {}

        def _update_option(stream: CameraStreamTypes):
            keys = _key_pair(stream)
            if keys[1] in user_input:
                channel_input[keys[0]] = user_input[keys[1]]

        live = self._device["abilities"]["abilityChn"][channel_id]["live"]["ver"]
        if live in (LiveAbilityVers.MAIN_SUB, LiveAbilityVers.MAIN_EXTERN_SUB):
            _update_option(CameraStreamTypes.MAIN)
            _update_option(CameraStreamTypes.SUB)
        if live == LiveAbilityVers.MAIN_EXTERN_SUB:
            _update_option(CameraStreamTypes.EXT)

        return channel_input

    def _get_output_streams(self):
        output_types: list[OutputStreamTypes] = []
        if self._device["abilities"]["rtsp"]["ver"]:
            output_types.append(OutputStreamTypes.RTSP)
        if self._device["abilities"]["rtmp"]["ver"]:
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
            return await self.async_step_init()

        user_input = self._get_channel_options(channel_id, user_input or self._data)
        output_types = self._get_output_streams()
        live = self._device["abilities"]["abilityChn"][channel_id]["live"]["ver"]
        main = (
            self._device["abilities"]["abilityChn"][channel_id]
            .get("mainEncType", NO_ABILITY)
            .get("ver", EncodingTypeAbilityVers.H264)
        )

        name = (
            self._device["channels"][channel_id]
            if self._device["channels"] is not None
            else "Stream"
        )

        return self.async_show_form(
            step_id="channel",
            description_placeholders={
                "name": self._device["devinfo"]["name"],
                "channel": name,
            },
            data_schema=vol.Schema(
                _channel_schema(live, main, output_types, user_input)
            ),
            errors=errors,
        )

    async def async_step_options(
        self, user_input: dict[str, any] = None, errors: dict = None
    ):
        """ "General Options"""

        if user_input is not None and errors is None:
            self._data.update(user_input)
            return await self.async_step_init()

        return self.async_show_form(
            step_id="options",
            description_placeholders={"name": self._device["devinfo"]["name"]},
            data_schema=vol.Schema(_options_schema(user_input)),
            errors=errors,
        )
