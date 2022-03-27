""""Base Components"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    Debouncer,
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import EntityDescription, DeviceInfo

from reolinkapi.rest import Client
from reolinkapi.typings import system as rs, network as rn, abilities as ra
from reolinkapi.helpers.abilities.ability import NO_ABILITY
from reolinkapi import helpers as clientHelpers

from .const import DOMAIN
from .utility import astypeddict


@dataclass
class EntityData:
    """Reolink Client Entity Data"""

    connection_id: int
    uid: str
    abilities: ra.Abilities
    channels: list[rn.ChannelStatus] | None
    ports: rn.NetworkPorts
    abilities: ra.Abilities
    client_device_info: rs.DeviceInfo | None
    device_info: DeviceInfo


class EntityDataUpdateCoordinator(DataUpdateCoordinator[EntityData]):
    """Entity Data Update Coordinator"""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        client: Client,
        *,
        name: str,
        update_interval: timedelta | None = None,
        request_refresh_debouncer: Debouncer | None = None,
    ) -> None:
        super().__init__(
            hass,
            logger,
            name=name,
            update_interval=update_interval,
            update_method=None,
            request_refresh_debouncer=request_refresh_debouncer,
        )
        self.client = client
        self._device: dr.DeviceEntry | None = None

    async def async_stop(self):
        """stop update coordinator (similar to shutdown event)"""
        self._async_stop_refresh(None)
        await self.client.disconnect()
        self.client = None

    async def _async_update_data(self):
        abilities = self.data.abilities if self.data is not None else None
        client_device_info = (
            self.data.client_device_info if self.data is not None else None
        )
        device_info = self.data.device_info if self.data is not None else None

        if not self.client.authenticated:
            if not await self.client.login(
                self.config_entry.data.get(CONF_USERNAME),
                self.config_entry.data.get(CONF_PASSWORD),
            ):
                await self.client.disconnect()
                raise ConfigEntryAuthFailed()

        commands = []
        if abilities is None:
            abilities = await self.client.get_ability(
                self.config_entry.data.get(CONF_USERNAME)
            )
            if self.client.authentication_required:
                await self.client.disconnect()
                raise ConfigEntryAuthFailed()
            if abilities is None:
                raise ConfigEntryNotReady()
        else:
            commands.append(clientHelpers.system.create_get_ability())

        commands.append(clientHelpers.network.create_get_network_ports())

        if abilities.get("p2p", NO_ABILITY)["ver"]:
            commands.append(clientHelpers.network.create_get_p2p())

        if abilities.get("localLink", NO_ABILITY)["ver"]:
            commands.append(clientHelpers.network.create_get_local_link())

        if abilities.get("devInfo", NO_ABILITY)["ver"]:
            commands.append(clientHelpers.system.create_get_device_info())
            if (
                client_device_info is not None
                and client_device_info.get("channelNum", 0) > 1
            ):
                commands.append(clientHelpers.network.create_get_channel_status())

        responses = await self.client.batch(commands)
        if clientHelpers.security.has_auth_failure(responses):
            await self.client.logout()
            return await self._async_update_data()
        abilities = next(
            clientHelpers.system.get_ability_responses(responses), abilities
        )
        if abilities is None:
            await self.client.disconnect()
            raise ConfigEntryNotReady()
        ports = next(clientHelpers.network.get_network_ports_responses(responses), None)
        if ports is None:
            await self.client.disconnect()
            raise ConfigEntryNotReady()
        p2p = next(clientHelpers.network.get_p2p_responses(responses), None)
        link = next(clientHelpers.network.get_local_link_responses(responses), None)
        client_device_info = next(
            clientHelpers.system.get_devinfo_responses(responses),
            client_device_info,
        )
        _channels = next(
            clientHelpers.network.get_channel_status_responses(responses), None
        )
        channels = _channels["status"] if _channels is not None else None
        if (
            channels is None
            and client_device_info is not None
            and client_device_info["channelNum"] > 1
        ):
            channels = await self.client.get_channel_status()

        device_registry = dr.async_get(self.hass)
        if self._device is None:
            _type = (
                client_device_info.get("exactType", client_device_info["type"])
                if client_device_info is not None
                else ""
            )
            uid = (
                p2p["uid"]
                if p2p is not None
                else f'{_type}-{client_device_info["serial"]}'
                if client_device_info is not None
                else None
            )
            identifiers = {(DOMAIN, uid)} if uid is not None else None
            connections = (
                {(dr.CONNECTION_NETWORK_MAC, link["mac"])} if link is not None else None
            )

            self._device = device_registry.async_get_or_create(
                config_entry_id=self.config_entry.entry_id,
                default_manufacturer="Reolink",
                default_name=client_device_info["name"],
                identifiers=identifiers,
                connections=connections,
                sw_version=client_device_info["firmVer"],
                hw_version=client_device_info["hardVer"],
                default_model=client_device_info["model"],
                configuration_url=self.client.base_url,
            )
            device_info = DeviceInfo(astypeddict(self._device, DeviceInfo))
        else:
            self._device = device_registry.async_update_device(
                self._device.id,
                name=client_device_info["name"],
                configuration_url=self.client.base_url,
            )
            device_info.update(astypeddict(self._device, DeviceInfo))

        return EntityData(
            self.client.connection_id,
            next(map(lambda t: t[1], self._device.identifiers))
            if self._device.identifiers is not None
            else link["mac"]
            if link is not None
            else "",
            abilities,
            channels,
            ports,
            client_device_info,
            device_info,
        )


class ReolinkEntity(CoordinatorEntity[EntityData]):
    """Base class for Reolink Entities"""

    def __init__(
        self,
        update_coordinator: EntityDataUpdateCoordinator,
        channel_id: int,
        description: EntityDescription = None,
    ):
        self._channel_id = channel_id
        super().__init__(update_coordinator)
        self.entity_description = description
        self._enabled = False
        self._attr_device_info = self.coordinator.data.device_info
        self.coordinator = update_coordinator

    @property
    def _channel_ability(self):
        return self.coordinator.data.abilities["abilityChn"][self._channel_id]

    @property
    def _channel_status(self):
        if self.coordinator.data.channels is None:
            return None
        return next(
            (
                channel
                for channel in self.coordinator.data.channels
                if channel["channel"] == self._channel_id
            ),
            None,
        )
