"""API Interface"""

from datetime import timedelta
import ssl
from time import time
from types import SimpleNamespace
from typing import (
    Callable,
    Iterable,
    Mapping,
    Protocol,
    Set,
    TypedDict,
)

import aiohttp
from async_reolink.api.const import DEFAULT_PASSWORD, DEFAULT_TIMEOUT, DEFAULT_USERNAME
from async_reolink.api.errors import ErrorCodes, ReolinkResponseError
from async_reolink.api.network.typing import ChannelStatus, NetworkPorts
from async_reolink.api.connection.model import Request, Response, ErrorResponse
from async_reolink.api.system.capabilities import Capabilities
from async_reolink.api.system.typing import (
    DeviceInfo as ReolinkDeviceInfo,
    DaylightSavingsTimeInfo,
    TimeInfo,
)
from async_reolink.api.system.model import NO_CAPABILITY, NO_DEVICEINFO

from async_reolink.api.security.typing import AuthenticationId
from async_reolink.rest.errors import AUTH_ERRORCODES
from async_reolink.rest.client import Client
from async_reolink.rest.connection.typing import Encryption
from async_reolink.rest.network.model import (
    ChannelStatuses as UpdatableChannelStatuses,
)
from async_reolink.rest.system.capabilities import Capabilities as UpdatableCapabilities
from async_reolink.rest.system.model import DeviceInfo as UpdatableDeviceInfo
from async_reolink.rest.system import command as system_command
from async_reolink.rest.network import command as network_command
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_TIMEOUT,
    CONF_USERNAME,
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    async_get as async_get_device_registry,
    UNDEFINED,
    DeviceEntry,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue
from homeassistant.helpers.update_coordinator import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    DataUpdateCoordinator,
)
from homeassistant.util import dt

from .const import (
    BRAND,
    CONF_USE_HTTPS,
    DEFAULT_HISPEED_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    OPT_BATCH_CAPABILITIES,
    OPT_DISCOVERY,
    OPT_HISPEED_INTERVAL,
    OPT_PREFIX_CHANNEL,
    OPT_SSL,
    SSLMode,
)

from .discovery import DiscoveryDict


class ChannelData(Protocol):
    """Channel Data"""

    channel_id: int
    device: DeviceInfo


ChannelStatuses = Mapping[int, ChannelStatus]
ChannelInfo = Mapping[int, ChannelData]

QueueResponse = tuple[Response, ...]


def weak_ssl_context(__base_url: str):
    """Create a weak ssl context to work with self signed certs"""
    return False


def insecure_ssl_context(__base_url: str):
    """Create an insecure ssl context to work with outdated hardware"""
    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT")
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


@callback
def async_get_poll_interval(options: Mapping[str, any]):
    """Get the poll interval"""
    interval = options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    return timedelta(seconds=interval)


@callback
def async_get_hispeed_poll_interval(options: Mapping[str, any]):
    """Get the high speed poll interval"""
    interval = options.get(OPT_HISPEED_INTERVAL, DEFAULT_HISPEED_INTERVAL)
    return timedelta(seconds=interval)


@callback
def _get_config_entry(hass: HomeAssistant, entry_id: str):
    config_entry = hass.config_entries.async_get_entry(entry_id)
    if not config_entry:
        raise KeyError()
    return config_entry


class ClientData(Protocol):
    """Client Data"""

    connection_id: int
    authentication_id: AuthenticationId
    device_entry: DeviceEntry
    capabilities: Capabilities
    device_info: ReolinkDeviceInfo
    ports: NetworkPorts
    channel_statuses: ChannelStatuses
    channel_info: ChannelInfo
    dst: DaylightSavingsTimeInfo
    timestamp: float
    time: TimeInfo
    time_diff: timedelta


class EntryData(TypedDict, total=False):
    """Entry Data"""

    client: Client
    client_data: ClientData
    coordinator: DataUpdateCoordinator[QueueResponse]
    hispeed_coordinator: DataUpdateCoordinator[QueueResponse]


@callback
def async_get_entry_data(hass: HomeAssistant, entry_id: str, allow_create=True):
    """Get Entity Entry Data"""

    domain_data: dict
    entry_data: EntryData

    if allow_create:
        domain_data = hass.data.setdefault(DOMAIN, {})
        entry_data = domain_data.setdefault(entry_id, EntryData())
    else:
        entry_data = None
        if domain_data := hass.data.get(DOMAIN, None):
            entry_data = domain_data.get(entry_id, None)
    return entry_data


def _ensure_client(hass: HomeAssistant, entry_id: str):
    entry_data = async_get_entry_data(hass, entry_id)
    client = entry_data.get("client", None)
    if client is None:
        config_entry = _get_config_entry(hass, entry_id)
        ssl_mode = SSLMode(config_entry.options.get(OPT_SSL, SSLMode.NORMAL))
        if ssl_mode == SSLMode.WEAK:
            ssl_mode = weak_ssl_context
        elif ssl_mode == SSLMode.INSECURE:
            ssl_mode = insecure_ssl_context
        else:
            ssl_mode = None
        client = Client(ssl=ssl_mode)
        entry_data["client"] = client
    return client


def _ensure_client_data(hass: HomeAssistant, entry_id: str):
    entry_data = async_get_entry_data(hass, entry_id)
    data: ClientData = entry_data.get("client_data", None)
    if data is None:
        data = SimpleNamespace(
            timestamp=time(),
            channel_info={},
            capabilities=NO_CAPABILITY,
            device_info=NO_DEVICEINFO,
        )
        entry_data["client_data"] = data
    return data


async def _ensure_connection(hass: HomeAssistant, entry_id: str):
    client = _ensure_client(hass, entry_id)
    data = _ensure_client_data(hass, entry_id)
    if client.is_connected:
        try:
            connection_id = data.connection_id
        except AttributeError:
            connection_id = 0
    else:
        connection_id = 0

    discovery: DiscoveryDict
    if not client.is_connected or connection_id != client.connection_id:
        config_entry = _get_config_entry(hass, entry_id)
        config = config_entry.data
        discovery = config_entry.options.get(OPT_DISCOVERY, None)

        host: str = config.get(
            CONF_HOST,
            discovery.get("ip", None) if discovery is not None else None,
        )
        if config.get(CONF_USE_HTTPS, False):
            encryption = Encryption.HTTPS
        else:
            encryption = Encryption.NONE

        if not host:
            raise ConfigEntryNotReady(
                "No host configured, and none discovered (was device lost?)"
            )

        if not await client.connect(
            host,
            config.get(CONF_PORT, None),
            config.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
            encryption=encryption,
        ):
            raise ConfigEntryNotReady(f"Could not connect to device {host}")

        if connection_id != client.connection_id:
            data.connection_id = client.connection_id
            data.authentication_id = None

    name = client.hostname
    if hasattr(data, "device_entry") and data.device_entry is not None:
        name = data.device_entry.name_by_user or data.device_entry.name or name
    elif discovery:
        name = discovery.get("name", name)
    name = data.device_info.name or name

    if (
        not client.is_authenticated
        or data.authentication_id != client.authentication_id
    ):
        if not config:
            config_entry = _get_config_entry(hass, entry_id)
            config = config_entry.data

        try:
            if not await client.login(
                config.get(CONF_USERNAME, DEFAULT_USERNAME),
                config.get(CONF_PASSWORD, DEFAULT_PASSWORD),
            ):
                data.authentication_id = None
                await client.disconnect()
                raise ConfigEntryAuthFailed()
        except aiohttp.ClientResponseError as http_error:
            if (
                http_error.status in (301, 302, 308)
                and "location" in http_error.headers
            ):
                location = http_error.headers["location"]
                # TODO : verify redirect stays on device
                if client.secured and location.startswith("http://"):
                    async_create_issue(
                        hass,
                        DOMAIN,
                        "from_ssl_redirect",
                        severity=IssueSeverity.ERROR,
                        is_fixable=True,
                        data={"entry_id": entry_id},
                        translation_key="from_ssl_redirect",
                        translation_placeholders={
                            "name": name,
                        },
                    )
                elif not client.secured and location.startswith("https://"):
                    async_create_issue(
                        hass,
                        DOMAIN,
                        "to_ssl_redirect",
                        severity=IssueSeverity.ERROR,
                        is_fixable=True,
                        data={"entry_id": entry_id},
                        translation_key="from_ssl_redirect",
                        translation_placeholders={
                            "name": name,
                        },
                    )
            elif http_error.status == 500:
                if client.secured:
                    # this error occurs when HTTPS is disabled on the camera but we try to connect to it.
                    async_create_issue(
                        hass,
                        DOMAIN,
                        "from_ssl_redirect",
                        severity=IssueSeverity.ERROR,
                        is_fixable=True,
                        data={"entry_id": entry_id},
                        translation_key="from_ssl_redirect",
                        translation_placeholders={
                            "name": name,
                        },
                    )
                else:
                    async_create_issue(
                        hass,
                        DOMAIN,
                        "http_error",
                        severity=IssueSeverity.CRITICAL,
                        is_fixable=True,
                        data={"entry_id": entry_id},
                        translation_key="http_error",
                        translation_placeholders={
                            "name": name,
                        },
                    )
            raise http_error
        except ssl.SSLError as ssl_error:
            if ssl_error.errno == 1:
                async_create_issue(
                    hass,
                    DOMAIN,
                    "insecure_ssl",
                    severity=IssueSeverity.ERROR,
                    is_fixable=True,
                    data={"entry_id": entry_id},
                    translation_key="insecure_ssl",
                    translation_placeholders={
                        "name": name,
                    },
                )
            raise ssl_error
        except ReolinkResponseError as reoresp:
            if reoresp.code in AUTH_ERRORCODES:
                await client.disconnect()
                raise ConfigEntryAuthFailed() from reoresp
            raise reoresp
        data.authentication_id = client.authentication_id
    return (name, client, data)


def _get_device_lookup(data: ClientData, config_entry: ConfigEntry):
    if (
        (device_info := data.device_info)
        and device_info.channels < 2
        and 0 in data.channel_info
    ):
        return data.channel_info[0].device
    if hasattr(data, "device_entry") and data.device_entry is not None:
        return DeviceInfo(
            identifiers=data.device_entry.identifiers.copy(),
            connections=data.device_entry.connections.copy(),
        )
    if config_entry.unique_id is None:
        return None
    device = DeviceInfo(identifiers={(DOMAIN, config_entry.unique_id)})
    discovery: DiscoveryDict = config_entry.options.get(OPT_DISCOVERY, None)
    if discovery is not None:
        if "uuid" in discovery:
            device["identifiers"].add((f"{DOMAIN}_uuid", discovery["uuid"]))
        if "mac" in discovery:
            device["connections"] = {(CONNECTION_NETWORK_MAC, discovery["mac"])}
    return device


def _add_or_update_device_entry(
    hass: HomeAssistant, entry_id: str, data: ClientData, device: DeviceInfo
):
    registry = async_get_device_registry(hass)
    if not hasattr(data, "device_entry") or data.device_entry is None:
        data.device_entry = registry.async_get_device(
            device["identifiers"], device.get("connections", None)
        )
    if data.device_entry is None:
        data.device_entry = registry.async_get_or_create(
            config_entry_id=entry_id,
            configuration_url=device.get("configuration_url", UNDEFINED),
            connections=device.get("connections", None),
            default_manufacturer=device.get("default_manufacturer", BRAND),
            default_model=device.get("default_model", data.device_info.model),
            default_name=device.get("default_name", data.device_info.name),
            hw_version=device.get("hw_version", data.device_info.version.hardware),
            identifiers=device["identifiers"],
            sw_version=device.get("sw_version", data.device_info.version.firmware),
        )
        return data.device_entry

    return registry.async_update_device(
        data.device_entry.id,
        configuration_url=device.get("configuration_url", UNDEFINED),
        manufacturer=device.get(
            "manufacturer", device.get("default_manufacturer", BRAND)
        ),
        model=device.get("model", device.get("default_model", data.device_info.model)),
        name=device.get("name", device.get("default_name", data.device_info.name)),
        hw_version=device.get("hw_version", data.device_info.version.hardware),
        sw_version=device.get("sw_version", data.device_info.version.firmware),
        merge_connections=device.get("connections", UNDEFINED),
        merge_identifiers=device["identifiers"],
    )


INLINE_CALLBACK = Callable[[Response], None]  # pylint: disable=invalid-name


class RequestQueue(Set[Request]):
    """Request Queue"""

    __slots__ = ("_requests", "_callbacks")

    def __init__(self) -> None:
        super().__init__()
        self._requests: list[Request] = []
        self._callbacks: dict[int, list[INLINE_CALLBACK]] = {}

    def __iter__(self):
        return self._requests.__iter__()

    def __len__(self) -> int:
        return self._requests.__len__()

    def __contains__(self, value: object):
        return self._requests.__contains__(value)

    def clear(self) -> None:
        self._requests.clear()
        self._callbacks.clear()

    def append(self, value: Request, __callback: INLINE_CALLBACK | None = None) -> None:
        """Add Request to Queue"""
        self._requests.append(value)
        if __callback is None:
            return
        self._callbacks.setdefault(value.id, []).append(__callback)

    def extend(self, __iterable: Iterable[Request]):
        """Extend queue"""
        self._requests.extend(__iterable)
        if isinstance(__iterable, RequestQueue):
            # pylint: disable=protected-access
            for _k, _v in __iterable._callbacks.items():
                if _l := self._callbacks.get(_k, None):
                    _l.extend(_v)
                else:
                    self._callbacks[_k] = _v.copy()

    def add_listener(self, __callback: INLINE_CALLBACK):
        """Add a listener that fires at the end (response argument is None)"""

        self._callbacks.setdefault(0, []).append(__callback)

    def get_callbacks(self, request_id: int):
        """get callbacks associated with the given request"""
        return tuple(self._callbacks.get(request_id, []))


async def _create_primary_queue(hass: HomeAssistant, entry_id: str):
    name: str
    client: Client
    data: ClientData

    name, client, data = await _ensure_connection(hass, entry_id)
    config_entry = _get_config_entry(hass, entry_id)

    def update_capabilities(caps: Capabilities):
        if isinstance(data.capabilities, UpdatableCapabilities):
            data.capabilities.update(caps)
        else:
            data.capabilities = caps

    queue = RequestQueue()
    if data.capabilities is NO_CAPABILITY or not config_entry.options.get(
        OPT_BATCH_CAPABILITIES, True
    ):
        try:
            caps = await client.get_capabilities(
                config_entry.data.get(CONF_USERNAME, DEFAULT_USERNAME)
            )
        except ReolinkResponseError as reoresp:
            if reoresp.code in AUTH_ERRORCODES:
                data.authentication_id = None
                await client.disconnect()
                queue, name, client, data = await _create_primary_queue(hass, entry_id)
                return (queue, name, client, data)
            if reoresp.code == ErrorCodes.PROTOCOL_ERROR:
                # possible weird encryption bug or other invalid response so we will force a reconnect
                data.connection_id = 0
                data.authentication_id = None
            raise reoresp

        update_capabilities(caps)
    else:
        caps = data.capabilities
        queue.append(
            system_command.GetAbilitiesRequest(
                config_entry.data.get(CONF_USERNAME, DEFAULT_USERNAME)
            ),
            lambda response: isinstance(response, system_command.GetAbilitiesResponse)
            and update_capabilities(response.capabilities),
        )

    def update_time(response: Response):
        nonlocal data
        if not isinstance(response, system_command.GetTimeResponse):
            return
        data.dst = response.dst
        data.time = response.time
        data.time_diff = dt.utcnow() - dt.as_utc(response.to_datetime())

    if (
        not hasattr(data, "dst")
        or not hasattr(data, "time")
        or time() - data.timestamp > 3600
    ):
        queue.append(system_command.GetTimeRequest(), update_time)

    device = None

    def update_device_info(response: Response):
        nonlocal data, device
        if not isinstance(response, system_command.GetDeviceInfoResponse):
            return
        if isinstance(data.device_info, UpdatableDeviceInfo):
            data.device_info.update(response.info)
        else:
            data.device_info = response.info
        if not device:
            device = _get_device_lookup(data, config_entry)
        if device:
            device["configuration_url"] = client.base_url
            device["default_name"] = data.device_info.name or name

    def update_channel_status(response: Response):
        nonlocal data
        if not isinstance(response, network_command.GetChannelStatusResponse):
            return
        if not hasattr(data, "channel_statuses") or not isinstance(
            data.channel_statuses, UpdatableChannelStatuses
        ):
            data.channel_statuses = response.channels
        else:
            data.channel_statuses.update(response.channels)
        via_device = None
        for status in data.channel_statuses.values():
            channel_data = data.channel_info.get(
                status.channel_id,
                None,
            )
            channel_name = status.name or f"Channel {status.channel_id}"
            if config_entry.options.get(OPT_PREFIX_CHANNEL, False):
                channel_name = f"{data.device_entry.name_by_user or data.device_entry.name} {channel_name}"
            if channel_data is None:
                if not via_device:
                    if unique_id := config_entry.unique_id:
                        via_device = (DOMAIN, unique_id)
                if via_device:
                    channel_device = DeviceInfo(
                        identifiers={
                            (f"{'_'.join(via_device)}_channel", status.channel_id)
                        },
                        via_device=via_device,
                    )
                else:
                    channel_device = DeviceInfo()
                channel_data: ChannelData = SimpleNamespace(
                    channel_id=status.channel_id, device=channel_device
                )
                data.channel_info[status.channel_id] = channel_data
            else:
                channel_device = channel_data.device
            channel_device.update(
                default_name=channel_name,
                default_model=status.type,
                default_manufacturer="",
            )
            registry = async_get_device_registry(hass)
            entry = registry.async_get_device(channel_device)
            if entry:
                entry = registry.async_update_device(entry.id, name=channel_name)
                channel_device["name"] = entry.name_by_user or entry.name

    if caps.device.info:
        if data.device_info is NO_DEVICEINFO:
            dev_info = await client.get_device_info()
            data.device_info = dev_info
            name = dev_info.name or name
            device = _get_device_lookup(data, config_entry)
            device["configuration_url"] = client.base_url
            device["default_manufacturer"] = BRAND
            device["default_model"] = dev_info.model
            device["default_name"] = name
            device["hw_version"] = dev_info.version.hardware
            device["sw_version"] = dev_info.version.firmware
            if dev_info.channels < 2 and not hasattr(data, "channel_statuses"):
                data.channel_statuses = {
                    0: SimpleNamespace(
                        channel_id=0, name=None, online=True, type=dev_info.type
                    )
                }
                data.channel_info[0] = SimpleNamespace(channel_id=0, device=device)
            elif dev_info.channels > 1:
                entry = (
                    _add_or_update_device_entry(hass, entry_id, data, device)
                    or data.device_entry
                )
                if entry is not None:
                    name = entry.name or name
        else:
            queue.append(system_command.GetDeviceInfoRequest(), update_device_info)

        if data.device_info.channels > 1:
            queue.append(
                network_command.GetChannelStatusRequest(), update_channel_status
            )
    elif not hasattr(data, "dev_info"):
        data.device_info = None

    def update_ports(response: Response):
        nonlocal data
        if not isinstance(response, network_command.GetNetworkPortsResponse):
            return
        data.ports = response.ports

    if not hasattr(data, "ports") or data.ports is None:
        queue.append(network_command.GetNetworkPortsRequest(), update_ports)

    def update_p2p(response: Response):
        nonlocal data, device, discovery
        if not isinstance(response, network_command.GetP2PResponse):
            return
        if not device:
            device = _get_device_lookup(data, config_entry)
        if device:
            device.setdefault("identifiers", set()).add(
                (f"{DOMAIN}_uuid", response.info.uid)
            )

    def update_local_link(response: Response):
        nonlocal data, device, discovery
        if not isinstance(response, network_command.GetLocalLinkResponse):
            return
        if not device:
            device = _get_device_lookup(data, config_entry)
        if device:
            device.setdefault("connections", set()).add(
                (CONNECTION_NETWORK_MAC, response.local_link.mac)
            )

    if device is not None:
        discovery: DiscoveryDict = config_entry.options.get(OPT_DISCOVERY, None)
        if (discovery is None or "uuid" not in discovery) and caps.p2p:
            queue.append(network_command.GetP2PRequest(), update_p2p)
        if (discovery is None or "mac" not in discovery) and caps.local_link:
            queue.append(network_command.GetLocalLinkRequest(), update_local_link)

    def update_device(_):
        nonlocal data, device, discovery
        if (
            device is None
            or not hasattr(data, "device_entry")
            or data.device_entry is None
        ):
            return
        data.device_entry = (
            _add_or_update_device_entry(hass, entry_id, data, device)
            or data.device_entry
        )

    queue.add_listener(update_device)

    return (queue, name, client, data)


def _create_queue(*args):
    queue = RequestQueue()

    for arg in args:
        if isinstance(arg, RequestQueue):
            queue.extend(arg)
            arg.clear()
        elif isinstance(arg, Iterable):
            matched = False
            for item in arg:
                if isinstance(item, Request):
                    matched = True
                    queue.append(item)
            if matched and hasattr(arg, "clear") and callable(arg.clear):
                arg.clear()

    return queue


async def _execute_queue(client: Client, queue: RequestQueue):
    responses: list[Response] = []
    if len(queue) < 1:
        return tuple(responses)

    async for response in client.batch(queue):
        if isinstance(response, ErrorResponse):
            response.throw()
        elif not isinstance(response, Response):
            raise ReolinkResponseError(
                response,
                code=ErrorCodes.PROTOCOL_ERROR,
                details="Did not get a valid response",
            )
        responses.append(response)
        if request_id := response.request_id:
            for __callback in queue.get_callbacks(request_id):
                __callback(response)

    for __callback in queue.get_callbacks(0):
        __callback(None)
    return tuple(responses)


async def async_update_client_data(coordinator: DataUpdateCoordinator):
    """Data Updater to ensure and update ClientData for related integration entry"""
    hass = coordinator.hass
    entry_id = coordinator.config_entry.entry_id
    queue, _, client, _ = await _create_primary_queue(hass, entry_id)
    return await _execute_queue(
        client,
        _create_queue(queue, *coordinator.async_contexts()),
    )


async def async_update_queue(coordinator: DataUpdateCoordinator):
    """Data Update to pull a command queue from a coordinator and batch the commands and provide the responses"""
    hass = coordinator.hass
    entry_id = coordinator.config_entry.entry_id
    _, client, _ = await _ensure_connection(hass, entry_id)
    return await _execute_queue(client, _create_queue(*coordinator.async_contexts()))


# class ReolinkRestApi:
#     """Reolink REST API"""

#     _client: Client = None
#     _coordinator: DataUpdateCoordinator[QueueResponse] = None
#     _hispeed_coordinator: DataUpdateCoordinator[QueueResponse] = None
#     _device_entry: DeviceEntry = None
#     _capabilities: Capabilities = None
#     _device_info: ReolinkDeviceInfo = None
#     _ports: NetworkPorts = None
#     _channel_statuses: ChannelStatuses = None
#     _dst: DaylightSavingsTimeInfo = None
#     _time: TimeInfo = None
#     _time_diff: timedelta = None

#     @property
#     def client(self):
#         """Client"""

#         return self._client

#     @property
#     def channel_statuses(self):
#         """Channels"""

#         return self._channel_statuses

#     @property
#     def channel_info(self):
#         """Channels"""

#         return self._channel_info

#     @property
#     def capabilities(self):
#         """Capabilities"""

#         return self._capabilities

#     @property
#     def ports(self):
#         """Network Ports"""

#         return self._ports

#     @property
#     def device_info(self):
#         """Device Info"""
#         return self._device_info

#     @property
#     def device_entry(self):
#         """Device Registry Entry"""

#         return self._device_entry

#     @property
#     def time_offset(self):
#         """Device time offset"""

#         return self._time_diff

#     def __init__(self):
#         self._connection_id = 0
#         self._authentication_id = 0
#         self._timestamp = time()
#         self._channel_info: ChannelInfo = {}

#     # async def _update_config(self, _hass: HomeAssistant, entry: ConfigEntry):
#     #     self._config = entry.data
#     #     self._options = entry.options
#     #     self._unique_id = entry.unique_id
#     #     if self._coordinator is not None:
#     #         self._coordinator.update_interval = _async_get_poll_interval(self._options)
#     #     if self._hispeed_coordinator is not None:
#     #         self._hispeed_coordinator.update_interval = (
#     #             _async_get_hispeed_poll_interval(self._options)
#     #         )

#     def _get_device_lookup(self):
#         if (
#             (device_info := self._device_info)
#             and device_info.channels < 2
#             and 0 in self._channel_info
#         ):
#             return self._channel_info[0].device
#         if self._device_entry is not None:
#             return DeviceInfo(
#                 identifiers=self._device_entry.identifiers.copy(),
#                 connections=self._device_entry.connections.copy(),
#             )
#         config_entry = self._coordinator.config_entry
#         if config_entry.unique_id is None:
#             return None
#         device = DeviceInfo(identifiers={(DOMAIN, config_entry.unique_id)})
#         discovery: DiscoveryDict = config_entry.options.get(OPT_DISCOVERY, None)
#         if discovery is not None:
#             if "uuid" in discovery:
#                 device["identifiers"].add((f"{DOMAIN}_uuid", discovery["uuid"]))
#             if "mac" in discovery:
#                 device["connections"] = {(CONNECTION_NETWORK_MAC, discovery["mac"])}
#         return device

#     def _add_or_update_device_entry(self, device: DeviceInfo):
#         registry = async_get_device_registry(self._coordinator.hass)
#         if self._device_entry is None:
#             self._device_entry = registry.async_get_device(
#                 device["identifiers"], device.get("connections", None)
#             )
#         if self._device_entry is None:
#             self._device_entry = registry.async_get_or_create(
#                 config_entry_id=self._coordinator.config_entry.entry_id,
#                 configuration_url=device.get("configuration_url", UNDEFINED),
#                 connections=device.get("connections", None),
#                 default_manufacturer=device.get("default_manufacturer", BRAND),
#                 default_model=device.get("default_model", self._device_info.model),
#                 default_name=device.get("default_name", self._device_info.name),
#                 hw_version=device.get("hw_version", self._device_info.version.hardware),
#                 identifiers=device["identifiers"],
#                 sw_version=device.get("sw_version", self._device_info.version.firmware),
#             )
#             return self._device_entry

#         return registry.async_update_device(
#             self._device_entry.id,
#             configuration_url=device.get("configuration_url", UNDEFINED),
#             manufacturer=device.get(
#                 "manufacturer", device.get("default_manufacturer", BRAND)
#             ),
#             model=device.get(
#                 "model", device.get("default_model", self._device_info.model)
#             ),
#             name=device.get("name", device.get("default_name", self._device_info.name))
#             if self._device_entry.name_by_user is None
#             else UNDEFINED,
#             hw_version=device.get("hw_version", self._device_info.version.hardware),
#             sw_version=device.get("sw_version", self._device_info.version.firmware),
#             merge_connections=device.get("connections", UNDEFINED),
#             merge_identifiers=device["identifiers"],
#         )

#     def _ensure_client(self):
#         if self._client is None:
#             config_entry = self._coordinator.config_entry
#             ssl_mode = SSLMode(config_entry.options.get(OPT_SSL, SSLMode.NORMAL))
#             if ssl_mode == SSLMode.WEAK:
#                 ssl_mode = weak_ssl_context
#             elif ssl_mode == SSLMode.INSECURE:
#                 ssl_mode = insecure_ssl_context
#             else:
#                 ssl_mode = None

#             self._client = Client(ssl=ssl_mode)
#         return self._client

#     async def _ensure_connection(self):
#         client = self._ensure_client()
#         config_entry = self._coordinator.config_entry
#         discovery: DiscoveryDict = config_entry.options.get(OPT_DISCOVERY, None)
#         if not client.is_connected or self._connection_id != client.connection_id:
#             config = config_entry.data
#             host: str = config.get(
#                 CONF_HOST,
#                 discovery.get("ip", None) if discovery is not None else None,
#             )
#             if config.get(CONF_USE_HTTPS, False):
#                 encryption = Encryption.HTTPS
#             else:
#                 encryption = Encryption.NONE

#             if not host:
#                 raise ConfigEntryNotReady(
#                     "No host configured, and none discovered (was device lost?)"
#                 )

#             await client.connect(
#                 host,
#                 config.get(CONF_PORT, None),
#                 config.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
#                 encryption=encryption,
#             )
#             if self._connection_id != client.connection_id:
#                 self._connection_id = client.connection_id
#                 self._authentication_id = 0

#         name = client.hostname
#         if self._device_entry is not None:
#             name = self._device_entry.name or name
#         elif self._device_info is not None:
#             name = self._device_info.name or name
#         elif discovery is not None:
#             name = discovery.get("name", name)

#         if (
#             not client.is_authenticated
#             or self._authentication_id != client.authentication_id
#         ):
#             config = config_entry.data

#             try:
#                 if not await client.login(
#                     config.get(CONF_USERNAME, DEFAULT_USERNAME),
#                     config.get(CONF_PASSWORD, DEFAULT_PASSWORD),
#                 ):
#                     self._authentication_id = 0
#                     await client.disconnect()
#                     raise ConfigEntryAuthFailed()
#             except aiohttp.ClientResponseError as http_error:
#                 if (
#                     http_error.status in (301, 302, 308)
#                     and "location" in http_error.headers
#                 ):
#                     location = http_error.headers["location"]
#                     # TODO : verify redirect stays on device
#                     if client.secured and location.startswith("http://"):
#                         async_create_issue(
#                             self._coordinator.hass,
#                             DOMAIN,
#                             "from_ssl_redirect",
#                             severity=IssueSeverity.ERROR,
#                             is_fixable=True,
#                             data={"entry_id": config_entry.entry_id},
#                             translation_key="from_ssl_redirect",
#                             translation_placeholders={
#                                 "name": name,
#                             },
#                         )
#                     elif not client.secured and location.startswith("https://"):
#                         async_create_issue(
#                             self._coordinator.hass,
#                             DOMAIN,
#                             "to_ssl_redirect",
#                             severity=IssueSeverity.ERROR,
#                             is_fixable=True,
#                             data={"entry_id": config_entry.entry_id},
#                             translation_key="from_ssl_redirect",
#                             translation_placeholders={
#                                 "name": name,
#                             },
#                         )
#                 elif http_error.status == 500:
#                     if client.secured:
#                         # this error occurs when HTTPS is disabled on the camera but we try to connect to it.
#                         async_create_issue(
#                             self._coordinator.hass,
#                             DOMAIN,
#                             "from_ssl_redirect",
#                             severity=IssueSeverity.ERROR,
#                             is_fixable=True,
#                             data={"entry_id": config_entry.entry_id},
#                             translation_key="from_ssl_redirect",
#                             translation_placeholders={
#                                 "name": name,
#                             },
#                         )
#                     else:
#                         async_create_issue(
#                             self._coordinator.hass,
#                             DOMAIN,
#                             "http_error",
#                             severity=IssueSeverity.CRITICAL,
#                             is_fixable=True,
#                             data={"entry_id": config_entry.entry_id},
#                             translation_key="http_error",
#                             translation_placeholders={
#                                 "name": name,
#                             },
#                         )
#                 raise http_error
#             except ssl.SSLError as ssl_error:
#                 if ssl_error.errno == 1:
#                     async_create_issue(
#                         self._coordinator.hass,
#                         DOMAIN,
#                         "insecure_ssl",
#                         severity=IssueSeverity.ERROR,
#                         is_fixable=True,
#                         data={"entry_id": config_entry.entry_id},
#                         translation_key="insecure_ssl",
#                         translation_placeholders={
#                             "name": name,
#                         },
#                     )
#                 raise ssl_error
#             except ReolinkResponseError as reoresp:
#                 if reoresp.code in AUTH_ERRORCODES:
#                     await client.disconnect()
#                     raise ConfigEntryAuthFailed() from reoresp
#                 raise reoresp
#             self._authentication_id = client.authentication_id
#         return name

#     async def _primary_queue(self, name: str) -> CommandQueue:
#         client = self._client
#         config_entry = self._coordinator.config_entry
#         commands = client.commands

#         queue = CommandQueue()
#         if self._capabilities is None or not config_entry.options.get(
#             OPT_BATCH_CAPABILITIES, True
#         ):
#             try:
#                 caps = await client.get_capabilities(
#                     config_entry.data.get(CONF_USERNAME, DEFAULT_USERNAME)
#                 )
#             except ReolinkResponseError as reoresp:
#                 if reoresp.code in AUTH_ERRORCODES:
#                     self._authentication_id = 0
#                     await client.disconnect()
#                     queue = await self._primary_queue(name)
#                     return queue
#                 if reoresp.code == ErrorCodes.PROTOCOL_ERROR:
#                     # possible weird encryption bug or other invalid response so we will force a reconnect
#                     self._connection_id = 0
#                     self._authentication_id = 0
#                 raise reoresp

#             if isinstance(self._capabilities, UpdatableCapabilities):
#                 self._capabilities.update(caps)
#             else:
#                 self._capabilities = caps
#         else:
#             caps = self._capabilities
#             queue.add(
#                 commands.create_get_capabilities_request(
#                     config_entry.data.get(CONF_USERNAME, DEFAULT_USERNAME)
#                 )
#             )

#         if self._dst is None or self._time is None or time() - self._timestamp > 3600:
#             queue.add(commands.create_get_time_request())

#         if caps.device.info:
#             if self._device_info is None:
#                 dev_info = await client.get_device_info()
#                 self._device_info = dev_info
#                 name = dev_info.name or name
#                 device = self._get_device_lookup()
#                 device["configuration_url"] = client.base_url
#                 device["default_manufacturer"] = BRAND
#                 device["default_model"] = dev_info.model
#                 device["default_name"] = name
#                 device["hw_version"] = dev_info.version.hardware
#                 device["sw_version"] = dev_info.version.firmware
#                 if dev_info.channels < 2 and self._channel_statuses is None:
#                     self._channel_statuses = {
#                         0: SimpleNamespace(
#                             channel_id=0, name=None, online=True, type=dev_info.type
#                         )
#                     }
#                     self._channel_info[0] = SimpleNamespace(channel_id=0, device=device)
#                 elif dev_info.channels > 1:
#                     entry = (
#                         self._add_or_update_device_entry(device) or self._device_entry
#                     )
#                     if entry is not None:
#                         name = entry.name or name
#             else:
#                 queue.add(commands.create_get_device_info_request())

#             if self._device_info.channels > 1:
#                 queue.add(commands.create_get_channel_status_request())

#         if self._ports is None:
#             queue.add(commands.create_get_ports_request())

#         discovery: DiscoveryDict = config_entry.options.get(OPT_DISCOVERY, None)
#         if (discovery is None or "uuid" not in discovery) and caps.p2p:
#             queue.add(commands.create_get_p2p_request())
#         if (discovery is None or "mac" not in discovery) and caps.local_link:
#             queue.add(commands.create_get_local_link_request())

#         return queue

#     async def _execute_queue(self, queue: Collection[CommandRequest], name: str = None):
#         responses: list[CommandResponse] = []
#         if len(queue) < 1:
#             return responses
#         commands = self._client.commands
#         device: DeviceInfo = None
#         async for response in self._client.batch(queue):
#             if commands.is_error(response):
#                 response.throw()
#             elif commands.is_get_capabilities_response(response):
#                 if isinstance(self._capabilities, UpdatableCapabilities):
#                     self._capabilities.update(response.capabilities)
#                 else:
#                     self._capabilities = response.capabilities
#             elif commands.is_get_device_info_response(response):
#                 if isinstance(self._device_info, UpdatableDeviceInfo):
#                     self._device_info.update(response.info)
#                 else:
#                     self._device_info = response.info
#                 if device is None:
#                     device = self._get_device_lookup()
#                 if device is not None:
#                     device["configuration_url"] = self.client.base_url
#                     device["default_name"] = self._device_info.name or name
#             elif commands.is_get_channel_status_response(response):
#                 if isinstance(self._channel_statuses, UpdatableChannelStatuses):
#                     self._channel_statuses.update(response.channels)
#                 else:
#                     self._channel_statuses = response.channels
#                 via_device = None
#                 config_entry = self._coordinator.config_entry
#                 for channel_status in self._channel_statuses.values():
#                     channel_data = self._channel_info.get(
#                         channel_status.channel_id, None
#                     )
#                     channel_name = (
#                         channel_status.name or f"Channel {channel_status.channel_id}"
#                     )
#                     if config_entry.options.get(OPT_PREFIX_CHANNEL, False):
#                         channel_name = f"{self._device_entry.name} {channel_name}"
#                     if channel_data is None:
#                         if via_device is None:
#                             if unique_id := config_entry.unique_id:
#                                 via_device = (DOMAIN, unique_id)
#                         channel_device = None
#                         if via_device is not None:
#                             channel_device = DeviceInfo(
#                                 identifiers={
#                                     (
#                                         f'{"_".join(via_device)}_channel',
#                                         channel_status.channel_id,
#                                     )
#                                 },
#                                 via_device=via_device,
#                             )
#                         channel_data: ChannelData = SimpleNamespace(
#                             channel_id=channel_status.channel_id,
#                             device=channel_device,
#                         )
#                         self._channel_info[channel_status.channel_id] = channel_data
#                     else:
#                         channel_device = channel_data.device
#                     channel_device["default_name"] = channel_name
#                     channel_device["default_model"] = channel_status.type
#                     channel_device["default_manufacturer"] = ""
#                     registry = async_get_device_registry(self._coordinator.hass)
#                     entry = registry.async_get_device(channel_device)
#                     if entry is not None:
#                         if entry.name_by_user is None and entry.name != channel_name:
#                             entry = (
#                                 registry.async_update_device(
#                                     entry.id, name=channel_name
#                                 )
#                                 or entry
#                             )
#                         channel_device["name"] = entry.name

#             elif commands.is_get_p2p_response(response):
#                 if device is None:
#                     device = self._get_device_lookup()
#                 if device is not None:
#                     ids = device.setdefault("identifiers", set())
#                     ids.add((f"{DOMAIN}_uuid", response.info.uid))
#             elif commands.is_get_local_link_response(response):
#                 if device is None:
#                     device = self._get_device_lookup()
#                 if device is not None:
#                     cons = device.setdefault("connections", set())
#                     cons.add((CONNECTION_NETWORK_MAC, response.local_link.mac))
#             elif commands.is_get_ports_response(response):
#                 self._ports = response.ports
#             elif commands.is_get_time_response(response):
#                 self._dst = response.dst
#                 self._time = response.time
#                 self._time_diff = dt.utcnow() - dt.as_utc(response.to_datetime())
#             responses.append(response)

#         if device is not None and self._device_entry is not None:
#             self._add_or_update_device_entry(device)

#         return responses

#     def _get_requests(self, coordinator: DataUpdateCoordinator):
#         if coordinator is None:
#             return
#         commands = self.client.commands
#         for context in coordinator.async_contexts():
#             if isinstance(context, Iterable):
#                 for request in filter(commands.is_request, context):
#                     yield request
#                 if isinstance(context, MutableSet):
#                     context.clear()
#                 elif isinstance(context, MutableSequence):
#                     del context[:]

#     async def _update_method(self):
#         name = await self._ensure_connection()
#         queue = await self._primary_queue(name)
#         for request in self._get_requests(self._coordinator):
#             if request not in queue:
#                 queue.add(request)
#         return (*await self._execute_queue(queue),)

#     async def _hispeed_update_method(self):
#         queue = []
#         queue.extend(self._get_requests(self._hispeed_coordinator))
#         return (*await self._execute_queue(queue),)

#     @property
#     def coordinator(self):
#         """Data Update Coordinator"""

#         return self._coordinator

#     @property
#     def hispeed_coordinator(self):
#         """Hi-Speed Data Update Coordinator"""
#         return self._hispeed_coordinator

#     async def async_initialize(self, hass: HomeAssistant, logger: logging.Logger):
#         """Initialize API"""

#         config_entry = None
#         if self._coordinator is None:
#             coordinator = DataUpdateCoordinator(
#                 hass,
#                 logger,
#                 name="",
#                 update_method=self._update_method,
#             )
#             self._coordinator = coordinator
#             config_entry = coordinator.config_entry
#             coordinator.name = f"{config_entry.title} Update Coordinator"
#             coordinator.update_interval = async_get_poll_interval(config_entry.options)

#         if self._hispeed_coordinator is None:
#             coordinator = DataUpdateCoordinator(
#                 hass,
#                 logger,
#                 name="",
#                 update_method=self._hispeed_update_method,
#             )
#             self._hispeed_coordinator = coordinator
#             if config_entry is None:
#                 config_entry = coordinator.config_entry
#             coordinator.name = f"{config_entry.title} High Speed Coordinator"
#             coordinator.update_interval = async_get_hispeed_poll_interval(
#                 config_entry.options
#             )

#         if config_entry is not None:
#             await self._coordinator.async_config_entry_first_refresh()
