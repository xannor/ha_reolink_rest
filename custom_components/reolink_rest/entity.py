"""Reolink Entities"""
from __future__ import annotations
import asyncio

from datetime import timedelta
import logging

from typing import TYPE_CHECKING, cast
import async_timeout
from homeassistant.core import HomeAssistant, callback
from homeassistant import config_entries
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    CoordinatorEntity,
    UpdateFailed,
)
from homeassistant.helpers.debounce import Debouncer
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

from reolinkapi.errors import ReolinkResponseError, ErrorCodes
from reolinkapi import system, network, ai, alarm
from reolinkapi.commands import CommandRequest
from reolinkapi.const import DEFAULT_USERNAME, DEFAULT_PASSWORD, DEFAULT_TIMEOUT
from reolinkrestapi import Client as ReolinkClient
from reolinkrestapi.connection import Encryption

from .models import EntityData, ChannelMotionData, ReolinkEntityDescription

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


class ReolinkDataUpdateCoordinator(DataUpdateCoordinator[EntityData]):
    """Reolink Data Update Coordinator"""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        *,
        name: str,
        update_interval: timedelta | None = None,
        request_refresh_debouncer: Debouncer | None = None,
        client: ReolinkClient | None = None,
        device: device_registry.DeviceEntry | None = None,
        motion_update_interval: timedelta | None = None,
    ) -> None:
        super().__init__(
            hass,
            logger,
            name=name,
            update_interval=update_interval,
            request_refresh_debouncer=request_refresh_debouncer,
        )
        self._client = client
        self._connection_id = client.connection_id if client else 0
        self._auth_id = 0
        self._device = device
        self._update_motion = True
        # setup a separate "sub" update for motion data as it
        # will update on a different schedule than the normal polling updates
        self._motion_coordinator = DataUpdateCoordinator(
            hass,
            logger,
            name=f"{name} Motion",
            update_interval=motion_update_interval,
            request_refresh_debouncer=request_refresh_debouncer,
            update_method=self._async_update_motion,
        )

    @property
    def client(self) -> ReolinkClient:
        """Reolink Client"""
        return self._client

    @property
    def device(self) -> device_registry.DeviceEntry:
        """Device Registry Entry"""
        return self._device

    @property
    def motion_coordinator(self):
        """Motion Coordinator"""
        return self._motion_coordinator

    def _config_entry_unload(self):
        if self._client:
            self.hass.create_task(self._client.disconnect())
        self._motion_coordinator.update_interval = None
        self._motion_coordinator._unschedule_refresh()  # pylint: disable=protected-access
        self._client = None
        self.update_interval = None
        self._unschedule_refresh()

    def _add_motion_commands(
        self,
        commands: list[CommandRequest],
        abilities: system.abilities.Abilities,
    ):
        md_index: dict[int, int] = {}
        if len(abilities.channels) > 1:
            channels: list[int] = self.config_entry.options.get(
                OPT_CHANNELS, list(range(len(abilities.channels)))
            )
        else:
            channels = [0]

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

    async def _async_update_motion(self):
        motion: dict[int, ChannelMotionData] = {}
        if not self._client:
            self._update_motion = True
            await self.async_request_refresh()
            motion = self._motion_coordinator.data
            return motion

        (commands, md_index) = self._add_motion_commands([], self.data.abilities)
        idx = 0
        async for response in self._client.batch(commands):
            if alarm.GetMotionStateCommand.is_response(response):
                state = alarm.GetMotionStateCommand.get_value(response)
                channel = md_index[idx]
                motion.setdefault(channel, ChannelMotionData()).motion = bool(state)
            if ai.GetAiStateCommand.is_response(response):
                state = ai.GetAiStateCommand.get_value(response)
                channel = state["channel"]  # pylint: disable=unsubscriptable-object
                if ai.AITypes.is_ai_response_values(state):
                    for (_type, value) in state.items():
                        if (
                            isinstance(value, dict)
                            and value["support"]
                            and _type in (e.value for e in ai.AITypes)
                        ):
                            motion.setdefault(channel, ChannelMotionData()).detected[
                                ai.AITypes(_type)
                            ] = bool(value["alarm_state"])
            idx += 1

        return motion

    async def _async_update_data(self):
        first_run = False
        if not self._client and not self.hass.is_stopping:
            first_run = True
            self._client = ReolinkClient()
            self.config_entry.async_on_unload(self._config_entry_unload)

        if not self._client.is_connected or self.last_exception is ConfigEntryNotReady:
            host = self.config_entry.data.get(CONF_HOST, None)
            discovery: dict = self.config_entry.options.get(OPT_DISCOVERY, None)
            if host is None and discovery and "ip" in discovery:
                host = discovery["ip"]
            if self.config_entry.data.get(CONF_USE_HTTPS, False):
                encryption = Encryption.HTTPS
            else:
                encryption = Encryption.NONE

            if not host:
                raise ConfigEntryNotReady(
                    "No host configured, and none discovered (was device lost?)"
                )

            await self._client.connect(
                host,
                self.config_entry.data.get(CONF_PORT, DEFAULT_PORT),
                self.config_entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
                encryption=encryption,
            )

        if (
            not self._client.is_authenticated
            or self.last_exception is ConfigEntryAuthFailed
        ):

            async def _auth():
                if not await self._client.login(
                    self.config_entry.data.get(CONF_USERNAME, DEFAULT_USERNAME),
                    self.config_entry.data.get(CONF_PASSWORD, DEFAULT_PASSWORD),
                ):
                    await self._client.disconnect()
                    raise ConfigEntryAuthFailed()

            if first_run:
                try:
                    async with async_timeout.timeout(5):
                        await _auth()
                except asyncio.TimeoutError:
                    self.logger.info(
                        "Camera is not responding quickly on first load so delaying"
                    )
                    await self._client.disconnect()
                    raise
            else:
                await _auth()

        commands = []
        data = self.data
        if TYPE_CHECKING:
            data = cast(EntityData, data)
        abilities = data.abilities if data else None
        if abilities is None:
            try:
                abilities = await self._client.get_ability(
                    self.config_entry.data.get(CONF_USERNAME, None)
                )
            except ReolinkResponseError as reoresp:
                if reoresp.code == ErrorCodes.AUTH_REQUIRED:
                    await self._client.disconnect()
                    raise ConfigEntryAuthFailed()
                raise ConfigEntryNotReady()
        else:
            commands.append(
                system.GetAbilitiesCommand(
                    self.config_entry.data.get(CONF_USERNAME, None)
                )
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
        if abilities.devInfo:
            commands.append(system.GetDeviceInfoCommand())
            if device_info and device_info.get("channelNum", 1) > 1:
                commands.append(network.GetChannelStatusCommand())
        if not self._device:
            discovery: dict = self.config_entry.options.get(OPT_DISCOVERY, None)
            mac = discovery["mac"] if discovery and "mac" in discovery else None
            if abilities.localLink:
                commands.append(network.GetLocalLinkCommand())
            uuid = discovery["uuid"] if discovery and "uuid" in discovery else None
            if abilities.p2p:
                commands.append(network.GetP2PCommand())
        if self._update_motion or self._motion_coordinator.update_interval is None:
            motion: dict[int, ChannelMotionData] = {}
            (_, md_index) = self._add_motion_commands(commands, abilities)
        else:
            motion = None

        idx = 0
        try:
            async for response in self._client.batch(commands):
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
                    motion.setdefault(channel, ChannelMotionData()).motion = bool(state)
                if ai.GetAiStateCommand.is_response(response):
                    state = ai.GetAiStateCommand.get_value(response)
                    channel = state["channel"]  # pylint: disable=unsubscriptable-object
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
                await self._client.disconnect()
                raise ConfigEntryAuthFailed()
            raise ConfigEntryNotReady()

        if device_info and device_info.get("channelNum", 0) > 1 and channels is None:
            channels = await self._client.get_channel_status()

        # pylint: disable=unsubscriptable-object
        if not self._device:
            registry = device_registry.async_get(self.hass)
            self._device = registry.async_get_or_create(
                config_entry_id=self.config_entry.entry_id,
                default_manufacturer="Reolink",
                default_name=device_info["name"],
                identifiers={(DOMAIN, uuid)} if uuid else None,
                connections={(device_registry.CONNECTION_NETWORK_MAC, mac)}
                if mac
                else None,
                sw_version=device_info["firmVer"],
                hw_version=device_info["hardVer"],
                default_model=device_info["model"],
                configuration_url=self._client.base_url,
            )
            if len(abilities.channels) < 2:
                channel_info[0] = _dev_to_info(self._device)
        else:
            registry = device_registry.async_get(self.hass)
            updated = registry.async_update_device(
                self._device.id,
                name=device_info["name"],
                sw_version=device_info["firmVer"],
                hw_version=device_info["hardVer"],
            )
            if updated and updated != self._device:
                self._device = updated
                if len(abilities.channels) < 2:
                    channel_info[0] = _dev_to_info(updated)

        if len(abilities.channels) > 1 and channels:
            for i in self.config_entry.options.get(
                OPT_CHANNELS, list(range(len(abilities.channels)))
            ):
                status = next(c for c in channels if c["channel"] == i)
                name = status.get("name", f"Channel {status['channel']}")
                if self.config_entry.options.get(OPT_PREFIX_CHANNEL, False):
                    name = f"{self._device.name}: {name}"
                if not status["channel"] in channel_info:
                    if not registry:
                        registry = device_registry.async_get(self.hass)
                    channel_device = registry.async_get_or_create(
                        config_entry_id=self.config_entry.entry_id,
                        via_device=self._device.identifiers.copy().pop(),
                        default_model=f"{status.get('typeInfo', '')} Channel {status['channel']}",
                        default_name=name,
                        identifiers={
                            (DOMAIN, f"{self._device.id}-{status['channel']}")
                        },
                        default_manufacturer=self._device.manufacturer,
                    )
                    channel_info[status["channel"]] = _dev_to_info(channel_device)
                else:
                    if not registry:
                        registry = device_registry.async_get(self.hass)
                    channel_device = registry.async_get_device(
                        channel_info[status["channel"]]["identifiers"]
                    )
                    updated = registry.async_update_device(channel_device.id, name=name)
                    if updated and updated != channel_device:
                        channel_info[status["channel"]] = _dev_to_info(updated)

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

        self.async_request_refresh
        self._update_motion = False
        if motion:
            self._motion_coordinator.async_set_updated_data(motion)
        return EntityData(time, drift, abilities, device_info, channel_info, ports)


class ReolinkEntity(CoordinatorEntity[ReolinkDataUpdateCoordinator]):
    """Reolink Entity"""

    entity_description: ReolinkEntityDescription

    def __init__(
        self,
        coordinator: ReolinkDataUpdateCoordinator,
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
        self._attr_name = self._attr_device_info["name"]
        self._attr_name += f" {description.name}"

    def _handle_coordinator_update(self) -> None:
        self._attr_device_info = self.coordinator.data.channels[
            self.entity_description.channel
        ]
        return super()._handle_coordinator_update()


class ReolinkMotionEntity(ReolinkEntity):
    """Reolion Motion Entity"""

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.motion_coordinator.last_update_success
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.motion_coordinator.async_add_listener(
                self._handle_coordinator_motion_update, self.coordinator_context
            )
        )

    @callback
    def _handle_coordinator_motion_update(self) -> None:
        """Handle updated motion data from the coordinator."""
        self.async_write_ha_state()
