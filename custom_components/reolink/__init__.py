""" Reolink Intergration """

from __future__ import annotations
import logging
from typing import Mapping

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.typing import ConfigType

from .settings import Settings, async_get_settings
from .discovery import DISCOVERY_INTERVAL, async_start_discovery

from .const import (
    DOMAIN,
    SETTING_DISCOVERY,
    SETTING_DISCOVERY_INTERVAL,
    SETTING_DISCOVERY_STARTUP,
)

_LOGGER = logging.getLogger(__name__)

# PLATFORMS = [Platform.CAMERA, Platform.BINARY_SENSOR]
PLATFORMS = []


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Setup ReoLink Component"""

    settings: Settings = await async_get_settings(hass)
    discovery: Mapping[str, any] = settings.get(SETTING_DISCOVERY, {})
    if discovery.get(SETTING_DISCOVERY_STARTUP, True):
        async_start_discovery(
            hass, discovery.get(SETTING_DISCOVERY_INTERVAL, DISCOVERY_INTERVAL)
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ReoLink Device from a config entry."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data[entry.entry_id] = True

    hass.config_entries.async_setup_platforms(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
