""" Reolink Intergration """

from __future__ import annotations
import asyncio

import logging
from typing import Final

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry, SOURCE_INTEGRATION_DISCOVERY
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.discovery import async_listen
from homeassistant.helpers.discovery_flow import async_create_flow
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.typing import DiscoveryInfoType
from homeassistant.const import Platform

from .entity import (
    async_get_motion_poll_interval,
    async_get_poll_interval,
    create_channel_motion_data_update_method,
    create_device_data_update_method,
)

from .typing import ReolinkDomainData, ReolinkEntryData

from .const import (
    DATA_COORDINATOR,
    DISCOVERY_EVENT,
    DOMAIN,
    OPT_DISCOVERY,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: Final = [Platform.CAMERA, Platform.BINARY_SENSOR]


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    """Setup ReoLink Component"""

    async def _discovery(service: str, info: DiscoveryInfoType):
        if service == DISCOVERY_EVENT:
            for entry in hass.config_entries.async_entries(DOMAIN):
                if OPT_DISCOVERY in entry.options:
                    discovery: dict = entry.options[OPT_DISCOVERY]
                    key = "uuid"
                    if not key in discovery or not key in info:
                        key = "mac"
                    if key in discovery and key in info and discovery[key] == info[key]:
                        if next(
                            (
                                True
                                for k in info
                                if k not in discovery or discovery[k] != info[k]
                            ),
                            False,
                        ):
                            options = entry.options.copy()
                            options[OPT_DISCOVERY] = discovery = discovery.copy()
                            discovery.update(info)

                            if not hass.config_entries.async_update_entry(
                                entry, options=options
                            ):
                                _LOGGER.warning("Could not update options")
                        return

            async_create_flow(
                hass, DOMAIN, {"source": SOURCE_INTEGRATION_DISCOVERY}, info
            )

    async_listen(hass, DISCOVERY_EVENT, _discovery)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ReoLink Device from a config entry."""

    _LOGGER.debug("Setting up entry")

    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))

    domain_data: ReolinkDomainData = hass.data.setdefault(DOMAIN, {})

    entry_data = domain_data.setdefault(entry.entry_id, {})
    coordinator = entry_data.get("coordinator", None)
    # if setup fails we do not want to recreate the coordinators
    if coordinator is None:
        coordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-DataUpdateCoordinator-{entry.entry_id}",
            update_interval=async_get_poll_interval(entry),
            update_method=create_device_data_update_method(entry_data),
        )
        entry_data["coordinator"] = coordinator
    motion_coordinator = entry_data.get("motion_coordinator", None)
    if motion_coordinator is None:
        motion_coordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-Motion-DataUpdateCooridator-{entry.entry_id}",
            update_interval=async_get_motion_poll_interval(entry),
            update_method=create_channel_motion_data_update_method(entry_data),
        )
        entry_data["motion_coordinator"] = motion_coordinator
        entry_data["motion_data_request"] = set()

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
