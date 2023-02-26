"""PTZ"""

from asyncio import Task
import asyncio
import dataclasses
from typing import Callable, Final, Protocol, TYPE_CHECKING

from homeassistant.components.number import NumberEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from async_reolink.api.system import capabilities

from async_reolink.rest.connection.model import ResponseTypes
from async_reolink.api.ptz import typing as ptz_typing
from async_reolink.rest.ptz import command as ptz_command

from .._utilities.object import setdefaultattr

from ..typing import RequestHandler

from ..api import ChannelData

from ..entity import ReolinkEntity

from ..number_typing import (
    NumberEntityConfig,
    NumberChannelEntityConfig,
    NumberEntityDescription,
    NumberEntityChannelDescription as BaseNumberEntityChannelDescription,
)


@dataclasses.dataclass()
class NumberEntityChannelDescription(BaseNumberEntityChannelDescription):
    """PTZ Number Entity Desctiption"""

    entity_category = EntityCategory.CONFIG


class PTZChannelData(ChannelData, Protocol):
    """PTZ Channel Data"""

    zoom_focus_task: Task[ptz_command.GetZoomFocusResponse]


class _RangeData(Protocol):
    pass


async def _init_zoom_focus(
    self: NumberEntity,
    get_value: Callable[[ptz_command.GetZoomFocusResponse], float],
    get_range: Callable[[ptz_command._ZoomFocusRange], tuple[float, float]],
):
    # pylint: disable=protected-access
    if not isinstance(self, ReolinkEntity):
        raise ValueError()

    channel_data: PTZChannelData = self._channel_data

    def handle_response(response: ptz_command.GetZoomFocusResponse):
        self._attr_native_value = get_value(response)

    self.coordinator_context = (
        RequestHandler(ptz_command.GetZoomFocusRequest(channel_data.channel_id), handle_response),
    )

    setdefaultattr(channel_data, "zoom_focus_task", None)

    if (task := channel_data.zoom_focus_task) is not None:
        response = await task
    else:

        async def get_zoomfocus():
            client = self._client

            async for response in client.batch(
                [ptz_command.GetZoomFocusRequest(channel_data.channel_id, ResponseTypes.DETAILED)]
            ):
                if isinstance(response, ptz_command.GetZoomFocusResponse):
                    return response
            raise ValueError()

        def clear_task():
            channel_data.zoom_focus_task = None

        task = self.hass.async_create_task(get_zoomfocus())
        channel_data.zoom_focus_task = task

        response = await task
        self.hass.loop.call_later(1, clear_task)

    if response.is_detailed:
        self._attr_min_value, self._attr_max_value = get_range(response.state_range)
    handle_response(response)
    self._attr_available = True


# ptz_command.GetZoomFocusRequest
async def _init_focus(self: NumberEntity):
    # pylint: disable=protected-access
    if not isinstance(self, ReolinkEntity):
        raise ValueError()

    def get_value(response: ptz_command.GetZoomFocusResponse):
        return float(response.state.focus)

    def get_range(range: ptz_command._ZoomFocusRange):
        return (float(range.focus.min), float(range.focus.max))

    await _init_zoom_focus(self, get_value, get_range)


async def _set_zoom_focus(self: NumberEntity, value: float, operation: ptz_typing.ZoomOperation):
    # pylint: disable=protected-access
    if not isinstance(self, ReolinkEntity):
        raise ValueError()

    def should_release(self: NumberEntity):
        return self.value == value

    await self._client.set_ptz_zoom_focus(int(value), operation, self._channel_id)
    self._temp_attach_hispeed_coordinator(should_release)()
    # even the the command suceeds, there is a delay before it actually happens so we will
    # wait for confirmation
    while self.value != value:
        await asyncio.sleep(1)
    if (
        TYPE_CHECKING
    ):  # useless statement for forced static type check since isinstance throws on generics
        if not isinstance(self, CoordinatorEntity[DataUpdateCoordinator]):
            return
    # we still queue a refresh because other things may need updating
    self.hass.create_task(self.coordinator.async_request_refresh())


async def _set_focus(self: NumberEntity, value: float):
    await _set_zoom_focus(self, value, ptz_typing.ZoomOperation.FOCUS)


async def _init_zoom(self: NumberEntity):
    # pylint: disable=protected-access
    if not isinstance(self, ReolinkEntity):
        raise ValueError()

    def get_value(response: ptz_command.GetZoomFocusResponse):
        return float(response.state.zoom)

    def get_range(range: ptz_command._ZoomFocusRange):
        return (float(range.zoom.min), float(range.zoom.max))

    await _init_zoom_focus(self, get_value, get_range)


async def _set_zoom(self: NumberEntity, value: float):
    await _set_zoom_focus(self, value, ptz_typing.ZoomOperation.ZOOM)


# ptz_command.GetAutoFocusRequest
# ptz_command.GetPatrolRequest
# ptz_command.GetPresetRequest
# ptz_command.GetTatternRequest
NUMBERS: Final = (
    NumberChannelEntityConfig.create(
        NumberEntityChannelDescription(
            "ptz_focus_position",
            name="Focus",
            icon="mdi:camera-iris",
        ),
        lambda _self, channel, _data: channel.ptz.control == capabilities.PTZControl.ZOOM_FOCUS,
        _set_focus,
        init_handler=_init_focus,
    ),
    NumberChannelEntityConfig.create(
        NumberEntityChannelDescription(
            "ptz_zoom_position",
            name="Zoom",
            icon="mdi:magnify",
        ),
        lambda _self, channel, _data: channel.ptz.control
        in {capabilities.PTZControl.ZOOM, capabilities.PTZControl.ZOOM_FOCUS},
        _set_zoom,
        init_handler=_init_zoom,
    ),
)
