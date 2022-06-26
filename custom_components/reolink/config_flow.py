"""Configuration flow"""
from __future__ import annotations
import logging
from types import MappingProxyType
from typing import TypeVar, cast
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import DiscoveryInfoType

from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
    CONF_USERNAME,
    CONF_PASSWORD,
)

from reolinkapi.const import DEFAULT_USERNAME, DEFAULT_PASSWORD

from reolinkrestapi import Client as RestClient
from reolinkrestapi.parts.connection import Encryption
from reolinkapi.typings.discovery import Device as DeviceType
from reolinkapi.typings.network import ChannelStatus
from reolinkapi import errors as reo_errors

from .settings import Settings, async_get_settings, async_set_setting

from . import discovery

from .const import (
    DEFAULT_PREFIX_CHANNEL,
    DOMAIN,
    CONF_USE_HTTPS,
    CONF_PREFIX_CHANNEL,
    CONF_CHANNELS,
    SETTING_DISCOVERY,
    SETTING_DISCOVERY_STARTUP,
)

_LOGGER = logging.getLogger(__name__)

UserDataType = dict[str, any]

_K = TypeVar("_K")
_V = TypeVar("_V")


def dslice(obj: dict[_K, _V], *keys: _K):
    """slice dictionary"""
    return ((k, obj[k]) for k in keys if k in obj)


def _connection_schema(**defaults: UserDataType):
    return {
        vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, vol.UNDEFINED)): str,
        vol.Optional(
            CONF_PORT, default=defaults.get(CONF_HOST, vol.UNDEFINED)
        ): cv.port,
        vol.Optional(
            CONF_USE_HTTPS, default=defaults.get(CONF_USE_HTTPS, vol.UNDEFINED)
        ): bool,
    }


def _validate_connection_data(data: UserDataType):
    host = data.get(CONF_HOST, None)
    if host is None:
        return False
    port = data.get(CONF_PORT, None)
    https = data.get(CONF_USE_HTTPS, None)
    uri = urlparse(str(host) or "")
    if uri.scheme != "":
        if uri.scheme != "http" and uri.scheme != "https":
            return False
        host = uri.hostname
        https = uri.scheme == "https"
        if https and (uri.port == 443 or port == 443):
            port = None
        elif not https and (uri.port == 80 or port == 80):
            port = None
        elif uri.port is not None:
            port = uri.port
    else:
        host = str(host)
        if port is not None:
            port = int(port)
        if https is not None:
            https = bool(https)

    data[CONF_HOST] = host
    if port is not None:
        data[CONF_PORT] = port
    else:
        data.pop(CONF_PORT, None)
    if https:
        data[CONF_USE_HTTPS] = https
    else:
        data.pop(CONF_USE_HTTPS, None)
    return True


def _auth_schema(require_password: bool = False, **defaults: UserDataType):
    if require_password:
        passwd = vol.Required(CONF_PASSWORD)
    else:
        passwd = vol.Optional(CONF_PASSWORD)

    return {
        vol.Required(
            CONF_USERNAME,
            description={
                "suggested_value": defaults.get(CONF_USERNAME, DEFAULT_USERNAME)
            },
        ): str,
        passwd: str,
    }


def _simple_channels(channels: list[ChannelStatus]):
    return (
        {channel["channel"]: channel["name"] for channel in channels}
        if channels is not None
        else None
    )


def _channels_schema(channels: dict, **defaults: UserDataType):
    return {
        vol.Required(
            CONF_PREFIX_CHANNEL,
            default=defaults.get(CONF_PREFIX_CHANNEL, DEFAULT_PREFIX_CHANNEL),
        ): bool,
        vol.Required(
            CONF_CHANNELS, default=defaults.get(CONF_CHANNELS, set(channels.keys()))
        ): cv.multi_select(channels),
    }


def _create_unique_id(
    *,
    uuid: str | None = None,
    device_type: str | None = None,
    serial: str | None = None,
    mac: str | None = None,
):
    if uuid is not None:
        return f"reolink-uid-{uuid}"
    if device_type is not None and serial is not None:
        return f"reolink-device-{device_type}-{serial}"
    if mac is not None:
        return f"reolink-mac-{mac}"
    return None


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ReoLink"""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self.data: UserDataType = None
        self.options: UserDataType = None
        self._in_discovery = False

    async def async_step_user(
        self, user_input: dict[str, any] | None = None
    ) -> FlowResult:
        """Handle the intial step."""

        if self.data is None and self.init_data is None:
            settings: Settings = await async_get_settings(self.hass)
            dsettings: MappingProxyType = settings.get(SETTING_DISCOVERY, {})
            if dsettings.get(SETTING_DISCOVERY_STARTUP, True):
                menu_options = ["connection"]
                if discovery.async_discovery_active(self.hass):
                    menu_options.insert(0, "stop_discovery")
                else:
                    menu_options.insert(0, "discovery")
                return self.async_show_menu(
                    step_id="user",
                    menu_options=menu_options,
                    description_placeholders={},
                )
            return self.async_step_connection()

        if self._in_discovery:
            self._in_discovery = False
            return self.async_show_progress_done(next_step_id="user")

        if self.data is None or not _validate_connection_data(self.data):
            return await self.async_step_connection(self.data)

        client = RestClient()
        encryption = (
            Encryption.HTTPS
            if self.data.get(CONF_USE_HTTPS, False)
            else Encryption.NONE
        )
        try:
            await client.connect(
                self.data[CONF_HOST],
                self.data.get(CONF_PORT, None),
                encryption=encryption,
            )
        except Exception:
            return await self.async_step_connection(
                self.data, {"base": "unknown exception"}
            )

        try:
            if not client.login(
                self.data.get(CONF_USERNAME, DEFAULT_USERNAME),
                self.data.get(CONF_PASSWORD, DEFAULT_PASSWORD),
            ):
                errors = None
                if CONF_USERNAME in self.data:
                    errors = {"base": "invalid-auth"}
                return await self.async_step_auth(self.data, errors)

            abilities = await client.get_ability(
                self.data.get(CONF_USERNAME, DEFAULT_USERNAME)
            )
            if abilities["devInfo"]["ver"]:
                devinfo = await client.get_device_info()
                if self.unique_id is None:
                    if abilities["p2p"]["ver"]:
                        p2p = await client.get_p2p()
                    if abilities["localLink"]["ver"]:
                        link = await client.get_local_link()
                    unique_id = _create_unique_id(
                        uuid=p2p["uid"] if p2p is not None else None,
                        device_type=devinfo["type"] if devinfo is not None else None,
                        serial=devinfo["serial"] if devinfo is not None else None,
                        mac=link["mac"] if link is not None else None,
                    )
                    if unique_id is not None:
                        self.async_set_unique_id(unique_id)
                        self._abort_if_unique_id_configured()

                if devinfo["channelNum"] > 1:
                    channels = await client.get_channel_status()
                    if channels is not None:
                        self.context["channels"] = _simple_channels(channels)
                        if (
                            self.options is None
                            or not CONF_CHANNELS in self.options.keys()
                        ):
                            return await self.async_step_channels(self.options)

        except reo_errors.ReolinkConnectionError:
            errors = {"base": "cannot_connect"}
            return await self.async_step_connection(self.data, errors)
        except reo_errors.ReolinkTimeoutError:
            errors = {"base": "timeout"}
            return await self.async_step_connection(self.data, errors)
        except reo_errors.ReolinkResponseError:
            errors = {"base": "invalid_auth"}
            return await self.async_step_auth(self.data, errors)
        except Exception:
            return await self.async_step_connection(self.data, {"base": "unknown"})
        finally:
            await client.disconnect()

        return self.async_create_entry(
            title=self.context["name"], data=self.data, options=self.options
        )

    async def async_step_integration_discovery(
        self, discovery_info: DiscoveryInfoType
    ) -> FlowResult:
        device = cast(DeviceType, discovery_info)
        if "ip" in device:
            self.data = {CONF_HOST: device["ip"], CONF_USERNAME: DEFAULT_USERNAME}
        unique_id = _create_unique_id(
            uuid=device.get("uuid", None), mac=device.get("mac")
        )
        if unique_id is not None:
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()
        if "name" in device:
            self.context["title_placeholders"] = {"name": device["name"]}
        return await super().async_step_integration_discovery(discovery_info)

    async def async_step_stop_discovery(
        self,
        *_,
    ):
        """Stop Discovery"""
        discovery.async_stop_discovery(self.hass)
        return await self.async_step_user()

    async def async_step_discovery(
        self, discovery_info: DiscoveryInfoType
    ) -> FlowResult:
        if discovery_info is None:
            settings: Settings = await async_get_settings(self.hass)
            dsettings: MappingProxyType = settings.get(SETTING_DISCOVERY, {})
            if dsettings.get(SETTING_DISCOVERY_STARTUP, True):
                if not SETTING_DISCOVERY in settings:
                    await async_set_setting(
                        settings, SETTING_DISCOVERY, {SETTING_DISCOVERY_STARTUP: True}
                    )
                elif not SETTING_DISCOVERY_STARTUP in dsettings:
                    await async_set_setting(dsettings, SETTING_DISCOVERY_STARTUP, True)
                discovery.async_start_discovery(self.hass)
                return self.async_abort(reason="discovery_started")
        self._in_discovery = True
        return await super().async_step_discovery(discovery_info)

    async def async_step_connection(
        self,
        user_input: UserDataType | None = None,
        errors: dict[str, str] | None = None,
    ) -> FlowResult:
        """Connection form"""

        if user_input is not None and errors is None:
            if _validate_connection_data(user_input):
                user_input = {dslice(user_input, CONF_HOST, CONF_PORT, CONF_USE_HTTPS)}
                if self.data is not None:
                    self.data.update(user_input)
                else:
                    self.data = user_input
                return await self.async_step_user()

        schema = vol.Schema(_connection_schema(**(user_input or {})))

        return self.async_show_form(
            step_id="connection",
            data_schema=schema,
            errors=errors,
            description_placeholders={},
        )

    async def async_step_auth(
        self,
        user_input: UserDataType | None = None,
        errors: dict[str, str] | None = None,
    ) -> FlowResult:
        """Authentication form"""

        if user_input is not None and errors is None:
            if _validate_connection_data(user_input):
                user_input = {dslice(user_input, CONF_USERNAME, CONF_PASSWORD)}
                self.data.update(user_input)
                return await self.async_step_user()

        schema = _auth_schema(errors is not None, **(user_input or self.data))

        return self.async_show_form(
            step_id="auth",
            data_schema=schema,
            errors=errors,
            description_placeholders={},
        )

    async def async_step_channels(
        self,
        user_input: UserDataType | None = None,
        errors: dict[str, str] | None = None,
    ) -> FlowResult:
        """Channels form"""

        if user_input is not None and errors is None:
            return await self.async_step_user()

        schema = _channels_schema(
            self.context["channels"], **(user_input or self.options or {})
        )

        return self.async_show_form(
            step_id="channels",
            data_schema=schema,
            errors=errors,
            description_placeholders={},
        )

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> OptionsFlow:
        return OptionsFlow(config_entry)


class OptionsFlow(config_entries.OptionsFlow):
    """Handle an Options Flow for reolink"""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        super().__init__()
        self.config_entry = config_entry
        self.data: UserDataType = config_entry.data.copy()
        self.options: UserDataType = config_entry.options.copy()

    async def async_step_init(
        self,
        user_input: UserDataType | None = None,
    ) -> FlowResult:
        """Options form"""

        return self.async_show_menu(step_id="init", menu_options=["channels"])

    async def async_step_commit(self) -> FlowResult:
        """Save Changes"""

        return self.async_create_entry(title="", data=self.options)
