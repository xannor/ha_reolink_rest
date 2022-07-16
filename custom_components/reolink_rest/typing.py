"""Common Typings"""

from typing import Protocol, TypeVar, TypedDict

from aiohttp.web import Request, Response

from homeassistant.core import HomeAssistant, CALLBACK_TYPE
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.device_registry import DeviceEntry
from async_reolink.rest import Client

from .models import DeviceData, MotionData

_T = TypeVar("_T")


class ReolinkEntryData(TypedDict, total=False):
    """Reolink Entry Data"""

    client: Client
    device: DeviceEntry
    coordinator: DataUpdateCoordinator[DeviceData]
    motion_coordinator: DataUpdateCoordinator[MotionData]
    motion_data_request: set[int]


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
