"""Reolink Light Platform"""

import logging
from typing import Final


from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from homeassistant.components.light import LightEntity

from ._utilities.bind import bind

from .const import OPT_CHANNELS

from .api import QueueResponse, RequestQueue, async_get_entry_data

from .entity import (
    ReolinkEntity,
)

from .lights.model import ReolinkLightEntityDescription
from .lights.floodlight import LIGHTS as FLOODLIGHTS


_LOGGER = logging.getLogger(__name__)


class ReolinkLightEntity(ReolinkEntity[QueueResponse], LightEntity):
    """Reolink Light Entity"""

    coordinator_context: RequestQueue
    _attr_brightness_max: int = 255

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[QueueResponse],
        description: ReolinkLightEntityDescription,
    ) -> None:
        LightEntity.__init__(self)
        self.entity_description = description
        ReolinkEntity.__init__(self, coordinator, RequestQueue())
        self._turn_on = bind(self, description.on_call)
        self._turn_off = bind(self, description.off_call)

    async def async_turn_on(self, **kwargs: any) -> None:
        return await self._turn_on(**kwargs)

    async def async_turn_off(self, **kwargs: any) -> None:
        return await self._turn_off(**kwargs)


LIGHTS: Final = FLOODLIGHTS


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Setup light platform"""

    _LOGGER.debug("Setting up.")

    entities: list[LightEntity] = []

    entry_data = async_get_entry_data(hass, config_entry.entry_id, False)
    config_entry = hass.config_entries.async_get_entry(config_entry.entry_id)
    api = entry_data["client_data"]

    _capabilities = api.capabilities

    channels: list[int] = config_entry.options.get(OPT_CHANNELS, None)
    for status in api.channel_statuses.values():
        if not status.online or (
            channels is not None and not status.channel_id in channels
        ):
            continue
        channel_capabilities = _capabilities.channels[status.channel_id]
        info = api.channel_info[status.channel_id]

        for light in LIGHTS:
            description = light
            if (device_supported := description.device_supported_fn) and not (
                description := device_supported(description, _capabilities, api)
            ):
                continue
            if (channel_supported := description.channel_supported_fn) and not (
                description := channel_supported(
                    description, channel_capabilities, info
                )
            ):
                continue

            entities.append(ReolinkLightEntity(entry_data["coordinator"], description))

    if entities:
        async_add_entities(entities)

    _LOGGER.debug("Finished setup")


# async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
#     """Unload Light Entities"""

#     return True
