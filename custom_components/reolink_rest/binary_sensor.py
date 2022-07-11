"""Reolink Binary Sensor Platform"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
import logging
from typing import Final

from aiohttp.web import Request

from homeassistant.core import HomeAssistant, CALLBACK_TYPE
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
    BinarySensorDeviceClass,
)

from reolinkapi.ai import AITypes

from .push import async_get_push_manager, async_parse_notification

from .webhook import async_get_webhook_manager

from .entity import (
    ReolinkDataUpdateCoordinator,
    ReolinkEntityDescription,
    ReolinkMotionEntity,
)

from .typing import ReolinkDomainData

from .const import DATA_COORDINATOR, DOMAIN

_LOGGER = logging.getLogger(__name__)

DATA_MOTION_DEBOUNCE: Final = "onvif_motion_debounce"


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

MOTION_DEBOUCE: Final = timedelta(seconds=2)

DATA_STORAGE: Final = "onvif_storage"


async def _handle_onvif_notify(hass: HomeAssistant, request: Request):
    motion = await async_parse_notification(request)
    if motion is None:
        return None

    # the motion event is fairly useless since it is just a motion changed "somwhere"
    # and not an explicit this is or is not detecting motion
    # it sometimes will send a IsMotion false but not always realiably so instead
    # we will "debounce" a final refresh request for
    domain_data: ReolinkDomainData = hass.data[DOMAIN]
    entry_data = domain_data[request["entry_id"]]
    _ed: dict = entry_data
    _cb: CALLBACK_TYPE = _ed.pop(DATA_MOTION_DEBOUNCE, None)
    if _cb:
        _cb()

    # ideally we would get better notices from onvif, but since we only know
    # motion is/was happening we have to poll for any detail

    async def _try_again(*_):
        _ed.pop(DATA_MOTION_DEBOUNCE, None)
        await entry_data[DATA_COORDINATOR].motion_coordinator.async_request_refresh()

    if motion != "false":
        _ed[DATA_MOTION_DEBOUNCE] = async_track_point_in_utc_time(
            hass, _try_again, dt.utcnow() + MOTION_DEBOUCE
        )

    # hand off refresh to task so we dont hold the hook too long
    hass.create_task(
        entry_data[DATA_COORDINATOR].motion_coordinator.async_request_refresh()
    )

    return None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Setup camera platform"""

    _LOGGER.debug("Setting up motion")
    domain_data: ReolinkDomainData = hass.data[DOMAIN]
    entry_data = domain_data[config_entry.entry_id]
    coordinator = entry_data[DATA_COORDINATOR]

    entities = []
    data = coordinator.data
    push_setup = not data.abilities.onvif

    for channel in data.channels.keys():
        ability = coordinator.data.abilities.channels[channel]
        if not ability.alarm.motion:
            continue

        if not push_setup:
            push_setup = True
            webhook = async_get_webhook_manager(hass, _LOGGER, config_entry)
            if webhook:
                config_entry.async_on_unload(
                    webhook.async_add_handler(_handle_onvif_notify)
                )
                push = async_get_push_manager(hass, _LOGGER, config_entry, webhook)

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
