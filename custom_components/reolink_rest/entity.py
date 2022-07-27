"""Reolink Entities"""

from datetime import timedelta

from typing import Mapping, Sequence
from homeassistant.core import HomeAssistant
from homeassistant import config_entries
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    CoordinatorEntity,
    UpdateFailed,
)
from homeassistant.helpers import device_registry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.util import dt

from homeassistant.const import (
    CONF_SCAN_INTERVAL,
    CONF_HOST,
    CONF_PORT,
    CONF_TIMEOUT,
    CONF_USERNAME,
    CONF_PASSWORD,
)

from async_reolink.api.errors import ReolinkResponseError, ErrorCodes
from async_reolink.api import system, network, ai, alarm, ptz
from async_reolink.api.const import DEFAULT_USERNAME, DEFAULT_PASSWORD, DEFAULT_TIMEOUT
from async_reolink.rest import Client as ReolinkClient
from async_reolink.rest.connection import Encryption

from async_reolink.rest.errors import (
    CONNECTION_ERRORS,
    AUTH_ERRORCODES,
)

from .typing import EntityData

from .models import (
    MutableMotionData,
    MutablePTZDisabled,
    MutablePTZPosition,
    ReolinkEntityDescription,
)

from .const import (
    CONF_USE_HTTPS,
    DEFAULT_MOTION_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    OPT_CHANNELS,
    OPT_DISCOVERY,
    OPT_MOTION_INTERVAL,
    OPT_PREFIX_CHANNEL,
)


def async_get_poll_interval(config_entry: config_entries.ConfigEntry):
    """Get the poll interval"""
    interval = config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    return timedelta(seconds=interval)


def async_get_motion_poll_interval(config_entry: config_entries.ConfigEntry):
    """Get the motion poll interval"""
    interval = config_entry.options.get(OPT_MOTION_INTERVAL, DEFAULT_MOTION_INTERVAL)
    return timedelta(seconds=interval)


def _dev_to_info(device: device_registry.DeviceEntry):
    return DeviceInfo(
        configuration_url=device.configuration_url,
        connections=device.connections,
        entry_type=device.entry_type,
        hw_version=device.hw_version,
        identifiers=device.identifiers,
        manufacturer=device.manufacturer,
        model=device.model,
        name=device.name,
        suggested_area=device.suggested_area,
        via_device=device.via_device_id,
        sw_version=device.sw_version,
    )


def _get_channels(
    abilities: system.abilities.Abilities, options: Mapping[str, any] | None = None
):
    channels = set(range(len(abilities.channels)))
    if options:
        return set(options.get(OPT_CHANNELS, channels))
    return channels


class _MutablePTZData:
    def __init__(self) -> None:
        self.pan = MutablePTZPosition()
        self.tilt = MutablePTZPosition()
        self.zoom = MutablePTZPosition()
        self.focus = MutablePTZPosition()
        self.autofocus = MutablePTZDisabled()
        self.presets: dict[ptz.PTZPresetId, ptz.PTZPreset] = {}
        self.patrol: dict[ptz.PTZPatrolId, ptz.PTZPatrol] = {}
        self.tattern: dict[ptz.PTZTrackId, ptz.PTZTrack] = {}


class ReolinkEntityData:
    """Reolink Entity Data and API"""

    def __init__(self, hass: HomeAssistant, config_entry: config_entries.ConfigEntry):
        self.hass = hass
        self.config_entry = config_entry
        self.client = ReolinkClient()
        self.device: device_registry.DeviceEntry = None
        self.time_difference = timedelta()
        self.abilities: system.abilities.Abilities = None
        self.device_info: system.DeviceInfoType = None
        self.channels: dict[int, DeviceInfo] = {}
        self.ports: network.NetworkPortsType = None
        self._batch_ability = True
        self._connection_id = 0
        self._authentication_id = 0
        self.updated_motion: set[int] = set()
        self._update_motion: set[int] = set()
        self.motion: dict[int, MutableMotionData] = {}
        self.updated_ptz: set[int] = set()
        self._update_ptz: set[int] = set()
        self.ptz: dict[int, _MutablePTZData] = {}
        discovery: dict = config_entry.options.get(OPT_DISCOVERY, None)
        if discovery is not None and (
            "name" in discovery or "uuid" in discovery or "mac" in discovery
        ):
            self._name: str = discovery.get(
                "name", discovery.get("uuid", discovery["mac"])
            )
        else:
            self._name: str = config_entry[CONF_HOST]

    @property
    def name(self):
        """short name"""
        return self._name

    def _processes_responses(self, response):
        if system.GetAbilitiesCommand.is_response(response):
            self.abilities = system.abilities.Abilities(
                system.GetAbilitiesCommand.get_value(response)
            )
            return True
        if system.GetTimeCommand.is_response(response):
            result = system.GetTimeCommand.get_value(response)
            # pylint: disable=unsubscriptable-object
            time = system.as_dateime(result["Time"], tzinfo=system.get_tzinfo(result))
            self.time_difference = dt.utcnow() - dt.as_utc(time)
            return True
        if network.GetNetworkPortsCommand.is_response(response):
            self.ports = network.GetNetworkPortsCommand.get_value(response)
            return True
        if system.GetDeviceInfoCommand.is_response(response):
            self.device_info = system.GetDeviceInfoCommand.get_value(response)
            return True
        return False

    async def _execute_commands(
        self, commands: list, /, command_channel: dict[int, int] = None
    ):
        idx = 0
        channels = None
        mac = None
        uuid = None
        try:
            async for response in self.client.batch(commands):
                if network.GetChannelStatusCommand.is_response(response):
                    channels = network.GetChannelStatusCommand.get_value(response)
                elif network.GetLocalLinkCommand.is_response(response):
                    _mac = network.GetLocalLinkCommand.get_value(response)["mac"]
                    if not mac:
                        mac = _mac
                    elif mac.lower() != _mac.lower():
                        raise UpdateFailed(
                            "Found different mac so possible wrong device"
                        )
                elif network.GetP2PCommand.is_response(response):
                    _uuid = network.GetP2PCommand.get_value(response)["uid"]
                    if not uuid:
                        uuid = _uuid
                    elif uuid.lower() != _uuid.lower():
                        raise UpdateFailed(
                            "Did not find the same device as last time at this address!"
                        )
                else:
                    _ = (
                        self._processes_responses(response)
                        or self._process_motion_responses(
                            response, command_index=idx, command_channel=command_channel
                        )
                        or self._process_ptz_responses(
                            response, command_index=idx, command_channel=command_channel
                        )
                    )
                idx += 1
        except CONNECTION_ERRORS:
            self._connection_id = 0
            raise
        # except RESPONSE_ERRORS:
        #    raise
        except ReolinkResponseError as reoresp:
            # do not trap auth errors, instead we will just fail as usual
            # auth errors at this point could be expired tokens
            # so we do not want to assume password issues
            if reoresp.code in AUTH_ERRORCODES:
                await self.client.disconnect()
                return False
            if reoresp.code == ErrorCodes.READ_FAILED and True in (
                True
                for command in commands
                if isinstance(command, system.GetAbilitiesCommand)
            ):
                # some cameras do not like to batch in the ability command
                # we will note this and no do that anymore
                # TODO : update options to prevent it completely
                self._batch_ability = False
                return False
            raise reoresp
        return (channels, mac, uuid)

    async def async_update(self):
        """update"""

        if (
            not self.client.is_connected
            or self._connection_id != self.client.connection_id
        ):
            host: str = self.config_entry.data.get(CONF_HOST, None)
            discovery: dict = None
            if (
                host is None
                and (discovery := self.config_entry.options.get(OPT_DISCOVERY, None))
                and "ip" in discovery
            ):
                host = discovery["ip"]
            if self.config_entry.data.get(CONF_USE_HTTPS, False):
                encryption = Encryption.HTTPS
            else:
                encryption = Encryption.NONE

            if not host:
                raise ConfigEntryNotReady(
                    "No host configured, and none discovered (was device lost?)"
                )

            await self.client.connect(
                host,
                self.config_entry.data.get(CONF_PORT, DEFAULT_PORT),
                self.config_entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
                encryption=encryption,
            )
            if self._connection_id != self.client.connection_id:
                self._connection_id = self.client.connection_id
                self._authentication_id = 0

        if (
            not self.client.is_authenticated
            or self._authentication_id != self.client.authentication_id
        ):
            try:
                if not await self.client.login(
                    self.config_entry.data.get(CONF_USERNAME, DEFAULT_USERNAME),
                    self.config_entry.data.get(CONF_PASSWORD, DEFAULT_PASSWORD),
                ):
                    self._authentication_id = 0
                    await self.client.disconnect()
                    raise ConfigEntryAuthFailed()
            except ReolinkResponseError as reoresp:
                if reoresp.code in AUTH_ERRORCODES:
                    await self.client.disconnect()
                    raise ConfigEntryAuthFailed()
                raise reoresp
            self._authentication_id = self.client.authentication_id

        commands = []
        if self.abilities is None or not self._batch_ability:
            try:
                self.abilities = await self.client.get_ability(
                    self.config_entry.data.get(CONF_USERNAME, None)
                )
            except ReolinkResponseError as reoresp:
                if reoresp.code in AUTH_ERRORCODES:
                    # this could be because of a reboot or token expiration
                    await self.async_update()
                    return self
                if reoresp.code == ErrorCodes.PROTOCOL_ERROR:
                    # possible weird encryption bug or other invalid response so we will force a reconnect
                    self._connection_id = 0
                    self._authentication_id = 0
                raise reoresp
        else:
            commands.append(
                system.GetAbilitiesCommand(
                    self.config_entry.data.get(CONF_USERNAME, None)
                )
            )

        commands.append(system.GetTimeCommand())

        channels = None
        commands.append(network.GetNetworkPortsCommand())
        mac = None
        uuid = None
        if self.abilities.devInfo:
            commands.append(system.GetDeviceInfoCommand())
            if self.device_info and self.device_info.get("channelNum", 1) > 1:
                commands.append(network.GetChannelStatusCommand())
        if self.device is None:
            discovery: dict = self.config_entry.options.get(OPT_DISCOVERY, None)
            mac = discovery["mac"] if discovery and "mac" in discovery else None
            if self.abilities.localLink:
                commands.append(network.GetLocalLinkCommand())
            uuid = discovery["uuid"] if discovery and "uuid" in discovery else None
            if self.abilities.p2p:
                commands.append(network.GetP2PCommand())
        (_, command_channel) = self._create_motion_commands(commands)
        (_, command_channel) = self._create_ptz_commands(
            commands, command_channel=command_channel
        )

        self._update_motion.clear()
        self.updated_motion.clear()
        self._update_ptz.clear()
        self.updated_ptz.clear()
        result = await self._execute_commands(commands, command_channel=command_channel)
        if not result:
            await self.async_update()
            return self

        channels, mac, uuid = result

        if (
            self.device_info
            and self.device_info.get("channelNum", 0) > 1
            and channels is None
        ):
            channels = await self.client.get_channel_status()

        # pylint: disable=unsubscriptable-object
        if self.device is None:
            registry = device_registry.async_get(self.hass)
            self.device = registry.async_get_or_create(
                config_entry_id=self.config_entry.entry_id,
                default_manufacturer="Reolink",
                default_name=self.device_info["name"],
                identifiers={(DOMAIN, uuid)} if uuid else None,
                connections={(device_registry.CONNECTION_NETWORK_MAC, mac)}
                if mac
                else None,
                sw_version=self.device_info["firmVer"],
                hw_version=self.device_info["hardVer"],
                default_model=self.device_info["model"],
                configuration_url=self.client.base_url,
            )
            if len(self.abilities.channels) < 2:
                self.channels[0] = _dev_to_info(self.device)
        else:
            registry = device_registry.async_get(self.hass)
            updated_device = registry.async_update_device(
                self.device.id,
                name=self.device_info["name"],
                sw_version=self.device_info["firmVer"],
                hw_version=self.device_info["hardVer"],
            )
            if updated_device and updated_device != self.device:
                self.device = updated_device
                if len(self.abilities.channels) < 2:
                    self.channels[0] = _dev_to_info(updated_device)

        if len(self.abilities.channels) > 1 and channels:
            for i in self.config_entry.options.get(
                OPT_CHANNELS, list(range(len(self.abilities.channels)))
            ):
                status = next(c for c in channels if c["channel"] == i)
                name = status.get("name", f"Channel {status['channel']}")
                if self.config_entry.options.get(OPT_PREFIX_CHANNEL, False):
                    name = f"{self.device.name} {name}"
                if not status["channel"] in self.channels:
                    if not registry:
                        registry = device_registry.async_get(self.hass)
                    channel_device = registry.async_get_or_create(
                        config_entry_id=self.config_entry.entry_id,
                        via_device=self.device.identifiers.copy().pop(),
                        default_model=f"{status.get('typeInfo', '')} Channel {status['channel']}",
                        default_name=name,
                        identifiers={(DOMAIN, f"{self.device.id}-{status['channel']}")},
                        default_manufacturer=self.device.manufacturer,
                    )
                    self.channels[status["channel"]] = _dev_to_info(channel_device)
                else:
                    if not registry:
                        registry = device_registry.async_get(self.hass)
                    channel_device = registry.async_get_device(
                        self.channels[status["channel"]]["identifiers"]
                    )
                    updated_device = registry.async_update_device(
                        channel_device.id, name=name
                    )
                    if updated_device and updated_device != channel_device:
                        self.channels[status["channel"]] = _dev_to_info(updated_device)

        if (uuid or mac) and OPT_DISCOVERY not in self.config_entry.options:
            options = self.config_entry.options.copy()
            options[OPT_DISCOVERY] = {}
            if mac:
                options[OPT_DISCOVERY]["mac"] = mac
            if uuid:
                options[OPT_DISCOVERY]["uuid"] = uuid
            self.hass.config_entries.async_update_entry(
                self.config_entry, options=options
            )

        return self

    def _create_motion_commands(
        self,
        /,
        commands: list = None,
        command_channel: dict[int, int] = None,
        channels: Sequence[int] = None,
    ):
        if commands is None:
            commands = []
        if command_channel is None:
            command_channel = {}
        if len(self.abilities.channels) == 1:
            channels = set({0})
        elif channels is None or len(channels) == 0:
            channels = _get_channels(self.abilities, self.config_entry.options)

        for i in channels:
            # the MD command does not return the channel it replies to
            command_channel[len(commands)] = i
            commands.append(alarm.GetMotionStateCommand(i))
            ability = self.abilities.channels[i]
            if (
                ability.support.ai.animal
                or ability.support.ai.face
                or ability.support.ai.people
                or ability.support.ai.pet
                or ability.support.ai.vehicle
            ):
                commands.append(ai.GetAiStateCommand(i))

        return (commands, command_channel)

    def _process_motion_responses(
        self, response, /, command_index: int, command_channel: dict[int, int]
    ):
        if alarm.GetMotionStateCommand.is_response(response):
            state = alarm.GetMotionStateCommand.get_value(response)
            channel = command_channel[command_index]
            self.updated_motion.add(channel)
            if channel not in self.motion:
                self.motion.setdefault(channel, MutableMotionData())
            self.motion[channel].detected = bool(state)
            return True
        if ai.GetAiStateCommand.is_response(response):
            state = ai.GetAiStateCommand.get_value(response)
            channel = state["channel"]  # pylint: disable=unsubscriptable-object
            self.updated_motion.add(channel)
            if ai.AITypes.is_ai_response_values(state):
                for (_type, value) in state.items():
                    if (
                        isinstance(value, dict)
                        and value["support"]
                        and _type in (e.value for e in ai.AITypes)
                    ):
                        if channel not in self.motion:
                            self.motion.setdefault(channel, MutableMotionData())
                        self.motion[channel][ai.AITypes(_type)] = bool(
                            value["alarm_state"]
                        )
            return True
        return False

    def async_request_motion_update(self, channel: int = 0):
        """Request update of PTZ data for channel"""
        self._update_motion.add(channel)

    async def async_update_motion_data(self):
        """update motion only"""

        (commands, command_channel) = self._create_motion_commands(
            channels=self._update_motion,
        )
        self.updated_motion.clear()
        self._update_motion.clear()
        await self._execute_commands(commands, command_channel=command_channel)

        return self

    def _create_ptz_commands(
        self,
        /,
        commands: list = None,
        command_channel: dict[int, int] = None,
        channels: set[int] = None,
    ):
        if commands is None:
            commands = []
        if command_channel is None:
            command_channel = {}
        if len(self.abilities.channels) == 1:
            channels = set({0})
        elif channels is None or len(channels) == 0:
            channels = _get_channels(self.abilities, self.config_entry.options)

        for i in channels:
            ability = self.abilities.channels[i]
            if ability.ptz.control in (
                system.abilities.channel.PTZControlValues.ZOOM,
                system.abilities.channel.PTZControlValues.ZOOM_FOCUS,
            ):
                commands.append(ptz.GetPTZZoomFocusCommand(i))
            if ability.ptz.type == system.abilities.channel.PTZTypeValues.AF:
                command_channel[len(commands)] = i
                commands.append(ptz.GetPTZAutoFocusCommand(i))
            if ability.ptz.patrol:
                commands.append(ptz.GetPTZPatrolCommand(i))
            if ability.ptz.tattern:
                commands.append(ptz.GetPTZTatternCommand(i))
        return (commands, command_channel)

    def _process_ptz_responses(
        self, response, /, command_index: int, command_channel: dict[int, int]
    ):
        if ptz.GetPTZAutoFocusCommand.is_response(response):
            value = ptz.GetPTZAutoFocusCommand.get_value(response)
            channel = command_channel[command_index]
            self.updated_ptz.add(channel)
            if channel not in self.ptz:
                data = self.ptz.setdefault(channel, _MutablePTZData())
            else:
                data = self.ptz[channel]
            data.autofocus.disabled = value["disable"]
            return True
        if ptz.GetPTZZoomFocusCommand.is_response(response):
            value = ptz.GetPTZZoomFocusCommand.get_value(response)
            channel = value["channel"]
            self.updated_ptz.add(channel)
            if channel not in self.ptz:
                data = self.ptz.setdefault(channel, _MutablePTZData())
            else:
                data = self.ptz[channel]
            if "zoom" in value:
                data.zoom.value = value["zoom"].get("pos", 0)
            else:
                data.zoom.value = 0
            if "focus" in value:
                data.focus.value = value["focus"].get("pos", 0)
            else:
                data.focus.value = 0
            return True
        if ptz.GetPTZPresetCommand.is_response(response):
            for preset in ptz.GetPTZPresetCommand.get_value(response):
                channel = preset["channel"]
                self.updated_ptz.add(channel)
                if channel not in self.ptz:
                    data = self.ptz.setdefault(channel, _MutablePTZData())
                else:
                    data = self.ptz[channel]
                if data.presets is None:
                    data.presets = {}
                if preset["id"] in data.presets:
                    data.presets[preset["id"]].update(**preset)
                else:
                    data.presets[preset["id"]] = ptz.PTZPreset(**preset)

            return True
        if ptz.GetPTZPatrolCommand.is_response(response):
            for track in ptz.GetPTZPatrolCommand.get_value(response):
                channel = track["channel"]
                self.updated_ptz.add(channel)
                if channel not in self.ptz:
                    data = self.ptz.setdefault(channel, _MutablePTZData())
                else:
                    data = self.ptz[channel]
                if data.patrol is None:
                    data.patrol = {}
                if track["id"] in data.patrol:
                    data.patrol[track["id"]].update(**track)
                else:
                    data.patrol[track["id"]] = ptz.PTZPatrol(**track)
            return True
        if ptz.GetPTZTatternCommand.is_response(response):
            for track in ptz.GetPTZTatternCommand.get_value(response):
                channel = track["channel"]
                self.updated_ptz.add(channel)
                if channel not in self.ptz:
                    data = self.ptz.setdefault(channel, _MutablePTZData())
                else:
                    data = self.ptz[channel]
                if data.tattern is None:
                    data.tattern = {}
                if track["id"] in data.tattern:
                    data.tattern[track["id"]].update(**track)
                else:
                    data.tattern[track["id"]] = ptz.PTZPatrol(**track)
            return True
        return False

    def async_request_ptz_update(self, channel: int = 0):
        """Request update of PTZ data for channel"""
        self._update_ptz.add(channel)

    async def async_update_ptz_data(self):
        """update ptz only"""
        (commands, command_channel) = self._create_ptz_commands(
            channels=self._update_ptz,
        )
        self.updated_ptz.clear()
        self._update_ptz.clear()
        await self._execute_commands(commands, command_channel=command_channel)

        return self

    async def async_close(self):
        """close"""
        if self.client is not None:
            await self.client.disconnect()
            self.client = None


ReolinkEntityDataUpdateCoordinator = DataUpdateCoordinator[EntityData]


class ReolinkEntity(CoordinatorEntity[ReolinkEntityDataUpdateCoordinator]):
    """Reolink Entity"""

    entity_description: ReolinkEntityDescription

    def __init__(
        self,
        coordinator: ReolinkEntityDataUpdateCoordinator,
        description: ReolinkEntityDescription,
        context: any = None,
    ) -> None:
        super().__init__(coordinator, context)
        self.entity_description = description
        self._attr_device_info = self.coordinator.data.channels[
            self.entity_description.channel
        ]
        self._attr_unique_id = self.coordinator.config_entry.unique_id
        self._attr_unique_id += f"_ch_{description.channel}"
        self._attr_unique_id += f"_{description.key}"

    def _handle_coordinator_update(self) -> None:
        self._attr_device_info = self.coordinator.data.channels[
            self.entity_description.channel
        ]
        return super()._handle_coordinator_update()
