"""Wifi sensors"""

from types import SimpleNamespace
from typing import Final, Protocol

from homeassistant.helpers.entity import EntityCategory
from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)

from homeassistant.const import PERCENTAGE

from async_reolink.rest.connection.model import ResponseTypes
from async_reolink.rest.network import command as network_command

from ..typing import RequestHandler

from ..entity import ReolinkEntity

from ..sensor_typing import SensorEntityConfig
from ..sensor_typing import (
    SensorEntityDescription,
)

from .._utilities.object import lazysetdefaultattr


async def _info_init(self: SensorEntity):
    # pylint: disable=protected-access
    if not isinstance(self, ReolinkEntity):
        raise ValueError()

    def handle_response(response: network_command.GetWifiInfoResponse):
        self._attr_native_value = response.info.ssid

    self.coordinator_context = (
        RequestHandler(network_command.GetWifiInfoRequest(), handle_response),
    )

    info = await self._client.get_wifi()
    self._attr_native_value = info.ssid


class _RangeData(Protocol):
    wifi_signal_range: network_command.MinMaxRange[int]


def _set_signal_value(self: SensorEntity, response: network_command.GetWifiSignalResponse):
    # pylint: disable=protected-access

    range_data: _RangeData
    if (
        (range_data := getattr(self, "_wifi_data", None)) is None
        or not hasattr(range_data, "wifi_signal_range")
        or not (signal_range := range_data.wifi_signal_range)
    ):
        return

    self._attr_native_value = min(response.signal, signal_range.min) / signal_range.max


async def _signal_init(self: SensorEntity):
    # pylint: disable=protected-access
    if not isinstance(self, ReolinkEntity):
        raise ValueError()

    def handle_response(response: network_command.GetWifiSignalResponse):
        _set_signal_value(self, response)

    self.coordinator_context = (
        RequestHandler(network_command.GetWifiSignalRequest(), handle_response),
    )

    client = self._client

    async for response in client.batch(
        [network_command.GetWifiSignalRequest(ResponseTypes.DETAILED)]
    ):
        if isinstance(response, network_command.GetWifiSignalResponse):
            if response.is_detailed:
                range_data: _RangeData = lazysetdefaultattr(self, "_wifi_data", SimpleNamespace)
                range_data.wifi_signal_range = response.signal_range

                _set_signal_value(self, response)


_SENSOR: Final = SensorEntityConfig.create(
    SensorEntityDescription(
        key="wifi_ssid",
        name="Wi-Fi SSID",
        icon="mdi:wifi",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
    ),
    lambda _self, device, _data: device.wifi,
    init_handler=_info_init,
)

SENSORS: Final = (
    _SENSOR,
    SensorEntityConfig.create(
        SensorEntityDescription(
            key="wifi_signal",
            name="Wi-Fi signal",
            icon="mdi:wifi",
            native_unit_of_measurement=PERCENTAGE,
            state_class=SensorStateClass.MEASUREMENT,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        _SENSOR.device_supported,
        init_handler=_signal_init,
    ),
)
