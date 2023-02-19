"""Motion Entities"""

from asyncio import Task
import dataclasses
from typing import TYPE_CHECKING, Callable, Final, Protocol


from homeassistant.core import CALLBACK_TYPE, Event, callback
from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)

from .motion_typing import ChannelMotionEventData, MotionEvent, MotionEventData

from ..typing import DomainDataType, RequestHandler

if TYPE_CHECKING:
    from homeassistant.helpers import dispatcher as dispatcher_helper

from async_reolink.api.ai.command import AiStateResponseState
from async_reolink.api.ai.typing import AITypes

from async_reolink.rest.ai.model import (
    ai_types_str,
)
from async_reolink.rest.ai import command as ai_command
from async_reolink.rest.alarm import command as alarm_command

from .._utilities.object import lazysetdefaultattr, setdefaultattr
from .._utilities.typeguards import is_type
from .._utilities.hass_typing import hass_bound

from ..api import ChannelData

from ..entity import ReolinkEntity, UpdateMethods

from ..const import DATA_HISPEED_COORDINDATOR, DATA_ONVIF, DOMAIN

from ..binary_sensor_typing import (
    BinarySensorChannelEntityConfig,
    BinarySensorEntityChannelDescription,
)


@dataclasses.dataclass
class ReolinkMotionSensorEntityDescription(BinarySensorEntityChannelDescription):
    """Reolink Motion Sensor Entity Description"""

    has_entity_name: bool = True
    device_class: BinarySensorDeviceClass | str | None = BinarySensorDeviceClass.MOTION


_MOTION_EVENT: Final = DOMAIN + "_motion_event"


class ChannelMotionData(ChannelData, Protocol):
    """Channel Motion Data"""

    motion_task: Task[bool]
    ai_state_task: Task[AiStateResponseState]


async def _init_handler(self: BinarySensorEntity, event_handler: Callable[[MotionEventData], None]):
    # pylint: disable=protected-access
    if not isinstance(self, ReolinkEntity):
        raise ValueError()

    channel_id = self._channel_id
    domain_data: DomainDataType = self.hass.data[DOMAIN]
    entry_data = domain_data[self._entry_id]
    # we will use the hispeed coordinator for polling
    coordinator = entry_data[DATA_HISPEED_COORDINDATOR]
    coord_cleanup: CALLBACK_TYPE = None

    def handle_data():
        pass

    def unsubscribe():
        nonlocal coord_cleanup
        cleanup = coord_cleanup
        if not cleanup:
            return
        coord_cleanup = None
        cleanup()

    def subscribe():
        nonlocal coord_cleanup
        if coord_cleanup is not None:
            return
        coord_cleanup = coordinator.async_add_listener(handle_data, self.coordinator_context)

    subscribe()

    @callback
    def event_listener(event: Event):
        nonlocal coord_cleanup
        data: MotionEventData
        if not (data := event.data) or not is_type(data, MotionEventData):
            return

        if is_type(data, MotionEvent) and (method := data.get("method")) is not None:
            method = UpdateMethods(method)
            if method != UpdateMethods.POLL and coord_cleanup:
                unsubscribe()
            elif method == UpdateMethods.POLL and not coord_cleanup:
                subscribe()
            self._attr_extra_state_attributes.update({"update_method": method})
            self.async_schedule_update_ha_state()

        if (
            is_type(data, MotionEvent)
            and (channels := data.get("channels")) is not None
            and isinstance(channels, list)
        ):
            data = next(
                filter(
                    lambda c: c.get("channel_id") == channel_id
                    if is_type(c, ChannelMotionEventData)
                    else False,
                    channels,
                ),
                None,
            )

        if not is_type(data, MotionEventData):
            return

        event_handler(data)

    @callback
    def event_filter(event: Event):
        if not event.data or not isinstance(event.data, dict):
            return False

        return any(k in event.data for k in ("method", "detected", "ai", "channels"))

    unique_id = coordinator.config_entry.unique_id or coordinator.config_entry.entry_id

    _EVENT = entry_data.setdefault("motion_event", f"{_MOTION_EVENT}_{unique_id}")
    cleanup = self.hass.bus.async_listen(_EVENT, event_listener, event_filter, True)
    self.async_on_remove(cleanup)

    lazysetdefaultattr(self, "_attr_extra_state_attributes", dict).update(
        {"event": _EVENT, "update_method": self._update_method}
    )


async def _ai_init_handler(self: BinarySensorEntity):
    # pylint: disable=protected-access
    if not isinstance(self, ReolinkEntity):
        raise ValueError()

    description: ReolinkAIMotionSensorEntityDescription = self.entity_description
    channel_data: ChannelMotionData = self._device_data.channel_info[self._channel_id]
    # dispatcher: dispatcher_helper = self.hass.helpers.dispatcher
    # dispatcher_send = hass_bound(dispatcher.dispatcher_send)
    # async_dispatcher_send = hass_bound(dispatcher.async_dispatcher_send)
    # signal = f"{DOMAIN}_{self.coordinator.config_entry.entry_id}_{description.key}"

    setdefaultattr(channel_data, "ai_state_task", None)

    def update_state(state: bool):
        if state == self._attr_is_on:
            return False
        self._attr_is_on = state
        return True

    def response_handler(response: ai_command.GetAiStateResponse):
        state = response.state.get(description.ai_type)
        if state is not None:
            self._attr_available = bool(state.supported)
            if update_state(bool(state.state)):
                pass
                # dispatcher_send(signal, self)
        else:
            self._attr_available = False

    self.coordinator_context = (
        RequestHandler(
            ai_command.GetAiStateRequest(channel_id=channel_data.channel_id), response_handler
        ),
    )

    async def fetch_state(force=False, sync=True):
        if (task := channel_data.ai_state_task) is not None:
            _ai = await task
        else:
            # share fetch task with rapid calls
            task = self.hass.async_create_task(self._client.get_ai_state(channel_data.channel_id))
            channel_data.ai_state_task = task

            def clear_task():
                channel_data.ai_state_task = None

            _ai = await task
            # "cache" results for a second for other threads or rapid calls
            self.hass.loop.call_later(1, clear_task)
        state = _ai[description.ai_type]
        if state.supported is False:
            self._attr_available = False
            if sync:
                self.async_schedule_update_ha_state()
        elif update_state(state.state) and sync:
            self.async_schedule_update_ha_state()
            # async_dispatcher_send(signal, self)

    ai_key = ai_types_str(description.ai_type)

    def event_handler(data: MotionEventData):
        if (_ai := data.get("ai")) is not None:
            if not isinstance(_ai, dict) or (motion := _ai.get(ai_key)) is None:
                self.hass.create_task(fetch_state())
                return
        elif (motion := data.get("detected")) is True:
            self.hass.create_task(fetch_state())
            return
        elif motion is None:
            return

        if update_state(bool(motion)):
            self.schedule_update_ha_state()
            # dispatcher_send(signal, self)

    await _init_handler(self, event_handler)


async def _motion_init_handler(self: BinarySensorEntity):
    # pylint: disable=protected-access
    if not isinstance(self, ReolinkEntity):
        raise ValueError()

    description: ReolinkMotionSensorEntityDescription = self.entity_description
    channel_data: ChannelMotionData = self._device_data.channel_info[self._channel_id]
    dispatcher: dispatcher_helper = self.hass.helpers.dispatcher
    dispatcher_send = hass_bound(dispatcher.dispatcher_send)
    async_dispatcher_send = hass_bound(dispatcher.async_dispatcher_send)
    signal = f"{DOMAIN}_{self._entry_id}_{description.key}"
    multi_channel = len(self._device_data.channel_info) > 0

    setdefaultattr(channel_data, "motion_task", None)

    def update_state(state: bool):
        if state == self._attr_is_on:
            return False
        self._attr_is_on = state
        return True

    def response_handler(response: alarm_command.GetMotionStateResponse):
        self._attr_available = True
        if update_state(bool(response.state)):
            dispatcher_send(signal, self)

    self.coordinator_context = (
        RequestHandler(
            alarm_command.GetMotionStateRequest(channel_id=channel_data.channel_id),
            response_handler,
        ),
    )

    async def fetch_state(force=False, sync=True):
        if multi_channel or force:
            if (task := channel_data.motion_task) is not None:
                motion = await task
            else:
                # share fetch task with rapid calls
                task = self.hass.async_create_task(
                    self._client.get_md_state(channel_data.channel_id)
                )
                channel_data.motion_task = task

                def clear_task():
                    channel_data.motion_task = None

                motion = await task
                # "cache" results for a second for other threads or rapid calls
                self.hass.loop.call_later(1, clear_task)

            self._attr_available = True
            if update_state(motion) and sync:
                self.async_schedule_update_ha_state()
                async_dispatcher_send(signal, self)

    def event_handler(data: MotionEventData):
        if (motion := data.get("detected")) is None or (motion and multi_channel):
            self.hass.create_task(fetch_state())
            return

        if update_state(bool(motion)):
            self.schedule_update_ha_state()
            dispatcher_send(signal, self)

    entry = self.hass.config_entries.async_get_entry(self._entry_id)
    if (
        entry.options.get(DATA_ONVIF, True)
        and (client_data := self._device_data)
        and (capabilities := client_data.capabilities)
        and capabilities.onvif
        and (not capabilities.supports.onvif_enable or client_data.ports.onvif.enabled)
        and client_data.ports.onvif.value > 0
    ):
        from ..services.onvif import async_get as async_get_onvif, Error as OnvifError

        domain_data: DomainDataType = self.hass.data[DOMAIN]
        entry_id = self._entry_id
        entry_data = domain_data[entry_id]
        if DATA_ONVIF not in entry_data:
            service = async_get_onvif(self.hass)
            entry_data[DATA_ONVIF] = True

            enabled = False

            def onvif_handler(value: OnvifError | bool | MotionEvent | None):
                nonlocal enabled
                if is_type(value, MotionEvent):
                    data = value
                else:
                    data = {}
                if isinstance(value, OnvifError):
                    if not enabled:
                        return
                    enabled = False
                    data["method"] = UpdateMethods.POLL
                else:
                    if not enabled:
                        enabled = True
                        data["method"] = UpdateMethods.PUSH_POLL
                    if value is not None and value is not dict:
                        data["detected"] = bool(value)
                self.hass.bus.async_fire(entry_data["motion_event"], data)

            self.hass.config_entries.async_get_entry(entry_id).async_on_unload(
                service.async_subscribe(entry_id, onvif_handler)
            )

    await _init_handler(self, event_handler)
    await fetch_state(True, False)


_MOTION: Final = BinarySensorChannelEntityConfig.create(
    ReolinkMotionSensorEntityDescription("motion", name="Motion"),
    lambda _self, channel, _data: channel.alarm.motion or channel.supports.motion_detection,
    init_handler=_motion_init_handler,
)


@dataclasses.dataclass
class ReolinkAIMotionSensorEntityDescriptionMixin:
    """Mixin for required keys"""

    ai_type: AITypes


@dataclasses.dataclass
class ReolinkAIMotionSensorEntityDescription(
    ReolinkMotionSensorEntityDescription, ReolinkAIMotionSensorEntityDescriptionMixin
):
    """Reolink AI Motion Sensor Entity Description"""

    key: str = None

    def __post_init__(self):
        if self.key is None:
            self.key = f"{_MOTION.description.key}_{ai_types_str(self.ai_type)}"
        if self.name is None:
            self.name = f"{self.ai_type.name.title()} {_MOTION.description.name}"


_AI_MOTION: Final = tuple(
    BinarySensorChannelEntityConfig.create(
        ReolinkAIMotionSensorEntityDescription(ai),
        lambda self, channel, data: _MOTION.channel_supported(self, channel, data)
        and bool(getattr(channel.supports.ai, ai_types_str(self.ai_type), None)),
        init_handler=_ai_init_handler,
    )
    for ai in AITypes
)

BINARY_SENSORS = (_MOTION,) + _AI_MOTION
