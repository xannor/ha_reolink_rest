"""Webhook interface and handlers"""

from __future__ import annotations

import logging
from typing import Callable, NamedTuple

from aiohttp.web import Request, Response

from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.network import get_url, NoURLAvailableError
from homeassistant.helpers.singleton import singleton

from .typing import AsyncWebhookHandler

try:
    from homeassistant.components import webhook
except ImportError:
    webhook = None

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class Webhook:
    """Webhook"""

    def __init__(
        self, manager: WebhookManager, webook_id: str, remove: Callable[[], None]
    ) -> None:
        self._id = webook_id
        self._url = f"{manager.url}{webhook.async_generate_path(webook_id)}"
        self._handlers: list[AsyncWebhookHandler] = []
        self._remove = remove

    @property
    def id(self):
        """id"""
        return self._id

    @property
    def url(self):
        """url"""
        return self._url

    def async_add_handler(self, handler: AsyncWebhookHandler):
        """Add Handler"""
        self._handlers.append(handler)

        def _remove():
            self._handlers.remove(handler)

        return _remove

    async def async_notify_handlers(self, hass: HomeAssistant, request: Request):
        """Notify handlers of webhook call"""

        for handler in self._handlers.copy():
            response = await handler(hass, request)
            if response is not None:
                return response

        return None

    def async_remove(self):
        """Remove webook"""
        self._remove()


class _WebhookAndEntryId(NamedTuple):

    entry_id: str
    webhook: Webhook


class WebhookManager:
    """Webhook Manager"""

    def __init__(
        self,
        base_url: str,
    ) -> None:
        self._base_url = base_url
        self._webhooks: dict[str, _WebhookAndEntryId] = {}

    def async_register(self, hass: HomeAssistant, config_entry: ConfigEntry):
        """Get or create webhook for entry"""
        for t in self._webhooks.values():
            if t.entry_id == config_entry.entry_id:
                return t.webhook

        def _unregister():
            if _webhook.id in self._webhooks:
                del self._webhooks[_webhook.id]
                webhook.async_unregister(hass, _webhook.id)

        _webhook = Webhook(self, f"{DOMAIN}_{config_entry.unique_id}", _unregister)
        self._webhooks[_webhook.id] = _WebhookAndEntryId(
            config_entry.entry_id, _webhook
        )

        config_entry.async_on_unload(_unregister)

        webhook.async_register(
            hass,
            DOMAIN,
            f"{config_entry.title} Webhook",
            _webhook.id,
            self._handle_webhook,
        )

        return _webhook

    @property
    def url(self):
        """Current url"""
        return self._base_url

    async def _handle_webhook(
        self, hass: HomeAssistant, webhook_id: str, request: Request
    ) -> Response | None:
        if webhook_id not in self._webhooks:
            return None

        _LOGGER.debug("Webhook hit for %s", webhook_id)
        _entry = self._webhooks[webhook_id]
        request["entry_id"] = _entry.entry_id
        return await _entry.webhook.async_notify_handlers(hass, request)


@singleton(f"{DOMAIN}-webhook-manager")
@callback
def async_get_webhook_manager(hass: HomeAssistant):
    """Get or create new Webhook Manager for entry"""

    if not webhook:
        return None

    try:
        url = get_url(hass, prefer_external=False, allow_cloud=False)
    except NoURLAvailableError:
        _LOGGER.warning(
            "Could not get internal url from system"
            ", will attempt external url but this is not preferred"
            ", please verify your installation."
        )

        try:
            url = get_url(hass, allow_cloud=False)
        except NoURLAvailableError:
            _LOGGER.warning(
                "Could not get an addressable url, disabling webook support"
            )
            url = None

    if not url:
        return None

    return WebhookManager(url)
