"""Common Typings"""

from typing import (
    Protocol,
)

from aiohttp.web import Request, Response

from homeassistant.core import HomeAssistant, CALLBACK_TYPE


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
