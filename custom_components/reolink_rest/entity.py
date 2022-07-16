"""Reolink Entities"""
from __future__ import annotations
import asyncio

from datetime import timedelta

from typing import TYPE_CHECKING, Final, Mapping, cast
import async_timeout
from homeassistant.core import callback
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
from async_reolink.api import system, network, ai, alarm
from async_reolink.api.commands import CommandRequest
from async_reolink.api.const import DEFAULT_USERNAME, DEFAULT_PASSWORD, DEFAULT_TIMEOUT
from async_reolink.rest import Client as ReolinkClient
from async_reolink.rest.connection import Encryption

from async_reolink.rest.errors import CONNECTION_ERRORS, RESPONSE_ERRORS

from .typing import ReolinkDomainData, ReolinkEntryData

from .models import DeviceData, ChannelMotionData, MotionData, ReolinkEntityDescription

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


def _add_motion_commands(
    commands: list[CommandRequest],
    abilities: system.abilities.Abilities,
    *,
    options: Mapping[str, any] | None = None,
    channels: frozenset[int] | None = None,
):
    md_index: dict[int, int] = {}
    if len(abilities.channels) == 1:
        channels = set(0)
    elif channels is None or len(channels) == 0:
        channels = _get_channels(abilities, options)

    for i in channels:
        # the MD command does not return the channel it replies to
        md_index[len(commands)] = i
        commands.append(alarm.GetMotionStateCommand(i))
        ability = abilities.channels[i]
        if (
            ability.support.ai.animal
            or ability.support.ai.face
            or ability.support.ai.people
            or ability.support.ai.pet
            or ability.support.ai.vehicle
        ):
            commands.append(ai.GetAiStateCommand(i))

    return (commands, md_index)


def create_channel_motion_data_update_method(entry_data: ReolinkEntryData):
    """ "ChannelMotionData Updater Method"""

    async def _data_updater():
        client = entry_data["client"]
        data = entry_data["motion_coordinator"].data
        abilities = entry_data["coordinator"].data.abilities

        channels = entry_data.setdefault("motion_data_request", set())
        updated = data.updated if data else set()
        motion = data.channel if data else {}
        if TYPE_CHECKING:
            updated = cast(set[int], updated)
            motion = cast(dict[int, ChannelMotionData], motion)
        (commands, md_index) = _add_motion_commands(
            [],
            abilities,
            options=entry_data["coordinator"].config_entry.options,
            channels=channels,
        )
        idx = 0
        updated.clear()
        channels.clear()
        try:
            async for response in client.batch(commands):
                if alarm.GetMotionStateCommand.is_response(response):
                    state = alarm.GetMotionStateCommand.get_value(response)
                    channel = md_index[idx]
                    updated.add(channel)
                    motion.setdefault(channel, ChannelMotionData()).motion = bool(state)
                if ai.GetAiStateCommand.is_response(response):
                    state = ai.GetAiStateCommand.get_value(response)
                    channel = state["channel"]  # pylint: disable=unsubscriptable-object
                    updated.add(channel)
                    if ai.AITypes.is_ai_response_values(state):
                        for (_type, value) in state.items():
                            if (
                                isinstance(value, dict)
                                and value["support"]
                                and _type in (e.value for e in ai.AITypes)
                            ):
                                motion.setdefault(
                                    channel, ChannelMotionData()
                                ).detected[ai.AITypes(_type)] = bool(
                                    value["alarm_state"]
                                )
                idx += 1
        except ReolinkResponseError as reoresp:
            if reoresp.code == ErrorCodes.AUTH_REQUIRED:
                await client.disconnect()
                raise ConfigEntryAuthFailed()
            raise reoresp

        return MotionData(updated, motion)

    return _data_updater


def create_device_data_update_method(entry_data: ReolinkEntryData):
    """DeviceData Updater Method"""

    first_run = True
    conn_id = 0
    auth_id = 0
    if "client" in entry_data:
        client = entry_data["client"]
        first_run = not client.is_connected
        conn_id = client.connection_id
        auth_id = client.authentication_id
    else:
        entry_data.setdefault("client", ReolinkClient())

    async def _data_updater():
        nonlocal first_run, conn_id, auth_id

        client = entry_data["client"]
        coordinator = entry_data["coordinator"]
        config = coordinator.config_entry

        if (
            not client.is_connected
            or not coordinator.last_update_success
            or conn_id != client.connection_id
        ):
            host: str = config.data.get(CONF_HOST, None)
            discovery: dict = None
            if (
                host is None
                and (discovery := config.options.get(OPT_DISCOVERY, None))
                and "ip" in discovery
            ):
                host = discovery["ip"]
            if config.data.get(CONF_USE_HTTPS, False):
                encryption = Encryption.HTTPS
            else:
                encryption = Encryption.NONE

            if not host:
                raise ConfigEntryNotReady(
                    "No host configured, and none discovered (was device lost?)"
                )

            await client.connect(
                host,
                config.data.get(CONF_PORT, DEFAULT_PORT),
                config.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
                encryption=encryption,
            )
            if conn_id != client.connection_id:
                conn_id = client.connection_id
                auth_id = 0

        if (
            not client.is_authenticated
            or auth_id != client.authentication_id
            or coordinator.last_exception is ConfigEntryAuthFailed
        ):

            async def _auth():
                nonlocal auth_id
                if not await client.login(
                    config.data.get(CONF_USERNAME, DEFAULT_USERNAME),
                    config.data.get(CONF_PASSWORD, DEFAULT_PASSWORD),
                ):
                    auth_id = 0
                    await client.disconnect()
                    raise ConfigEntryAuthFailed()
                auth_id = client.authentication_id
                if (
                    coordinator.update_interval
                    and coordinator.update_interval.total_seconds()
                    >= client.authentication_timeout
                ):
                    # TODO : should we drop the interval to below the timeout?
                    pass

            if first_run:
                try:
                    async with async_timeout.timeout(5):
                        await _auth()
                except asyncio.TimeoutError:
                    coordinator.logger.info(
                        "Camera is not responding quickly on first load so delaying"
                    )
                    await client.disconnect()
                    raise
            else:
                await _auth()

        commands = []
        data = coordinator.data
        if TYPE_CHECKING:
            data = cast(DeviceData, data)
        abilities = data.abilities if data else None
        if abilities is None:
            try:
                abilities = await client.get_ability(
                    config.data.get(CONF_USERNAME, None)
                )
            except ReolinkResponseError as reoresp:
                if reoresp.code == ErrorCodes.AUTH_REQUIRED:
                    await client.disconnect()
                    raise ConfigEntryAuthFailed()
                if reoresp.code == ErrorCodes.PROTOCOL_ERROR:
                    # possible weird encryption bug or other invalid response so we will force a reconnect
                    conn_id = 0
                    auth_id = 0
                raise reoresp
        else:
            commands.append(
                system.GetAbilitiesCommand(config.data.get(CONF_USERNAME, None))
            )

        time = data.time if data else None
        drift = data.drift if data else None
        commands.append(system.GetTimeCommand())
        device_info = data.device_info if data else None
        channels = None
        channel_info = data.channels if data else {}
        ports = data.ports if data else None
        commands.append(network.GetNetworkPortsCommand())
        mac = None
        uuid = None
        updated_motion = None
        if abilities.devInfo:
            commands.append(system.GetDeviceInfoCommand())
            if device_info and device_info.get("channelNum", 1) > 1:
                commands.append(network.GetChannelStatusCommand())
        if "device" not in entry_data:
            discovery: dict = config.options.get(OPT_DISCOVERY, None)
            mac = discovery["mac"] if discovery and "mac" in discovery else None
            if abilities.localLink:
                commands.append(network.GetLocalLinkCommand())
            uuid = discovery["uuid"] if discovery and "uuid" in discovery else None
            if abilities.p2p:
                commands.append(network.GetP2PCommand())
        if (
            entry_data["motion_coordinator"].update_interval is None
            or not coordinator.last_update_success
            or data is None
        ):
            updated_motion: set[int] = set()
            motion: dict[int, ChannelMotionData] = {}
            (_, md_index) = _add_motion_commands(
                commands, abilities, options=config.options
            )
        else:
            motion = None

        idx = 0
        try:
            async for response in client.batch(commands):
                if system.GetAbilitiesCommand.is_response(response):
                    abilities = system.abilities.Abilities(
                        system.GetAbilitiesCommand.get_value(response)
                    )
                if system.GetTimeCommand.is_response(response):
                    result = system.GetTimeCommand.get_value(response)
                    # pylint: disable=unsubscriptable-object
                    time = system.as_dateime(
                        result["Time"], tzinfo=system.get_tzinfo(result)
                    )
                    drift = dt.utcnow() - dt.as_utc(time)
                if network.GetNetworkPortsCommand.is_response(response):
                    ports = network.GetNetworkPortsCommand.get_value(response)
                if system.GetDeviceInfoCommand.is_response(response):
                    device_info = system.GetDeviceInfoCommand.get_value(response)
                if network.GetChannelStatusCommand.is_response(response):
                    channels = network.GetChannelStatusCommand.get_value(response)
                if network.GetLocalLinkCommand.is_response(response):
                    _mac = network.GetLocalLinkCommand.get_value(response)["mac"]
                    if not mac:
                        mac = _mac
                    elif mac.lower() != _mac.lower():
                        raise UpdateFailed(
                            "Found different mac so possible wrong device"
                        )
                if network.GetP2PCommand.is_response(response):
                    _uuid = network.GetP2PCommand.get_value(response)["uid"]
                    if not uuid:
                        uuid = _uuid
                    elif uuid.lower() != _uuid.lower():
                        raise UpdateFailed(
                            "Did not find the same device as last time at this address!"
                        )
                if alarm.GetMotionStateCommand.is_response(response):
                    state = alarm.GetMotionStateCommand.get_value(response)
                    channel = md_index[idx]
                    updated_motion.add(channel)
                    motion.setdefault(channel, ChannelMotionData()).motion = bool(state)
                if ai.GetAiStateCommand.is_response(response):
                    state = ai.GetAiStateCommand.get_value(response)
                    channel = state["channel"]  # pylint: disable=unsubscriptable-object
                    updated_motion.add(channel)
                    if ai.AITypes.is_ai_response_values(state):
                        for (_type, value) in state.items():
                            if (
                                isinstance(value, dict)
                                and value["support"]
                                and _type in (e.value for e in ai.AITypes)
                            ):
                                motion.setdefault(
                                    channel, ChannelMotionData()
                                ).detected[ai.AITypes(_type)] = bool(
                                    value["alarm_state"]
                                )
                idx += 1
        except CONNECTION_ERRORS:
            conn_id = 0
            raise
        # except RESPONSE_ERRORS:
        #    raise
        except ReolinkResponseError as reoresp:
            if reoresp.code == ErrorCodes.AUTH_REQUIRED:
                await client.disconnect()
                raise ConfigEntryAuthFailed() from reoresp
            raise reoresp

        if device_info and device_info.get("channelNum", 0) > 1 and channels is None:
            channels = await client.get_channel_status()

        # pylint: disable=unsubscriptable-object
        if "device" not in entry_data:
            registry = device_registry.async_get(coordinator.hass)
            entry_data["device"] = registry.async_get_or_create(
                config_entry_id=config.entry_id,
                default_manufacturer="Reolink",
                default_name=device_info["name"],
                identifiers={(DOMAIN, uuid)} if uuid else None,
                connections={(device_registry.CONNECTION_NETWORK_MAC, mac)}
                if mac
                else None,
                sw_version=device_info["firmVer"],
                hw_version=device_info["hardVer"],
                default_model=device_info["model"],
                configuration_url=client.base_url,
            )
            if len(abilities.channels) < 2:
                channel_info[0] = _dev_to_info(entry_data["device"])
        else:
            registry = device_registry.async_get(coordinator.hass)
            updated_device = registry.async_update_device(
                entry_data["device"].id,
                name=device_info["name"],
                sw_version=device_info["firmVer"],
                hw_version=device_info["hardVer"],
            )
            if updated_device and updated_device != entry_data["device"]:
                entry_data["device"] = updated_device
                if len(abilities.channels) < 2:
                    channel_info[0] = _dev_to_info(updated_device)

        if len(abilities.channels) > 1 and channels:
            for i in config.options.get(
                OPT_CHANNELS, list(range(len(abilities.channels)))
            ):
                status = next(c for c in channels if c["channel"] == i)
                name = status.get("name", f"Channel {status['channel']}")
                if config.options.get(OPT_PREFIX_CHANNEL, False):
                    name = f"{entry_data['device'].name} {name}"
                if not status["channel"] in channel_info:
                    if not registry:
                        registry = device_registry.async_get(coordinator.hass)
                    channel_device = registry.async_get_or_create(
                        config_entry_id=config.entry_id,
                        via_device=entry_data["device"].identifiers.copy().pop(),
                        default_model=f"{status.get('typeInfo', '')} Channel {status['channel']}",
                        default_name=name,
                        identifiers={
                            (DOMAIN, f"{entry_data['device'].id}-{status['channel']}")
                        },
                        default_manufacturer=entry_data["device"].manufacturer,
                    )
                    channel_info[status["channel"]] = _dev_to_info(channel_device)
                else:
                    if not registry:
                        registry = device_registry.async_get(coordinator.hass)
                    channel_device = registry.async_get_device(
                        channel_info[status["channel"]]["identifiers"]
                    )
                    updated_device = registry.async_update_device(
                        channel_device.id, name=name
                    )
                    if updated_device and updated_device != channel_device:
                        channel_info[status["channel"]] = _dev_to_info(updated_device)

        if (uuid or mac) and OPT_DISCOVERY not in config.options:
            options = config.options.copy()
            options[OPT_DISCOVERY] = {}
            if mac:
                options[OPT_DISCOVERY]["mac"] = mac
            if uuid:
                options[OPT_DISCOVERY]["uuid"] = uuid
            coordinator.hass.config_entries.async_update_entry(
                coordinator.config_entry, options=options
            )

        if motion and updated_motion:
            entry_data["motion_coordinator"].async_set_updated_data(
                MotionData(updated_motion, motion)
            )
        return DeviceData(time, drift, abilities, device_info, channel_info, ports)

    return _data_updater


ReolinkEntityDataUpdateCoordinator: Final = DataUpdateCoordinator[DeviceData]


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


ReolinkMotionEntityDataUpdateCooridnator: Final = DataUpdateCoordinator[MotionData]


class ReolinkMotionEntity(ReolinkEntity):
    """Reolink Motion Entity"""

    def __init__(
        self,
        coordinator: ReolinkEntityDataUpdateCoordinator,
        description: ReolinkEntityDescription,
        context: any = None,
    ) -> None:
        super().__init__(coordinator, description, context)
        domain_data: ReolinkDomainData = self.coordinator.hass.data[DOMAIN]
        self.motion_coordinator = domain_data[self.coordinator.config_entry.entry_id][
            "motion_coordinator"
        ]

    @property
    def available(self) -> bool:
        return super().available and self.motion_coordinator.last_update_success

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        def _filter_coordinator_update():
            if self.entity_description.channel in self.motion_coordinator.data.updated:
                self._handle_coordinator_motion_update()

        self.async_on_remove(
            self.motion_coordinator.async_add_listener(
                _filter_coordinator_update, self.coordinator_context
            )
        )

    @callback
    def _handle_coordinator_motion_update(self) -> None:
        """Handle updated motion data from the coordinator."""
        self.async_write_ha_state()

    async def async_update(self) -> None:
        if not self.enabled:
            return

        await super().async_update()

        await self.motion_coordinator.async_request_refresh()
