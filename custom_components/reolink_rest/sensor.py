"""Reolink Sensor Platform"""

from dataclasses import dataclass
from enum import IntFlag, auto
import logging
from types import MappingProxyType
from typing import Final
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry, DiscoveryInfoType
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback,
    async_get_current_platform,
)


from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
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


class ReolinkPTZSensorEntityFeature(IntFlag):
    """REOLink Sensor Features"""

    ZOOM = auto()
    FOCUS = auto()


_PTZTYPE_FEATURE_MAP: Final = MappingProxyType(
    {
        capabilities.PTZType.AF: ReolinkPTZSensorEntityFeature.FOCUS
        | ReolinkPTZSensorEntityFeature.ZOOM,
        capabilities.PTZType.PTZ: ReolinkPTZSensorEntityFeature.ZOOM,
        capabilities.PTZType.PTZ_NO_SPEED: ReolinkPTZSensorEntityFeature.ZOOM,
    }
)


@dataclass
class ReolinkPTZSensorEntityDescription(SensorEntityDescription):
    """Describe Reolink PTZ Sensor Entity"""

    has_entity_name: bool = True
    feature: ReolinkPTZSensorEntityFeature | None = None


PTZ_SENSORS: Final = [
    ReolinkPTZSensorEntityDescription(
        key="ptz_focus_position",
        name="Focus",
        icon="mdi:camera-iris",
        feature=ReolinkPTZSensorEntityFeature.FOCUS,
        entity_registry_visible_default=False,
    ),
    ReolinkPTZSensorEntityDescription(
        key="ptz_zoom_position",
        name="Zoom",
        icon="mdi:magnify",
        feature=ReolinkPTZSensorEntityFeature.ZOOM,
        entity_registry_visible_default=False,
    ),
]


async def async_setup_platform(
    _hass: HomeAssistant,
    _config_entry: ConfigEntry,
    _async_add_entities: AddEntitiesCallback,
    _discovery_info: DiscoveryInfoType | None = None,
):
    """Setup sensor platform"""

    platform = async_get_current_platform()

    platform.async_register_entity_service(
        "set_position",
        vol.Schema({"position": int}),
        "async_set_position",
        [
            ReolinkPTZSensorEntityFeature.FOCUS.value,
            ReolinkPTZSensorEntityFeature.ZOOM.value,
        ],
    )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Setup sensor platform"""

    _LOGGER.debug("Setting up sensors")
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
                ReolinkPTZSensorEntityFeature.FOCUS | ReolinkPTZSensorEntityFeature.ZOOM
            )
        elif ability.ptz.type in (
            capabilities.PTZType.PTZ,
            capabilities.PTZType.PTZ_NO_SPEED,
        ):
            features |= ReolinkPTZSensorEntityFeature.ZOOM
        else:
            continue

        for description in PTZ_SENSORS:
            if description.feature not in features:
                continue

            entities.append(ReolinkPTZSensor(coordinator, description, channel))

    if entities:
        async_add_entities(entities)


class ReolinkPTZSensor(ReolinkEntity, SensorEntity):
    """Reolink PTZ Sensor Entity"""

    entity_description: ReolinkPTZSensorEntityDescription

    def __init__(
        self,
        coordinator: ReolinkEntityDataUpdateCoordinator,
        description: ReolinkPTZSensorEntityDescription,
        channel_id: int,
        context: any = None,
    ) -> None:
        SensorEntity.__init__(self)
        ReolinkEntity.__init__(self, coordinator, channel_id, context)
        self.entity_description = description
        self._attr_available = False
        self._attr_supported_features = description.feature

    def _get_state(self):
        if self._attr_supported_features in ReolinkPTZSensorEntityFeature.FOCUS:
            return self.coordinator.data.ptz[self._channel_id].focus
        elif self._attr_supported_features in ReolinkPTZSensorEntityFeature.ZOOM:
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
        return await super().async_added_to_hass()

    async def async_update(self) -> None:
        return await super().async_update()

    async def async_set_position(self, position: int):
        """Set PTZ position"""
        if self._attr_supported_features in ReolinkPTZSensorEntityFeature.FOCUS:
            _op = typings.ZoomOperation.FOCUS
        elif self._attr_supported_features in ReolinkPTZSensorEntityFeature.ZOOM:
            _op = typings.ZoomOperation.ZOOM
        else:
            raise NotImplementedError()
        client = self.coordinator.data.client
        await client.set_ptz_zoomfocus(_op, position, self._channel_id)
        await self.coordinator.async_request_refresh()
