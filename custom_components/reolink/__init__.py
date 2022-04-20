""" Reolink Intergration """

from __future__ import annotations

from datetime import timedelta
import logging
from typing import cast
import aiohttp

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_TIMEOUT,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
    CONF_SCAN_INTERVAL,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from reolinkapi.rest import Client
from reolinkapi.rest.connection import Encryption
from reolinkapi.const import DEFAULT_TIMEOUT
from reolinkapi.typings import system as rs, abilities as ra
from reolinkapi.helpers.abilities.ability import NO_ABILITY
from reolinkapi import helpers as clientHelpers

import async_timeout

from .utility import astypeddict

from .typings.component import DomainData, HassDomainData, EntityData

from .const import (
    CONF_CHANNELS,
    CONF_MOTION_INTERVAL,
    CONF_USE_AES,
    CONF_USE_HTTPS,
    DEFAULT_MOTION_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_AES,
    DEFAULT_USE_HTTPS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CAMERA, Platform.BINARY_SENSOR]


def _create_async_update_entity_data(
    config_entry: ConfigEntry, client: Client, device_registry: dr.DeviceRegistry
):
    entity_data: EntityData = None
    device_id: str = None

    async def async_update_data() -> EntityData:
        nonlocal entity_data, device_id

        if not client.authenticated:
            if not await client.login(
                config_entry.data.get(CONF_USERNAME),
                config_entry.data.get(CONF_PASSWORD),
            ):
                await client.disconnect()
                raise ConfigEntryAuthFailed()

        commands = []
        if entity_data is None:
            abilities = await client.get_ability(config_entry.data.get(CONF_USERNAME))
            if client.authentication_required:
                await client.disconnect()
                raise ConfigEntryAuthFailed()
            if abilities is None:
                raise ConfigEntryNotReady()
        else:
            abilities = entity_data.abilities
            commands.append(clientHelpers.system.create_get_ability())

        commands.append(clientHelpers.network.create_get_network_ports())

        if abilities.get("p2p", NO_ABILITY)["ver"]:
            commands.append(clientHelpers.network.create_get_p2p())

        if abilities.get("localLink", NO_ABILITY)["ver"]:
            commands.append(clientHelpers.network.create_get_local_link())

        if abilities.get("devInfo", NO_ABILITY)["ver"]:
            commands.append(clientHelpers.system.create_get_device_info())
            if (
                entity_data is not None
                and entity_data.client_device_info.get("channelNum", 0) > 1
            ):
                commands.append(clientHelpers.network.create_get_channel_status())

        responses = await client.batch(commands)
        if clientHelpers.security.has_auth_failure(responses):
            await client.logout()
            return await async_update_data()
        abilities = next(
            clientHelpers.system.get_ability_responses(responses), abilities
        )
        if abilities is None:
            await client.disconnect()
            raise ConfigEntryNotReady()
        ports = next(clientHelpers.network.get_network_ports_responses(responses), None)
        if ports is None:
            await client.disconnect()
            raise ConfigEntryNotReady()
        p2p = next(clientHelpers.network.get_p2p_responses(responses), None)
        link = next(clientHelpers.network.get_local_link_responses(responses), None)
        client_device_info = next(
            clientHelpers.system.get_devinfo_responses(responses),
            entity_data.client_device_info if entity_data is not None else None,
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
            channels = await client.get_channel_status()

        if device_id is None:
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

            device = device_registry.async_get_or_create(
                config_entry_id=config_entry.entry_id,
                default_manufacturer="Reolink",
                default_name=client_device_info["name"],
                identifiers=identifiers,
                connections=connections,
                sw_version=client_device_info["firmVer"],
                hw_version=client_device_info["hardVer"],
                default_model=client_device_info["model"],
                configuration_url=client.base_url,
            )
            device_id = device.id
            device_info = DeviceInfo(astypeddict(device, DeviceInfo))
        else:
            uid = entity_data.uid
            device_info = entity_data.device_info
            device_info.update(
                astypeddict(
                    device_registry.async_update_device(
                        device_id,
                        name=client_device_info["name"],
                        configuration_url=client.base_url,
                    ),
                    DeviceInfo,
                )
            )

        entity_data = EntityData(
            client.connection_id,
            uid if uid is not None else link["mac"] if link is not None else "",
            abilities,
            channels,
            ports,
            client_device_info,
            device_info,
        )
        return entity_data

    return async_update_data


def get_poll_interval(config_entry: ConfigEntry):
    """Get the poll interval"""
    interval = config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    return timedelta(seconds=interval)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Setup Device"""

    domain_data: DomainData = hass.data.setdefault(DOMAIN, DomainData())

    if not config_entry.options:
        options = {
            CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
            CONF_MOTION_INTERVAL: DEFAULT_MOTION_INTERVAL,
            CONF_USE_AES: DEFAULT_USE_AES,
        }
        data = config_entry.data.copy()
        channels = data.pop(CONF_CHANNELS, None)
        if channels is not None:
            options[CONF_CHANNELS] = channels
        hass.config_entries.async_update_entry(
            config_entry,
            data=data,
            options=options,
        )

    client = Client(
        lambda base_url, timeout: async_create_clientsession(
            hass, False, base_url=base_url, timeout=aiohttp.ClientTimeout(total=timeout)
        )
    )
    await client.connect(
        config_entry.data.get(CONF_HOST),
        config_entry.data.get(CONF_PORT),
        config_entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
        encryption=Encryption.HTTPS
        if config_entry.data.get(CONF_USE_HTTPS, DEFAULT_USE_HTTPS)
        else Encryption.AES
        if config_entry.options.get(CONF_USE_AES, DEFAULT_USE_AES)
        else Encryption.NONE,
    )

    with async_timeout.timeout(10):
        if not await client.login(
            config_entry.data.get(CONF_USERNAME),
            config_entry.data.get(CONF_PASSWORD),
        ):
            raise ConfigEntryAuthFailed()

    if not client.authenticated:
        raise ConfigEntryNotReady()

    update_coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}-common-data",
        update_interval=get_poll_interval(config_entry),
        update_method=_create_async_update_entity_data(
            config_entry, client, dr.async_get(hass)
        ),
    )

    await update_coordinator.async_config_entry_first_refresh()
    domain_data.register_entry(config_entry.entry_id, client, update_coordinator)

    hass.config_entries.async_setup_platforms(config_entry, PLATFORMS)

    if not config_entry.update_listeners:
        config_entry.async_on_unload(
            config_entry.add_update_listener(async_update_options)
        )

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Unload Device"""
    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, PLATFORMS
    )
    if unload_ok:
        if (
            domain_data := cast(HassDomainData, hass.data).get(DOMAIN, None)
        ) and domain_data is not None:
            if (
                entry_data := domain_data.remove_entry(config_entry.entry_id)
            ) and entry_data is not None:
                await entry_data.client.disconnect()

    return unload_ok


async def async_update_options(hass: HomeAssistant, config_entry: ConfigEntry):
    """Update options."""
    await hass.config_entries.async_reload(config_entry.entry_id)
