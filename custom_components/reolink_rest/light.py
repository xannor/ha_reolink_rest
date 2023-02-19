"""Reolink Light Platform"""

import dataclasses
import logging
from typing import MutableSequence, Protocol
from typing_extensions import TypeVar, Self

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.components.light import (
    LightEntity,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, CoordinatorEntity

from .const import DATA_API, DATA_COORDINATOR, DOMAIN, OPT_CHANNELS


from .typing import (
    DomainDataType,
    RequestType,
    ResponseCoordinatorType,
    AsyncEntityInitializedCallback,
    EntityServiceCallback,
)

from .light_typing import LightEntityDescription

from .api import ReolinkDeviceApi

from .entity import (
    ChannelDescriptionMixin,
    ReolinkEntity,
)

from ._utilities.typing import bind

from .setups import floodlight

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Setup light platform"""

    _LOGGER.debug("Setting up.")

    entities: list[LightEntity] = []

    domain_data: DomainDataType = hass.data[DOMAIN]
    entry_data = domain_data[config_entry.entry_id]

    api = entry_data[DATA_API]
    device_data = api.data

    _capabilities = device_data.capabilities

    channels: list[int] = config_entry.options.get(OPT_CHANNELS, None)
    for status in device_data.channel_statuses.values():
        if not status.online or (channels is not None and not status.channel_id in channels):
            continue
        channel_capabilities = _capabilities.channels[status.channel_id]
        info = device_data.channel_info[status.channel_id]

        for light in floodlight.LIGHTS:
            description = light.description
            if (device_supported := light.device_supported) and not device_supported(
                description, _capabilities, device_data
            ):
                continue
            if channel_supported := light.channel_supported:
                # pylint: disable=not-callable
                if not channel_supported(description, channel_capabilities, info):
                    continue
                if isinstance(description, ChannelDescriptionMixin):
                    description = description.from_channel(info)

            entities.append(
                ReolinkLightEntity(
                    api,
                    entry_data[DATA_COORDINATOR],
                    description,
                    light.on_call,
                    light.off_call,
                    light.init_handler,
                )
            )

    if entities:
        async_add_entities(entities)

    _LOGGER.debug("Finished setup")


# async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
#     """Unload Light Entities"""

#     return True


class ReolinkLightEntity(ReolinkEntity, CoordinatorEntity[ResponseCoordinatorType], LightEntity):
    """Reolink Light Entity"""

    entity_description: LightEntityDescription
    coordinator_context: MutableSequence[RequestType] | None

    def __init__(
        self,
        api: ReolinkDeviceApi,
        coordinator: DataUpdateCoordinator[ResponseCoordinatorType],
        description: LightEntityDescription,
        on_call: EntityServiceCallback["ReolinkLightEntity"],
        off_call: EntityServiceCallback["ReolinkLightEntity"],
        init_handler: AsyncEntityInitializedCallback["ReolinkLightEntity"] = None,
    ) -> None:
        self.entity_description = description
        super().__init__(api, coordinator.config_entry.unique_id, coordinator=coordinator)
        self._on_call = bind(on_call, self)
        self._off_call = bind(off_call, self)
        self._init_handler = bind(init_handler, self)

    def turn_off(self, **kwargs: any):
        self.hass.create_task(self.async_turn_off(**kwargs))

    def turn_on(self, **kwargs: any):
        self.hass.create_task(self.async_turn_on(**kwargs))

    async def async_turn_on(self, **kwargs: any):
        await self._on_call(**kwargs)

    async def async_turn_off(self, **kwargs: any):
        await self._off_call(**kwargs)

    async def async_added_to_hass(self):
        """update"""
        if self._init_handler:
            await self._init_handler()
        return await super().async_added_to_hass()
