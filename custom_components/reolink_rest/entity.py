"""Reolink Entities"""

import ssl
from time import time
from types import SimpleNamespace

from typing import TYPE_CHECKING, Callable, Mapping, Protocol, cast
import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant import config_entries
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    CoordinatorEntity,
)
from homeassistant.helpers import device_registry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.util.dt import utcnow, as_utc


from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
    CONF_TIMEOUT,
    CONF_USERNAME,
    CONF_PASSWORD,
)

from async_reolink.api.errors import ReolinkResponseError, ErrorCodes
from async_reolink.api.connection.typing import CommandRequest
from async_reolink.api.const import DEFAULT_USERNAME, DEFAULT_PASSWORD, DEFAULT_TIMEOUT
from async_reolink.rest.client import Client as ReolinkClient
from async_reolink.rest.connection.typing import Encryption

from async_reolink.rest.system.capabilities import Capabilities as UpdatableCapabilities
from async_reolink.rest.system.models import DeviceInfo as UpdatableDeviceInfo
from async_reolink.rest.network.models import (
    ChannelStatuses as UpdatableChannelStatuses,
)

from async_reolink.rest.errors import (
    AUTH_ERRORCODES,
)

from async_reolink.api.system.typing import (
    DaylightSavingsTimeInfo,
    TimeInfo,
    DeviceInfo as ReolinkDeviceInfo,
)
from async_reolink.api.network.typing import ChannelStatus

from .discovery import DiscoveryDict

from .typing import (
    DomainData,
    EntityData as BaseEntityData,
    RequestQueue,
    ChannelData,
)

from .const import (
    CONF_USE_HTTPS,
    DEFAULT_PORT,
    DOMAIN,
    OPT_CHANNELS,
    OPT_DISCOVERY,
    OPT_PREFIX_CHANNEL,
    OPT_SSL,
    SSLMode,
)


class EntityData(BaseEntityData, RequestQueue, Protocol):
    """Entity Data"""

    connection_id: int
    authentication_id: int

    device_info: ReolinkDeviceInfo
    dst: DaylightSavingsTimeInfo
    time: TimeInfo
    channel_statuses: Mapping[int, ChannelStatus]


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


def _create_device_info(
    device: device_registry.DeviceEntry, registry: device_registry.DeviceRegistry
):
    if device.via_device_id is not None:
        via_device = registry.async_get(device.via_device_id).identifiers
    else:
        via_device = None
    return DeviceInfo(
        configuration_url=device.configuration_url,
        connections=device.connections,
        manufacturer=device.manufacturer,
        entry_type=device.entry_type,
        hw_version=device.hw_version,
        identifiers=device.identifiers,
        model=device.model,
        name=device.name,
        suggested_area=device.suggested_area,
        sw_version=device.sw_version,
        via_device=via_device,
    )


def _create_coordiator_data(**kwargs):
    queue = []
    responses = []

    def _append(request, force_unique=False):
        if not force_unique or request not in queue:
            queue.append(request)

            def remove():
                queue.remove(request)

            return remove

    def _index(request):
        if callable(request):
            return next((i for i, item in queue if request(item)))
        return queue.index(request)

    kwargs["append"] = _append
    kwargs["index"] = _index
    kwargs["responses"] = responses

    return (queue, responses, SimpleNamespace(**kwargs))


def create_low_frequency_data_update(
    hass: HomeAssistant, entry: config_entries.ConfigEntry
):
    """Create low frequency data update routine"""
    batch_capabilities = True
    data: EntityData
    (queue, responses, data) = _create_coordiator_data(
        ai=None,
        authentication_id=0,
        capabilities=None,
        channel_statuses=None,
        channels={},
        connection_id=0,
        device=None,
        device_info=None,
        dst=None,
        ports=None,
        queue=[],
        responses=[],
        time=None,
    )
    timestamp = time()

    device: device_registry.DeviceEntry = None
    channel_devices: dict[int, device_registry.DeviceEntry] = {}

    domain_data: DomainData = hass.data[DOMAIN]
    entry_data = domain_data[entry.entry_id]

    ssl_mode = SSLMode(entry.options.get(OPT_SSL, SSLMode.NORMAL))
    if ssl_mode == SSLMode.WEAK:
        ssl_mode = weak_ssl_context
    elif ssl_mode == SSLMode.INSECURE:
        ssl_mode = insecure_ssl_context
    else:
        ssl_mode = None

    client = entry_data.client
    if client is None:
        client = ReolinkClient(ssl=ssl_mode)
        entry_data.client = client

    discovery: DiscoveryDict = entry.options.get(OPT_DISCOVERY, None)

    def entry_update(hass: HomeAssistant, entry: config_entries.ConfigEntry):
        nonlocal discovery, ssl_mode
        ssl_mode = SSLMode(entry.options.get(OPT_SSL, SSLMode.NORMAL))
        discovery = entry.options.get(OPT_DISCOVERY, None)
        if entry_data.coordinator.last_exception is not None:
            hass.create_task(entry_data.coordinator.async_request_refresh())

    entry.add_update_listener(entry_update)

    async def data_update():
        nonlocal timestamp, device

        if not client.is_connected or data.connection_id != client.connection_id:
            host: str = entry.data.get(
                CONF_HOST, discovery.get("ip", None) if discovery is not None else None
            )
            if entry.data.get(CONF_USE_HTTPS, False):
                encryption = Encryption.HTTPS
            else:
                encryption = Encryption.NONE

            if not host:
                raise ConfigEntryNotReady(
                    "No host configured, and none discovered (was device lost?)"
                )

            await client.connect(
                host,
                entry.data.get(CONF_PORT, DEFAULT_PORT),
                entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
                encryption=encryption,
            )
            if data.connection_id != client.connection_id:
                data.connection_id = client.connection_id
                data.authentication_id = 0

        if (
            not client.is_authenticated
            or data.authentication_id != client.authentication_id
        ):
            try:
                if not await client.login(
                    entry.data.get(CONF_USERNAME, DEFAULT_USERNAME),
                    entry.data.get(CONF_PASSWORD, DEFAULT_PASSWORD),
                ):
                    data.authentication_id = 0
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
                            data={"entry_id": entry.entry_id},
                            translation_key="from_ssl_redirect",
                            translation_placeholders={
                                "name": device.name
                                if device is not None
                                else client.hostname,
                            },
                        )
                    elif not client.secured and location.startswith("https://"):
                        async_create_issue(
                            hass,
                            DOMAIN,
                            "to_ssl_redirect",
                            severity=IssueSeverity.ERROR,
                            is_fixable=True,
                            data={"entry_id": entry.entry_id},
                            translation_key="from_ssl_redirect",
                            translation_placeholders={
                                "name": device.name
                                if device is not None
                                else client.hostname,
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
                            data={"entry_id": entry.entry_id},
                            translation_key="from_ssl_redirect",
                            translation_placeholders={
                                "name": device.name
                                if device is not None
                                else client.hostname,
                            },
                        )
                    else:
                        async_create_issue(
                            hass,
                            DOMAIN,
                            "http_error",
                            severity=IssueSeverity.CRITICAL,
                            is_fixable=True,
                            data={"entry_id": entry.entry_id},
                            translation_key="http_error",
                            translation_placeholders={
                                "name": device.name
                                if device is not None
                                else client.hostname,
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
                        data={"entry_id": entry.entry_id},
                        translation_key="insecure_ssl",
                        translation_placeholders={
                            "name": device.name
                            if device is not None
                            else client.hostname,
                        },
                    )
                raise ssl_error
            except ReolinkResponseError as reoresp:
                if reoresp.code in AUTH_ERRORCODES:
                    await client.disconnect()
                    raise ConfigEntryAuthFailed() from reoresp
                raise reoresp
            data.authentication_id = client.authentication_id

        commands = client.commands

        caps: UpdatableCapabilities = data.capabilities
        if caps is None or not batch_capabilities:
            try:
                new_caps = await client.get_capabilities(
                    entry.data.get(CONF_USERNAME, None)
                )
            except ReolinkResponseError as reoresp:
                if reoresp.code in AUTH_ERRORCODES:
                    data.authentication_id = 0
                    await client.disconnect()
                    await data_update()
                    return data
                if reoresp.code == ErrorCodes.PROTOCOL_ERROR:
                    # possible weird encryption bug or other invalid response so we will force a reconnect
                    data.connection_id = 0
                    data.authentication_id = 0
                raise reoresp

            if caps is None:
                caps = new_caps
                data.capabilities = caps
            else:
                caps.update(new_caps)
        else:
            queue.append(
                commands.create_get_capabilities_request(
                    entry.data.get(CONF_USERNAME, None)
                )
            )

        if data.dst is None or data.time is None or time() - timestamp > 3600:
            queue.append(commands.create_get_time_request())

        dev_info: UpdatableDeviceInfo = data.device_info
        if caps.device.info:
            if dev_info is None:
                dev_info = await client.get_device_info()
                data.device_info = dev_info
            else:
                queue.append(commands.create_get_device_info_request())

            if dev_info.channels > 1:
                queue.append(commands.create_get_channel_status_request())
        channel_stats: UpdatableChannelStatuses = data.channel_statuses

        if data.ports is None:
            queue.append(commands.create_get_ports_request())

        mac = None
        uuid = None
        if data.device is None:
            mac: str = discovery.get("mac", None) if discovery is not None else None
            uuid: str = discovery.get("uuid", None) if discovery is not None else None

            if uuid is None:
                queue.append(commands.create_get_p2p_request())
                if mac is None:
                    queue.append(commands.create_get_local_link_request())

        lqueue = queue.copy()
        queue.clear()
        responses.clear()
        updated_chan_stats = False
        if len(lqueue) > 0:
            async for response in client.batch(lqueue):
                if commands.is_error(response):
                    response.throw()
                elif commands.is_get_capabilities_response(response):
                    caps.update(response.capabilities)
                elif commands.is_get_device_info_response(response):
                    dev_info.update(response.info)
                elif commands.is_get_channel_status_response(response):
                    updated_chan_stats = True
                    if channel_stats is None:
                        channel_stats = response.channels
                        data.channel_statuses = channel_stats
                    else:
                        channel_stats.update(response.channels)
                elif commands.is_get_p2p_response(response):
                    uuid = response.info.uid
                elif commands.is_get_local_link_response(response):
                    mac = response.local_link.mac
                elif commands.is_get_ports_response(response):
                    data.ports = response.ports
                elif commands.is_get_time_response(response):
                    data.dst = response.dst
                    data.time = response.time
                    data.time_diff = utcnow() - as_utc(response.to_datetime())
                else:
                    responses.append(response)

        if device is None:
            registry = device_registry.async_get(hass)
            device = registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                default_manufacturer="Reolink",
                default_name=dev_info.name,
                identifiers={(DOMAIN, uuid)} if uuid else None,
                connections={(device_registry.CONNECTION_NETWORK_MAC, mac)}
                if mac
                else None,
                sw_version=dev_info.version.firmware,
                hw_version=dev_info.version.hardware,
                default_model=dev_info.model,
                configuration_url=client.base_url,
            )
            data.device = _create_device_info(device, registry)
        else:
            registry = device_registry.async_get(hass)
            updated_device = registry.async_update_device(
                device.id,
                name=dev_info.name,
                sw_version=dev_info.version.firmware,
                hw_version=dev_info.version.hardware,
            )
            if updated_device and updated_device != device:
                device = updated_device
                if data.device is None:
                    data.device = _create_device_info(updated_device, registry)
                else:
                    data.device.update(_create_device_info(updated_device, registry))

        wanted_channels: list[int] = entry.options.get(OPT_CHANNELS, None)
        if wanted_channels is not None and len(wanted_channels) == 0:
            wanted_channels = None
        if updated_chan_stats or (dev_info.channels == 1 and len(data.channels) == 0):
            for channel in range(dev_info.channels):
                wanted = (
                    channel in wanted_channels if wanted_channels is not None else True
                )
                channel_data = data.channels.get(channel, None)
                if channel_data is None:
                    channel_data: ChannelData = SimpleNamespace(
                        device=None, offline=True
                    )
                    data.channels[channel] = channel_data
                status = (
                    channel_stats.get(channel, None)
                    if channel_stats is not None
                    else None
                )
                if status is None:
                    if channel == 0:
                        channel_data.offline = False
                        channel_data.device = data.device
                        continue
                    entry_data.coordinator.logger.warning(
                        "Device %s did not give a status for channel %s, this could be potential problem",
                        device.name,
                        channel,
                    )
                    channel_data.offline = True
                    continue
                channel_data.offline = status.online
                channel_device = channel_devices.get(status.channel_id, None)
                name = status.name or f"Channel {status.channel_id}"
                if entry.options.get(OPT_PREFIX_CHANNEL, False):
                    name = f"{device.name} {name}"
                disabled_by = None
                if channel_device is not None and channel_device.disabled:
                    disabled_by = channel_device.disabled_by
                elif not wanted:
                    disabled_by = device_registry.DeviceEntryDisabler.CONFIG_ENTRY
                elif not status.online:
                    disabled_by = device_registry.DeviceEntryDisabler.INTEGRATION
                if channel_device is None:
                    if not registry:
                        registry = device_registry.async_get(hass)
                    channel_device = registry.async_get_or_create(
                        config_entry_id=entry.entry_id,
                        via_device=device.identifiers.copy().pop(),
                        default_model=f"{status.type or ''} Channel {status.channel_id}",
                        default_name=name,
                        identifiers={(DOMAIN, f"{device.id}-{status.channel_id}")},
                        default_manufacturer=device.manufacturer,
                        disabled_by=disabled_by,
                    )
                    channel_devices[status.channel_id] = channel_device
                    channel_data.device = _create_device_info(channel_device, registry)
                else:
                    if not registry:
                        registry = device_registry.async_get(hass)
                    updated_device = registry.async_update_device(
                        channel_device.id, name=name, disabled_by=disabled_by
                    )
                    if updated_device and updated_device != channel_device:
                        channel_devices[status.channel_id] = updated_device
                        if channel_data.device is None:
                            channel_data.device = _create_device_info(
                                updated_device, registry
                            )
                        else:
                            channel_data.device.update(
                                _create_device_info(updated_device, registry)
                            )

        if (uuid or mac) and OPT_DISCOVERY not in entry.options:
            options = entry.options.copy()
            options[OPT_DISCOVERY] = {}
            if mac:
                options[OPT_DISCOVERY]["mac"] = mac
            if uuid:
                options[OPT_DISCOVERY]["uuid"] = uuid
            hass.config_entries.async_update_entry(entry, options=options)

        return data

    return data_update


def make_request(coordinator: DataUpdateCoordinator, request: CommandRequest):
    """Add a request to a coordinator queue"""
    queue: RequestQueue = coordinator.data
    return queue.push(request)


def create_high_frequency_data_update(
    hass: HomeAssistant, entry: config_entries.ConfigEntry
):
    """Create high frequency data update routine"""

    data: any
    (queue, responses, data) = _create_coordiator_data()

    domain_data: DomainData = hass.data[DOMAIN]
    entry_data = domain_data[entry.entry_id]

    client = entry_data.client

    if not TYPE_CHECKING:
        requests = cast(data, RequestQueue)

        def push_request(_self, request: CommandRequest):
            queue.append(request)

            def remove():
                queue.remove(request)

            return remove

        def find_request(_self, request: CommandRequest):
            return queue.index(request)

        requests.append = push_request
        requests.index = find_request
        requests.responses = responses

    async def data_update():
        lqueue = queue.copy()
        queue.clear()
        responses.clear()

        if len(lqueue) > 0:
            async for response in client.batch(lqueue):
                responses.append(response)

        return data

    return data_update


# def _get_channels(
#     abilities: system.Capabilities, options: Mapping[str, any] | None = None
# ):
#     channels = set(range(len(abilities.channels)))
#     if options:
#         return set(options.get(OPT_CHANNELS, channels))
#     return channels


# class _Motion(Motion):
#     def __init__(self) -> None:
#         super().__init__()
#         self._detected = False
#         self._ai = None

#     @property
#     def detected(self):
#         return self._detected

#     @detected.setter
#     def detected(self, value: bool):
#         self._detected = bool(value)

#     def __getitem__(self, __k: AITypes):
#         return (
#             _alarm.state
#             if self._ai is not None and (_alarm := self._ai.get(__k, None)) is not None
#             else False
#         )

#     def __iter__(self):
#         return self._ai.__iter__()

#     def __len__(self):
#         return self._ai.__len__() if self._ai is not None else 0

#     def __repr__(self) -> str:
#         _ai = ""
#         if self._ai is not None:
#             for key, value in self._ai.items():
#                 _ai += f"{key}:{value},"

#         return f"<{self.__class__.__name__}: detected={self._detected}, ai=<{_ai}>>"

#     def update_ai(self, state: ai.models.State):
#         if state is not None and not isinstance(state, ai.models.State):
#             raise TypeError("Invalid value")
#         self._ai = state


# class _PTZ(PTZ):
#     def __init__(self) -> None:
#         super().__init__()

#         self._zf = None
#         self._zf_range = None
#         self._pan = 0
#         self._tilt = 0
#         self._autofocus = False
#         self._presets = None
#         self._patrol = None
#         self._tattern = None

#     @property
#     def pan(self):
#         return self._pan

#     @property
#     def tilt(self):
#         return self._tilt

#     @property
#     def zoom(self):
#         return self._zf.zoom if self._zf is not None else 0

#     @property
#     def zoom_range(self):
#         return self._zf_range.zoom if self._zf_range is not None else None

#     @property
#     def focus(self):
#         return self._zf.focus if self._zf is not None else 0

#     @property
#     def focus_range(self):
#         return self._zf_range.focus if self._zf_range is not None else None

#     @property
#     def autofocus(self):
#         return self._autofocus

#     @autofocus.setter
#     def autofocus(self, value):
#         self._autofocus = value

#     @property
#     def presets(self):
#         return self._presets

#     @property
#     def patrol(self):
#         return self._patrol

#     @property
#     def tattern(self):
#         return self._tattern

#     def update_zf(self, value: ptz.ZoomFocus):
#         """update zoom/focus"""
#         if value is not None and not isinstance(value, ptz.ZoomFocus):
#             raise TypeError("Invalid value")
#         self._zf = value

#     def update_zf_range(self, value: ptz._ZoomFocusRange | None):
#         if value is not None and not isinstance(value, ptz._ZoomFocusRange):
#             raise TypeError("Invalid value")
#         self._zf_range = value

#     def update_presets(self, value: Mapping[int, ptz.Preset]):
#         """update presets"""
#         if value is not None and not isinstance(value, Mapping):
#             raise TypeError("Invalid value")
#         self._presets = value

#     def update_patrols(self, value: Mapping[int, ptz.Patrol]):
#         """update presets"""
#         if value is not None and not isinstance(value, Mapping):
#             raise TypeError("Invalid value")
#         self._patrol = value

#     def update_tracks(self, value: Mapping[int, ptz.Track]):
#         """update presets"""
#         if value is not None and not isinstance(value, Mapping[int, ptz.Track]):
#             raise TypeError("Invalid value")
#         self._tattern = value


# class ReolinkEntityData:
#     """Reolink Entity Data and API"""

#     def __init__(self, hass: HomeAssistant, config_entry: config_entries.ConfigEntry):
#         self.hass = hass
#         self._init = True
#         self.config_entry = config_entry
#         self.client = ReolinkClient()
#         self.device: device_registry.DeviceEntry = None
#         self.time_difference = timedelta()
#         self.abilities = None
#         self.device_info = None
#         self.channels: dict[int, DeviceInfo] = {}
#         self.ports = None
#         self._batch_ability = True
#         self._connection_id = 0
#         self._authentication_id = 0
#         self.updated_motion: set[int] = set()
#         self._update_motion: set[int] = set()
#         self.ai = None
#         self.motion: defaultdict[int, _Motion] = defaultdict(_Motion)
#         self.updated_ptz: set[int] = set()
#         self._update_ptz: set[int] = set()
#         self.ptz: defaultdict[int, _PTZ] = defaultdict(_PTZ)
#         discovery: dict = config_entry.options.get(OPT_DISCOVERY, None)
#         if discovery is not None and (
#             "name" in discovery or "uuid" in discovery or "mac" in discovery
#         ):
#             self._name: str = discovery.get(
#                 "name", discovery.get("uuid", discovery["mac"])
#             )
#         else:
#             self._name: str = config_entry.data[CONF_HOST]

#     @property
#     def name(self):
#         """short name"""
#         return self._name

#     def _processes_responses(self, response):
#         if isinstance(response, system.GetAbilitiesResponse):
#             if self.abilities is not None:
#                 self.abilities.update(response.capabilities)
#             else:
#                 self.abilities = response.capabilities
#             return True
#         if isinstance(response, system.GetTimeResponse):
#             result = response
#             time = result.to_datetime()
#             self.time_difference = dt.utcnow() - dt.as_utc(time)
#             return True
#         if isinstance(response, network.GetNetworkPortsResponse):
#             self.ports = response.ports
#             return True
#         if isinstance(response, system.GetDeviceInfoResponse):
#             if self.device_info is not None:
#                 self.device_info.update(response.info)
#             else:
#                 self.device_info = response.info
#             return True
#         if isinstance(response, ai.GetAiConfigResponse):
#             if self.ai is not None:
#                 self.ai.update(response.config)
#             else:
#                 self.ai = response.config
#             return True
#         return False

#     async def _execute_commands(
#         self, commands: list, /, command_channel: dict[int, int] = None
#     ):
#         idx = 0
#         channels = None
#         mac = None
#         uuid = None
#         try:
#             async for response in self.client.batch(commands):
#                 if isinstance(response, network.GetChannelStatusResponse):
#                     channels = response.channels
#                 elif isinstance(response, network.GetLocalLinkResponse):
#                     _mac = response.local_link.mac
#                     if not mac:
#                         mac = _mac
#                     elif mac.lower() != _mac.lower():
#                         raise UpdateFailed(
#                             "Found different mac so possible wrong device"
#                         )
#                 elif isinstance(response, network.GetP2PResponse):
#                     _uuid = response.info.uid
#                     if not uuid:
#                         uuid = _uuid
#                     elif uuid.lower() != _uuid.lower():
#                         raise UpdateFailed(
#                             "Did not find the same device as last time at this address!"
#                         )
#                 else:
#                     _ = (
#                         self._processes_responses(response)
#                         or self._process_motion_responses(
#                             response, command_index=idx, command_channel=command_channel
#                         )
#                         or self._process_ptz_responses(
#                             response, command_index=idx, command_channel=command_channel
#                         )
#                     )
#                 idx += 1
#         except CONNECTION_ERRORS:
#             self._connection_id = 0
#             raise
#         # except RESPONSE_ERRORS:
#         #    raise
#         except ReolinkResponseError as reoresp:
#             # do not trap auth errors, instead we will just fail as usual
#             # auth errors at this point could be expired tokens
#             # so we do not want to assume password issues
#             if reoresp.code in AUTH_ERRORCODES:
#                 await self.client.disconnect()
#                 return False
#             if reoresp.code == ErrorCodes.READ_FAILED and True in (
#                 True
#                 for command in commands
#                 if isinstance(command, system.GetAbilitiesRequest)
#             ):
#                 # some cameras do not like to batch in the ability command
#                 # we will note this and no do that anymore
#
#                 self._batch_ability = False
#                 return False
#             raise reoresp
#         return (channels, mac, uuid)

#     async def async_update(self):
#         """update"""

#         if (
#             not self.client.is_connected
#             or self._connection_id != self.client.connection_id
#         ):
#             host: str = self.config_entry.data.get(CONF_HOST, None)
#             discovery: dict = None
#             if (
#                 host is None
#                 and (discovery := self.config_entry.options.get(OPT_DISCOVERY, None))
#                 and "ip" in discovery
#             ):
#                 host = discovery["ip"]
#             if self.config_entry.data.get(CONF_USE_HTTPS, False):
#                 encryption = Encryption.HTTPS
#             else:
#                 encryption = Encryption.NONE

#             if not host:
#                 raise ConfigEntryNotReady(
#                     "No host configured, and none discovered (was device lost?)"
#                 )

#             await self.client.connect(
#                 host,
#                 self.config_entry.data.get(CONF_PORT, DEFAULT_PORT),
#                 self.config_entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
#                 encryption=encryption,
#             )
#             if self._connection_id != self.client.connection_id:
#                 self._connection_id = self.client.connection_id
#                 self._authentication_id = 0

#         if (
#             not self.client.is_authenticated
#             or self._authentication_id != self.client.authentication_id
#         ):
#             try:
#                 if not await self.client.login(
#                     self.config_entry.data.get(CONF_USERNAME, DEFAULT_USERNAME),
#                     self.config_entry.data.get(CONF_PASSWORD, DEFAULT_PASSWORD),
#                 ):
#                     self._authentication_id = 0
#                     await self.client.disconnect()
#                     raise ConfigEntryAuthFailed()
#             except ReolinkResponseError as reoresp:
#                 if reoresp.code in AUTH_ERRORCODES:
#                     await self.client.disconnect()
#                     raise ConfigEntryAuthFailed()
#                 raise reoresp
#             self._authentication_id = self.client.authentication_id

#         commands = []
#         if self.abilities is None or not self._batch_ability:
#             try:
#                 self.abilities = await self.client.get_ability(
#                     self.config_entry.data.get(CONF_USERNAME, None)
#                 )
#             except ReolinkResponseError as reoresp:
#                 if reoresp.code in AUTH_ERRORCODES:
#                     self._authentication_id = 0
#                     await self.client.disconnect()
#                     # this could be because of a reboot or token expiration
#                     await self.async_update()
#                     return self
#                 if reoresp.code == ErrorCodes.PROTOCOL_ERROR:
#                     # possible weird encryption bug or other invalid response so we will force a reconnect
#                     self._connection_id = 0
#                     self._authentication_id = 0
#                 raise reoresp
#         else:
#             commands.append(
#                 system.GetAbilitiesRequest(
#                     self.config_entry.data.get(CONF_USERNAME, None)
#                 )
#             )

#         commands.append(system.GetTimeRequest())
#         abilities = self.abilities

#         channels = None
#         commands.append(network.GetNetworkPortsRequest())
#         mac = None
#         uuid = None
#         if abilities.device.info:
#             commands.append(system.GetDeviceInfoRequest())
#             if self.device_info and self.device_info.channels > 1:
#                 commands.append(network.GetChannelStatusRequest())
#         if self.device is None:
#             discovery: dict = self.config_entry.options.get(OPT_DISCOVERY, None)
#             mac = discovery["mac"] if discovery and "mac" in discovery else None
#             if abilities.local_link:
#                 commands.append(network.GetLocalLinkRequest())
#             uuid = discovery["uuid"] if discovery and "uuid" in discovery else None
#             if abilities.p2p:
#                 commands.append(network.GetP2PRequest())
#         (_, command_channel) = self._create_motion_commands(commands)
#         (_, command_channel) = self._create_ptz_commands(
#             commands, command_channel=command_channel
#         )
#         for i, ability in abilities.channels.items():
#             if ability.supports.ai.detect_config:
#                 commands.append(ai.GetAiConfigRequest(i))

#         self._update_motion.clear()
#         self.updated_motion.clear()
#         self._update_ptz.clear()
#         self.updated_ptz.clear()
#         result = await self._execute_commands(commands, command_channel=command_channel)
#         if not result:
#             await self.async_update()
#             return self

#         channels, mac, uuid = result

#         if self.device_info and self.device_info.channels > 1 and channels is None:
#             channels = await self.client.get_channel_status()

#         # pylint: disable=unsubscriptable-object
#         if self.device is None:
#             registry = device_registry.async_get(self.hass)
#             self.device = registry.async_get_or_create(
#                 config_entry_id=self.config_entry.entry_id,
#                 default_manufacturer="Reolink",
#                 default_name=self.device_info.name,
#                 identifiers={(DOMAIN, uuid)} if uuid else None,
#                 connections={(device_registry.CONNECTION_NETWORK_MAC, mac)}
#                 if mac
#                 else None,
#                 sw_version=self.device_info.version.firmware,
#                 hw_version=self.device_info.version.hardware,
#                 default_model=self.device_info.model,
#                 configuration_url=self.client.base_url,
#             )
#             if len(abilities.channels) < 2:
#                 self.channels[0] = _dev_to_info(self.device)
#         else:
#             registry = device_registry.async_get(self.hass)
#             updated_device = registry.async_update_device(
#                 self.device.id,
#                 name=self.device_info.name,
#                 sw_version=self.device_info.version.firmware,
#                 hw_version=self.device_info.version.hardware,
#             )
#             if updated_device and updated_device != self.device:
#                 self.device = updated_device
#                 if len(abilities.channels) < 2:
#                     self.channels[0] = _dev_to_info(updated_device)

#         if len(abilities.channels) > 1 and channels:
#             for i in self.config_entry.options.get(
#                 OPT_CHANNELS, list(range(len(abilities.channels)))
#             ):
#                 status = channels.get(i, None)
#                 if status is None:
#                     continue
#

#                 name = status.name or f"Channel {i}"
#                 if self.config_entry.options.get(OPT_PREFIX_CHANNEL, False):
#                     name = f"{self.device.name} {name}"
#                 channel_device = self.channels.get(status.channel_id, None)
#                 if channel_device is None:
#                     if not registry:
#                         registry = device_registry.async_get(self.hass)
#                     channel_device = registry.async_get_or_create(
#                         config_entry_id=self.config_entry.entry_id,
#                         via_device=self.device.identifiers.copy().pop(),
#                         default_model=f"{status.type or ''} Channel {status.channel_id}",
#                         default_name=name,
#                         identifiers={(DOMAIN, f"{self.device.id}-{status.channel_id}")},
#                         default_manufacturer=self.device.manufacturer,
#                     )
#                     self.channels[status.channel_id] = _dev_to_info(channel_device)
#                 else:
#                     if not registry:
#                         registry = device_registry.async_get(self.hass)
#                     channel_device = registry.async_get_device(
#                         self.channels[status.channel_id]["identifiers"]
#                     )
#                     updated_device = registry.async_update_device(
#                         channel_device.id, name=name
#                     )
#                     if updated_device and updated_device != channel_device:
#                         self.channels[status.channel_id] = _dev_to_info(updated_device)

#         if (uuid or mac) and OPT_DISCOVERY not in self.config_entry.options:
#             options = self.config_entry.options.copy()
#             options[OPT_DISCOVERY] = {}
#             if mac:
#                 options[OPT_DISCOVERY]["mac"] = mac
#             if uuid:
#                 options[OPT_DISCOVERY]["uuid"] = uuid
#             self.hass.config_entries.async_update_entry(
#                 self.config_entry, options=options
#             )

#         self._init = False
#         return self

#     def _create_motion_commands(
#         self,
#         /,
#         commands: list = None,
#         command_channel: dict[int, int] = None,
#         channels: Sequence[int] = None,
#     ):
#         abilities = self.abilities
#         if commands is None:
#             commands = []
#         if command_channel is None:
#             command_channel = {}
#         if len(abilities.channels) == 1:
#             channels = set({0})
#         elif channels is None or len(channels) == 0:
#             channels = _get_channels(self.abilities, self.config_entry.options)

#         for i in channels:
#             # the MD command does not return the channel it replies to
#             command_channel[len(commands)] = i
#             commands.append(alarm.GetMotionStateRequest(i))
#             ability = abilities.channels[i]
#             if (
#                 ability.supports.ai.animal
#                 or ability.supports.ai.face
#                 or ability.supports.ai.people
#                 or ability.supports.ai.pet
#                 or ability.supports.ai.vehicle
#             ):
#                 commands.append(ai.GetAiStateRequest(i))

#         return (commands, command_channel)

#     def _process_motion_responses(
#         self, response, /, command_index: int, command_channel: dict[int, int]
#     ):
#         if isinstance(response, alarm.GetMotionStateResponse):
#             state = response.state
#             channel = command_channel[command_index]
#             self.updated_motion.add(channel)
#             self.motion[channel].detected = state
#             return True
#         if isinstance(response, ai.GetAiStateResponse):
#             state = response.state
#             channel = response.channel_id
#             self.updated_motion.add(channel)
#             self.motion[channel].update_ai(state)
#             return True
#         return False

#     def async_request_motion_update(self, channel: int = 0):
#         """Request update of PTZ data for channel"""
#         self._update_motion.add(channel)

#     async def async_update_motion_data(self):
#         """update motion only"""

#         (commands, command_channel) = self._create_motion_commands(
#             channels=self._update_motion,
#         )
#         self.updated_motion.clear()
#         self._update_motion.clear()
#         await self._execute_commands(commands, command_channel=command_channel)

#         return self

#     def _create_ptz_commands(
#         self,
#         /,
#         commands: list = None,
#         command_channel: dict[int, int] = None,
#         channels: set[int] = None,
#     ):
#         abilities = self.abilities
#         if commands is None:
#             commands = []
#         if command_channel is None:
#             command_channel = {}
#         if len(abilities.channels) == 1:
#             channels = set({0})
#         elif channels is None or len(channels) == 0:
#             channels = _get_channels(self.abilities, self.config_entry.options)

#         _r_type = (
#             CommandResponseTypes.DETAILED
#             if self._init
#             else CommandResponseTypes.VALUE_ONLY
#         )

#         for i in channels:
#             ability = abilities.channels[i]
#             if ability.ptz.control in (PTZControl.ZOOM, PTZControl.ZOOM_FOCUS):
#                 commands.append(ptz.GetZoomFocusRequest(i, _r_type))
#             if ability.ptz.type == PTZType.AF:
#                 command_channel[len(commands)] = i
#                 commands.append(ptz.GetAutoFocusRequest(i))
#             if ability.ptz.preset:
#                 commands.append(ptz.GetPresetRequest(i, _r_type))
#             if ability.ptz.patrol:
#                 commands.append(ptz.GetPatrolRequest(i, _r_type))
#             if ability.ptz.tattern:
#                 commands.append(ptz.GetTatternRequest(i, _r_type))
#         return (commands, command_channel)

#     def _process_ptz_responses(
#         self, response, /, command_index: int, command_channel: dict[int, int]
#     ):
#         if isinstance(response, ptz.GetAutoFocusResponse):
#             channel = command_channel[command_index]
#             self.updated_ptz.add(channel)
#             self.ptz[channel].autofocus = not response.disabled
#             return True
#         if isinstance(response, ptz.GetZoomFocusResponse):
#             channel = response.channel_id
#             self.updated_ptz.add(channel)
#             self.ptz[channel].update_zf(response.state)
#             if response.is_detailed:
#                 self.ptz[channel].update_zf_range(response.state_range)

#             return True
#         if isinstance(response, ptz.GetPresetResponse):
#             channel = response.channel_id
#             self.updated_ptz.add(channel)
#             self.ptz[channel].update_presets(response.presets)
#             return True
#         if isinstance(response, ptz.GetPatrolResponse):
#             channel = response.channel_id
#             self.updated_ptz.add(channel)
#             self.ptz[channel].update_patrols(response.patrols)
#             return True
#         if isinstance(response, ptz.GetTatternResponse):
#             channel = response.channel_id
#             self.updated_ptz.add(channel)
#             self.ptz[channel].update_tracks(response.tracks)
#             return True
#         return False

#     def async_request_ptz_update(self, channel: int = 0):
#         """Request update of PTZ data for channel"""
#         self._update_ptz.add(channel)

#     async def async_update_ptz_data(self):
#         """update ptz only"""
#         (commands, command_channel) = self._create_ptz_commands(
#             channels=self._update_ptz,
#         )
#         self.updated_ptz.clear()
#         self._update_ptz.clear()
#         await self._execute_commands(commands, command_channel=command_channel)

#         return self

#     async def async_close(self):
#         """close"""
#         if self.client is not None:
#             await self.client.disconnect()
#             self.client = None


ReolinkEntityDataUpdateCoordinator = DataUpdateCoordinator[EntityData]


class ReolinkEntity(CoordinatorEntity[ReolinkEntityDataUpdateCoordinator]):
    """Reolink Entity"""

    _channel_id: int
    _generated_unique_id: bool | None

    def __init__(
        self,
        coordinator: ReolinkEntityDataUpdateCoordinator,
        channel_id: int,
        context: any = None,
    ) -> None:
        super().__init__(coordinator, context)
        self._channel_id = channel_id
        self._attr_device_info = self.coordinator.data.channels[channel_id].device
        self._attr_extra_state_attributes = {"channel": channel_id}

    def _handle_coordinator_update(self) -> None:
        self._attr_device_info = self.coordinator.data.channels[self._channel_id].device
        return super()._handle_coordinator_update()

    @property
    def _entry_data(self):
        domain_data: DomainData = self.hass.data[DOMAIN]
        return domain_data[self.coordinator.config_entry.entry_id]

    def _generate_unique_id(self):
        self._generated_unique_id = True
        device_info = self.device_info
        description = (
            self.entity_description if hasattr(self, "entity_description") else None
        )
        if (
            self.hass is not None
            and device_info is not None
            and description is not None
            and description.key is not None
        ):
            reg = device_registry.async_get(self.hass)
            entry = reg.async_get_device(
                device_info["identifiers"], device_info["connections"]
            )
            if entry is not None:
                return f"{entry.id}_{description.key}"
        return None

    @property
    def unique_id(self):
        if self._attr_unique_id is None and not self._generated_unique_id:
            self._attr_unique_id = self._generate_unique_id()
        return super().unique_id

    @property
    def channel_id(self):
        """channel id"""
        return self._channel_id
