""" Reolink Intergration """

from __future__ import annotations
import asyncio
from datetime import timedelta

import logging
from typing import Final

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.service import async_extract_config_entry_ids

from homeassistant.const import (
    Platform,
    CONF_SCAN_INTERVAL,
)

from .api import ReolinkRestApi

from .discovery import async_discovery_handler


from .const import (
    DEFAULT_HISPEED_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    OPT_HISPEED_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: Final = [
    Platform.CAMERA,
    # Platform.BINARY_SENSOR,
    # Platform.NUMBER,
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

    domain_data: dict = hass.data.setdefault(DOMAIN, {})

    await async_discovery_handler(hass)

    async def _reboot_handler(call: ServiceCall):
        _LOGGER.debug("Reboot called.")
        entries: set[str] = await async_extract_config_entry_ids(hass, call)
        for entry_id in entries:
            api: ReolinkRestApi = domain_data.get(entry_id, None)
            if api is not None:
                await api.client.reboot()
                hass.create_task(api.coordinator.async_request_refresh())

    hass.services.async_register(DOMAIN, "reboot", _reboot_handler)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ReoLink Device from a config entry."""

    _LOGGER.debug("Setting up entry")

    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))

    domain_data: dict = hass.data.setdefault(DOMAIN, {})

    first_load = False
    api: ReolinkRestApi = domain_data.get(entry.entry_id, None)
    if api is None:
        api = ReolinkRestApi()
        domain_data[entry.entry_id] = api
        first_load = True

    async def setup_platforms():
        if first_load:
            await api.async_initialize(hass, _LOGGER)
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_setup(entry, platform)
                for platform in PLATFORMS
            ]
        )

    hass.async_create_task(setup_platforms())

    return True


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry):
    domain_data: dict = hass.data.get(DOMAIN, None)
    if not domain_data:
        return
    api: ReolinkRestApi = domain_data.get(entry.entry_id, None)
    if not api:
        return

    # TODO: if the channel options changed we should probably unload/reload to adjust related entities

    try:
        # this could "double" refresh because the options were updated by the coordinator, but that should be rare
        await api.coordinator.async_request_refresh()
    except AttributeError:
        return


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        domain_data: dict = hass.data.get(DOMAIN, None)
        if domain_data:
            api: ReolinkRestApi = domain_data.pop(entry.entry_id, None)
            if api:
                try:
                    await api.client.disconnect()
                except AttributeError:
                    pass
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Error ocurred while cleaning up entry")

    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
):
    """Remove device from configuration"""
    return False
