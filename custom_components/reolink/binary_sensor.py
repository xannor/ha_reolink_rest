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
    Debouncer,
)

from reolinkapi.rest import Client
from reolinkapi.typings.abilities.channel import ChannelAbilities
from reolinkapi.typings.ai import AiAlarmState
from reolinkapi.models.ai import AITypes
from reolinkapi.helpers.ability import NO_ABILITY, NO_CHANNEL_ABILITIES

from .typings.motion import (
    MultiChannelMotionData,
    SimpleChannelMotionData,
    SimpleMotionData,
)

from .helpers import addons

from .typings import motion

from .entity import EntityDataUpdateCoordinator, ReolinkEntity

from .const import (
    AI_TYPE_NONE,
    CONF_CHANNELS,
    CONF_MOTION_INTERVAL,
    DATA_COORDINATOR,
    DATA_MOTION_COORDINATOR,
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


class ChannelMotionState(motion.GetAiStateResponseValue, total=False):
    """Motion State Data"""

    motion: bool


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Setup binary sensor platform"""

    domain_data: dict = hass.data[DOMAIN]
    entry_data: dict = domain_data[config_entry.entry_id]
    data_coordinator: EntityDataUpdateCoordinator = entry_data[DATA_COORDINATOR]

    update_coordinator: MotionDataUpdateCoordinator = entry_data.get(
        DATA_MOTION_COORDINATOR, None
    )
    if update_coordinator is None:
        update_interval = get_poll_interval(config_entry)
        if update_interval.seconds < 2:
            update_interval = None
        update_coordinator = MotionDataUpdateCoordinator(
            data_coordinator,
            _LOGGER,
            name=f"{data_coordinator.name}-motion",
            update_interval=update_interval,
        )
        entry_data[DATA_MOTION_COORDINATOR] = update_coordinator

        await update_coordinator.async_config_entry_first_refresh()

    services = await addons.async_find_service_providers(hass, "reolink_motion")

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
            for response in Client.get_ai_config_responses(responses)
        }

    channel_ai_support = None
    if next(
        (
            True
            for abilities in data_coordinator.data.abilities.get(
                "abilityChn", [NO_CHANNEL_ABILITIES]
            )
            if update_coordinator.channel_supports_ai(abilities)
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
            ReolinkMotionEntity(
                update_coordinator,
                channel,
                AI_TYPE_NONE,
            )
        )
        for ai_type in AITypes:
            if channel_ai.get(ai_type, 0):
                entities.append(
                    ReolinkMotionEntity(
                        update_coordinator,
                        channel,
                        ai_type,
                    )
                )

    if (
        data_coordinator.data.channels is not None
        and CONF_CHANNELS in config_entry.data
    ):
        for _c in config_entry.data.get(CONF_CHANNELS, []):
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


class MotionDataUpdateCoordinator(DataUpdateCoordinator[dict[int, ChannelMotionState]]):
    """Reolink Motion Data Update Coordinator"""

    def __init__(
        self,
        data_update_coordinator: EntityDataUpdateCoordinator,
        logger: logging.Logger,
        *,
        name: str,
        update_interval: timedelta | None = None,
        request_refresh_debouncer: Debouncer | None = None,
    ) -> None:
        super().__init__(
            data_update_coordinator.hass,
            logger,
            name=name,
            update_interval=update_interval,
            update_method=None,
            request_refresh_debouncer=request_refresh_debouncer,
        )
        self.coordinator = data_update_coordinator
        self._pending_commands = []
        self._channel_state_index: dict[int, int] = {}
        self.event_id = f"{DOMAIN}-motion-{self.coordinator.data.uid}"
        self._unregister = self.hass.bus.async_listen(self.event_id, self._handle_event)

    async def async_stop(self):
        """stop coordinator"""
        self._unregister()
        self._async_stop_refresh(None)
        # TODO : handle service unregister as well

    def channel_supports_ai(self, abilities: int | ChannelAbilities):
        """check if channel supports ai detection"""

        if isinstance(abilities, int):
            channels = self.coordinator.data.abilities.get(
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

    def _append_channel_refresh(self, channel: int):
        if channel in self._channel_state_index:
            return
        self._channel_state_index[channel] = len(self._pending_commands)
        self._pending_commands.append(
            self.coordinator.client.create_get_md_state(channel)
        )
        if self.channel_supports_ai(channel):
            self._pending_commands.append(
                self.coordinator.client.create_get_ai_state(channel)
            )

    async def _async_update_data(self):
        def _retry():
            nonlocal need_refresh
            need_refresh()
            need_refresh = None
            self.hass.async_add_job(self.coordinator.async_refresh)

        if len(self._pending_commands) == 0:
            if (
                self.coordinator.data.channels is not None
                and CONF_CHANNELS in self.config_entry.data
            ):
                for _c in cast(
                    list[int], self.config_entry.data.get(CONF_CHANNELS, [])
                ):
                    if (
                        not next(
                            (
                                ch
                                for ch in self.coordinator.data.channels
                                if ch["channel"] == _c
                            )
                        )
                        is None
                    ):
                        self._append_channel_refresh(_c)
            else:
                self._append_channel_refresh(0)

        try:
            responses = await self.coordinator.client.batch(self._pending_commands)
        except Exception:
            if need_refresh is None:
                need_refresh = self.coordinator.async_add_listener(_retry)
            raise
        if Client.has_auth_failure(responses):
            await self.coordinator.logout()
            if need_refresh is None:
                need_refresh = self.coordinator.async_add_listener(_retry)
            raise UpdateFailed()
        ai_states = list(Client.get_ai_state_responses(responses))
        channels = self.data or {}
        for channel, index in self._channel_state_index.items():
            _motion = channels.setdefault(channel, ChannelMotionState())
            state = next(Client.get_md_state_responses([responses[index]]), None)
            _motion["motion"] = state == 1
            _ai = next(
                (ai_state for ai_state in ai_states if ai_state["channel"] == channel),
                None,
            )
            if _ai is not None:
                _motion.update(_ai)

        return channels

    async def _handle_event(self, _event: Event):
        channels = None
        if "channels" in _event.data:
            channels = cast(MultiChannelMotionData, _event.data)["channels"]
        elif "motion" in _event.data or "channel" in _event.data:
            channels = [
                cast(
                    SimpleChannelMotionData
                    if "channel" in _event.data
                    else SimpleMotionData,
                    _event.data,
                )
            ]
        force_refresh = True
        if channels is not None:
            for data in channels:
                channel = data.get("channel", 0)
                _motion = self.data[channel]
                if "motion" in data:
                    force_refresh = False
                    _motion["motion"] = data["motion"]
                if self.channel_supports_ai(channel):
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
            await self.async_refresh()
        else:  # notify listeners of "changes" anyway
            self.async_set_updated_data(self.data)


class ReolinkMotionEntity(ReolinkEntity, BinarySensorEntity):
    """Reolink Motion Entity"""

    def __init__(
        self,
        motion_coordinator: MotionDataUpdateCoordinator,
        channel_id: int,
        ai_type: AITypes | AI_TYPE_NONE,
    ) -> None:
        super().__init__(
            motion_coordinator.coordinator, channel_id, MOTION_TYPE[ai_type]
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
        self.motion_coordinator = motion_coordinator
        self._additional_updates()

    def _additional_updates(self):
        if self._prefix_channel and self._channel_status is not None:
            self._attr_name = f'{self.coordinator.data.device_info["name"]} {self._channel_status["name"]} {self.entity_description.name}'
        else:
            self._attr_name = f'{self.coordinator.data.device_info["name"]} {self.entity_description.name}'

    @callback
    def _handle_coordinator_update(self):
        self._additional_updates()

        super()._handle_coordinator_update()

    @callback
    def _handle_motion_update(self):
        data = self.motion_coordinator.data
        _state = 0
        if self._ai_type is None:
            _state = 1 if data[self._channel_id]["motion"] is True else 0
        else:
            _state = cast(
                AiAlarmState, data[self._channel_id].get(self._ai_type, {})
            ).get("alarm_state", 0)

        self._attr_is_on = _state != 0
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._handle_coordinator_update()
        self.async_on_remove(
            self.motion_coordinator.async_add_listener(self._handle_motion_update)
        )
        self._handle_motion_update()
