""" Reolink Intergration """

from __future__ import annotations
import asyncio
from datetime import timedelta

import logging
from time import time
from types import SimpleNamespace
from typing import Final

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.service import async_extract_config_entry_ids

from homeassistant.const import (
    Platform,
    CONF_SCAN_INTERVAL,
    CONF_HOST,
)

from .entity import create_low_frequency_data_update, create_high_frequency_data_update

from .discovery import async_discovery_handler, DiscoveryDict

from .typing import DomainData

from .const import (
    DEFAULT_HISPEED_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    OPT_DISCOVERY,
    OPT_HISPEED_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: Final = [
    Platform.CAMERA,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    # Platform.SENSOR,
    # Platform.SWITCH,
    # Platform.LIGHT,
    # Platform.BUTTON,
]


def _async_get_poll_interval(config_entry: ConfigEntry):
    """Get the poll interval"""
    interval = config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    return timedelta(seconds=interval)


def _async_get_hispeed_poll_interval(config_entry: ConfigEntry):
    """Get the high speed poll interval"""
    interval = config_entry.options.get(OPT_HISPEED_INTERVAL, DEFAULT_HISPEED_INTERVAL)
    return timedelta(seconds=interval)


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    """Setup ReoLink Component"""

    domain_data: DomainData = hass.data.setdefault(DOMAIN, {})

    await async_discovery_handler(hass)

    async def _reboot_handler(call: ServiceCall):
        _LOGGER.debug("Reboot called.")
        entries: set[str] = await async_extract_config_entry_ids(hass, call)
        for entry_id in entries:
            entry_data = domain_data.get(entry_id, None)
            if entry_data is not None:
                await entry_data.client.reboot()
                hass.create_task(entry_data.coordinator.async_request_refresh())

    hass.services.async_register(DOMAIN, "reboot", _reboot_handler)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ReoLink Device from a config entry."""

    _LOGGER.debug("Setting up entry")

    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))

    domain_data: DomainData = hass.data[DOMAIN]

    entry_data = domain_data.get(entry.entry_id, None)
    if entry_data is None:
        entry_data = domain_data.setdefault(
            entry.entry_id,
            SimpleNamespace(client=None, coordinator=None, hispeed_coordinator=None),
        )

    first_load = False
    coordinator = entry_data.coordinator
    if coordinator is None:
        discovery: DiscoveryDict = entry.options.get(OPT_DISCOVERY, None)
        if discovery is not None and (
            "name" in discovery or "uuid" in discovery or "mac" in discovery
        ):
            name: str = discovery.get(
                "name", discovery.get("uuid", discovery.get("mac", None))
            )
        else:
            name: str = entry.data[CONF_HOST]

        first_load = True
        coordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{name}",
            update_method=create_low_frequency_data_update(hass, entry),
            update_interval=_async_get_poll_interval(entry),
        )
        entry_data.coordinator = coordinator

        def entry_update(_hass: HomeAssistant, entry: ConfigEntry):
            coordinator.update_interval = _async_get_poll_interval(entry)

        entry.add_update_listener(entry_update)

    if entry_data.hispeed_coordinator is None:
        entry_data.hispeed_coordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{name}-hispeed",
            update_method=create_high_frequency_data_update(hass, entry),
            update_interval=_async_get_hispeed_poll_interval(entry),
        )
        await entry_data.hispeed_coordinator.async_config_entry_first_refresh()

    async def setup_platforms():
        if first_load:
            await coordinator.async_config_entry_first_refresh()
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_setup(entry, platform)
                for platform in PLATFORMS
            ]
        )

    hass.async_create_task(setup_platforms())

    return True


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry):
    domain_data: DomainData = hass.data.get(DOMAIN, None)
    if not domain_data:
        return
    entry_data = domain_data.get(entry.entry_id, None)
    if not entry_data:
        return

    # TODO: if the channel options changed we should probably unload/reload to adjust related entities

    try:
        # this could "double" refresh because the options were updated by the coordinator, but that should be rare
        await entry_data.coordinator.async_request_refresh()
    except AttributeError:
        return


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        domain_data: DomainData = hass.data.get(DOMAIN, None)
        if domain_data:
            entry_data = domain_data.pop(entry.entry_id, None)
            if entry_data:
                try:
                    await entry_data.client.disconnect()
                except AttributeError:
                    pass
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Error ocurred while cleaning up entry")

    return unload_ok
