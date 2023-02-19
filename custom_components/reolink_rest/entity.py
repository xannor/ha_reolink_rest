"""Reolink Entities"""

import dataclasses
from homeassistant.config_entries import current_entry
from homeassistant.helpers.entity import Entity, EntityDescription

from homeassistant.backports.enum import StrEnum

from ._utilities.object import setdefaultattr

from .typing import ChannelData, EntryId

from .api import (
    ReolinkDeviceApi,
)

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


@dataclasses.dataclass
class ChannelDescriptionMixin:
    """Mixin for required keys"""

    channel_id: int = 0

    def from_channel(self, channel_data: ChannelData):
        """Create a new instance from merging channel data"""
        return dataclasses.replace(
            self,
            channel_id=channel_data.channel_id,
            key=f"ch_{channel_data.channel_id}_{self.key}",
        )


# _T = TypeVar("_T", infer_variance=True)
# _TContext = TypeVar("_TContext", infer_variance=True)  # plint: disable=invalid-name


class UpdateMethods(StrEnum):
    """Update Method"""

    POLL = "polling"
    PUSH = "push"
    PUSH_POLL = "push/poll"
    INPUT = "input"
    INPUT_POLL = "input/poll"


class ReolinkEntity(Entity):
    """Reolink Entity"""

    _attr_channel_id: int
    _attr_update_method: UpdateMethods

    def __init__(self, api: ReolinkDeviceApi, unique_id: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        config_entry = current_entry.get()
        self._entry_id = config_entry.entry_id
        if not unique_id:
            unique_id = config_entry.unique_id
        self._api = api
        device_data = api.data
        self._attr_device_info = api.async_get_device_lookup_info(unique_id or self._entry_id)
        if not (description := getattr(self, "entity_description", None)) or not isinstance(
            description, EntityDescription
        ):
            description = None
        if (
            isinstance(
                description,
                ChannelDescriptionMixin,
            )
            and (channel_id := description.channel_id) is not None
        ):
            self._attr_channel_id = channel_id
            self._attr_device_info = device_data.channel_info[channel_id].device
            setdefaultattr(self, "_attr_extra_state_attributes", {})["channel"] = channel_id
        if description and description.key and unique_id:
            self._attr_unique_id = f"{unique_id}_{description.key}"

    @property
    def _client(self):
        return self._api.client

    @property
    def _device_data(self):
        return self._api.data

    @property
    def _channel_id(self) -> int:
        return getattr(self, "_attr_channel_id", 0)

    @property
    def _update_method(self) -> UpdateMethods:
        return getattr(self, "_attr_update_method", UpdateMethods.POLL)

    @_update_method.setter
    def _update_method(self, value):
        if not value:
            raise ValueError("value must be provided")
        self._attr_update_method = UpdateMethods(value)
        self._attr_extra_state_attributes["update_method"] = self._attr_update_method
