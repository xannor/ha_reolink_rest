"""Webhook interface and handlers"""

from __future__ import annotations

import logging
from typing import Final

from aiohttp.web import Request, Response

from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.network import get_url, NoURLAvailableError
from homeassistant.loader import bind_hass

from .typing import AsyncWebhookHandler

try:
    from homeassistant.components import webhook
except ImportError:
    webhook = None

from .const import DOMAIN

DATA_MANAGER: Final = "webhook"


class WebhookManager:
    """Webhook Manager"""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        entry: ConfigEntry,
        base_url: str,
    ) -> None:
        self._id = f"{DOMAIN}_{entry.unique_id}"
        self._entry_id = entry.entry_id
        self._logger = logger
        self._handlers: list[AsyncWebhookHandler] = []

        webhook.async_register(
            hass,
            DOMAIN,
            entry.title + " Webhook",
            self._id,
            self._handle_webhook,
        )
        self._webhook_url = f"{base_url}{webhook.async_generate_path(self._id)}"

        def _unregister():
            webhook.async_unregister(hass, self._id)

        entry.async_on_unload(_unregister)

    @property
    def url(self):
        """Current url"""
        return self._webhook_url

    async def _handle_webhook(
        self, hass: HomeAssistant, webhook_id: str, request: Request
    ) -> Response | None:
        if webhook_id != self._id:
            return None

        request["entry_id"] = self._entry_id

        for handler in self._handlers.copy():
            response = await handler(hass, request)
            if response is not None:
                return response

        return None

    def async_add_handler(self, handler: AsyncWebhookHandler):
        """Register Handler"""

        self._handlers.append(handler)

        def _remove():
            self._handlers.remove(handler)

        return _remove


@callback
@bind_hass
def async_get_webhook_manager(
    hass: HomeAssistant, logger: logging.Logger, entry: ConfigEntry
) -> WebhookManager:
    """Get or create new Webhook Manager for entry"""

    if not webhook:
        return None

    domain_data: dict = hass.data[DOMAIN]
    entry_data: dict = domain_data[entry.entry_id]

    if DATA_MANAGER in entry_data:
        return entry_data[DATA_MANAGER]

    try:
        url = get_url(hass, prefer_external=False, allow_cloud=False)
    except NoURLAvailableError:
        logger.warning(
            "Could not get internal url from system"
            ", will attempt external url but this is not preferred"
            ", please verify your installation."
        )

        try:
            url = get_url(hass, allow_cloud=False)
        except NoURLAvailableError:
            logger.warning("Could not get an addressable url, disabling webook support")
            url = None

    if not url:
        return None

    entry_data[DATA_MANAGER] = manager = WebhookManager(hass, logger, entry, url)
    return manager
