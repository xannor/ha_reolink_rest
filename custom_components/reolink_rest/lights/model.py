"""Models"""

import dataclasses

from homeassistant.components.light import LightEntityDescription, LightEntityFeature

from ..entity import (
    EntityServiceCall,
    RequestQueueMixin,
    DeviceSupportedMixin,
    ChannelMixin,
)


@dataclasses.dataclass
class ReolinkLightServiceCallMixin:
    """required fields"""

    on_call: EntityServiceCall
    off_call: EntityServiceCall


@dataclasses.dataclass
class ReolinkLightEntityDescription(
    DeviceSupportedMixin,
    ChannelMixin,
    RequestQueueMixin,
    LightEntityDescription,
    ReolinkLightServiceCallMixin,
):
    """Reolink Light Entity Description"""

    has_entity_name: bool = True
