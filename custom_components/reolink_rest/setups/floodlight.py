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


def _create_attach_handler(self: LightEntity):
    # pylint: disable=protected-access
    if not isinstance(self, ReolinkEntity):
        raise ValueError()

    rself = self

    cleanup = None
    want_state = None
    bail_after: float = 0

    def should_detach():
        nonlocal cleanup
        if not cleanup:
            return
        if self.is_on == want_state or time() > bail_after:
            _cleanup = cleanup
            cleanup = None
            _cleanup()

    def state_handler(state: bool):
        nonlocal cleanup, want_state, bail_after

        if cleanup:
            if want_state != state:
                want_state = state
            return
        if self.is_on == state:
            return
        want_state = state
        bail_after = time() + 2
        domain_data: DomainDataType = self.hass.data[DOMAIN]
        entry_data = domain_data[rself._entry_id]
        coordinator = entry_data[DATA_HISPEED_COORDINDATOR]

        async def register():
            nonlocal cleanup
            cleanup = coordinator.async_add_listener(should_detach, rself.coordinator_context)

        self.hass.create_task(register())

    return state_handler


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
        attach = _create_attach_handler(self)

        def on_motion(sensor: BinarySensorEntity):
            attach(sensor.is_on)

        signal = f"{DOMAIN}_{self._entry_id}_ch_{self._channel_id}_motion"

        dispatcher: helper_dispatcher = self.hass.helpers.dispatcher
        self.async_on_remove(hass_bound(dispatcher.async_dispatcher_connect)(signal, on_motion))


def _floodlight_state(state: bool):
    # pylint: disable=protected-access

    async def call(self: ReolinkEntity, **_kwargs: any):
        info: led_typing.WhiteLedInfo = SimpleNamespace(state=state)
        await self._client.set_white_led(info, self._channel_id)
        _create_attach_handler(self)(state)
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
