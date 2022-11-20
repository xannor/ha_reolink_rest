"""Models"""

import dataclasses

from homeassistant.components.sensor import SensorEntityDescription

from ..entity import (
    ChannelMixin,
    DeviceSupportedMixin,
    DataUpdateHandlerMixin,
)


@dataclasses.dataclass
class ReolinkSensorEntityDescription(
    DeviceSupportedMixin,
    DataUpdateHandlerMixin,
    SensorEntityDescription,
):
    """Reolink Sensor Entity Description"""

    has_entity_name: bool = True


@dataclasses.dataclass
class ReolinkChannelSensorEntityDescription(
    ChannelMixin,
    ReolinkSensorEntityDescription,
):
    """Reolink Channeled Sensor Entity Description"""
