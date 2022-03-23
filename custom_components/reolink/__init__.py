""" Reolink Intergration """

from __future__ import annotations

from datetime import timedelta
import logging
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
from httpx import options

from reolinkapi.rest import Client
from reolinkapi.rest.connection import Encryption
from reolinkapi.const import DEFAULT_TIMEOUT

import async_timeout

from .models import DataUpdateCoordinatorStop

from .entity import EntityDataUpdateCoordinator

from .typings import component

from .const import (
    CONF_CHANNELS,
    CONF_MOTION_INTERVAL,
    CONF_USE_AES,
    CONF_USE_HTTPS,
    DATA_COORDINATOR,
    DATA_MOTION_COORDINATOR,
    DEFAULT_MOTION_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_AES,
    DEFAULT_USE_HTTPS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CAMERA, Platform.BINARY_SENSOR]


def get_poll_interval(config_entry: ConfigEntry):
    """Get the poll interval"""
    interval = config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    return timedelta(seconds=interval)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Setup Device"""

    domain_data: dict = hass.data.setdefault(DOMAIN, {})

    if not config_entry.options:
        hass.config_entries.async_update_entry(
            config_entry,
            options={
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                CONF_MOTION_INTERVAL: DEFAULT_MOTION_INTERVAL,
                CONF_USE_AES: DEFAULT_USE_AES,
            },
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

    update_coordinator = EntityDataUpdateCoordinator(
        hass,
        _LOGGER,
        client,
        name=f"{DOMAIN}-common-data",
        update_interval=get_poll_interval(config_entry),
    )

    await update_coordinator.async_config_entry_first_refresh()

    domain_data[config_entry.entry_id] = {DATA_COORDINATOR: update_coordinator}

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, client.disconnect)

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
        if DOMAIN in hass.data:
            domain_data: dict = hass.data[DOMAIN]
            if config_entry.entry_id in domain_data:
                entry_data: dict = domain_data.pop(config_entry.entry_id)
                if DATA_MOTION_COORDINATOR in entry_data:
                    coordinator: DataUpdateCoordinatorStop = entry_data[
                        DATA_MOTION_COORDINATOR
                    ]
                    await coordinator.async_stop()
                if DATA_COORDINATOR in entry_data:
                    coordinator: DataUpdateCoordinatorStop = entry_data[
                        DATA_COORDINATOR
                    ]
                    await coordinator.async_stop()

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
