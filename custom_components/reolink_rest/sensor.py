"""sensor platform"""

import logging
from typing import Final
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from homeassistant.components.sensor import SensorEntity

from .const import OPT_CHANNELS

from .api import QueueResponse, RequestQueue, async_get_entry_data

from .entity import ReolinkEntity, ChannelSupportedMixin

from .sensors.model import (
    ReolinkSensorEntityDescription,
)

from .sensors import storage

# async def async_setup_platform(
#     hass: HomeAssistant,
#     config_entry: ConfigEntry,
#     async_add_entities: AddEntitiesCallback,
#     discovery_info: DiscoveryInfoType | None = None,
# ):
#     """Setup Sensor platform"""
#
#     platform = async_get_current_platform()

_LOGGER = logging.getLogger(__name__)


class ReolinkSensorEntity(ReolinkEntity[QueueResponse], SensorEntity):
    """Reolink Sensor Entity"""

    coordinator_context: RequestQueue

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[QueueResponse],
        description: ReolinkSensorEntityDescription,
    ) -> None:
        SensorEntity.__init__(self)
        self.entity_description = description
        ReolinkEntity.__init__(self, coordinator, RequestQueue())


SENSORS: Final[tuple[ReolinkSensorEntityDescription, ...]] = tuple()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Setup Sensor Entities"""

    _LOGGER.debug("Setting up.")

    entities: list[SensorEntity] = []

    entry_data = async_get_entry_data(hass, config_entry.entry_id, False)
    config_entry = hass.config_entries.async_get_entry(config_entry.entry_id)
    api = entry_data["client_data"]

    _capabilities = api.capabilities

    sensors = SENSORS + await storage.get_sensors(entry_data)

    channels: list[int] = config_entry.options.get(OPT_CHANNELS, None)
    for status in api.channel_statuses.values():
        if not status.online or (
            channels is not None and not status.channel_id in channels
        ):
            continue
        channel_capabilities = _capabilities.channels[status.channel_id]
        info = api.channel_info[status.channel_id]

        for sensor in sensors:
            description = sensor
            if (device_supported := description.device_supported_fn) and not (
                description := device_supported(description, _capabilities, api)
            ):
                continue
            if (
                isinstance(description, ChannelSupportedMixin)
                and (channel_supported := description.channel_supported_fn)
                and not (
                    description := channel_supported(
                        description, channel_capabilities, info
                    )
                )
            ):
                continue

            entities.append(ReolinkSensorEntity(entry_data["coordinator"], description))

    if entities:
        async_add_entities(entities)

    _LOGGER.debug("Finished setup")


# async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
#     """Unload Sensor Entities"""

#     return True
