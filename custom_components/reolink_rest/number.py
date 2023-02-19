"""Reolink Number Platform"""
import logging

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.number import (
    NumberEntity,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, CoordinatorEntity

from .const import DATA_API, DATA_COORDINATOR, DOMAIN, OPT_CHANNELS

from .typing import (
    DomainDataType,
    AsyncEntityInitializedCallback,
    EntityDataHandlerCallback,
    ChannelEntityConfig,
    ResponseCoordinatorType,
)

from .api import ReolinkDeviceApi

from .entity import ChannelDescriptionMixin, ReolinkEntity

from .number_typing import NumberEntityDescription

from ._utilities.typing import bind

_LOGGER = logging.getLogger(__name__)


# async def async_setup_platform(
#     _hass: HomeAssistant,
#     _config_entry: ConfigEntry,
#     _async_add_entities: AddEntitiesCallback,
#     _discovery_info: DiscoveryInfoType | None = None,
# ):
#     """Setup sensor platform"""

#     platform = async_get_current_platform()

#     platform.async_register_entity_service(
#         "set_position",
#         vol.Schema({"position": int}),
#         "async_set_position",
#         [
#             ReolinkPTZSensorEntityFeature.FOCUS.value,
#             ReolinkPTZSensorEntityFeature.ZOOM.value,
#         ],
#     )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Setup number platform"""

    _LOGGER.debug("Setting up numbers")
    domain_data: DomainData = hass.data[DOMAIN]
    entry_data = domain_data[config_entry.entry_id]
    coordinator = entry_data.coordinator

    entities = []
    data = coordinator.data
    _capabilities = data.capabilities

    for channel in data.channels.keys():
        ability = _capabilities.channels[channel]

        features = 0
        if ability.ptz.type == capabilities.PTZType.AF:
            features |= ReolinkPTZNumberEntityFeature.FOCUS | ReolinkPTZNumberEntityFeature.ZOOM
        elif ability.ptz.type in (
            capabilities.PTZType.PTZ,
            capabilities.PTZType.PTZ_NO_SPEED,
        ):
            features |= ReolinkPTZNumberEntityFeature.ZOOM
        else:
            continue

        for description in PTZ_NUMBERS:
            if description.feature not in features:
                continue

            entities.append(ReolinkPTZNumber(coordinator, description, channel))

    if entities:
        async_add_entities(entities)


# async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
#     """Unload Sensor Entities"""

#     return True


class ReolinkNumberEntity(ReolinkEntity, CoordinatorEntity[ResponseCoordinatorType], NumberEntity):
    """Reolink Number Entity"""

    entity_description: NumberEntityDescription

    def __init__(
        self,
        api: ReolinkDeviceApi,
        coordinator: DataUpdateCoordinator[ResponseCoordinatorType],
        description: NumberEntityDescription,
        data_handler: EntityDataHandlerCallback["ReolinkNumberEntity"] | None = None,
        init_handler: AsyncEntityInitializedCallback["ReolinkNumberEntity"] | None = None,
    ) -> None:
        self.entity_description = description
        super().__init__(api, coordinator.config_entry.unique_id, coordinator=coordinator)
        self._data_handler = bind(data_handler, self)
        self._init_handler = bind(init_handler, self)

    def _handle_coordinator_update(self) -> None:
        if self._data_handler:
            self._data_handler()
        return super()._handle_coordinator_update()

    async def async_added_to_hass(self):
        """update"""
        if self._init_handler:
            await self._init_handler()
        return await super().async_added_to_hass()


# class ReolinkPTZNumber(ReolinkEntity, NumberEntity):
#     """Reolink PTZ Sensor Entity"""

#     entity_description: ReolinkPTZNumberEntityDescription
#     _hispeed_callback: CALLBACK_TYPE | None

#     def __init__(
#         self,
#         coordinator: ReolinkEntityDataUpdateCoordinator,
#         description: ReolinkPTZNumberEntityDescription,
#         channel_id: int,
#         context: any = None,
#     ) -> None:
#         NumberEntity.__init__(self)
#         ReolinkEntity.__init__(self, coordinator, channel_id, context)
#         self.entity_description = description
#         self._attr_available = False
#         self._attr_supported_features = description.feature

#     def _get_state(self):
#         if self._attr_supported_features in ReolinkPTZNumberEntityFeature.FOCUS:
#             return self.coordinator.data.ptz[self._channel_id].focus
#         if self._attr_supported_features in ReolinkPTZNumberEntityFeature.ZOOM:
#             return self.coordinator.data.ptz[self._channel_id].zoom
#         return None

#     def _update_state(self, value: int):
#         updated = value != self._attr_native_value if value is not None else False
#         if value is None:
#             self._attr_available = False
#         else:
#             self._attr_available = True
#             self._attr_native_value = value
#         return updated

#     def _update_state_from_queue(self, queue: RequestQueue, only_requeue_on_change: bool = False):
#         commands = self._api.client.commands

#         changed = False
#         for response in queue.responses:
#             if (
#                 commands.is_get_ptz_zoom_focus_response(response)
#                 and response.channel_id == self._channel_id
#             ):
#                 if self._attr_supported_features in ReolinkPTZNumberEntityFeature.FOCUS:
#                     if response.is_detailed:
#                         self._attr_native_min_value = response.state_range.focus.min
#                         self._attr_native_max_value = response.state_range.focus.max
#                     changed |= self._update_state(response.state.focus)
#                 elif self._attr_supported_features in ReolinkPTZNumberEntityFeature.ZOOM:
#                     if response.is_detailed:
#                         self._attr_native_min_value = response.state_range.zoom.min
#                         self._attr_native_max_value = response.state_range.zoom.max
#                     changed |= self._update_state(response.state.zoom)

#         if not only_requeue_on_change or changed:
#             queue.append(commands.create_get_ptz_zoom_focus_request(self._channel_id), True)
#             return True
#         return False

#     def _handle_coordinator_update(self):
#         self._update_state_from_queue(self.coordinator.data)
#         return super()._handle_coordinator_update()

#     def _handle_hispeed_coordinator_update(self):
#         if (
#             not self._update_state_from_queue(self._api.hispeed_coordinator.data, True)
#             and self._hispeed_callback is not None
#         ):
#             self._hispeed_callback()
#             self._hispeed_callback = None
#         return super()._handle_coordinator_update()

#     async def async_added_to_hass(self) -> None:
#         await super().async_added_to_hass()
#         client = self._api.client
#         commands = client.commands
#         queue: RequestQueue = self.coordinator.data
#         request = commands.create_get_ptz_zoom_focus_request(self._channel_id)
#         request.response_type = commands.response_types.DETAILED
#         queue.append(request, True)
#         self.hass.create_task(self.coordinator.async_request_refresh())

#     async def async_update(self) -> None:
#         return await super().async_update()

#     async def async_set_native_value(self, value: float) -> None:
#         if self._attr_supported_features in ReolinkPTZNumberEntityFeature.FOCUS:
#             _op = typing.ZoomOperation.FOCUS
#         elif self._attr_supported_features in ReolinkPTZNumberEntityFeature.ZOOM:
#             _op = typing.ZoomOperation.ZOOM
#         else:
#             raise NotImplementedError()
#         client = self._api.client
#         await client.set_ptz_zoom_focus(int(value), _op, self._channel_id)
#         coordinator = self._api.hispeed_coordinator
#         queue: RequestQueue = coordinator.data
#         queue.append(client.commands.create_get_ptz_zoom_focus_request(self._channel_id), True)
#         self._hispeed_callback = coordinator.async_add_listener(
#             self._handle_hispeed_coordinator_update
#         )
