"""Reolink Number Platform"""

from dataclasses import dataclass
from enum import IntFlag, auto
import logging
from types import MappingProxyType
from typing import Final

# import voluptuous as vol

from homeassistant.core import HomeAssistant, CALLBACK_TYPE
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback,
    # async_get_current_platform,
)
from homeassistant.helpers.entity import EntityCategory

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
)

from async_reolink.api.system import capabilities
from async_reolink.api.ptz import typing

from .const import DOMAIN

from .entity import (
    ReolinkEntity,
    ReolinkEntityDataUpdateCoordinator,
)

from .typing import DomainData, RequestQueue


_LOGGER = logging.getLogger(__name__)


class ReolinkPTZNumberEntityFeature(IntFlag):
    """REOLink Sensor Features"""

    ZOOM = auto()
    FOCUS = auto()


_PTZTYPE_FEATURE_MAP: Final = MappingProxyType(
    {
        capabilities.PTZType.AF: ReolinkPTZNumberEntityFeature.FOCUS
        | ReolinkPTZNumberEntityFeature.ZOOM,
        capabilities.PTZType.PTZ: ReolinkPTZNumberEntityFeature.ZOOM,
        capabilities.PTZType.PTZ_NO_SPEED: ReolinkPTZNumberEntityFeature.ZOOM,
    }
)


@dataclass
class ReolinkPTZNumberEntityDescription(NumberEntityDescription):
    """Describe Reolink PTZ Sensor Entity"""

    has_entity_name: bool = True
    entity_category: EntityCategory | None = EntityCategory.CONFIG
    feature: ReolinkPTZNumberEntityFeature | None = None


PTZ_NUMBERS: Final = [
    ReolinkPTZNumberEntityDescription(
        key="ptz_focus_position",
        name="Focus",
        icon="mdi:camera-iris",
        feature=ReolinkPTZNumberEntityFeature.FOCUS,
        native_min_value=1,
        native_max_value=64,
        native_step=1,
    ),
    ReolinkPTZNumberEntityDescription(
        key="ptz_zoom_position",
        name="Zoom",
        icon="mdi:magnify",
        feature=ReolinkPTZNumberEntityFeature.ZOOM,
        native_min_value=1,
        native_max_value=64,
        native_step=1,
    ),
]


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
            features |= (
                ReolinkPTZNumberEntityFeature.FOCUS | ReolinkPTZNumberEntityFeature.ZOOM
            )
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


class ReolinkPTZNumber(ReolinkEntity, NumberEntity):
    """Reolink PTZ Sensor Entity"""

    entity_description: ReolinkPTZNumberEntityDescription
    _hispeed_callback: CALLBACK_TYPE | None

    def __init__(
        self,
        coordinator: ReolinkEntityDataUpdateCoordinator,
        description: ReolinkPTZNumberEntityDescription,
        channel_id: int,
        context: any = None,
    ) -> None:
        NumberEntity.__init__(self)
        ReolinkEntity.__init__(self, coordinator, channel_id, context)
        self.entity_description = description
        self._attr_available = False
        self._attr_supported_features = description.feature

    def _get_state(self):
        if self._attr_supported_features in ReolinkPTZNumberEntityFeature.FOCUS:
            return self.coordinator.data.ptz[self._channel_id].focus
        if self._attr_supported_features in ReolinkPTZNumberEntityFeature.ZOOM:
            return self.coordinator.data.ptz[self._channel_id].zoom
        return None

    def _update_state(self, value: int):
        updated = value != self._attr_native_value if value is not None else False
        if value is None:
            self._attr_available = False
        else:
            self._attr_available = True
            self._attr_native_value = value
        return updated

    def _update_state_from_queue(
        self, queue: RequestQueue, only_requeue_on_change: bool = False
    ):
        commands = self._entry_data.client.commands

        changed = False
        for response in queue.responses:
            if (
                commands.is_get_ptz_zoom_focus_response(response)
                and response.channel_id == self._channel_id
            ):
                if self._attr_supported_features in ReolinkPTZNumberEntityFeature.FOCUS:
                    if response.is_detailed:
                        self._attr_native_min_value = response.state_range.focus.min
                        self._attr_native_max_value = response.state_range.focus.max
                    changed |= self._update_state(response.state.focus)
                elif (
                    self._attr_supported_features in ReolinkPTZNumberEntityFeature.ZOOM
                ):
                    if response.is_detailed:
                        self._attr_native_min_value = response.state_range.zoom.min
                        self._attr_native_max_value = response.state_range.zoom.max
                    changed |= self._update_state(response.state.zoom)

        if not only_requeue_on_change or changed:
            queue.append(
                commands.create_get_ptz_zoom_focus_request(self._channel_id), True
            )
            return True
        return False

    def _handle_coordinator_update(self):
        self._update_state_from_queue(self.coordinator.data)
        return super()._handle_coordinator_update()

    def _handle_hispeed_coordinator_update(self):
        if (
            not self._update_state_from_queue(
                self._entry_data.hispeed_coordinator.data, True
            )
            and self._hispeed_callback is not None
        ):
            self._hispeed_callback()
            self._hispeed_callback = None
        return super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        client = self._entry_data.client
        commands = client.commands
        queue: RequestQueue = self.coordinator.data
        request = commands.create_get_ptz_zoom_focus_request(self._channel_id)
        request.response_type = commands.response_types.DETAILED
        queue.append(request, True)
        self.hass.create_task(self.coordinator.async_request_refresh())

    async def async_update(self) -> None:
        return await super().async_update()

    async def async_set_native_value(self, value: float) -> None:
        if self._attr_supported_features in ReolinkPTZNumberEntityFeature.FOCUS:
            _op = typing.ZoomOperation.FOCUS
        elif self._attr_supported_features in ReolinkPTZNumberEntityFeature.ZOOM:
            _op = typing.ZoomOperation.ZOOM
        else:
            raise NotImplementedError()
        client = self._entry_data.client
        await client.set_ptz_zoom_focus(int(value), _op, self._channel_id)
        coordinator = self._entry_data.hispeed_coordinator
        queue: RequestQueue = coordinator.data
        queue.append(
            client.commands.create_get_ptz_zoom_focus_request(self._channel_id), True
        )
        self._hispeed_callback = coordinator.async_add_listener(
            self._handle_hispeed_coordinator_update
        )
