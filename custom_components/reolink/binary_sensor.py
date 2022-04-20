"""Reolink motion sensor"""

from __future__ import annotations

from datetime import timedelta

import logging
from typing import cast

from homeassistant.core import HomeAssistant, callback, Event
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
)
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from reolinkapi.typings.abilities.channel import ChannelAbilities
from reolinkapi.typings.ai import AiAlarmState
from reolinkapi.models.ai import AITypes
from reolinkapi.helpers.abilities.ability import NO_ABILITY, NO_CHANNEL_ABILITIES
from reolinkapi import helpers as clientHelpers

from .typings.component import HassDomainData, EntryData

from .typings.motion import (
    MultiChannelMotionData,
    SimpleChannelMotionData,
    SimpleMotionData,
)

from .helpers import addons

from .typings import motion

from .entity import ReolinkMotionEntity

from .const import (
    AI_TYPE_NONE,
    CONF_CHANNELS,
    CONF_MOTION_INTERVAL,
    DEFAULT_MOTION_INTERVAL,
    DOMAIN,
    MOTION_TYPE,
    CONF_PREFIX_CHANNEL,
)

_LOGGER = logging.getLogger(__name__)


def get_poll_interval(config_entry: ConfigEntry):
    """Get the poll interval"""
    interval = config_entry.options.get(CONF_MOTION_INTERVAL, DEFAULT_MOTION_INTERVAL)
    return timedelta(seconds=interval)


def _channel_supports_ai(entry_data: EntryData, abilities: int | ChannelAbilities):
    """check if channel supports ai detection"""

    if isinstance(abilities, int):
        channels = entry_data.coordinator.data.abilities.get(
            "abilityChn", [NO_CHANNEL_ABILITIES]
        )
        if abilities > len(channels) - 1:
            abilities = NO_CHANNEL_ABILITIES
        else:
            abilities = channels[abilities]

    return (
        abilities.get("supportAi", NO_ABILITY)["ver"]
        or abilities.get("supportAiAnimal", NO_ABILITY)["ver"]
        or abilities.get("supportAiDogCat", NO_ABILITY)["ver"]
        or abilities.get("supportAiFace", NO_ABILITY)["ver"]
        or abilities.get("supportAiPeople", NO_ABILITY)["ver"]
        or abilities.get("supportAiVehicle", NO_ABILITY)["ver"]
    )


def _create_async_update_motion_data(entry_data: EntryData):
    async def async_update_data():
        channel_state_index: dict[int, int] = {}
        pending_commands = []

        def append_channel_refresh(channel: int):
            if channel in channel_state_index:
                return
            channel_state_index[channel] = len(pending_commands)
            pending_commands.append(clientHelpers.alarm.create_get_md_state(channel))
            if _channel_supports_ai(entry_data, channel):
                pending_commands.append(clientHelpers.ai.create_get_ai_state(channel))

        def _retry():
            nonlocal need_refresh
            need_refresh()
            need_refresh = None
            entry_data.coordinator.hass.async_add_job(
                entry_data.coordinator.async_refresh
            )

        if entry_data.coordinator.data.channels is not None:
            channels = cast(
                list[int],
                entry_data.coordinator.config_entry.options.get(CONF_CHANNELS, []),
            )
            for channel in (
                channel
                for channel in entry_data.coordinator.data.channels
                if channel["channel"] in channels
            ):
                append_channel_refresh(channel["channel"])
        else:
            append_channel_refresh(0)

        try:
            responses = await entry_data.coordinator.client.batch(pending_commands)
        except Exception:
            if need_refresh is None:
                need_refresh = entry_data.coordinator.async_add_listener(_retry)
            raise
        if clientHelpers.security.has_auth_failure(responses):
            await entry_data.coordinator.logout()
            if need_refresh is None:
                need_refresh = entry_data.coordinator.async_add_listener(_retry)
            raise UpdateFailed()
        ai_states = list(clientHelpers.ai.get_ai_state_responses(responses))
        channels: dict[int, motion.ChannelMotionState] = {}
        for channel, index in channel_state_index.items():
            _motion = channels.setdefault(channel, motion.ChannelMotionState())
            state = next(
                clientHelpers.alarm.get_md_state_responses([responses[index]]), None
            )
            _motion["motion"] = state == 1
            _ai = next(
                (ai_state for ai_state in ai_states if ai_state["channel"] == channel),
                None,
            )
            if _ai is not None:
                _motion.update(_ai)

        return channels

    return async_update_data


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Setup binary sensor platform"""

    domain_data = cast(HassDomainData, hass.data)[DOMAIN]
    entry_data = domain_data[config_entry.entry_id]
    data_coordinator = entry_data.coordinator

    services = await addons.async_get_addon_tracker(hass)
    if (update_coordinator := entry_data.data_motion_coordinator) is None:
        update_interval = get_poll_interval(config_entry)
        if update_interval.seconds < 2:
            update_interval = None
        # if we have viable methods other than timed polling we will not poll
        # TODO : detect late registration of services
        if len([item for items in services.items() for item in items]) > 0:
            update_interval = None
        entry_data.data_motion_coordinator = update_coordinator = DataUpdateCoordinator(
            data_coordinator.hass,
            _LOGGER,
            name=f"{data_coordinator.name}-motion",
            update_interval=update_interval,
            update_method=_create_async_update_motion_data(entry_data),
        )

        async def handle_event(event: Event):
            channels = None
            data: MultiChannelMotionData | SimpleChannelMotionData | SimpleMotionData = (
                event.data
            )
            if not isinstance(channels := data.get("channels", None), list):

                channels = [
                    cast(
                        SimpleChannelMotionData
                        if "channel" in data
                        else SimpleMotionData,
                        data,
                    )
                ]
            force_refresh = True
            if channels is not None:
                for data in channels:
                    channel = data.get("channel", 0)
                    _motion = update_coordinator.data[channel]
                    if "motion" in data:
                        force_refresh = False
                        _motion["motion"] = data["motion"]
                    if _channel_supports_ai(entry_data, channel):
                        force_refresh = True
                        for (
                            key
                        ) in (
                            motion.GetAiStateResponseValue.__annotations__.keys()  # pylint: disable=no-member
                        ):  # not sure why pylink thinks a typeddict does not have __annotations
                            if key in data:
                                force_refresh = False
                                _motion[key] = data[key]

            if force_refresh:
                await update_coordinator.async_refresh()
            else:  # notify listeners of "changes" anyway
                update_coordinator.async_set_updated_data(update_coordinator.data)

        event_id = f"{DOMAIN}-motion-{data_coordinator.data.uid}"
        remove_listener = data_coordinator.hass.bus.async_listen(event_id, handle_event)

        await update_coordinator.async_config_entry_first_refresh()

    entities = []

    async def _update_ai_config():

        if data_coordinator.data.channels is not None:
            commands = list(
                map(
                    data_coordinator.client.create_get_ai_config,
                    range(0, len(data_coordinator.data.channels) - 1),
                )
            )
        else:
            commands = [data_coordinator.client.create_get_ai_config(0)]

        responses = await data_coordinator.client.batch(commands)
        return {
            response["channel"]: response["AiDetectType"]
            for response in clientHelpers.ai.get_ai_config_responses(responses)
        }

    channel_ai_support = None
    if next(
        (
            True
            for abilities in data_coordinator.data.abilities.get(
                "abilityChn", [NO_CHANNEL_ABILITIES]
            )
            if _channel_supports_ai(entry_data, abilities)
        ),
        False,
    ):
        channel_ai_support = await _update_ai_config()

    def _create_entities(channel: int):
        channel_ai = (
            channel_ai_support.get(channel, {})
            if channel_ai_support is not None
            else {}
        )
        entities.append(
            ReolinkMotionSensor(
                data_coordinator,
                update_coordinator,
                channel,
                AI_TYPE_NONE,
            )
        )
        for ai_type in AITypes:
            if channel_ai.get(ai_type, 0):
                entities.append(
                    ReolinkMotionSensor(
                        data_coordinator,
                        update_coordinator,
                        channel,
                        ai_type,
                    )
                )

    if (
        data_coordinator.data.channels is not None
        and CONF_CHANNELS in config_entry.options
    ):
        for _c in config_entry.options.get(CONF_CHANNELS, []):
            if (
                not next(
                    (ch for ch in data_coordinator.data.channels if ch["channel"] == _c)
                )
                is None
            ):
                _create_entities(_c)
    else:
        _create_entities(0)

    if len(entities) > 0:
        async_add_entities(entities)

    return True


class ReolinkMotionSensor(ReolinkMotionEntity, BinarySensorEntity):
    """Reolink Motion Sensor"""

    def __init__(
        self,
        coordinator: any,
        motion_coordinator: any,
        channel_id: int,
        ai_type: AITypes | AI_TYPE_NONE,
    ) -> None:
        super().__init__(
            coordinator,
            motion_coordinator,
            channel_id,
            MOTION_TYPE[ai_type],
        )
        BinarySensorEntity.__init__(
            self
        )  # explicitly call BinarySensorEntity init since UpdateCoordinatorEntity does not super()
        self._ai_type: AITypes | None = (
            ai_type if isinstance(ai_type, AITypes) else None
        )
        self._prefix_channel: bool = self.coordinator.config_entry.data.get(
            CONF_PREFIX_CHANNEL
        )
        self._attr_unique_id = f"{self.coordinator.data.uid}.{self._channel_id}.{self.entity_description.name}"
        self._additional_updates()

    def _additional_updates(self):
        if self._prefix_channel and self._channel_status is not None:
            self._attr_name = f'{self.coordinator.data.device_info["name"]} {self._channel_status["name"]} {self.entity_description.name}'
        else:
            self._attr_name = f'{self.coordinator.data.device_info["name"]} {self.entity_description.name}'

    @callback
    def _handle_coordinator_update(self):
        self._additional_updates()
        data = self.motion_coordinator.data
        _state = 0
        if self._ai_type is None:
            _state = 1 if data[self._channel_id]["motion"] is True else 0
        else:
            _state = cast(
                AiAlarmState, data[self._channel_id].get(self._ai_type, {})
            ).get("alarm_state", 0)

        self._attr_is_on = _state != 0
        super()._handle_coordinator_update()
