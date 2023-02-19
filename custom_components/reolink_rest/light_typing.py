"""Light Entity Typings"""

__all__ = (
    "LightEntityDescription",
    "LightEntityChannelDescription",
    "LightEntityConfigMixin",
    "create_entity_channel_config",
)

import dataclasses
from typing import Protocol
from typing_extensions import TypeVar, Self
from homeassistant.components.light import (
    LightEntity,
    LightEntityDescription as BaseLightEntityDescription,
    LightEntityFeature,
    ColorMode,
)

from .typing import (
    DeviceEntityConfig,
    ChannelEntityConfig,
    AsyncEntityServiceCallback,
    AsyncEntityInitializedCallback,
    EntityDataHandlerCallback,
    DeviceSupportedCallback,
    ChannelSupportedCallback,
)

from .entity import ChannelDescriptionMixin

_NO_FEATURE: LightEntityFeature = 0


@dataclasses.dataclass
class LightEntityDescription(BaseLightEntityDescription):
    """Reolink Light Entity Description"""

    has_entity_name: bool = True
    features: LightEntityFeature = dataclasses.field(default=_NO_FEATURE, kw_only=True)
    color_modes: set[ColorMode] = dataclasses.field(
        default_factory=lambda: set(ColorMode.ONOFF), kw_only=True
    )


@dataclasses.dataclass
class LightEntityChannelDescription(
    ChannelDescriptionMixin,
    LightEntityDescription,
):
    """Reolink Channeled Light Entity Description"""


@dataclasses.dataclass(frozen=True, kw_only=True)
class LightEntityConfigMixin:
    """Light Entity Configuration Mixin"""

    on_call: AsyncEntityServiceCallback[LightEntity]
    off_call: AsyncEntityServiceCallback[LightEntity]
    init_handler: AsyncEntityInitializedCallback[LightEntity] | None = None
    data_handler: EntityDataHandlerCallback[LightEntity] | None = None


_DT = TypeVar(
    "_DT",
    bound=LightEntityDescription,
    infer_variance=True,
    default=LightEntityDescription,
)


@dataclasses.dataclass(frozen=True, kw_only=True)
class LighEntityConfig(LightEntityConfigMixin, DeviceEntityConfig[_DT]):
    """Light Channel Entity Configuration"""

    @classmethod
    def create(
        cls,
        description: _DT,
        device_supported: DeviceSupportedCallback[_DT],
        on_call: AsyncEntityServiceCallback[LightEntity],
        off_call: AsyncEntityServiceCallback[LightEntity],
        /,
        init_handler: AsyncEntityInitializedCallback[LightEntity] | None = None,
        data_handler: EntityDataHandlerCallback[LightEntity] | None = None,
        **kwargs: any,
    ) -> Self:
        return super().create(
            description,
            device_supported,
            on_call=on_call,
            off_call=off_call,
            init_handler=init_handler,
            data_handler=data_handler**kwargs,
        )


@dataclasses.dataclass(frozen=True, kw_only=True)
class LightChannelEntityConfig(LightEntityConfigMixin, ChannelEntityConfig[_DT]):
    """Light Channel Entity Configuration"""

    @classmethod
    def create(
        cls,
        description: _DT,
        channel_supported: ChannelSupportedCallback[_DT],
        on_call: AsyncEntityServiceCallback[LightEntity],
        off_call: AsyncEntityServiceCallback[LightEntity],
        /,
        device_supported: DeviceSupportedCallback[_DT] = None,
        init_handler: AsyncEntityInitializedCallback[LightEntity] = None,
        data_handler: EntityDataHandlerCallback[LightEntity] | None = None,
        **kwargs: any,
    ) -> Self:
        return super().create(
            description,
            channel_supported,
            device_supported=device_supported,
            on_call=on_call,
            off_call=off_call,
            init_handler=init_handler,
            data_handler=data_handler,
            **kwargs,
        )
