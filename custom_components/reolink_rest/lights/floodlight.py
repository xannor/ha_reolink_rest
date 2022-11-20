"""Floodlight"""

from types import SimpleNamespace
from typing import Final

from async_reolink.api.led import typing as led_typing

from async_reolink.rest.connection.model import ResponseTypes
from async_reolink.rest.led import command as led_command


from ..api import RequestQueue
from ..entity import ChannelMixin, ReolinkEntity
from .. import light

from .model import ReolinkLightEntityDescription


def _queue_floodlight(self: ReolinkEntity, queue: RequestQueue):
    if not isinstance(self, light.ReolinkLightEntity):
        return

    # pylint: disable=protected-access

    def handle_update(response):
        if isinstance(response, led_command.GetWhiteLedResponse):
            if response.is_detailed:
                _r = response.info_range
                self._attr_brightness_max = _r.brightness.max
            self.coordinator_context.append(
                led_command.GetWhiteLedRequest(self._channel_id), handle_update
            )
            info = response.info
            self._attr_is_on = info.state
            self._attr_brightness = 255 * (info.brightness / self._attr_brightness_max)

    queue.append(
        led_command.GetWhiteLedRequest(
            self._channel_id, response_type=ResponseTypes.DETAILED
        ),
        handle_update,
    )


def _floodlight_state(state: bool):
    # pylint: disable=protected-access

    async def call(self: ReolinkEntity, **_kwargs: any):
        info: led_typing.WhiteLedInfo = SimpleNamespace(state=state)
        return await self._entry_data["client"].set_white_led(info, self._channel_id)

    return call


LIGHTS: Final = (
    ReolinkLightEntityDescription(
        _queue_floodlight,
        _floodlight_state(True),
        _floodlight_state(False),
        "floodlight",
        name="Floodlight",
        channel_supported_fn=ChannelMixin.simple_test_and_set(
            lambda channel: channel.supports.flood_light.switch
        ),
    ),
)
