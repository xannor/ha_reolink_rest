"""Reolink Binary Sensor Platform"""

from __future__ import annotations

import logging


from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
)

from .binary_sensors.motion import async_get_binary_sensor_entities

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Setup binary_sensor platform"""

    _LOGGER.debug("Setting up.")

    entities: list[BinarySensorEntity] = []

    entities.extend(await async_get_binary_sensor_entities(hass, config_entry.entry_id))

    if entities:
        async_add_entities(entities)

    _LOGGER.debug("Finished setup")


# async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
#     """Unload Camera Entities"""

#     return True
