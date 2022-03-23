"""Addon Helpers"""

from homeassistant.components import hassio
from homeassistant.core import HomeAssistant


async def async_find_service_providers(hass: HomeAssistant, service: str):
    """Return a list of addons that provide a service"""

    _hassio: hassio.handler.HassIO = hass.data.get(hassio.const.DOMAIN, None)
    if _hassio is None:
        return []
    discovered = await _hassio.retrieve_discovery_messages()

    return []
