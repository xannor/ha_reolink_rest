"""Common Typings"""

from datetime import timedelta
from typing import Mapping, Protocol, TypedDict

from aiohttp.web import Request, Response

from homeassistant.core import HomeAssistant, CALLBACK_TYPE
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.device_registry import DeviceEntry
from async_reolink.api.ai import typings as ai
from async_reolink.api.network import typings as network
from async_reolink.api.system import typings as system
from async_reolink.api.system.capabilities import Capabilities


from async_reolink.rest import Client

from .models import Motion, PTZ


class EntityData(Protocol):
    """Entity Data and API"""

    client: Client
    device: DeviceEntry
    time_difference: timedelta
    abilities: Capabilities
    device_info: system.DeviceInfo
    channels: Mapping[int, DeviceInfo]
    ports: network.NetworkPorts
    updated_motion: frozenset[int]
    ai: ai.Config
    motion: Mapping[int, Motion]
    updated_ptz: frozenset[int]
    ptz: Mapping[int, PTZ]

    def async_request_motion_update(self, channel: int = 0) -> None:
        """Request motion update for channel"""

    def async_request_ptz_update(self, channel: int = 0) -> None:
        """Request PTZ update for channel"""


class ReolinkEntryData(TypedDict, total=False):
    """Common entry data"""

    coordinator: DataUpdateCoordinator[EntityData]
    motion_coordinators: dict[int, DataUpdateCoordinator[EntityData]]


ReolinkDomainData = dict[str, ReolinkEntryData]


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
