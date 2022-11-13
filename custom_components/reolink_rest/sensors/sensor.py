""" Base Sensor and structures"""

from dataclasses import dataclass
from homeassistant.components.sensor import (
    SensorEntityDescription,
)

from ..entity import ReolinkDeviceEntityDescriptionMixin


@dataclass
class ReolinkDeviceSensorEntityDescription(
    SensorEntityDescription, ReolinkDeviceEntityDescriptionMixin
):
    """Reolink Device Sensor Entity Description"""
