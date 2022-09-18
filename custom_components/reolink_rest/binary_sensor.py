"""Reolink Binary Sensor Platform"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
import logging
from typing import Final, cast

from aiohttp.web import Request

from homeassistant.core import HomeAssistant, CALLBACK_TYPE
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
    BinarySensorDeviceClass,
)

from async_reolink.api.ai import AITypes

from .push import async_get_push_manager, async_parse_notification

from .webhook import async_get_webhook_manager

from .entity import (
    ReolinkEntity,
    ReolinkEntityData,
    ReolinkEntityDataUpdateCoordinator,
    async_get_motion_poll_interval,
)

from .typing import ReolinkDomainData

from .const import DATA_COORDINATOR, DATA_MOTION_COORDINATORS, DOMAIN

_LOGGER = logging.getLogger(__name__)

DATA_MOTION_DEBOUNCE: Final = "onvif_motion_debounce"


@dataclass
class ReolinkMotionSensorEntityDescription(BinarySensorEntityDescription):
    """Describe Reolink Motion Sensor Entity"""

    has_entity_name: bool = True
    ai_type: AITypes | None = None
    device_class: BinarySensorDeviceClass | str | None = BinarySensorDeviceClass.MOTION


MOTION_SENSORS: Final = [
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

    async def _refresh():
        coordinator = entry_data[DATA_COORDINATOR]
        if not coordinator.last_update_success:
            return
        data: ReolinkEntityData = coordinator.data
        for channel in entry_data[DATA_MOTION_COORDINATORS].keys():
            data.async_request_motion_update(channel)
        try:
            await data.async_update_motion_data()
        except Exception:  # pylint: disable=broad-except
            # since we are updating outside a coordinator, we need to handle errors
            await coordinator.async_request_refresh()
        for _coordinator in entry_data[DATA_MOTION_COORDINATORS].values():
            _coordinator.async_set_updated_data(data)

    async def _try_again(*_):
        _ed.pop(DATA_MOTION_DEBOUNCE, None)
        await _refresh()

    if motion != "false":
        _ed[DATA_MOTION_DEBOUNCE] = async_track_point_in_utc_time(
            hass, _try_again, dt.utcnow() + MOTION_DEBOUCE
        )

    # hand off refresh to task so we dont hold the hook too long
    hass.create_task(_refresh())

    return None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Setup binary_sensor platform"""

    def _setup_hooks(
        channel: int, motion_coordinator: ReolinkEntityDataUpdateCoordinator
    ):
        add_listener = motion_coordinator.async_add_listener

        def _coord_update():
            if channel in coordinator.data.updated_motion:
                motion_coordinator.async_set_updated_data(coordinator.data)

        coord_cleanup = None

        def _add_listener(update_callback: CALLBACK_TYPE, context: any = None):
            nonlocal coord_cleanup
            # pylint: disable = protected-access
            if len(motion_coordinator._listeners) == 0:
                coord_cleanup = coordinator.async_add_listener(_coord_update)

            cleanup = add_listener(update_callback, context)

            def _cleanup():
                cleanup()
                if len(motion_coordinator._listeners) == 0:
                    coord_cleanup()

            return _cleanup

        motion_coordinator.async_add_listener = _add_listener

    _LOGGER.debug("Setting up binary sensors")
    domain_data: ReolinkDomainData = hass.data[DOMAIN]
    entry_data = domain_data[config_entry.entry_id]
    coordinator = entry_data[DATA_COORDINATOR]

    entities = []
    data = coordinator.data
    push_setup = True
    abilities = data.abilities

    for channel in data.channels.keys():
        ability = abilities.channels[channel]
        if not ability.alarm.motion:
            continue

        push_setup = False

        ai_types = []
        # if ability.support.ai: <- in my tests this ability was not set
        if ability.supports.ai.animal:
            ai_types.append(AITypes.ANIMAL)
        if ability.supports.ai.face:
            ai_types.append(AITypes.FACE)
        if ability.supports.ai.people:
            ai_types.append(AITypes.PEOPLE)
        if ability.supports.ai.pet:
            ai_types.append(AITypes.PET)
        if ability.supports.ai.vehicle:
            ai_types.append(AITypes.VEHICLE)

        motion_coordinator = None
        for description in MOTION_SENSORS:
            if description.ai_type is not None and description.ai_type not in ai_types:
                continue

            if motion_coordinator is None:
                coordinators = entry_data.setdefault(DATA_MOTION_COORDINATORS, {})
                if channel not in coordinators:
                    motion_coordinator = DataUpdateCoordinator(
                        hass,
                        _LOGGER,
                        name=f"{coordinator.name}-motion",
                        update_interval=async_get_motion_poll_interval(config_entry),
                        update_method=cast(
                            ReolinkEntityData, coordinator.data
                        ).async_update_motion_data,
                    )
                    coordinators[channel] = motion_coordinator
                    motion_coordinator.data = data

                    _setup_hooks(channel, motion_coordinator)

                else:
                    motion_coordinator: ReolinkEntityDataUpdateCoordinator = (
                        coordinators[channel]
                    )

            entities.append(
                ReolinkMotionSensor(motion_coordinator, description, channel)
            )

    if entities:
        async_add_entities(entities)

    if not push_setup and abilities.onvif:
        webhooks = async_get_webhook_manager(hass)
        if webhooks is not None:
            webhook = webhooks.async_register(hass, config_entry)
            config_entry.async_on_unload(
                webhook.async_add_handler(_handle_onvif_notify)
            )
            push = async_get_push_manager(hass)
            subscription = None

            async def _async_sub():
                nonlocal subscription
                subscription = await push.async_subscribe(webhook.url, config_entry)
                if subscription is not None:
                    for coordinator in coordinators.values():
                        coordinator.update_interval = None

            resub_cleanup = None
            onvif_warned = False

            def _sub_failure(entry_id: str):
                nonlocal subscription, resub_cleanup, onvif_warned
                if entry_id != config_entry.entry_id or config_entry.data is None:
                    return
                if subscription is not None:
                    for _coordinator in coordinators.values():
                        _coordinator.update_interval = async_get_motion_poll_interval(
                            config_entry
                        )
                        hass.create_task(_coordinator.async_request_refresh())
                subscription = None
                if not coordinator.data.ports.onvif.enabled and not onvif_warned:
                    onvif_warned = True
                    coordinator.logger.warning(
                        "ONVIF not enabled for device %s, forcing polling mode",
                        coordinator.data.device.name,
                    )

                def _sub_resub():
                    nonlocal resub_cleanup
                    resub_cleanup()
                    resub_cleanup = None
                    hass.create_task(_async_sub())

                resub_cleanup = coordinator.async_add_listener(_sub_resub)

            sub_fail_cleanup = push.async_on_subscription_failure(_sub_failure)

            await _async_sub()

            def _unsubscribe():
                sub_fail_cleanup()
                if resub_cleanup is not None:
                    resub_cleanup()  # pylint: disable=not-callable
                if subscription is not None:
                    hass.create_task(push.async_unsubscribe(subscription))

            config_entry.async_on_unload(_unsubscribe)


class ReolinkMotionSensor(ReolinkEntity, BinarySensorEntity):
    """Reolink Motion Sensor Entity"""

    entity_description: ReolinkMotionSensorEntityDescription

    def __init__(
        self,
        coordinator: ReolinkEntityDataUpdateCoordinator,
        description: ReolinkMotionSensorEntityDescription,
        channel_id: int,
        context: any = None,
    ) -> None:
        BinarySensorEntity.__init__(self)
        ReolinkEntity.__init__(self, coordinator, channel_id, context)
        self.entity_description = description

    def _handle_coordinator_update(self) -> None:
        data = self.coordinator.data.motion[self._channel_id]
        _LOGGER.info("Motion<-%r", data)
        if self.entity_description.ai_type is None:
            self._attr_is_on = data.detected
        else:
            self._attr_is_on = data.get(self.entity_description.ai_type, False)
        return super()._handle_coordinator_update()

    async def async_update(self) -> None:
        return await super().async_update()

    @property
    def extra_state_attributes(self):
        return {
            "update_method": "push"
            if self.coordinator.update_interval is None
            else "poll"
        }
