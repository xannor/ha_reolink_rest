"""Configuration flow"""
from __future__ import annotations

import logging
import ssl
from typing import Mapping, TypeVar
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import DiscoveryInfoType

from homeassistant.const import (
    CONF_SCAN_INTERVAL,
    CONF_HOST,
    CONF_PORT,
    CONF_USERNAME,
    CONF_PASSWORD,
)

from async_reolink.api.const import DEFAULT_USERNAME, DEFAULT_PASSWORD

from async_reolink.api import errors as reo_errors
from async_reolink.api.network.typing import ChannelStatus
from async_reolink.rest.client import Client as RestClient
from async_reolink.rest.connection.typing import Encryption
from async_reolink.rest.errors import AUTH_ERRORCODES

from .typing import DomainData

from .entity import weak_ssl_context

from .const import (
    DEFAULT_HISPEED_INTERVAL,
    DEFAULT_PREFIX_CHANNEL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    CONF_USE_HTTPS,
    OPT_HISPEED_INTERVAL,
    OPT_PREFIX_CHANNEL,
    OPT_CHANNELS,
    OPT_DISCOVERY,
    OPT_WEAK_SSL,
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
            CONF_PORT,
            description={"suggested_value": defaults.get(CONF_PORT, None)},
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
            default=defaults.get(CONF_USERNAME, DEFAULT_USERNAME),
        ): str,
        passwd: str,
    }


def _simple_channels(channels: Mapping[int, ChannelStatus]):
    return (
        {i: channel.name for i, channel in channels.items()}
        if channels is not None
        else None
    )


def _channels_schema(channels: dict, **defaults: UserDataType):
    return {
        vol.Required(
            OPT_PREFIX_CHANNEL,
            default=defaults.get(OPT_PREFIX_CHANNEL, DEFAULT_PREFIX_CHANNEL),
        ): bool,
        vol.Required(
            OPT_CHANNELS, default=defaults.get(OPT_CHANNELS, set(channels.keys()))
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
        return f"uid_{uuid}"
    uid = "device"
    if mac is not None:
        uid = +f"_mac_{mac.replace(':', '')}"
    if device_type is not None and serial is not None:
        uid += f"_type_{device_type}_ser_{serial}"
    if len(uid) > 6:
        return uid
    return None


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ReoLink"""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self.data: UserDataType = None
        self.options: UserDataType = None

    async def async_step_user(
        self, user_input: dict[str, any] | None = None
    ) -> FlowResult:
        """Handle the intial step."""
        if user_input is None and self.init_data is None:
            return await self.async_step_connection()

        data = self.data
        if data is None:
            self.data = data = {}
        if (
            (CONF_HOST not in data)
            and self.options is not None
            and OPT_DISCOVERY in self.options
        ):
            data = self.data.copy()
            if CONF_HOST not in data and "ip" in self.options[OPT_DISCOVERY]:
                data[CONF_HOST] = self.options[OPT_DISCOVERY]["ip"]
        if not _validate_connection_data(data):
            return await self.async_step_connection(data)

        if self.options is not None and self.options.get(OPT_WEAK_SSL, False):
            weak_ssl = weak_ssl_context
        else:
            weak_ssl = None
        client = RestClient(ssl=weak_ssl)
        encryption = (
            Encryption.HTTPS if data.get(CONF_USE_HTTPS, False) else Encryption.NONE
        )
        try:
            await client.connect(
                data[CONF_HOST],
                data.get(CONF_PORT, None),
                encryption=encryption,
            )
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unhandled exception occurred")
            return await self.async_step_connection(data, {"base": "unknown"})

        connection_id = client.connection_id
        title = (self.init_data or {}).get("name", "Camera")
        try:
            if not await client.login(
                data.get(CONF_USERNAME, DEFAULT_USERNAME),
                data.get(CONF_PASSWORD, DEFAULT_PASSWORD),
            ):
                if (
                    data.get(CONF_USERNAME, DEFAULT_USERNAME) == DEFAULT_USERNAME
                    and data.get(CONF_PASSWORD, DEFAULT_PASSWORD) == DEFAULT_PASSWORD
                ):
                    data.pop(CONF_USERNAME, None)
                    data.pop(CONF_PASSWORD, None)
                errors = None
                if CONF_USERNAME in data:
                    errors = {"base": "invalid_auth"}
                return await self.async_step_auth(data, errors)

            # check to see if login redirected us and update the base_url
            if client.connection_id != connection_id:
                connection_id = client.connection_id
                _user_data = {CONF_HOST: client.base_url}
                if _validate_connection_data(_user_data):
                    _user_data = dict(
                        dslice(_user_data, CONF_HOST, CONF_PORT, CONF_USE_HTTPS)
                    )
                    data.update(_user_data)
                    _LOGGER.warning(
                        "Corrected camera(%s) port during setup, you can safely ignore previous warnings about redirecting.",
                        data[CONF_HOST],
                    )

            capabilities = await client.get_capabilities(
                data.get(CONF_USERNAME, DEFAULT_USERNAME)
            )

            if capabilities.device.info:
                devinfo = await client.get_device_info()
                title: str = devinfo.name or title
                if self.unique_id is None:
                    if capabilities.p2p:
                        p2p = await client.get_p2p()
                    if capabilities.local_link:
                        link = await client.get_local_link()
                    unique_id = _create_unique_id(
                        uuid=p2p.uid if p2p is not None else None,
                        device_type=devinfo.type if devinfo is not None else None,
                        serial=devinfo.serial if devinfo is not None else None,
                        mac=link.mac if link is not None else None,
                    )
                    if unique_id is not None:
                        await self.async_set_unique_id(unique_id)
                        self._abort_if_unique_id_configured()

                if devinfo.channels > 1:
                    channels = await client.get_channel_status()
                    if channels is not None:
                        self.context["channels"] = _simple_channels(channels)
                        if self.options is None or OPT_CHANNELS not in self.options:
                            return await self.async_step_channels(self.options, {})

        except reo_errors.ReolinkConnectionError:
            errors = {"base": "cannot_connect"}
            return await self.async_step_connection(data, errors)
        except reo_errors.ReolinkTimeoutError:
            errors = {"base": "timeout"}
            return await self.async_step_connection(data, errors)
        except reo_errors.ReolinkResponseError as resp_error:
            if resp_error.code in AUTH_ERRORCODES:
                errors = (
                    {"base": "invalid_auth"}
                    if data.get(CONF_USERNAME, DEFAULT_USERNAME) != DEFAULT_USERNAME
                    or data.get(CONF_PASSWORD, DEFAULT_PASSWORD) != DEFAULT_PASSWORD
                    else {"base": "auth_required"}
                )
                return await self.async_step_auth(data, errors)
            _LOGGER.exception(
                "An internal device error occurred on %s, configuration aborting",
                data[CONF_HOST],
            )
            return self.async_abort(reason="device_error")
        except ssl.SSLError as ssl_error:
            if (
                ssl_error.errno
                == 1
                # and ssl_error.reason == "SSLV3_ALERT_HANDSHAKE_FAILURE"
            ):
                _LOGGER.warning(
                    "Device %s only supports weak (deprecated) SSL, enabling weak support for this device. If SSL is not necessary, please consider disabling SSL on device, otherwise I STRONGLY advise upgrading the device firmware or replacing the device.",
                    data[CONF_HOST],
                )
                if self.options is None:
                    self.options = {}
                self.options[OPT_WEAK_SSL] = True
                return await self.async_step_user(user_input)
            _LOGGER.exception("SSL error occurred")
            return await self.async_step_connection(data, {"base": "unknown"})
        except Exception:  # pylint: disable=broad-except
            # we want to "cleanly" fail as possible
            _LOGGER.exception("Unhandled exception occurred")
            return await self.async_step_connection(data, {"base": "unknown"})
        finally:
            await client.disconnect()

        if (
            self.options is not None
            and OPT_DISCOVERY in self.options
            and "ip" in self.options[OPT_DISCOVERY]
            and data.get(CONF_HOST, None) == self.options[OPT_DISCOVERY]["ip"]
        ):
            # if we used discovery for host we wont keep in data so we fall back on discovery everytime
            data.pop(CONF_HOST, None)

        return self.async_create_entry(title=title, data=data, options=self.options)

    async def async_step_integration_discovery(
        self, discovery_info: DiscoveryInfoType
    ) -> FlowResult:
        device = discovery_info
        unique_id = _create_unique_id(
            uuid=device.get("uuid", None), mac=device.get("mac")
        )
        if unique_id is not None:
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()
        if "name" in device:
            self.context["title_placeholders"] = {"name": device["name"]}

        if self.options is None:
            self.options = {}
        self.options[OPT_DISCOVERY] = discovery_info

        await self._async_handle_discovery_without_unique_id()
        return self.async_show_progress_done(next_step_id="user")

    async def async_step_connection(
        self,
        user_input: UserDataType | None = None,
        errors: dict[str, str] | None = None,
    ) -> FlowResult:
        """Connection form"""

        if user_input is not None and errors is None:
            if _validate_connection_data(user_input):
                user_input = dict(
                    dslice(user_input, CONF_HOST, CONF_PORT, CONF_USE_HTTPS)
                )
                if self.data is not None:
                    self.data.update(user_input)
                else:
                    self.data = user_input
                return await self.async_step_user(user_input)

        schema = _connection_schema(**(user_input or {}))

        return self.async_show_form(
            step_id="connection",
            data_schema=vol.Schema(schema),
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
            user_input = dict(dslice(user_input, CONF_USERNAME, CONF_PASSWORD))
            self.data.update(user_input)
            return await self.async_step_user(user_input)

        schema = _auth_schema(errors is not None, **(user_input or self.data or {}))

        return self.async_show_form(
            step_id="auth",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={},
        )

    # async def async_step_reauth(
    #    self,
    #    user_input: UserDataType | None = None,
    #    errors: dict[str, str] | None = None,
    # ) -> FlowResult:
    #    """Re-authorize form"""

    async def async_step_channels(
        self,
        user_input: UserDataType | None = None,
        errors: dict[str, str] | None = None,
    ) -> FlowResult:
        """Channels form"""

        if user_input is not None and errors is None:
            if not self.options:
                self.options = {}
            self.options.update(user_input)
            return await self.async_step_user(user_input)

        schema = _channels_schema(
            self.context["channels"], **(user_input or self.options or {})
        )

        return self.async_show_form(
            step_id="channels",
            data_schema=vol.Schema(schema),
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
        self.data: UserDataType = config_entry.options.copy()

    async def async_step_init(
        self,
        user_input: UserDataType | None = None,
    ) -> FlowResult:
        """Options form"""

        menu = ["options"]

        domain: DomainData = self.hass.data[DOMAIN]
        entry_data = domain[self.config_entry.entry_id]
        if len(entry_data.coordinator.data.capabilities.channels) > 1:
            menu.append("channels")

        if len(menu) == 1:
            method = f"async_step_{menu[0]}"
            return await getattr(self, method)(user_input)

        return self.async_show_menu(step_id=self.cur_step, menu_options=menu)

    async def async_step_options(
        self,
        user_input: UserDataType | None = None,
        errors: dict[str, str] | None = None,
    ) -> FlowResult:
        """Options"""

        if user_input is None:
            user_input = self.data

        schema = {
            vol.Optional(
                CONF_SCAN_INTERVAL,
                description={
                    "suggested_value": user_input.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    )
                },
            ): int,
            vol.Optional(
                OPT_HISPEED_INTERVAL,
                description={
                    "suggested_value": user_input.get(
                        OPT_HISPEED_INTERVAL, DEFAULT_HISPEED_INTERVAL
                    )
                },
            ): int,
        }

        if self.config_entry.data.get(CONF_USE_HTTPS, False):
            schema[
                vol.Required(
                    OPT_WEAK_SSL,
                    description={
                        "suggested_value": user_input.get(OPT_WEAK_SSL, False)
                    },
                )
            ] = bool

        return self.async_show_form(
            step_id="options",
            data_schema=vol.Schema(schema),
        )

    async def async_step_channels(
        self,
        user_input: UserDataType | None = None,
        errors: dict[str, str] | None = None,
    ) -> FlowResult:
        """Channels"""

        if user_input is None:
            user_input = self.data

        domain: DomainData = self.hass.data[DOMAIN]
        entry_data = domain[self.config_entry.entry_id]

        schema = _channels_schema(
            _simple_channels(entry_data.channel_statuses), **user_input
        )

        return self.async_show_form(
            step_id="channels",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={},
        )

    async def async_step_commit(self) -> FlowResult:
        """Save Changes"""

        return self.async_create_entry(title="", data=self.data)
