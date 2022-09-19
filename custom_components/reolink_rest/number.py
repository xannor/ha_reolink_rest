"""Reolink Number Platform"""

from dataclasses import dataclass
from enum import IntFlag, auto
import logging
from types import MappingProxyType
from typing import Final

# import voluptuous as vol

from homeassistant.core import HomeAssistant
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
from async_reolink.api.ptz import typings

from .entity import (
    ReolinkEntity,
    ReolinkEntityDataUpdateCoordinator,
)

from .typing import ReolinkDomainData

from .const import DATA_COORDINATOR, DOMAIN


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
    domain_data: ReolinkDomainData = hass.data[DOMAIN]
    entry_data = domain_data[config_entry.entry_id]
    coordinator = entry_data[DATA_COORDINATOR]

    entities = []
    data = coordinator.data
    abilities = data.abilities

    for channel in data.channels.keys():
        ability = abilities.channels[channel]

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
        elif self._attr_supported_features in ReolinkPTZNumberEntityFeature.ZOOM:
            return self.coordinator.data.ptz[self._channel_id].zoom
        return None

    def _update_state(self, value: int):
        if value is None:
            self._attr_available = False
        else:
            self._attr_available = True
            self._attr_native_value = value

    def _handle_coordinator_update(self) -> None:
        self._update_state(self._get_state())
        return super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        self._update_state(self._get_state())
        if self._attr_supported_features in ReolinkPTZNumberEntityFeature.FOCUS:
            if (
                _range := self.coordinator.data.ptz[self._channel_id].focus_range
            ) is not None:
                self._attr_native_min_value = _range.min
                self._attr_native_max_value = _range.max
        elif self._attr_supported_features in ReolinkPTZNumberEntityFeature.ZOOM:
            if (
                _range := self.coordinator.data.ptz[self._channel_id].zoom_range
            ) is not None:
                self._attr_native_min_value = _range.min
                self._attr_native_max_value = _range.max
        return await super().async_added_to_hass()

    async def async_update(self) -> None:
        return await super().async_update()

    async def async_set_native_value(self, value: float) -> None:
        if self._attr_supported_features in ReolinkPTZNumberEntityFeature.FOCUS:
            _op = typings.ZoomOperation.FOCUS
        elif self._attr_supported_features in ReolinkPTZNumberEntityFeature.ZOOM:
            _op = typings.ZoomOperation.ZOOM
        else:
            raise NotImplementedError()
        client = self.coordinator.data.client
        await client.set_ptz_zoomfocus(int(value), _op, self._channel_id)
        await self.coordinator.async_request_refresh()
