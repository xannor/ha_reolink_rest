"""Reolink Number Typings"""

import dataclasses
from typing_extensions import TypeVar, Self

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription as BaseNumberEntityDescription,
)

from .typing import (
    AsyncCallable,
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


@dataclasses.dataclass
class NumberEntityChannelDescription(
    ChannelDescriptionMixin,
    NumberEntityDescription,
):
    """Reolink Channeled Number Entity Description"""


_DT = TypeVar(
    "_DT",
    bound=NumberEntityDescription,
    infer_variance=True,
    default=NumberEntityDescription,
)


@dataclasses.dataclass(frozen=True, kw_only=True)
class NumberEntityConfigMixin:
    """Number Entity Configuration Mixin"""

    set_value_call: AsyncCallable[[NumberEntity, float], None]
    data_handler: EntityDataHandlerCallback[NumberEntity] | None = None
    init_handler: AsyncEntityInitializedCallback[NumberEntity] | None = None


@dataclasses.dataclass(frozen=True, kw_only=True)
class NumberEntityConfig(NumberEntityConfigMixin, DeviceEntityConfig[_DT]):
    """Number Channel Entity Configuration"""

    @classmethod
    def create(
        cls,
        description: _DT,
        device_supported: DeviceSupportedCallback[_DT],
        set_value_call: AsyncCallable[[NumberEntity, float], None],
        /,
        data_handler: EntityDataHandlerCallback[NumberEntity] = None,
        init_handler: AsyncEntityInitializedCallback[NumberEntity] = None,
        **kwargs: any,
    ) -> Self:
        return super().create(
            description,
            device_supported,
            set_value_call=set_value_call,
            data_handler=data_handler,
            init_handler=init_handler,
            **kwargs,
        )


@dataclasses.dataclass(frozen=True, kw_only=True)
class NumberChannelEntityConfig(NumberEntityConfigMixin, ChannelEntityConfig[_DT]):
    """Number Channel Entity Configuration"""

    @classmethod
    def create(
        cls,
        description: _DT,
        channel_supported: ChannelSupportedCallback[_DT],
        set_value_call: AsyncCallable[[NumberEntity, float], None],
        /,
        device_supported: DeviceSupportedCallback[_DT] = None,
        data_handler: EntityDataHandlerCallback[NumberEntity] = None,
        init_handler: AsyncEntityInitializedCallback[NumberEntity] = None,
        **kwargs: any,
    ) -> Self:
        return super().create(
            description,
            channel_supported,
            device_supported=device_supported,
            set_value_call=set_value_call,
            data_handler=data_handler,
            init_handler=init_handler,
            **kwargs,
        )
