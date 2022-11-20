"""Wifi sensors"""

from dataclasses import dataclass
from typing import Final
from homeassistant.helpers.entity import EntityCategory
from homeassistant.components.sensor import (
    SensorStateClass,
)

from homeassistant.const import PERCENTAGE

from async_reolink.api.network.command import GetWifiInfoResponse, GetWifiSignalResponse

from ..entity import ReolinkValueEntityDescriptionMixin, _S, T

from .sensor import ReolinkDeviceSensorEntityDescription


@dataclass
class ReolinkWifiSensorEntityDescription(
    ReolinkDeviceSensorEntityDescription, ReolinkValueEntityDescriptionMixin[_S, T]
):
    """Reolink Wifi Sensor Entity Description"""


@dataclass
class ReolinkWifiInfoSensorEntityDescription(
    ReolinkWifiSensorEntityDescription[GetWifiInfoResponse, T]
):
    """Reolink Wifi Info Sensor Entity Description"""


@dataclass
class ReolinkWifiSignalSensorEntityDescription(
    ReolinkWifiSensorEntityDescription[GetWifiSignalResponse, T]
):
    """Reolink Wifi Info Sensor Entity Description"""


SENSORS: Final = (
    ReolinkWifiSignalSensorEntityDescription(
        key="wifi_signal",
        name="Wi-Fi signal",
        icon="mdi:wifi",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        exists_fn=lambda caps: caps.wifi,
        value_fn=lambda response: response.signal,
    ),
    ReolinkWifiInfoSensorEntityDescription(
        key="wifi_ssid",
        name="Wi-Fi SSID",
        icon="mdi:wifi",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        exists_fn=lambda caps: caps.wifi,
        value_fn=lambda response: response.info.ssid,
    ),
)
