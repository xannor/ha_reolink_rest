"""Reolink Light Platform"""

import dataclasses
import logging
from typing import TYPE_CHECKING, MutableSequence, Protocol, cast
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
    ChannelEntityConfig,
    DomainDataType,
    EntityDataHandlerCallback,
    RequestType,
    ResponseCoordinatorType,
    AsyncEntityInitializedCallback,
    AsyncEntityServiceCallback,
)

from .light_typing import LighEntityConfig, LightEntityDescription

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

    setups = floodlight.LIGHTS

    for init in setups:
        first_channel = True

        for status in device_data.channel_statuses.values():
            if not status.online or (channels is not None and not status.channel_id in channels):
                continue
            channel_capabilities = _capabilities.channels[status.channel_id]
            info = device_data.channel_info[status.channel_id]

            description = init.description
            if (device_supported := init.device_supported) and not device_supported(
                description, _capabilities, device_data
            ):
                continue

            if isinstance(init, ChannelEntityConfig):
                channel_supported = init.channel_supported
            elif not first_channel:
                # if this is not a channel based sensor, but we have a multi-channel device
                # we need to ensure we dont create multiple entities
                continue
            else:
                channel_supported = None

            if first_channel:
                first_channel = False

            if channel_supported:
                # pylint: disable=not-callable
                if not channel_supported(description, channel_capabilities, info):
                    continue
                if isinstance(description, ChannelDescriptionMixin):
                    description = description.from_channel(info)

            if TYPE_CHECKING:
                init = cast(LighEntityConfig, init)

            entities.append(
                ReolinkLightEntity(
                    api,
                    entry_data[DATA_COORDINATOR],
                    description,
                    init.on_call,
                    init.off_call,
                    init.data_handler,
                    init.init_handler,
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
        on_call: AsyncEntityServiceCallback["ReolinkLightEntity"],
        off_call: AsyncEntityServiceCallback["ReolinkLightEntity"],
        data_handler: EntityDataHandlerCallback["ReolinkLightEntity"] | None = None,
        init_handler: AsyncEntityInitializedCallback["ReolinkLightEntity"] | None = None,
    ) -> None:
        self.entity_description = description
        super().__init__(api, coordinator.config_entry.unique_id, coordinator=coordinator)
        self.__on_call = on_call
        self.__off_call = off_call
        self.__data_handler = data_handler
        self.__init_handler = init_handler

    def turn_off(self, **kwargs: any):
        self.hass.create_task(self.async_turn_off(**kwargs))

    def turn_on(self, **kwargs: any):
        self.hass.create_task(self.async_turn_on(**kwargs))

    async def async_turn_on(self, **kwargs: any):
        await self.__on_call(self, **kwargs)

    async def async_turn_off(self, **kwargs: any):
        await self.__off_call(self, **kwargs)

    def _handle_coordinator_update(self) -> None:
        if self.__data_handler:
            self.__data_handler(self)
        return super()._handle_coordinator_update()

    async def async_added_to_hass(self):
        """update"""
        if self.__init_handler:
            await self.__init_handler(self)
        return await super().async_added_to_hass()
