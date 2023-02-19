"""Reolink Binary Sensor Typings"""

import dataclasses
from typing_extensions import TypeVar, Self

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription as BaseBinarySensorEntityDescription,
)

from .typing import (
    ChannelEntityConfig,
    ChannelSupportedCallback,
    DeviceEntityConfig,
    DeviceSupportedCallback,
)

from .typing import (
    EntityDataHandlerCallback,
    AsyncEntityInitializedCallback,
)

from .entity import ChannelDescriptionMixin


@dataclasses.dataclass
class BinarySensorEntityDescription(BaseBinarySensorEntityDescription):
    """Reolink BinarySensor Entity Description"""

    has_entity_name: bool = True


@dataclasses.dataclass
class BinarySensorEntityChannelDescription(
    ChannelDescriptionMixin,
    BinarySensorEntityDescription,
):
    """Reolink Channeled Sensor Entity Description"""


_DT = TypeVar(
    "_DT",
    bound=BinarySensorEntityDescription,
    infer_variance=True,
    default=BinarySensorEntityDescription,
)


@dataclasses.dataclass(frozen=True, kw_only=True)
class BinarySensorEntityConfigMixin:
    """Sensor Entity Configuration Mixin"""

    data_handler: EntityDataHandlerCallback[BinarySensorEntity] | None = None
    init_handler: AsyncEntityInitializedCallback[BinarySensorEntity] | None = None


@dataclasses.dataclass(frozen=True, kw_only=True)
class BinarySensorEntityConfig(BinarySensorEntityConfigMixin, DeviceEntityConfig[_DT]):
    """Binary Binary Sensor Channel Entity Configuration"""

    @classmethod
    def create(
        cls,
        description: _DT,
        device_supported: DeviceSupportedCallback[_DT],
        /,
        init_handler: AsyncEntityInitializedCallback[BinarySensorEntity] | None = None,
        data_handler: EntityDataHandlerCallback[BinarySensorEntity] | None = None,
        **kwargs: any,
    ) -> Self:
        return super().create(
            description,
            device_supported,
            data_handler=data_handler,
            init_handler=init_handler,
            **kwargs,
        )


@dataclasses.dataclass(frozen=True, kw_only=True)
class BinarySensorChannelEntityConfig(BinarySensorEntityConfigMixin, ChannelEntityConfig[_DT]):
    """Sensor Channel Entity Configuration"""

    @classmethod
    def create(
        cls,
        description: _DT,
        channel_supported: ChannelSupportedCallback[_DT],
        /,
        device_supported: DeviceSupportedCallback[_DT] = None,
        init_handler: AsyncEntityInitializedCallback[BinarySensorEntity] | None = None,
        data_handler: EntityDataHandlerCallback[BinarySensorEntity] | None = None,
        **kwargs: any,
    ) -> Self:
        return super().create(
            description,
            channel_supported,
            device_supported=device_supported,
            data_handler=data_handler,
            init_handler=init_handler,
            **kwargs,
        )
