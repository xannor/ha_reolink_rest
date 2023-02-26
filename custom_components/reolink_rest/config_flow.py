"""Configuration flow"""
from __future__ import annotations

import logging
import ssl
from typing import TYPE_CHECKING, Final, Mapping, TypeVar
from urllib.parse import urlparse
import aiohttp

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.typing import DiscoveryInfoType
from homeassistant.data_entry_flow import AbortFlow


if TYPE_CHECKING:
    from homeassistant.helpers import device_registry as helper_device_registry

from ._utilities.hass_typing import hass_bound

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

from .typing import DiscoveredDevice, DomainDataType

from .api import ReolinkDeviceApi

from .const import (
    DATA_API,
    DEFAULT_HISPEED_INTERVAL,
    DEFAULT_PREFIX_CHANNEL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    CONF_USE_HTTPS,
    OPT_HISPEED_INTERVAL,
    OPT_PREFIX_CHANNEL,
    OPT_CHANNELS,
    OPT_SSL,
    SSLMode,
)

from ._utilities.dict import slice_keys

_LOGGER = logging.getLogger(__name__)

UserDataType = dict[str, any]


def _connection_schema(**defaults: UserDataType):
    return {
        vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, vol.UNDEFINED)): str,
        vol.Optional(
            CONF_PORT,
            description={"suggested_value": defaults.get(CONF_PORT, None)},
        ): cv.port,
        vol.Optional(CONF_USE_HTTPS, default=defaults.get(CONF_USE_HTTPS, vol.UNDEFINED)): bool,
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
    if channels is None:
        return None

    return {str(i): channel.name for i, channel in channels.items()}


def _channels_schema(__channels: dict, **defaults: UserDataType):
    channels = defaults.get(OPT_CHANNELS, None)
    if channels is not None:
        channels = tuple(str(i) for i in channels)
    else:
        channels = tuple()
    return {
        vol.Required(
            OPT_PREFIX_CHANNEL,
            default=defaults.get(OPT_PREFIX_CHANNEL, DEFAULT_PREFIX_CHANNEL),
        ): bool,
        vol.Optional(OPT_CHANNELS, default=channels): cv.multi_select(__channels),
    }


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ReoLink"""

    VERSION = 2

    def __init__(self) -> None:
        super().__init__()
        self.data: UserDataType = None
        self.options: UserDataType = None

    async def async_step_user(self, user_input: dict[str, any] | None = None) -> FlowResult:
        """Handle the intial step."""
        if user_input is None and self.init_data is None:
            return await self.async_step_connection()
        if self.source in config_entries.DISCOVERY_SOURCES and self.cur_step is None:
            return self.async_show_progress_done(next_step_id="user")
        if user_input:
            if not self.data:
                self.data = user_input
            else:
                self.data.update(user_input)

        data = self.data or {}
        if not _validate_connection_data(data):
            return await self.async_step_connection(data)

        api = ReolinkDeviceApi()
        try:
            options = self.options or {}
            await api.async_ensure_connection(**data | options)
            client = api.client
        except reo_errors.ReolinkConnectionError:
            errors = {"base": "cannot_connect"}
            return await self.async_step_connection(data, errors)
        except reo_errors.ReolinkTimeoutError:
            errors = {"base": "timeout"}
            return await self.async_step_connection(data, errors)
        except ConfigEntryNotReady:
            errors = {"base": "cannot_connect"}
            return await self.async_step_connection(data, errors)
        except ConfigEntryAuthFailed:
            errors = (
                {"base": "invalid_auth"}
                if data.get(CONF_USERNAME, DEFAULT_USERNAME) != DEFAULT_USERNAME
                or data.get(CONF_PASSWORD, DEFAULT_PASSWORD) != DEFAULT_PASSWORD
                else {"base": "auth_required"}
            )
            return await self.async_step_auth(data, errors)
        except aiohttp.ClientResponseError as http_error:
            if http_error.status in (301, 302, 308) and "location" in http_error.headers:
                location = http_error.headers["location"]
                client = api.client
                if not client.secured and location.startswith("https://"):
                    self.data[CONF_USE_HTTPS] = True
                    return await self.async_step_user()
                elif client.secured and location.startswith("http://"):
                    del self.data[CONF_USE_HTTPS]
                    return await self.async_step_user()

            return self.async_abort(reason="response_error")
        except ssl.SSLError as ssl_error:
            if (
                ssl_error.errno
                == 1
                # and ssl_error.reason == "SSLV3_ALERT_HANDSHAKE_FAILURE"
            ):
                _LOGGER.warning(
                    "Device %s certificate only supports a weak (deprecated) key, enabling INSECURE SSL support for this device. If SSL is not necessary, please consider disabling SSL on device, or manually installing a strong certificate.",
                    data[CONF_HOST],
                )
                if self.options is None:
                    self.options = {}
                self.options[OPT_SSL] = SSLMode.INSECURE
                return await self.async_step_user()
            _LOGGER.exception("SSL error occurred")
            return await self.async_step_connection(data, {"base": "unknown"})
        except Exception:
            _LOGGER.exception("Unhandled exception occurred")
            return await self.async_step_connection(data, {"base": "unknown"})

        title: str = self.context.get("title_placeholders", {}).get("name", "Camera")

        # check to see if login redirected us and update the base_url

        try:
            capabilities = await client.get_capabilities(data.get(CONF_USERNAME, DEFAULT_USERNAME))

            if capabilities.device.info:
                devinfo = await client.get_device_info()
                title: str = devinfo.name or title
                if self.unique_id is None:
                    if capabilities.p2p:
                        p2p = await client.get_p2p()
                        if p2p.uid:
                            await self.async_set_unique_id(p2p.uid.upper())
                    if self.unique_id is None and capabilities.local_link:
                        link = await client.get_local_link()
                        if link.mac:
                            device_registry: helper_device_registry = (
                                self.hass.helpers.device_registry
                            )
                            await self.async_set_unique_id(device_registry.format_mac(link.mac))
                    self._abort_if_unique_id_configured(updates=data)

                if devinfo.channels > 1:
                    channels = await client.get_channel_status()
                    if channels is not None:
                        self.context["channels"] = _simple_channels(channels)
                        if self.options is None or OPT_CHANNELS not in self.options:
                            return await self.async_step_channels(self.options, {})
        except reo_errors.ReolinkTimeoutError:
            errors = {"base": "timeout"}
            return await self.async_step_connection(data, errors)
        except reo_errors.ReolinkResponseError as resp_error:
            _LOGGER.exception(
                "An internal device error occurred on %s, configuration aborting",
                data[CONF_HOST],
            )
            return self.async_abort(reason="device_error")
        except Exception:  # pylint: disable=broad-except
            # we want to "cleanly" fail as possible
            _LOGGER.exception("Unhandled exception occurred")
            return await self.async_step_connection(data, {"base": "unknown"})
        finally:
            await client.disconnect()

        return self.async_create_entry(title=title, data=data, options=self.options)

    async def _async_handle_discovery(self, discovery_info: DiscoveredDevice.JSON):

        device_registry: helper_device_registry = self.hass.helpers.device_registry
        entry = None
        if uuid := discovery_info.get(DiscoveredDevice.Keys.uuid):
            entry = await self.async_set_unique_id(uuid.upper())
        elif mac := discovery_info.get(DiscoveredDevice.Keys.mac):
            entry = await self.async_set_unique_id(device_registry.format_mac(mac))
            if not entry:
                # TODO : track all devices NiC macs in device registry so we can handle it
                devices = device_registry.async_get(self.hass)
                device = devices.async_get_device(
                    set(), {(device_registry.CONNECTION_NETWORK_MAC, mac)}
                )
                if device:
                    for id in device.config_entries:
                        if (
                            e := self.hass.config_entries.async_get_entry(id)
                        ) and e.domain == self.handler:
                            if entry:
                                entry = None
                                break
                            else:
                                entry = e
                    if entry and entry.unique_id:
                        await self.async_set_unique_id(entry.unique_id)
        else:
            raise AbortFlow("not_implemented")

        if ip := discovery_info.get(DiscoveredDevice.Keys.ip):
            self.data = {CONF_HOST: ip}
        self._abort_if_unique_id_configured(updates=self.data)

        if name := discovery_info.get(DiscoveredDevice.Keys.name):
            self.context["title_placeholders"] = {"name": name}

    async def async_step_discovery(self, discovery_info: DiscoveryInfoType):
        await self._async_handle_discovery(discovery_info)
        return await super().async_step_discovery(discovery_info)

    async def async_step_integration_discovery(self, discovery_info: DiscoveryInfoType):
        await self._async_handle_discovery(discovery_info)
        return await super().async_step_integration_discovery(discovery_info)

    async def async_step_connection(
        self,
        user_input: UserDataType | None = None,
        errors: dict[str, str] | None = None,
    ) -> FlowResult:
        """Connection form"""

        if user_input is not None and errors is None:
            if _validate_connection_data(user_input):
                user_input = dict(slice_keys(user_input, CONF_HOST, CONF_PORT, CONF_USE_HTTPS))
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
            user_input = dict(slice_keys(user_input, CONF_USERNAME, CONF_PASSWORD))
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
            channels = user_input.get("channels", None)
            if channels is not None:
                if len(channels) == 0:
                    channels = None
                else:
                    channels = tuple(int(i) for i in channels)

            self.options.update(channels=channels)
            return await self.async_step_user(user_input)

        schema = _channels_schema(self.context["channels"], **(user_input or self.options or {}))

        return self.async_show_form(
            step_id="channels",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={},
        )

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> OptionsFlow:
        return OptionsFlow(config_entry)


class OptionsFlow(config_entries.OptionsFlowWithConfigEntry):
    """Handle an Options Flow for reolink"""

    _MENU: Final = tuple("options")

    async def async_step_init(
        self,
        user_input: UserDataType | None = None,
    ) -> FlowResult:
        """Options form"""

        menu = list(self._MENU)

        domain_data: DomainDataType = self.hass.data[DOMAIN]
        entry_data = domain_data[self.config_entry.entry_id]
        api = entry_data[DATA_API]
        if api.data.capabilities is not None and len(api.data.capabilities.channels) > 1:
            menu.append("channels")

        if len(menu) == 1:
            method = f"async_step_{menu[0]}"
            return await getattr(self, method)(user_input)

        menu.append("commit")
        self.context["menu"] = menu

        return await self.async_step_menu()

    async def async_step_menu(
        self,
        user_input: UserDataType | None = None,
    ):
        """Menu"""

        menu = self.context.get("menu", None)
        if menu is None:
            return await self.async_step_commit()

        return self.async_show_menu(step_id="menu", menu_options=menu)

    async def async_step_options(
        self,
        user_input: UserDataType | None = None,
        errors: dict[str, str] | None = None,
    ) -> FlowResult:
        """Options"""

        if user_input is not None and errors is None:
            self.data.update(**user_input)
            return await self.async_step_menu()

        if user_input is None:
            user_input = self.data

        schema = {
            vol.Optional(
                CONF_SCAN_INTERVAL,
                description={
                    "suggested_value": user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
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
                vol.Required(OPT_SSL, default=str(user_input.get(OPT_SSL, SSLMode.NORMAL)))
            ] = cv.multi_select({mode.value: mode.name for mode in SSLMode})

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

        if user_input is not None and errors is None:
            channels = user_input.get("channels", None)
            if channels is not None:
                if len(channels) > 0:
                    channels = tuple(int(i) for i in channels)
                else:
                    channels = None
            self.data.update(channels=channels)
            return await self.async_step_menu()

        if user_input is None:
            user_input = self.data

        domain_data: DomainDataType = self.hass.data[DOMAIN]
        entry_data = domain_data[self.config_entry.entry_id]
        api = entry_data[DATA_API]

        channels = _simple_channels(api.channel_statuses)
        schema = _channels_schema(channels, **user_input)

        return self.async_show_form(
            step_id="channels",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={},
        )

    async def async_step_commit(self, user_input: UserDataType | None = None) -> FlowResult:
        """Save Changes"""

        return self.async_create_entry(title="", data=self.data)
