"""Models"""

import dataclasses

from homeassistant.components.binary_sensor import BinarySensorEntityDescription

from ..entity import ChannelMixin, DeviceSupportedMixin


@dataclasses.dataclass
class ReolinkBinarySensorEntityDescription(
    DeviceSupportedMixin,
    ChannelMixin,
    BinarySensorEntityDescription,
):
    """Reolink BinarySensor Entity Description"""

    has_entity_name: bool = True
