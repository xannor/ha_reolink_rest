"""Common Typings"""

from typing import Generic, Literal, Mapping, Protocol, TypeVar

from aiohttp.web import Request, Response

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, CALLBACK_TYPE
from reolinkrestapi import Client

from .models import EntityData, ChannelMotionData

_T = TypeVar("_T")


class _DataUpdateCoordinator(Protocol, Generic[_T]):

    config_entry: ConfigEntry
    data: _T

    async def async_request_refresh(self) -> None:
        """Request a refresh.

        Refresh will wait a bit to see if it can batch them.
        """


class ReolinkDataUpdateCoordinator(_DataUpdateCoordinator[EntityData]):
    """Reolink Data Update Coordinator like"""

    @property
    def client(self) -> Client:
        """Active Client"""

    @property
    def motion_coordinator(self) -> _DataUpdateCoordinator[ChannelMotionData]:
        """Motion Cooridnator"""


ReolinkDomainData = Mapping[
    str, Mapping[Literal["coordinator"], ReolinkDataUpdateCoordinator]
]


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
