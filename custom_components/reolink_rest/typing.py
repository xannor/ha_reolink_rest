"""Common Typings"""

from abc import ABC
import dataclasses
from datetime import timedelta
from types import SimpleNamespace
from typing import (
    TYPE_CHECKING,
    Callable,
    Coroutine,
    Final,
    Generic,
    Mapping,
    NamedTuple,
    Protocol,
    TypeGuard,
    TypedDict,
)
from typing_extensions import TypeVar, TypeAlias, Self, NotRequired

from homeassistant.helpers.entity import DeviceInfo, EntityDescription, Entity
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, CoordinatorEntity

from async_reolink.api.connection.model import Request, Response
from async_reolink.api.system.capabilities import Capabilities, ChannelCapabilities
from async_reolink.api.network.typing import NetworkPorts, ChannelStatus
from async_reolink.api.system.typing import DeviceInfo as ReolinkDeviceInfo

if TYPE_CHECKING:
    from .api import ReolinkDeviceApi
else:
    from typing import Any

    ReolinkDeviceApi = Any

EntryId = str

CoordinatorDataType = tuple[Response, ...]

ResponseCoordinatorType = DataUpdateCoordinator[CoordinatorDataType]


class DiscoveredDevice(ABC):
    """Discovered Device"""

    class JSON(TypedDict):
        """JSON"""

        ip: str
        mac: str
        name: NotRequired[str]
        ident: NotRequired[str]
        uuid: NotRequired[str]

    class Keys(Protocol):
        """Keys"""

        ip: Final = "ip"
        mac: Final = "mac"
        name: Final = "name"
        ident: Final = "ident"
        uuid: Final = "uuid"


class EntryData(TypedDict, total=False):
    """Entry Data"""

    api: ReolinkDeviceApi
    coordinator: ResponseCoordinatorType
    hispeed_coordinator: ResponseCoordinatorType


DomainDataType = Mapping[EntryId, EntryData]


class ChannelData(Protocol):
    """Channel Data"""

    channel_id: int
    device: DeviceInfo


class DeviceData(Protocol):
    """Device Data"""

    capabilities: Capabilities
    device_info: ReolinkDeviceInfo
    ports: NetworkPorts
    channel_statuses: Mapping[int, ChannelStatus]
    channel_info: Mapping[int, ChannelData]
    time_diff: timedelta


ResponseHandlerType = Callable[[Response], None]
AsyncResponseHandlerType = Callable[[Response], Coroutine[any, any, None]]


class RequestHandler(NamedTuple):
    """Request Handler"""

    request: Request
    handler: ResponseHandlerType | AsyncResponseHandlerType


RequestHandlerTuple = tuple[Request, ResponseHandlerType | AsyncResponseHandlerType]

RequestType = Request | RequestHandler | RequestHandlerTuple


def is_request_handler_tuple(value: any) -> TypeGuard[RequestHandlerTuple]:
    """test is value is a tuple of request and handler"""
    return (
        isinstance(value, tuple)
        and len(value) == 2
        and isinstance(value[0], Request)
        and callable(value[1])
    )


_DT = TypeVar("_DT", bound=EntityDescription, infer_variance=True, default=EntityDescription)


class DeviceSupportedCallback(Protocol[_DT]):
    """Device Supported"""

    def __call__(
        self,
        description: _DT,
        capabilities: Capabilities,
        device_data: DeviceData,
    ) -> bool:
        ...


@dataclasses.dataclass(frozen=True, kw_only=True)
class DeviceEntityConfig(Generic[_DT]):
    """Base Device Entry"""

    description: _DT
    device_supported: DeviceSupportedCallback[_DT]

    @classmethod
    def create(
        cls, description: _DT, device_supported: DeviceSupportedCallback[_DT], **kwargs: any
    ) -> Self:
        return cls(description=description, device_supported=device_supported, **kwargs)


class ChannelSupportedCallback(Protocol[_DT]):
    """Channel Supported"""

    def __call__(
        self,
        description: _DT,
        capabilities: ChannelCapabilities,
        channel_data: ChannelData,
    ) -> bool:
        ...


def device_always_supported(_self: _DT, _caps: Capabilities, _data: DeviceData):
    """Device is always supported"""

    return True


@dataclasses.dataclass(frozen=True, kw_only=True)
class ChannelEntityConfig(DeviceEntityConfig[_DT]):
    """Base Device Channel Entry"""

    channel_supported: ChannelSupportedCallback[_DT]

    @classmethod
    def create(
        cls,
        description: _DT,
        channel_supported: ChannelSupportedCallback[_DT],
        /,
        device_supported: DeviceSupportedCallback[_DT] = None,
        **kwargs: any,
    ) -> Self:
        if device_supported is None or device_supported is ...:
            device_supported = device_always_supported
        return super().create(
            description, device_supported, channel_supported=channel_supported, **kwargs
        )


_TE = TypeVar("_TE", bound=Entity, infer_variance=True, default=Entity)

AsyncEntityInitializedCallback: TypeAlias = Callable[
    [
        _TE,
    ],
    Coroutine[any, any, None],
]

EntityDataHandlerCallback: TypeAlias = Callable[[_TE], None]


class EntityServiceCallback(Protocol[_TE]):
    def __call__(self, entity: _TE, **kwds: any) -> None:
        ...


class AsyncEntityServiceCallback(Protocol[_TE]):
    async def __call__(self, entity: _TE, **kwds: any) -> None:
        ...
