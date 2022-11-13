""" Reolink Intergration """

from __future__ import annotations
import asyncio
from datetime import timedelta

import logging
from typing import Final

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.service import async_extract_config_entry_ids

from homeassistant.const import (
    Platform,
    CONF_SCAN_INTERVAL,
)

from .discovery import async_discovery_handler

from .api import (
    async_get_poll_interval,
    async_get_hispeed_poll_interval,
    async_update_client_data,
    async_update_queue,
    async_get_entry_data,
)

from .const import (
    DEFAULT_HISPEED_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    OPT_HISPEED_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: Final = [
    Platform.CAMERA,
    Platform.BINARY_SENSOR,
    # Platform.NUMBER,
    # Platform.SENSOR,
    # Platform.SWITCH,
    # Platform.LIGHT,
    # Platform.BUTTON,
]


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    """Setup ReoLink Component"""

    # ensure data exists
    hass.data.setdefault(DOMAIN, {})

    await async_discovery_handler(hass)

    async def _reboot_handler(call: ServiceCall):
        _LOGGER.debug("Reboot called.")
        entries: set[str] = await async_extract_config_entry_ids(hass, call)
        for entry_id in entries:
            if entry_data := async_get_entry_data(hass, entry_id):
                if "client" in entry_data:
                    await entry_data["client"].reboot()
                    hass.create_task(entry_data["coordinator"].async_request_refresh())

    hass.services.async_register(DOMAIN, "reboot", _reboot_handler)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ReoLink Device from a config entry."""

    _LOGGER.debug("Setting up entry")

    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))

    entry_data = async_get_entry_data(hass, entry.entry_id)

    first_load = False
    if not (coordinator := entry_data.get("coordinator", None)):
        first_load = True

        async def update_method():
            return await async_update_client_data(coordinator)

        coordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name=f"{entry.title} Update Coordinator",
            update_interval=async_get_poll_interval(entry.options),
            update_method=update_method,
        )
        entry_data["coordinator"] = coordinator

    if not (hispeed_coordinator := entry_data.get("hispeed_coordinator", None)):

        async def hispeed_update_method():
            return await async_update_queue(hispeed_coordinator)

        hispeed_coordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name=f"{entry.title} Hi-Frequency Update Coordinator",
            update_interval=async_get_hispeed_poll_interval(entry.options),
            update_method=hispeed_update_method,
        )
        entry_data["hispeed_coordinator"] = hispeed_coordinator

    async def setup_platforms():
        if first_load:
            await coordinator.async_config_entry_first_refresh()
            await hispeed_coordinator.async_config_entry_first_refresh()

        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_setup(entry, platform)
                for platform in PLATFORMS
            ]
        )

    hass.async_create_task(setup_platforms())

    return True


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry):
    entry_data = async_get_entry_data(hass, entry.entry_id, False)
    if not entry_data:
        return

    if coordinator := entry_data.get("coordinator", None):
        coordinator.update_interval = async_get_poll_interval(entry.options)

    if hispeed_coordinator := entry_data.get("hispeed_coordinator", None):
        hispeed_coordinator.update_interval = async_get_hispeed_poll_interval(
            entry.options
        )

    # TODO: if the channel options changed we should probably unload/reload to adjust related entities

    if coordinator:
        await coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        if entry_data := async_get_entry_data(hass, entry.entry_id, False):
            if client := entry_data.get("client", None):
                try:
                    await client.disconnect()
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
