"""Reolink Binary Sensor Platform"""

from __future__ import annotations
from dataclasses import asdict, dataclass
import logging
from re import A
from typing import Final

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
    BinarySensorDeviceClass,
)

from reolinkapi.ai import AITypes

from .entity import (
    ReolinkDataUpdateCoordinator,
    ReolinkDomainData,
    ReolinkEntityDescription,
    ReolinkMotionEntity,
)

from .const import DATA_COORDINATOR, DOMAIN

_LOGGER = logging.getLogger(__name__)


@dataclass
class ReolinkMotionSensorEntityDescription(
    ReolinkEntityDescription, BinarySensorEntityDescription
):
    """Describe Reolink Motion Sensor Entity"""

    ai_type: AITypes | None = None
    device_class: BinarySensorDeviceClass | str | None = BinarySensorDeviceClass.MOTION


SENSORS: Final = [
    ReolinkMotionSensorEntityDescription(
        key="motion_general",
        name="Motion",
    ),
    ReolinkMotionSensorEntityDescription(
        key="motion_ai_animal",
        name="Animal",
        ai_type=AITypes.ANIMAL,
    ),
    ReolinkMotionSensorEntityDescription(
        key="motion_ai_face",
        name="Face",
        ai_type=AITypes.FACE,
    ),
    ReolinkMotionSensorEntityDescription(
        key="motion_ai_person",
        name="Person",
        ai_type=AITypes.PEOPLE,
    ),
    ReolinkMotionSensorEntityDescription(
        key="motion_ai_pet",
        name="Pet",
        ai_type=AITypes.PET,
    ),
    ReolinkMotionSensorEntityDescription(
        key="motion_ai_vehicle",
        name="Vehicle",
        ai_type=AITypes.VEHICLE,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Setup camera platform"""

    _LOGGER.debug("Setting up motion")
    domain_data: ReolinkDomainData = hass.data[DOMAIN]
    coordinator = domain_data[config_entry.entry_id][DATA_COORDINATOR]

    entities = []
    data = coordinator.data
    for channel in data.channels.keys():
        ability = coordinator.data.abilities.channels[channel]
        if not ability.alarm.motion:
            continue

        ai_types = []
        # if ability.support.ai: <- in my tests this ability was not set
        if ability.support.ai.animal:
            ai_types.append(AITypes.ANIMAL)
        if ability.support.ai.face:
            ai_types.append(AITypes.FACE)
        if ability.support.ai.people:
            ai_types.append(AITypes.PEOPLE)
        if ability.support.ai.pet:
            ai_types.append(AITypes.PET)
        if ability.support.ai.vehicle:
            ai_types.append(AITypes.VEHICLE)

        for description in SENSORS:
            if description.ai_type is not None and description.ai_type not in ai_types:
                continue
            description = ReolinkMotionSensorEntityDescription(**asdict(description))
            description.channel = channel
            entities.append(ReolinkMotionSensor(coordinator, description))

    if entities:
        async_add_entities(entities)


class ReolinkMotionSensor(ReolinkMotionEntity, BinarySensorEntity):
    """Reolink Motion Sensor Entity"""

    entity_description: ReolinkMotionSensorEntityDescription

    def __init__(
        self,
        coordinator: ReolinkDataUpdateCoordinator,
        description: ReolinkMotionSensorEntityDescription,
        context: any = None,
    ) -> None:
        BinarySensorEntity.__init__(self)
        ReolinkMotionEntity.__init__(self, coordinator, description, context)

    def _handle_coordinator_motion_update(self) -> None:
        data = self.coordinator.motion_coordinator.data[self.entity_description.channel]
        if self.entity_description.ai_type is None:
            self._attr_is_on = data.motion
        else:
            self._attr_is_on = data.detected.get(self.entity_description.ai_type, False)

        return super()._handle_coordinator_motion_update()
