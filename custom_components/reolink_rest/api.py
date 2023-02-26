"""API Interface"""

from datetime import timedelta
import ssl
from time import time
from types import MappingProxyType, SimpleNamespace
from typing import (
    TYPE_CHECKING,
    Mapping,
    Union,
)

import aiohttp
from async_reolink.api.const import DEFAULT_PASSWORD, DEFAULT_TIMEOUT, DEFAULT_USERNAME
from async_reolink.api.errors import ErrorCodes, ReolinkResponseError
from async_reolink.api.network.typing import ChannelStatus, NetworkPorts
from async_reolink.api.connection.model import Response
from async_reolink.api.system.capabilities import Capabilities
from async_reolink.api.system.typing import (
    DaylightSavingsTimeInfo,
    TimeInfo,
    DeviceInfo as ReolinkDeviceInfo,
)
from async_reolink.api.system.model import NO_CAPABILITY, NO_DEVICEINFO

from async_reolink.rest.errors import AUTH_ERRORCODES
from async_reolink.rest.client import Client
from async_reolink.rest.connection.typing import Encryption
from async_reolink.rest.network.model import (
    ChannelStatuses as UpdatableChannelStatuses,
)
from async_reolink.rest.system.capabilities import UpdatableCapabilities
from async_reolink.rest.system.model import DeviceInfo as UpdatableDeviceInfo
from async_reolink.rest.system import command as system_command
from async_reolink.rest.network import command as network_command
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_TIMEOUT,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    UNDEFINED,
    DeviceEntry,
)
from homeassistant.config_entries import ConfigEntry, current_entry as current_config_entry
from homeassistant.helpers.entity import DeviceInfo

if TYPE_CHECKING:
    from homeassistant.helpers import (
        device_registry as helper_device_registry,
    )
from ._utilities.hass_typing import hass_bound

from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
)
from homeassistant.util import dt

from .typing import DiscoveredDevice, EntryId, ChannelData, DeviceData, RequestType, RequestHandler

from .const import (
    BRAND,
    CONF_USE_HTTPS,
    DOMAIN,
    OPT_BATCH_CAPABILITIES,
    OPT_PREFIX_CHANNEL,
    OPT_SSL,
    SSLMode,
)


class _Data:
    def __init__(self, __default: any = None) -> None:
        self.__default = __default

    def __getattr__(self, __name: str):
        return self.__default


class _ChannelData(_Data, ChannelData):
    def __init__(self, info: "_ChannelInfo", __default=None) -> None:
        super().__init__(__default)
        self.__info = info

    @property
    def channel_id(self):
        return self.__info.channel_id

    @property
    def device(self):
        return self.__info.device


class _ChannelInfo:

    __slots__ = ("channel_id", "device", "data")

    def __init__(self, channel_id: int = 0, device: ReolinkDeviceInfo = NO_DEVICEINFO) -> None:
        self.channel_id = channel_id
        self.device = device
        self.data: ChannelData = _ChannelData(self)


class _ChannelInfoMap(Mapping[int, _ChannelData]):

    __slots__ = ("__info",)

    def __init__(self, info: Mapping[int, _ChannelInfo]) -> None:
        super().__init__()
        self.__info = info

    def __getitem__(self, __key: int):
        info = self.__info[__key]
        return info.data

    def __iter__(self):
        return self.__info.__iter__()

    def __len__(self):
        return self.__info.__len__()


class _DeviceData(_Data, DeviceData):
    def __init__(self, api: "ReolinkDeviceApi", __default=None) -> None:
        super().__init__(__default)
        self.__api = api

    @property
    def capabilities(self):
        return self.__api._capabilities

    @property
    def device_info(self):
        return self.__api._device_info

    @property
    def ports(self):
        return self.__api._ports

    @property
    def channel_statuses(self):
        return self.__api._channel_statuses

    @property
    def channel_info(self) -> Mapping[int, ChannelData]:
        return _ChannelInfoMap(self.__api._channel_info)

    @property
    def time_diff(self):
        return self.__api._time_diff


_NO_CONFIG: Mapping[str, any] = MappingProxyType({})


class ReolinkDeviceApi:
    """Reolink Device API"""

    __slots__ = (
        "_client",
        "_connection_id",
        "_authentication_id",
        "_device_entry",
        "_capabilities",
        "_device_info",
        "_ports",
        "_channel_statuses",
        "_channel_info",
        "_dst",
        "_timestamp",
        "_time",
        "_time_diff",
        "_data",
    )

    def __init__(self):
        self._client = None
        self._connection_id = -1
        self._authentication_id = None
        self._device_entry: DeviceEntry = None
        self._capabilities: Capabilities = NO_CAPABILITY
        self._device_info: ReolinkDeviceInfo = NO_DEVICEINFO
        self._ports: NetworkPorts = None
        self._channel_statuses: Mapping[int, ChannelStatus] = {}
        self._channel_info: Mapping[int, _ChannelInfo] = {}
        self._dst: DaylightSavingsTimeInfo = None
        self._timestamp = time()
        self._time: TimeInfo = None
        self._time_diff: timedelta = None
        self._data = None

    @classmethod
    def _create_weak_ssl_context(cls, base_url: str):
        """Create a weak ssl context to work with self signed certs"""
        return False

    @classmethod
    def _create_insecure_ssl_context(cls, base_url: str):
        """Create an insecure ssl context to work with outdated hardware"""
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT")
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    @classmethod
    def _create_Client(cls, **config_args: any):
        match SSLMode(config_args.get(OPT_SSL, SSLMode.NORMAL)):
            case SSLMode.WEAK:
                ssl_mode = cls._create_weak_ssl_context()
            case SSLMode.INSECURE:
                ssl_mode = cls._create_insecure_ssl_context()
            case _:
                ssl_mode = None
        return Client(ssl=ssl_mode)

    @property
    def client(self):
        if self._client is None:
            raise ValueError("async_ensure_connection must be executed before calling this")
        return self._client

    @property
    def has_client(self):
        return self._client is not None

    @property
    def data(self) -> DeviceData:
        if not self._data:
            self._data = _DeviceData(self)
        return self._data

    @property
    def device_name(self):
        name = None
        if self._device_entry:
            name = self._device_entry.name_by_user or self._device_entry.name
        if not name and self._device_info:
            name = self._device_info.name
        if not name and self._client:
            return self._client.hostname
        return name or ""

    async def async_ensure_connection(self, **config_args: any):
        if not self.has_client:
            self._client = self._create_Client(**config_args)
        client = self.client

        if not client.is_connected or self._connection_id != client.connection_id:
            if config_args.get(CONF_USE_HTTPS, False):
                encryption = Encryption.HTTPS
            else:
                encryption = Encryption.NONE

            host: str = config_args.get(CONF_HOST)
            if not host:
                raise ConfigEntryNotReady("No host configured?")

            if not await client.connect(
                host,
                config_args.get(CONF_PORT),
                config_args.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
                encryption=encryption,
            ):
                raise ConfigEntryNotReady(f"Could not connect to device {host}")

            if self._connection_id != client.connection_id:
                self._connection_id = client.connection_id
                self._authentication_id = None

        if not client.is_authenticated or self._authentication_id != client.authentication_id:
            try:
                if not await client.login(
                    config_args.get(CONF_USERNAME, DEFAULT_USERNAME),
                    config_args.get(CONF_PASSWORD, DEFAULT_PASSWORD),
                ):
                    self._authentication_id = None
                    await client.disconnect()
                    raise ConfigEntryAuthFailed()
            except ReolinkResponseError as reoresp:
                if reoresp.code in AUTH_ERRORCODES:
                    await client.disconnect()
                    raise ConfigEntryAuthFailed() from reoresp
                raise reoresp
            self._authentication_id = client.authentication_id

    def _update_capabilities(self, caps: Capabilities):
        if isinstance(self._capabilities, UpdatableCapabilities):
            self._capabilities.update(caps)
        else:
            self._capabilities = caps

    async def _async_ensure_capabilities(self, **config_args: any):
        client = self.client
        if self._capabilities is NO_CAPABILITY or not config_args.get(OPT_BATCH_CAPABILITIES, True):
            try:
                caps = await client.get_capabilities(
                    config_args.get(CONF_USERNAME, DEFAULT_USERNAME)
                )
            except ReolinkResponseError as reoresp:
                if reoresp.code in AUTH_ERRORCODES:
                    self._authentication_id = None
                    await client.disconnect()
                    return None
                if reoresp.code == ErrorCodes.PROTOCOL_ERROR:
                    # possible weird encryption bug or other invalid response so we will force a reconnect
                    self._connection_id = 0
                    self._authentication_id = None
                raise reoresp

            self._update_capabilities(caps)
        else:
            # TODO : trap error when cannot batch capabilities
            caps = self._capabilities
            return RequestHandler(
                system_command.GetAbilitiesRequest(
                    user_name=config_args.get(CONF_USERNAME, DEFAULT_USERNAME)
                ),
                lambda response: isinstance(response, system_command.GetAbilitiesResponse)
                and self._update_capabilities(response.capabilities),
            )
        return True

    def _ensure_time(self):
        def update_time(response: Response):
            if not isinstance(response, system_command.GetTimeResponse):
                return
            self._dst = response.dst
            self._time = response.time
            self._time_diff = dt.utcnow() - dt.as_utc(response.to_datetime())

        if self._capabilities.time and (
            not self._dst or not self._time or time() - self._timestamp > 3600
        ):
            return RequestHandler(system_command.GetTimeRequest(), update_time)
        return True

    @callback
    def async_get_device_lookup_info(self, unique_id: str | None):
        """get a device info value that can be used with an entity"""
        if self._device_info and self._device_info.channels < 2 and 0 in self._channel_info:
            return self._channel_info[0].device
        if self._device_entry is not None:
            return DeviceInfo(
                identifiers=self._device_entry.identifiers.copy(),
                connections=self._device_entry.connections.copy(),
            )
        if not unique_id:
            return None
        return DeviceInfo(identifiers={(DOMAIN, unique_id)})

    async def _async_ensure_device_info(
        self,
        hass: HomeAssistant,
        config_entry_id: EntryId,
        unique_id: str | None,
        **config_args: any,
    ):
        if not self._capabilities.device.info:
            return True

        if not unique_id:
            unique_id = config_entry_id

        device = None
        device_registry: helper_device_registry = hass.helpers.device_registry
        registry = await hass_bound(device_registry.async_get_registry)()
        name = self.client.hostname

        def add_or_update_device_entry(device: DeviceInfo):
            if self._device_entry is None:
                self._device_entry = registry.async_get_device(
                    device.get("identifiers"), device.get("connections")
                )
            if self._device_entry is not None:
                return (
                    registry.async_update_device(
                        self._device_entry.id,
                        configuration_url=device.get("configuration_url", UNDEFINED),
                        manufacturer=device.get(
                            "manufacturer", device.get("default_manufacturer", BRAND)
                        ),
                        model=device.get(
                            "model", device.get("default_model", self._device_info.model)
                        ),
                        name=device.get("name", device.get("default_name", self._device_info.name)),
                        hw_version=device.get("hw_version", self._device_info.version.hardware),
                        sw_version=device.get("sw_version", self._device_info.version.firmware),
                        merge_connections=device.get("connections", UNDEFINED),
                        merge_identifiers=device.get("identifiers", UNDEFINED),
                    )
                    or self._device_entry
                )

            self._device_entry = registry.async_get_or_create(
                config_entry_id=config_entry_id,
                configuration_url=device.get("configuration_url", UNDEFINED),
                connections=device.get("connections"),
                default_manufacturer=device.get("default_manufacturer", BRAND),
                default_model=device.get("default_model", self._device_info.model),
                default_name=device.get("default_name", self._device_info.name),
                hw_version=device.get("hw_version", self._device_info.version.hardware),
                identifiers=device["identifiers"],
                sw_version=device.get("sw_version", self._device_info.version.firmware),
            )
            return self._device_entry

        def update_device_info(response: Response):
            nonlocal device
            if not isinstance(response, system_command.GetDeviceInfoResponse):
                return
            if isinstance(self._device_info, UpdatableDeviceInfo):
                self._device_info.update(response.info)
            else:
                self._device_info = response.info
            if not device:
                device = self.async_get_device_lookup_info(unique_id)
            if device:
                device["configuration_url"] = self.client.base_url
            key = "name" if "name" in device else "default_name"
            device[key] = response.info.name

        def update_channel_status(response: Response):
            if not isinstance(response, network_command.GetChannelStatusResponse):
                return
            if isinstance(self._channel_statuses, UpdatableChannelStatuses):
                self._channel_statuses.update(response.channels)
            else:
                self._channel_statuses = response.channels
            via_device = None
            for status in self._channel_statuses.values():
                channel_data = self._channel_info.get(status.channel_id)
                channel_name = status.name or f"Channel {status.channel_id}"
                if config_args.get(OPT_PREFIX_CHANNEL, False):
                    channel_name = f"{self._device_entry.name_by_user or self._device_entry.name} {channel_name}"
                if channel_data is None:
                    if not via_device:
                        via_device = next(iter(device["identifiers"]))
                    if via_device:
                        channel_device = DeviceInfo(
                            identifiers={(f"{DOMAIN}_{unique_id}_channel", status.channel_id)},
                            via_device=via_device,
                        )
                    else:
                        channel_device = DeviceInfo()
                    channel_data = _ChannelInfo(channel_id=status.channel_id, device=channel_device)
                    self._channel_info[status.channel_id] = channel_data
                else:
                    channel_device = channel_data.device
                channel_device.update(
                    default_name=channel_name,
                    default_model=status.type,
                    default_manufacturer="",
                )
                entry = registry.async_get_device(channel_device)
                if entry:
                    entry = registry.async_update_device(entry.id, name=channel_name)
                    channel_device["name"] = entry.name_by_user or entry.name

        def update_p2p(response: Response):
            nonlocal device
            if not isinstance(response, network_command.GetP2PResponse):
                return
            if not device:
                device = self.async_get_device_lookup_info(unique_id)
            if device:
                device.setdefault("identifiers", set()).add((f"{DOMAIN}_uuid", response.info.uid))

        def update_local_link(response: Response):
            nonlocal device
            if not isinstance(response, network_command.GetLocalLinkResponse):
                return
            if not device:
                device = self.async_get_device_lookup_info(unique_id)
            if device:
                device.setdefault("connections", set()).add(
                    (CONNECTION_NETWORK_MAC, response.local_link.mac)
                )

        def update_device(*_: any):
            nonlocal device
            if device is None or self._device_entry is None:
                return
            self._device_entry = add_or_update_device_entry(device)

        commands: list[RequestHandler] = []
        if self._device_info is NO_DEVICEINFO:
            self._device_info = await self.client.get_device_info()
            dev_info = self._device_info
            device = self.async_get_device_lookup_info(unique_id)
            if not device:
                device = DeviceInfo(identifiers={(DOMAIN, unique_id)})
            device.update(
                {
                    "configuration_url": self.client.base_url,
                    "default_manufacturer": BRAND,
                    "default_model": dev_info.model,
                    "default_name": self.device_name,
                    "hw_version": dev_info.version.hardware,
                    "sw_version": dev_info.version.firmware,
                }
            )

            if dev_info.channels < 2 and 0 not in self._channel_statuses:
                self._channel_statuses[0] = SimpleNamespace(
                    channel_id=0, name=None, online=True, type=dev_info.type
                )
                self._channel_info[0] = _ChannelInfo(channel_id=0, device=device)
            elif dev_info.channels > 1:
                entry = add_or_update_device_entry(device)
                name = entry.name or name
        else:
            commands.append(
                RequestHandler(system_command.GetDeviceInfoRequest(), update_device_info)
            )

        if self._device_info.channels > 1:
            commands.append(
                RequestHandler(network_command.GetChannelStatusRequest(), update_channel_status)
            )

        if device is not None and self._device_entry is None:
            if self._capabilities.p2p:
                commands.append(RequestHandler(network_command.GetP2PRequest(), update_p2p))
            if self._capabilities.local_link:
                commands.append(
                    RequestHandler(network_command.GetLocalLinkRequest(), update_local_link)
                )

        if commands:
            commands.append(update_device)
            return commands
        return True

    def _ensure_ports(self):
        def update_ports(response: Response):
            if not isinstance(response, network_command.GetNetworkPortsResponse):
                return
            self._ports = response.ports

        if self._ports is None:
            return RequestHandler(network_command.GetNetworkPortsRequest(), update_ports)
        return True

    async def async_get_client_update_requests(
        self,
        hass: HomeAssistant,
        config_entry_id: EntryId,
        unique_id: str | None,
        **config_args: any,
    ):
        await self.async_ensure_connection(**config_args)
        requests: list[RequestType] = []
        ok = True

        if ok and isinstance(
            (ok := await self._async_ensure_capabilities(**config_args)), RequestHandler
        ):
            requests.append(ok)
        if ok and isinstance((ok := self._ensure_time()), RequestHandler):
            requests.append(ok)
        if ok and isinstance(
            (
                ok := await self._async_ensure_device_info(
                    hass, config_entry_id, unique_id, **config_args
                )
            ),
            list,
        ):
            for _i in ok:
                requests.append(_i)
        if ok and isinstance((ok := self._ensure_ports()), RequestHandler):
            requests.append(ok)

        return requests
