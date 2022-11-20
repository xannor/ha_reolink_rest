"""Motion Entities"""

from __future__ import annotations

import dataclasses
from typing import Final, Mapping

from aiohttp.web import Request

from homeassistant.core import HomeAssistant, CALLBACK_TYPE
from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.network import get_url, NoURLAvailableError

try:
    from homeassistant.components import webhook
except ImportError:
    webhook = None

from async_reolink.api.ai.typing import AITypes

from async_reolink.rest.ai.model import State as AIState, Config as AIConfig
from async_reolink.rest.ai import command as ai_command
from async_reolink.rest.alarm import command as alarm_command

from ..api import async_get_entry_data, RequestQueue

from ..entity import ChannelMixin, ReolinkEntity

from ..const import DOMAIN, OPT_CHANNELS

from ..onvif import async_get_subscription, async_parse_notification

from .model import ReolinkBinarySensorEntityDescription


class MotionData(Mapping[AITypes, bool]):
    """Motion Data"""

    def __init__(self) -> None:
        self._ai: AIState = None
        self.detected: bool = False

    def __getitem__(self, __key: AITypes):
        if self._ai is not None and (state := self._ai.get(__key, None)):
            return state.state
        return False

    def __iter__(self):
        if self._ai is None:
            return
        for __key in self._ai.__iter__():
            yield __key

    def __len__(self):
        if self._ai is None:
            return 0
        return self._ai.__len__()

    def clear(self):
        """Clear state"""
        self.detected = False
        self._ai = None


MotionDataMap = Mapping[int, MotionData]


@dataclasses.dataclass
class ReolinkMotionSensorEntityDescription(ReolinkBinarySensorEntityDescription):
    """Reolink Motion Sensor Entity Description"""

    has_entity_name: bool = True
    device_class: BinarySensorDeviceClass | str | None = BinarySensorDeviceClass.MOTION


MOTION: Final = ReolinkMotionSensorEntityDescription("motion", name="Motion")


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
            self.key = f"{MOTION.key}_{self.ai_type.name.lower()}"
        if self.name is None:
            self.name = f"{self.ai_type.name.title()} {MOTION.name}"


BINARY_SENSORS: Final = (MOTION,) + tuple(
    ReolinkAIMotionSensorEntityDescription(ai) for ai in AITypes
)


class ReolinkMotionBinarySensorEntity(ReolinkEntity[MotionDataMap], BinarySensorEntity):
    """Reolink Motion Binary Sensor Entity"""

    entity_description: ReolinkMotionSensorEntityDescription

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[MotionDataMap],
        description: ReolinkAIMotionSensorEntityDescription,
        context: any = None,
    ) -> None:
        BinarySensorEntity.__init__(self)
        self.entity_description = description
        ReolinkEntity.__init__(self, coordinator, context)
        self._channel_id = self.entity_description.channel_id
        self._attr_available = False
        self._attr_extra_state_attributes = {}
        if isinstance(
            self.entity_description, ReolinkAIMotionSensorEntityDescriptionMixin
        ):
            self._ai_type = self.entity_description.ai_type
            self._attr_extra_state_attributes["ai_type"] = self._ai_type.name
        else:
            self._ai_type = None

    def _handle_coordinator_update(self) -> None:
        self._attr_available = self._channel_id in self.coordinator.data
        if self._attr_available:
            if self._ai_type is None:
                self._attr_is_on = self.coordinator.data[self._channel_id].detected
            else:
                self._attr_is_on = self.coordinator.data[self._channel_id][
                    self._ai_type
                ]
        self._attr_extra_state_attributes["update_method"] = getattr(
            self.coordinator, "update_method", "polling"
        )

        return super()._handle_coordinator_update()


async def _setup_onvif(
    coordinator: DataUpdateCoordinator,
    motion_queue: RequestQueue,
    success_callback: CALLBACK_TYPE,
    fail_callback: CALLBACK_TYPE,
):
    if not webhook:
        fail_callback()
        return

    hass = coordinator.hass
    config_entry = coordinator.config_entry
    webhook_id = coordinator.config_entry.unique_id or config_entry.entry_id
    entry_data = async_get_entry_data(hass, coordinator.config_entry.entry_id, False)
    client_data = entry_data["client_data"]
    data: dict[int, MotionData] = coordinator.data

    async def update_listeners():
        # update listeners in a separate task
        coordinator.async_update_listeners()

    async def update_channels():
        queue = motion_queue.copy()

        for response in await entry_data["client"].batch(queue):
            if isinstance(response, alarm_command.GetMotionStateResponse):
                data.setdefault(
                    response.channel_id, MotionData()
                ).detected = response.state
            elif isinstance(response, ai_command.GetAiStateResponse):
                state = data.setdefault(response.channel_id, MotionData())
                # pylint: disable=protected-access
                if isinstance(state._ai, AIState):
                    state._ai.update(response.state)
                else:
                    state._ai = response.state

        await update_listeners()

    async def handler(hass: HomeAssistant, _webhook_id: str, request: Request):
        if "xml" not in request.content_type:
            return None

        motion = async_parse_notification(await request.text())
        if motion is None:
            return

        if not motion:
            for channel in data.values():
                channel.clear()
            hass.create_task(update_listeners())
        elif client_data.device_info.channels == 1 and len(motion_queue) == 1:
            # for single channel, non ai, devices we only need to update detected
            data[0].detected = True
            hass.create_task(update_listeners())
        else:
            hass.create_task(update_channels())

    try:
        url = get_url(hass, prefer_external=False, prefer_cloud=False)
    except NoURLAvailableError:
        coordinator.logger.warning(
            "Could not get internal url from system"
            ", will attempt external url but this is not preferred"
            ", please verify your installation."
        )

        try:
            url = get_url(hass, allow_cloud=False)
        except NoURLAvailableError:
            coordinator.logger.warning(
                "Could not get an addressable url, disabling webook support"
            )
            fail_callback()
            return

    webhook.async_register(
        hass, DOMAIN, f"{config_entry.title} ONVIF", webhook_id, handler
    )

    def unregister():
        webhook.async_unregister(hass, webhook_id)

    config_entry.async_on_unload(unregister)

    try:
        sub = await async_get_subscription(url, hass, coordinator.config_entry.entry_id)
    except:
        fail_callback()
        raise
    else:
        if isinstance(sub, tuple):
            # log error
            fail_callback()
        elif sub is not None:
            success_callback()


async def async_get_binary_sensor_entities(
    hass: HomeAssistant,
    entry_id: str,
):
    """Get a list of Motion Sensors"""

    entities: list[BinarySensorEntity] = []

    entry_data = async_get_entry_data(hass, entry_id, False)
    config_entry = hass.config_entries.async_get_entry(entry_id)
    api = entry_data["client_data"]

    coordinator: DataUpdateCoordinator[MotionDataMap] = None
    motion_queue: RequestQueue = None

    motion_data: dict[int, MotionData] = {}

    def update_md_data(response):
        if not isinstance(response, alarm_command.GetMotionStateResponse):
            return
        motion_data[response.channel_id].detected = response.state
        motion_queue.append(
            alarm_command.GetMotionStateRequest(response.channel_id), update_md_data
        )

    def update_ai_data(response):
        if not isinstance(response, ai_command.GetAiStateResponse):
            return
        motion = motion_data[response.channel_id]
        # pylint: disable=protected-access
        if isinstance(motion._ai, AIState):
            motion._ai.update(response.state)
        else:
            motion._ai = response.state
        motion_queue.append(
            ai_command.GetAiStateRequest(response.channel_id), update_ai_data
        )

    # config_queue: InlineCommandQueue = None
    # ai_config_map: dict[int, AIConfig] | None = None
    # no_ai_config: set[int] = set()

    _capabilities = api.capabilities
    channels: list[int] = config_entry.options.get(OPT_CHANNELS, None)
    for status in api.channel_statuses.values():
        if not status.online or (
            channels is not None and not status.channel_id in channels
        ):
            continue
        channel_capabilities = _capabilities.channels[status.channel_id]
        info = api.channel_info[status.channel_id]

        if (
            not channel_capabilities.alarm.motion
            and not channel_capabilities.supports.motion_detection
        ):
            continue

        first_ai = True
        for motion in BINARY_SENSORS:
            if isinstance(motion, ReolinkAIMotionSensorEntityDescriptionMixin):
                ai_type = motion.ai_type.name.lower()
                if not getattr(channel_capabilities.supports.ai, ai_type):
                    continue
                # if (
                #     channel_capabilities.supports.ai.detect_config
                #     and not status.channel_id in no_ai_config
                # ):
                #     # linter is wrong
                #     # pylint: disable=unsupported-membership-test
                #     if ai_config_map is None or status.channel_id not in ai_config_map:
                #         try:
                #             ai_config = await api.client.get_ai_config(
                #                 status.channel_id
                #             )
                #         except ReolinkResponseError as resp_err:
                #             if resp_err.code == ErrorCodes.NOT_SUPPORTED:
                #                 no_ai_config.add(status.channel_id)
                #             else:
                #                 raise
                #         else:
                #             if ai_config_map is None:
                #                 ai_config_map = {}
                #             ai_config_map[status.channel_id] = ai_config
                #             if config_queue is None:
                #                 config_queue = CommandQueue()
                #             config_queue.add(
                #                 commands.create_get_ai_config_request(status.channel_id)
                #             )
            else:
                ai_type = None

            if motion_queue is None:
                motion_queue = RequestQueue()
            if status.channel_id not in motion_data:
                motion_data[status.channel_id] = MotionData()
            if coordinator is None:
                coordinator = DataUpdateCoordinator(
                    hass,
                    entry_data["coordinator"].logger,
                    name=f"{config_entry.title} Motion Data Coordinator",
                )
                coordinator.async_set_updated_data(motion_data)
            if ai_type:
                if first_ai:
                    # we only want one command (and callback) per channel
                    first_ai = False
                    motion_queue.append(
                        ai_command.GetAiStateRequest(status.channel_id),
                        update_ai_data,
                    )
            else:
                motion_queue.append(
                    alarm_command.GetMotionStateRequest(status.channel_id),
                    update_md_data,
                )

            description = ChannelMixin.set_channel(motion, info)
            entities.append(ReolinkMotionBinarySensorEntity(coordinator, description))

    # if config_queue is not None:

    #     def _update_config():
    #         updated = False
    #         for response in api.coordinator.data:
    #             if commands.is_get_ai_config_response(response):
    #                 config_queue.add(
    #                     commands.create_get_ai_config_request(response.channel_id)
    #                 )
    #                 ai_config[response.channel_id].update(response.config)
    #                 updated = True
    #         if updated:
    #             coordinator.async_update_listeners()

    #     cleanup = api.coordinator.async_add_listener(_update_config, config_queue)

    #     def _do_cleanup():
    #         nonlocal cleanup
    #         if cleanup is not None:
    #             cleanup()
    #         cleanup = None

    #     api.coordinator.config_entry.async_on_unload(_do_cleanup)

    if motion_queue is not None:

        def update_coordinator():
            coordinator.async_set_updated_data(motion_data)

        cleanup: CALLBACK_TYPE = None

        # pylint: disable=not-callable
        def disable_hispeed():
            nonlocal cleanup
            if cleanup:
                cleanup()
            setattr(coordinator, "update_method", "push")
            cleanup = entry_data["coordinator"].async_add_listener(
                update_coordinator, motion_queue
            )

        def enable_hispeed():
            nonlocal cleanup
            if cleanup:
                cleanup()
            setattr(coordinator, "update_method", "polling")
            cleanup = entry_data["hispeed_coordinator"].async_add_listener(
                update_coordinator, motion_queue
            )

        if _capabilities.onvif:
            hass.async_run_job(
                _setup_onvif, coordinator, motion_queue, disable_hispeed, enable_hispeed
            )
        else:
            enable_hispeed()

    return entities
