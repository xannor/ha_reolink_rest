"""Reolink Number Typings"""

import dataclasses
from typing_extensions import TypeVar, Self

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription as BaseNumberEntityDescription,
)

from .typing import (
    AsyncEntityInitializedCallback,
    ChannelEntityConfig,
    ChannelSupportedCallback,
    DeviceEntityConfig,
    DeviceSupportedCallback,
    EntityDataHandlerCallback,
)

from .entity import ChannelDescriptionMixin


@dataclasses.dataclass
class NumberEntityDescription(BaseNumberEntityDescription):
    """Reolink Number Entity Description"""

    has_entity_name: bool = True
