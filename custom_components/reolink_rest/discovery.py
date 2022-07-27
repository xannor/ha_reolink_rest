"""Reolink Discovery support"""

import logging
from homeassistant.core import HomeAssistant
from homeassistant.loader import bind_hass
from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY
from homeassistant.helpers.singleton import singleton
from homeassistant.helpers.typing import DiscoveryInfoType
from homeassistant.helpers.discovery import async_listen
from homeassistant.helpers.discovery_flow import async_create_flow

from .const import DISCOVERY_EVENT, DOMAIN, OPT_DISCOVERY

_LOGGER = logging.getLogger(__name__)


@bind_hass
@singleton(f"{DOMAIN}_discovery_data")
async def async_discovery_handler(hass: HomeAssistant):
    """Discovery Handler"""

    data = {}

    async def _discovery(service: str, info: DiscoveryInfoType):
        if service == DISCOVERY_EVENT:
            for entry in hass.config_entries.async_entries(DOMAIN):
                if OPT_DISCOVERY in entry.options:
                    discovery: dict = entry.options[OPT_DISCOVERY]
                    key = "uuid"
                    if not key in discovery or not key in info:
                        key = "mac"
                    if key in discovery and key in info and discovery[key] == info[key]:
                        if next(
                            (
                                True
                                for k in info
                                if k not in discovery or discovery[k] != info[k]
                            ),
                            False,
                        ):
                            options = entry.options.copy()
                            options[OPT_DISCOVERY] = discovery = discovery.copy()
                            discovery.update(info)

                            if not hass.config_entries.async_update_entry(
                                entry, options=options
                            ):
                                _LOGGER.warning("Discovery: Could not update options")
                        return

            async_create_flow(
                hass, DOMAIN, {"source": SOURCE_INTEGRATION_DISCOVERY}, info
            )

    async_listen(hass, DISCOVERY_EVENT, _discovery)

    return data
