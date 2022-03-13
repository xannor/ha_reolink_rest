""" Reolink Intergration """

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import logging
from re import U
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
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo

from reolinkapi.rest import Client
from reolinkapi.rest.connection import Encryption
from reolinkapi.typings import system as rs, network as rn, abilities as ra
from reolinkapi.const import DEFAULT_TIMEOUT
from reolinkapi.exceptions import ReolinkError
from reolinkapi.helpers.ability import NO_ABILITY

import async_timeout

from . import base
from .typings import component

from .utility import astypeddict

from .const import (
    CONF_CHANNELS,
    CONF_USE_HTTPS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_HTTPS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CAMERA, Platform.BINARY_SENSOR]


def get_poll_interval(config_entry: ConfigEntry):
    """Get the poll interval"""
    return timedelta(
        seconds=config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Setup Device"""

    domain_data: component.DomainData | dict[
        str, component.EntryData
    ] = hass.data.setdefault(DOMAIN, {})

    if not config_entry.options:
        hass.config_entries.async_update_entry(
            config_entry, options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL}
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
        else Encryption.AES,
    )

    with async_timeout.timeout(10):
        if not await client.login(
            config_entry.data.get(CONF_USERNAME),
            config_entry.data.get(CONF_PASSWORD),
        ):
            raise ConfigEntryAuthFailed()

    if not client.authenticated:
        raise ConfigEntryNotReady()

    device: dr.DeviceEntry = None
    abilities: ra.Abilities = None
    client_device_info: rs.DeviceInfo = None
    device_info: DeviceInfo = None

    async def _update_data():
        nonlocal device, abilities, client_device_info, device_info
        if not client.authenticated:
            if not await client.login(
                config_entry.data.get(CONF_USERNAME),
                config_entry.data.get(CONF_PASSWORD),
            ):
                await client.disconnect()
                raise ConfigEntryAuthFailed()

        commands = []
        if abilities is None:
            abilities = await client.get_ability()
            if abilities is None:
                return await _update_data()
        else:
            commands.append(Client.create_get_ability())

        commands.append(Client.create_get_network_ports())

        if abilities.get("p2p", NO_ABILITY)["ver"]:
            commands.append(Client.create_get_p2p())

        if abilities.get("localLink", NO_ABILITY)["ver"]:
            commands.append(Client.create_get_local_link())

        if abilities.get("devInfo", NO_ABILITY)["ver"]:
            commands.append(Client.create_get_device_info())
            if (
                client_device_info is not None
                and client_device_info.get("channelNum", 0) > 1
            ):
                commands.append(Client.create_get_channel_status())

        responses = await client.batch(commands)
        if Client.has_auth_failure(responses):
            await client.logout()
            return await _update_data()
        abilities = next(Client.get_ability_responses(responses), abilities)
        if abilities is None:
            await client.disconnect()
            raise ConfigEntryNotReady()
        ports = next(Client.get_network_ports_responses(responses), None)
        if ports is None:
            await client.disconnect()
            raise ConfigEntryNotReady()
        p2p = next(Client.get_p2p_responses(responses), None)
        link = next(Client.get_local_link_responses(responses), None)
        client_device_info = next(
            Client.get_device_info_responses(responses), client_device_info
        )
        _channels = next(Client.get_channel_status_responses(responses), None)
        channels = _channels["status"] if _channels is not None else None
        if (
            channels is None
            and client_device_info is not None
            and client_device_info["channelNum"] > 1
        ):
            channels = await client.get_channel_status()

        device_registry = dr.async_get(hass)
        if device is None:
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
            device_info = DeviceInfo(astypeddict(device, DeviceInfo))
        else:
            device = device_registry.async_update_device(
                device.id,
                name=client_device_info["name"],
                configuration_url=client.base_url,
            )
            device_info.update(astypeddict(device, DeviceInfo))

        return base.ReolinkEntityData(
            client.connection_id,
            next(map(lambda t: t[1], device.identifiers))
            if device.identifiers is not None
            else link["mac"]
            if link is not None
            else "",
            abilities,
            channels,
            ports,
            client_device_info,
            device_info,
        )

    update_coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="Reolink-device-common-data",
        update_interval=get_poll_interval(config_entry),
        update_method=_update_data,
    )

    await update_coordinator.async_config_entry_first_refresh()

    entry_data: component.EntryData = {
        "client": client,
        "coordinator": update_coordinator,
    }
    domain_data[config_entry.entry_id] = entry_data

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
        domain_data: dict[str, component.EntryData] = hass.data.get(DOMAIN, None)
        entry_data = (
            domain_data.pop(config_entry.entry_id, None)
            if domain_data is not None
            else None
        )
        if entry_data is not None:
            await entry_data["client"].disconnect()

    return unload_ok


async def async_update_options(hass: HomeAssistant, config_entry: ConfigEntry):
    """Update options."""
    if CONF_CHANNELS in config_entry.options:
        data = config_entry.data.copy()
        options = config_entry.options.copy()
        data[CONF_CHANNELS] = options.pop(CONF_CHANNELS)
        if hass.config_entries.async_update_entry(
            config_entry, data=data, options=options
        ):
            return
    await hass.config_entries.async_reload(config_entry.entry_id)
