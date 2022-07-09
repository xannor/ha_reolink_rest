"""Webhook interface and handlers"""

from __future__ import annotations
import logging

from aiohttp.web import Request, Response

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.network import get_url, NoURLAvailableError

try:
    from homeassistant.components import webhook
except ImportError:
    webhook = None

from .const import DOMAIN


class WebHook:
    """Webhook Manager"""

    def __init__(
        self, hass: HomeAssistant, logger: logging.Logger, entry: ConfigEntry
    ) -> None:
        self._id = entry.unique_id
        self._logger = logger
        if webhook:
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
                    logger.warning(
                        "Could not get an addressable url, disabling webook support"
                    )
                    url = None

            if url:
                webhook.async_register(
                    hass,
                    DOMAIN,
                    entry.title + " Webhook",
                    self._id,
                    self._handle_webhook,
                )

                def _unregister():
                    webhook.async_unregister(hass, self._id)
                    self._unregister = None

                self._unregister = _unregister
                self._webhook_url = f"{url}{webhook.async_generate_path(self._id)}"

    @property
    def active(self):
        """webhook is active"""
        return bool(self._webhook_url)

    def async_close(self):
        """Remove webhook"""
        if self._unregister:
            self._unregister()

    async def _handle_webhook(
        self, hass: HomeAssistant, webhook_id: str, request: Request
    ) -> Response | None:
        pass
