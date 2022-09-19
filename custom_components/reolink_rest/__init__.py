""" Reolink Intergration """

from __future__ import annotations
import asyncio

import logging
from typing import Final
import async_timeout

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.service import async_extract_config_entry_ids
from homeassistant.const import Platform

from .discovery import async_discovery_handler

from .entity import (
    async_get_poll_interval,
    ReolinkEntityData,
)

from .typing import ReolinkDomainData

from .const import (
    DATA_COORDINATOR,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: Final = [
    Platform.CAMERA,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    # Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    """Setup ReoLink Component"""

    await async_discovery_handler(hass)

    domain_data: ReolinkDomainData = hass.data.setdefault(DOMAIN, {})

    async def _reboot_handler(call: ServiceCall):
        _LOGGER.debug("Reboot called.")
        entries: set[str] = await async_extract_config_entry_ids(hass, call)
        for entry_id in entries:
            entry_data = domain_data[entry_id]
            await entry_data["coordinator"].data.client.reboot()
            hass.create_task(entry_data["coordinator"].async_request_refresh())

    hass.services.async_register(DOMAIN, "reboot", _reboot_handler)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ReoLink Device from a config entry."""

    _LOGGER.debug("Setting up entry")

    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))

    domain_data: ReolinkDomainData = hass.data.setdefault(DOMAIN, {})

    entry_data = domain_data.setdefault(entry.entry_id, {})
    coordinator = entry_data.get(DATA_COORDINATOR, None)
    if coordinator is None:
        first_attempt = True

        async def _update_data():
            nonlocal first_attempt

            if first_attempt:
                first_attempt = False
                async with async_timeout.timeout(15):
                    return await entity_data.async_update()
            return await entity_data.async_update()

        entity_data = ReolinkEntityData(hass, entry)
        coordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{entity_data.name}",
            update_method=_update_data,
            update_interval=async_get_poll_interval(entry),
        )
        entry_data[DATA_COORDINATOR] = coordinator

    await coordinator.async_config_entry_first_refresh()

    hass.async_create_task(async_setup_platforms(hass, entry))

    return True


async def async_setup_platforms(hass: HomeAssistant, entry: ConfigEntry):
    """Setup platforms."""

    await asyncio.gather(
        *[
            hass.config_entries.async_forward_entry_setup(entry, platform)
            for platform in PLATFORMS
        ]
    )


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry):
    domain_data: ReolinkDomainData = hass.data.get(DOMAIN, None)
    if not domain_data:
        return
    entry_data = domain_data.get(entry.entry_id, None)
    if not entry_data:
        return
    coordinator = entry_data.get(DATA_COORDINATOR, None)
    if not coordinator:
        return
    # TODO: if the channel options changed we should probably unload/reload to adjust related entities

    # this could "double" refresh because the options were updated by the coordinator, but that should be rare
    await coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        domain_data: ReolinkDomainData = hass.data.get(DOMAIN, None)
        if domain_data:
            entry_data = domain_data.pop(entry.entry_id, None)
            if entry_data:
                client = entry_data.pop("client", None)
                if client:
                    try:
                        await client.disconnect()
                    except Exception:  # pylint: disable=broad-except
                        _LOGGER.exception("Error ocurred while cleaning up entry")

    return unload_ok
