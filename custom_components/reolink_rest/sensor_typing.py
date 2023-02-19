"""Reolink Sensor Typings"""

import dataclasses
from typing_extensions import TypeVar, Self

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription as BaseSensorEntityDescription,
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
class SensorEntityDescription(BaseSensorEntityDescription):
    """Reolink Sensor Entity Description"""

    has_entity_name: bool = True


@dataclasses.dataclass
class SensorEntityChannelDescription(
    ChannelDescriptionMixin,
    SensorEntityDescription,
):
    """Reolink Channeled Sensor Entity Description"""


_DT = TypeVar(
    "_DT",
    bound=SensorEntityDescription,
    infer_variance=True,
    default=SensorEntityDescription,
)


@dataclasses.dataclass(frozen=True, kw_only=True)
class SensorEntityConfigMixin:
    """Sensor Entity Configuration Mixin"""

    data_handler: EntityDataHandlerCallback[SensorEntity] | None = None
    init_handler: AsyncEntityInitializedCallback[SensorEntity] | None = None


@dataclasses.dataclass(frozen=True, kw_only=True)
class SensorEntityConfig(SensorEntityConfigMixin, DeviceEntityConfig[_DT]):
    """Sensor Channel Entity Configuration"""

    @classmethod
    def create(
        cls,
        description: _DT,
        device_supported: DeviceSupportedCallback[_DT],
        /,
        data_handler: EntityDataHandlerCallback[SensorEntity] = None,
        init_handler: AsyncEntityInitializedCallback[SensorEntity] = None,
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
class SensorChannelEntityConfig(SensorEntityConfigMixin, ChannelEntityConfig[_DT]):
    """Sensor Channel Entity Configuration"""

    @classmethod
    def create(
        cls,
        description: _DT,
        channel_supported: ChannelSupportedCallback[_DT],
        /,
        device_supported: DeviceSupportedCallback[_DT],
        data_handler: EntityDataHandlerCallback[SensorEntity] = None,
        init_handler: AsyncEntityInitializedCallback[SensorEntity] = None,
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
