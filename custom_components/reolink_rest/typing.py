"""Common Typings"""

from datetime import timedelta
from typing import (
    Mapping,
    MutableMapping,
    Protocol,
    Sequence,
)

from aiohttp.web import Request, Response

from homeassistant.core import HomeAssistant, CALLBACK_TYPE
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from async_reolink.api.connection import typing as commands

from async_reolink.api.network import typing as network
from async_reolink.api.system.capabilities import Capabilities


from async_reolink.rest.client import Client

# class _EntityData(Protocol):
#     """Entity Data and API"""

#     client: Client
#     device: DeviceEntry
#     time_difference: timedelta
#     abilities: Capabilities
#     device_info: system.DeviceInfo
#     channels: Mapping[int, DeviceInfo]
#     ports: network.NetworkPorts
#     updated_motion: frozenset[int]
#     ai: ai.Config
#     motion: Mapping[int, Motion]
#     updated_ptz: frozenset[int]
#     ptz: Mapping[int, PTZ]

#     def async_request_motion_update(self, channel: int = 0) -> None:
#         """Request motion update for channel"""

#     def async_request_ptz_update(self, channel: int = 0) -> None:
#         """Request PTZ update for channel"""


class ChannelData(Protocol):
    """Common Entity Channel Data"""

    device: DeviceInfo
    offline: bool


CANCEL_CALLBACK = CALLBACK_TYPE


class RequestQueue(Protocol):
    """Request Queue"""

    @property
    def responses(self) -> Sequence[commands.CommandResponse]:
        """current queue responses"""
        ...

    def append(self, request: commands.CommandRequest) -> CANCEL_CALLBACK:
        """Add request to queue, returns callback to remove pending request"""
        ...

    def index(self, request: commands.CommandRequest) -> int:
        """Return the index of the command in the queue or -1 for not found"""
        ...


class EntityData(Protocol):
    """Common Entity Data"""

    device: DeviceInfo
    capabilities: Capabilities
    ports: network.NetworkPorts
    channels: Mapping[int, ChannelData]
    time_diff: timedelta


class EntryData(Protocol):
    """Common Entry Data"""

    client: Client
    coordinator: DataUpdateCoordinator[EntityData]
    hispeed_coordinator: DataUpdateCoordinator


EntryKey = str
DomainData = MutableMapping[EntryKey, EntryData]


class AsyncWebhookHandler(Protocol):
    """Async Webhook Handler"""

    async def __call__(self, hass: HomeAssistant, request: Request) -> Response | None:
        ...


class WebhookManager(Protocol):
    """Webhook Manager"""

    @property
    def url(self) -> str:
        """full webhook url"""

    def async_add_handler(self, handler: AsyncWebhookHandler) -> CALLBACK_TYPE:
        """add callable handler returns removal callback"""
