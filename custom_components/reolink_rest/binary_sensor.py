"""Reolink Binary Sensor Platform"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from types import SimpleNamespace
from typing import Final, Mapping, Protocol

from aiohttp.web import Request

from homeassistant.core import HomeAssistant, CALLBACK_TYPE
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt

from homeassistant.loader import async_get_integration

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
    BinarySensorDeviceClass,
)

from async_reolink.api.system import capabilities
from async_reolink.api.ai.typing import AITypes, Config as AIConfig

from .push import async_get_push_manager, async_parse_notification

from .webhook import async_get_webhook_manager

from .entity import (
    ReolinkEntity,
    ReolinkEntityDataUpdateCoordinator,
)

from .typing import ChannelData, DomainData, EntityData, RequestQueue

from .const import DOMAIN

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


class ChannelMotionStateData(Protocol):
    """Channel Motion State Data"""

    detected: bool
    ai: Mapping[AITypes, bool]


class ChannelMotionData(Protocol):
    """Channel Motion Data"""

    motion_coordinator: DataUpdateCoordinator[EntityData]
    ai: AIConfig
    motion_state: ChannelMotionStateData


class _DataProxy:
    __slots__ = ("_writable", "_sources")

    def __init__(self, writable: any, *sources: any) -> None:
        self._writable = writable
        self._sources = sources

    def __getattr__(self, __name: str):
        try:
            return getattr(self._writable, __name)
        except AttributeError:
            for source in self._sources:
                try:
                    return getattr(source, __name)
                except AttributeError:
                    pass
            raise

    def __setattr__(self, __name: str, __value: any):
        if __name in _DataProxy.__slots__:
            super(_DataProxy, self).__setattr__(__name, __value)
        else:
            setattr(self._writable, __name, __value)

    def __delattr__(self, __name: str) -> None:
        delattr(self._writable, __name)


async def _handle_onvif_notify(hass: HomeAssistant, request: Request):
    motion = await async_parse_notification(request)
    if motion is None:
        return None

    domain_data: DomainData = hass.data[DOMAIN]
    entry_data = domain_data[request["entry_id"]]

    # the onvif motion event is only a simple notice as the device detected motion
    # TODO : does the NVR provide a channel or does it just to this for any device?

    try:
        _cb: CALLBACK_TYPE = entry_data.onvif_notify_debounce
        del entry_data.onvif_notify_debounce
        if _cb:
            # clear debouce
            _cb()
    except AttributeError:
        pass

    data = entry_data.coordinator.data

    include_md = False
    # when we have channels or ai, we need to know what type of motion and where
    # we do this separatly from the data updaters because we do not want to delay
    async def _fetch_actual_motion():
        client = entry_data.client
        commands = client.commands
        queue = []
        for channel in data.channels:
            if include_md or len(data.channels) > 1:
                queue.append(commands.create_get_md_state(channel))
            if len(_get_ai_support(data.capabilities.channels[channel])) > 0:
                queue.append(commands.create_get_ai_state_request(channel))
        idx = -1
        async for response in client.batch(queue):
            idx += 1
            if commands.is_get_md_response(response):
                motion_data: ChannelMotionData = data.channels[response.channel_id]
                motion_data.motion_state.detected = response.state
            elif commands.is_get_ai_state_response(response):
                motion_data: ChannelMotionData = data.channels[response.channel_id]
                if response.can_update(motion_data.motion_state.ai):
                    motion_data.motion_state.ai.update(response.state)
                else:
                    motion_data.motion_state.ai = response.state
        for motion_data in data.channels.items():
            motion_data.motion_coordinator.async_set_updated_data(
                motion_data.motion_coordinator.data
            )
        entry_data.onvif_fetch_task = None

    # sometimes the cameras fail to send the IsMotion false (or possibly it gets lost)
    # so we will "debounce" a final refresh while IsMotion is true
    if motion:

        async def _force_refresh():
            nonlocal include_md
            del entry_data.onvif_notify_debounce
            include_md = True
            await _fetch_actual_motion()

        entry_data.onvif_notify_debounce = async_track_point_in_utc_time(
            hass, _force_refresh, dt.utcnow() + MOTION_DEBOUCE
        )

    if len(data.channels) == 1:
        motion_data: ChannelMotionData = data.channels[0]
        motion_data.motion_state.detected = motion
        motion_data.motion_coordinator.async_set_updated_data(
            motion_data.motion_coordinator.data
        )

    if len(filter(lambda c: len(_get_ai_support(c)) > 0, data.channels.values())) > 0:
        if motion:
            try:
                fetch_task = entry_data.onvif_fetch_task
            except AttributeError:
                fetch_task = None
            if fetch_task is not None:
                # if we have a task pending we will bail on a motion update
                # incase updates come in faster than the API will give us results
                # as spamming a camera is a good way to cause errors
                return None
        # we add the fetch as a task so we can return as quick as possible
        entry_data.onvif_fetch_task = hass.async_create_task(_fetch_actual_motion())

    return None


def _get_ai_support(__capabilities: capabilities.ChannelCapabilities):
    ai_types: set[AITypes] = set()
    # if ability.support.ai: <- in my tests this ability was not set
    if __capabilities.supports.ai.animal:
        ai_types.add(AITypes.ANIMAL)
    if __capabilities.supports.ai.face:
        ai_types.add(AITypes.FACE)
    if __capabilities.supports.ai.people:
        ai_types.add(AITypes.PEOPLE)
    if __capabilities.supports.ai.pet:
        ai_types.add(AITypes.PET)
    if __capabilities.supports.ai.vehicle:
        ai_types.add(AITypes.VEHICLE)
    return ai_types


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Setup binary_sensor platform"""

    # def _setup_hooks(
    #     channel: int, motion_coordinator: ReolinkEntityDataUpdateCoordinator
    # ):
    #     add_listener = motion_coordinator.async_add_listener

    #     def _coord_update():
    #         if channel in coordinator.data.updated_motion:
    #             motion_coordinator.async_set_updated_data(coordinator.data)

    #     coord_cleanup = None

    #     def _add_listener(update_callback: CALLBACK_TYPE, context: any = None):
    #         nonlocal coord_cleanup
    #         # pylint: disable = protected-access
    #         if len(motion_coordinator._listeners) == 0:
    #             coord_cleanup = coordinator.async_add_listener(_coord_update)

    #         cleanup = add_listener(update_callback, context)

    #         def _cleanup():
    #             cleanup()
    #             if len(motion_coordinator._listeners) == 0:
    #                 coord_cleanup()

    #         return _cleanup

    #     motion_coordinator.async_add_listener = _add_listener

    def setup_ai_config_update(channel: int, motion_data: ChannelMotionData):
        def update_ai_config():
            response = next(
                filter(
                    lambda r: r.channel_id == channel,
                    filter(
                        commands.is_get_ai_config_response,
                        device_requests.responses,
                    ),
                ),
                None,
            )
            if response is not None:
                device_requests.append(commands.create_get_ai_config_request(channel))
                ai_config = motion_data.ai
                if response.can_update(ai_config):
                    ai_config.update(response.config)
                else:
                    motion_data.ai = response.config

        device_requests.append(commands.create_get_ai_config_request(channel))
        return coordinator.async_add_listener(update_ai_config)

    def setup_motion_coordinator(
        channel: int, channel_data: ChannelData, ai_types: list
    ) -> DataUpdateCoordinator[EntityData]:
        motion_coordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name=f"{coordinator.name}-motion",
        )
        proxy_data: EntityData = _DataProxy(channel_data, data)
        motion_data: ChannelMotionData = channel_data
        motion_coordinator.data = proxy_data

        _add_listener = motion_coordinator.async_add_listener

        def emit_requests():
            device_requests.append(commands.create_get_md_state(channel))
            if len(ai_types) > 0:
                device_requests.append(commands.create_get_ai_state_request(channel))

        def coordinator_listener():
            for response in device_requests.responses:
                if commands.is_get_md_response(response):
                    # TODO : change channel issues
                    motion_data.motion_state.ai = response.state
                elif commands.is_get_ai_state_response(response):
                    if response.can_update(motion_data.motion_state.ai):
                        motion_data.motion_state.ai.update(response.state)
                    else:
                        motion_data.motion_state.ai = response.state
            emit_requests()
            motion_coordinator.async_set_updated_data(motion_coordinator.data)

        cleanup = None

        def async_add_listener(
            update_callback: CALLBACK_TYPE,
            context: any = None,
        ):
            nonlocal cleanup
            self = motion_coordinator
            if len(self._listeners) == 0:
                emit_requests()
                cleanup = coordinator.async_add_listener(coordinator_listener)
            _cleanup = _add_listener(update_callback, context)

            def __cleanup():
                _cleanup()
                if len(self._listeners) == 0:
                    cleanup()

            return __cleanup

        motion_coordinator.async_add_listener = async_add_listener

        return motion_coordinator

    _LOGGER.debug("Setting up binary sensors")
    domain_data: DomainData = hass.data[DOMAIN]
    entry_data = domain_data[config_entry.entry_id]
    coordinator = entry_data.coordinator
    commands = entry_data.client.commands
    device_requests: RequestQueue = coordinator.data

    entities = []
    data = coordinator.data
    _capabilities = data.capabilities

    for channel, channel_data in data.channels.items():
        channel_capabilities = _capabilities.channels[channel]
        if not channel_capabilities.alarm.motion:
            continue

        ai_types = _get_ai_support(channel_capabilities)

        motion_data: ChannelMotionData = channel_data

        try:
            motion_data.ai
        except AttributeError:
            motion_data.ai = None

        try:
            motion_data.motion_state
        except AttributeError:
            motion_data.motion_state = SimpleNamespace(detected=False, ai=None)

        if len(ai_types) > 0:
            setup_ai_config_update(channel, motion_data)

        try:
            motion_coordinator = motion_data.motion_coordinator
        except AttributeError:
            motion_coordinator = motion_data.motion_coordinator = None

        for description in MOTION_SENSORS:
            if description.ai_type is not None and description.ai_type not in ai_types:
                continue

            if motion_coordinator is None:
                motion_data.motion_coordinator = (
                    motion_coordinator
                ) = setup_motion_coordinator(channel, channel_data, ai_types)

            entities.append(
                ReolinkMotionSensor(motion_coordinator, description, channel)
            )

    if entities:
        async_add_entities(entities)

        subscription = None
        hispeed_cleanup: CALLBACK_TYPE = None

        def update_coordinators():
            nonlocal hispeed_cleanup
            if subscription is not None:
                if hispeed_cleanup is not None:
                    hispeed_cleanup()  # pylint: disable=not-callable
                hispeed_cleanup = None
                return

            coordinator = entry_data.hispeed_coordinator

            def add_commands():
                client = entry_data.client
                commands = client.commands
                queue: RequestQueue = entry_data.hispeed_coordinator.data
                for channel in data.channels:
                    queue.append(commands.create_get_md_state(channel))
                    if len(_get_ai_support(data.capabilities.channels[channel])) > 0:
                        queue.append(commands.create_get_ai_state_request(channel))

            def update_motion_data():
                add_commands()
                queue: RequestQueue = entry_data.hispeed_coordinator.data
                for response in queue.responses:
                    if commands.is_get_md_response(response):
                        motion_data: ChannelMotionData = data.channels[
                            response.channel_id
                        ]
                        motion_data.motion_state.detected = response.state
                    elif commands.is_get_ai_state_response(response):
                        motion_data: ChannelMotionData = data.channels[
                            response.channel_id
                        ]
                        if response.can_update(motion_data.motion_state.ai):
                            motion_data.motion_state.ai.update(response.state)
                        else:
                            motion_data.motion_state.ai = response.state

                for motion_data in data.channels.values():
                    if motion_data.motion_coordinator is None:
                        continue
                    motion_data.motion_coordinator.async_set_updated_data(
                        motion_data.motion_coordinator.data
                    )

            add_commands()

            hispeed_cleanup = coordinator.async_add_listener(update_motion_data)

        update_coordinators()

        if _capabilities.onvif:
            # platform = async_get_current_platform()
            self = await async_get_integration(hass, DOMAIN)

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
                    update_coordinators()

                resub_cleanup = None
                onvif_warned = False

                def _sub_failure(entry_id: str, method: str, code: str, reason: str):
                    nonlocal subscription, resub_cleanup, onvif_warned
                    if entry_id != config_entry.entry_id or config_entry.data is None:
                        return

                    if subscription is not None:
                        subscription = None
                        update_coordinators()
                    subscription = None
                    if not coordinator.data.ports.onvif.enabled:
                        if not onvif_warned:
                            onvif_warned = True
                            coordinator.logger.warning(
                                "ONVIF not enabled for device %s, forcing polling mode",
                                coordinator.data.device["name"],
                            )
                        async_create_issue(
                            hass,
                            DOMAIN,
                            "onvif_disabled",
                            is_fixable=True,
                            severity=IssueSeverity.WARNING,
                            translation_key="onvif_disabled",
                            translation_placeholders={
                                "entry_id": config_entry.entry_id,
                                "name": data.device["name"],
                                "configuration_url": data.device["configuration_url"],
                            },
                            learn_more_url=self.documentation + "/ONVIF",
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
        data: ChannelMotionData = self.coordinator.data.channels[self._channel_id]
        _LOGGER.debug("Motion<-%r", data.motion_state)
        if self.entity_description.ai_type is None:
            self._attr_is_on = data.motion_state.detected
        else:
            self._attr_is_on = data.motion_state.ai.get(
                self.entity_description.ai_type, False
            )
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
