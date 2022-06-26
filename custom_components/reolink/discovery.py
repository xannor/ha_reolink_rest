"""Reloink integration device discovery"""
from __future__ import annotations
import asyncio
from datetime import timedelta
from ipaddress import IPv4Address

import logging

from dataclasses import asdict
from typing import Callable, Final

from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY
from homeassistant.core import HomeAssistant, callback, CALLBACK_TYPE
from homeassistant.components import network
from homeassistant.loader import bind_hass
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.helpers.event import async_track_time_interval

from reolinkapi.discovery import (
    Protocol as DiscoveryProtocol,
    Device as DiscoveredDevice,
)

from .settings import Settings, async_get_settings

from .const import DOMAIN, SETTING_DISCOVERY, SETTING_DISCOVERY_BROADCAST

_LOGGER = logging.getLogger(__name__)

DISCOVERY: Final = "discovery"

DISCOVERY_INTERVAL: Final = timedelta(seconds=5)

DEVICE_CALLBACK: Final = Callable[
    [DiscoveredDevice], None
]  # pylint: disable=invalid-name


class Protocol(DiscoveryProtocol):
    """Protocol"""

    def __init__(self) -> None:
        super().__init__()
        self._discovery_callback: DEVICE_CALLBACK = lambda _: None

    def discovered_device(self, device: DiscoveredDevice) -> None:
        self._discovery_callback(device)

    @property
    def discovery_callback(self):
        """Callback"""
        return self._discovery_callback

    @discovery_callback.setter
    def discovery_callback(self, value: DEVICE_CALLBACK):
        self._discovery_callback = value


@callback
@bind_hass
def async_start_discovery(
    hass: HomeAssistant, interval: timedelta = DISCOVERY_INTERVAL
):
    """Start discovery"""

    domain_data: dict = hass.data.setdefault(DOMAIN, {})
    if DISCOVERY in domain_data:
        return False

    listeners: list[tuple[asyncio.BaseTransport, Protocol]] = []
    interval_cleanup: CALLBACK_TYPE = lambda: None

    def _cleanup():
        nonlocal interval_cleanup
        interval_cleanup()
        for (transport, _) in listeners:
            transport.close()

    bus_cleanup: CALLBACK_TYPE = None

    def _shutdown():
        bus_cleanup()
        _cleanup()

    domain_data[DISCOVERY] = _shutdown

    bus_cleanup = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _cleanup)

    async def _ping(*_):
        # support an override setting for complex setups
        settings: Settings = await async_get_settings(hass)
        if addr := settings.get(SETTING_DISCOVERY, {}).get(
            SETTING_DISCOVERY_BROADCAST, None
        ):
            Protocol.ping(str(addr))
            return True

        for addr in await network.async_get_ipv4_broadcast_addresses(hass):
            Protocol.ping(str(addr))
        return True

    def _discovered(device: DiscoveredDevice):
        hass.create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_INTEGRATION_DISCOVERY},
                data=asdict(device),
            )
        )

    async def _startup():
        nonlocal interval_cleanup
        for addr in await network.async_get_enabled_source_ips(hass):
            if not isinstance(addr, IPv4Address):
                continue
            listener = await Protocol.listen(str(addr))
            listener[1].discovery_callback = _discovered
            listeners.append(listener)

        # interval_cleanup = async_track_time_interval(hass, _ping, interval)
        await _ping()

    hass.create_task(_startup())


@callback
@bind_hass
def async_stop_discovery(hass: HomeAssistant):
    """Stop Discovery"""
    domain_data: dict = hass.data.get(DOMAIN, None)
    if domain_data is None:
        return
    cleanup: CALLBACK_TYPE = domain_data.pop(DISCOVERY, None)
    if cleanup is not None:
        cleanup()


@callback
@bind_hass
def async_discovery_active(hass: HomeAssistant):
    """Check if discovery is running"""
    domain_data: dict = hass.data.get(DOMAIN, None)
    if domain_data is None:
        return False
    return DISCOVERY in domain_data


__all__ = ["async_start_discovery", "async_stop_discovery", "async_discovery_active"]
