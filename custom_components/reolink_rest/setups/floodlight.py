"""Floodlight"""

import asyncio
from time import time
import logging

from types import SimpleNamespace
from typing import TYPE_CHECKING, Final, Protocol

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.light import LightEntity, ColorMode
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from ..typing import DomainDataType, RequestHandler

if TYPE_CHECKING:
    from homeassistant.helpers import dispatcher as helper_dispatcher
from .._utilities.hass_typing import hass_bound

from async_reolink.api.led import typing as led_typing

from async_reolink.rest.connection.model import ResponseTypes
from async_reolink.rest.led import command as led_command

from ..const import DATA_HISPEED_COORDINDATOR, DOMAIN

from ..entity import ReolinkEntity

from .._utilities.object import lazysetdefaultattr

from ..light_typing import LightEntityChannelDescription, LightChannelEntityConfig


class _RangeData(Protocol):
    brightness: led_command.model.MinMaxRange[int]


def _set_brightness_value(self: LightEntity, info: led_typing.WhiteLedInfo):
    # pylint: disable=protected-access

    range_data: _RangeData
    if (
        (range_data := getattr(self, "_floodlight_data", None)) is None
        or not hasattr(range_data, "brightness")
        or not (brightness_range := range_data.brightness)
    ):
        return

    self._attr_brightness = min(info.brightness, brightness_range.min) / brightness_range.max


async def _floodlight_init(self: LightEntity):
    # pylint: disable=protected-access
    if not isinstance(self, ReolinkEntity):
        raise ValueError()

    client = self._client

    def response_handler(response: led_command.GetWhiteLedResponse):
        info = response.info
        _set_brightness_value(self, info)
        self._attr_is_on = bool(info.state)

    async for response in client.batch(
        [led_command.GetWhiteLedRequest(self._channel_id, ResponseTypes.DETAILED)]
    ):
        if isinstance(response, led_command.GetWhiteLedResponse):
            if response.channel_id == self._channel_id and response.is_detailed:
                if response.is_detailed:
                    range_data: _RangeData = lazysetdefaultattr(
                        self, "_floodlight_data", SimpleNamespace
                    )

                    range_data.brightness = response.info_range.brightness
                response_handler(response)

    self.coordinator_context = (
        RequestHandler(
            led_command.GetWhiteLedRequest(self._channel_id),
            response_handler,
        ),
    )

    channel = self._device_data.capabilities.channels[self._channel_id]
    if channel.alarm.motion or channel.supports.motion_detection:

        def has_motion(self: LightEntity):
            return bool(self.is_on)

        def has_no_motion(self: LightEntity):
            return not bool(self.is_on)

        attach_motion = self._temp_attach_hispeed_coordinator(has_motion, 2)
        attach_no_motion = self._temp_attach_hispeed_coordinator(has_no_motion, 2)

        def on_motion(sensor: BinarySensorEntity):
            if sensor.is_on:
                attach_motion()
            else:
                attach_no_motion()

        signal = f"{DOMAIN}_{self._entry_id}_ch_{self._channel_id}_motion"

        dispatcher: helper_dispatcher = self.hass.helpers.dispatcher
        self.async_on_remove(hass_bound(dispatcher.async_dispatcher_connect)(signal, on_motion))


def _floodlight_state(state: bool):
    # pylint: disable=protected-access

    def should_release(self: LightEntity):
        return self.is_on == state

    async def call(self: ReolinkEntity, **_kwargs: any):
        info: led_typing.WhiteLedInfo = SimpleNamespace(state=state)
        await self._client.set_white_led(info, self._channel_id)
        self._temp_attach_hispeed_coordinator(should_release, 2)()
        # even the the command suceeds, there is a delay before it actually happens so we will
        # wait for confirmation
        while self.is_on != state:
            await asyncio.sleep(1)
        if (
            TYPE_CHECKING
        ):  # useless statement for forced static type check since isinstance throws on generics
            if not isinstance(self, CoordinatorEntity[DataUpdateCoordinator]):
                return
        # we still queue a refresh because other things (such as linked lights) may need updating
        self.hass.create_task(self.coordinator.async_request_refresh())

    return call


LIGHTS: Final = (
    LightChannelEntityConfig.create(
        LightEntityChannelDescription(
            "floodlight",
            name="Floodlight",
            color_modes=set(ColorMode.BRIGHTNESS),
        ),
        lambda _self, channel, _data: channel.supports.flood_light.switch,
        _floodlight_state(True),
        _floodlight_state(False),
        init_handler=_floodlight_init,
    ),
)
