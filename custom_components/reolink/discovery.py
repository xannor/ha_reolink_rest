"""Reloink integration device discovery"""
from __future__ import annotations
import asyncio
from datetime import timedelta
from ipaddress import IPv4Address

import logging

from dataclasses import asdict

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

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

LISTENERS = "listeners"

LISTENER_CLEANUP = "listener_cleanup"

DISCOVERED = "discovered"

DISCOVERY = "discovery"

DISCOVERY_CLEANUP = "discovery_cleanup"

DISCOVERY_INTERVAL = timedelta(seconds=5)


class _Protocol(DiscoveryProtocol):
    def __init__(self, ping_message: bytes = ...) -> None:
        super().__init__(ping_message)
        self._cache: list[DiscoveredDevice] = []

    @property
    def discovered(self):
        """Devices discovered so far"""
        cache = self._cache
        self._cache = []
        return cache

    def discovered_device(self, device: DiscoveredDevice) -> None:
        self._cache.append(device)

    @classmethod
    async def listen(
        cls, address: str = "0.0.0.0", port: int = ...
    ) -> tuple[asyncio.BaseTransport, _Protocol]:
        return await super().listen(address, port)


@bind_hass
async def async_start_listener(hass: HomeAssistant, address: str = "0.0.0.0"):
    """Start discovery listener"""
    domain_data: dict = hass.data.setdefault(DOMAIN, {})
    listeners: dict[
        str, tuple[asyncio.BaseTransport, _Protocol]
    ] = domain_data.setdefault(LISTENERS, {})

    if address != "0.0.0.0":
        targets = [address]
    else:
        targets = [
            str(addr)
            for addr in filter(
                lambda addr: isinstance(addr, IPv4Address),
                await network.async_get_enabled_source_ips(hass),
            )
        ]

    targets = [filter(lambda addr: addr not in listeners, targets)]

    if len(targets) < 1:
        return

    for target in targets:
        listener = await _Protocol.listen(target)
        listeners[target] = listener

    # register global listener cleanup
    if LISTENER_CLEANUP not in domain_data:
        domain_data[LISTENER_CLEANUP] = hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, lambda _: async_stop_listener(hass)
        )


@callback
@bind_hass
def async_stop_listener(hass: HomeAssistant, address: str = "0.0.0.0"):
    "Stop discovery listener"
    domain_data: dict | None = hass.data.get(DOMAIN, None)
    listeners: dict[str, tuple[asyncio.BaseTransport, _Protocol]] | None = (
        domain_data.get(LISTENERS, None) if domain_data is not None else None
    )

    if listeners is None:
        return

    if address == "0.0.0.0":
        targets = [listeners.keys()]
    else:
        targets = [address]

    for target in targets:
        listener = listeners.pop(target, None)
        if listener is not None:
            listener[0].close()


@callback
@bind_hass
def async_start_discovery(
    hass: HomeAssistant, interval: timedelta = DISCOVERY_INTERVAL
):
    """Start discovery"""

    domain_data: dict = hass.data.setdefault(DOMAIN, {})
    if DISCOVERY in domain_data:
        return

    async def _async_discovery(*_: any):
        async_trigger_discovery(hass, await async_discover_devices(hass, 5))

    async def _startup():
        await async_start_listener(hass)
        await _async_discovery()

    domain_data[DISCOVERY] = async_track_time_interval(hass, _async_discovery, interval)
    hass.create_task(_startup)

    if DISCOVERY_CLEANUP not in domain_data:
        domain_data[DISCOVERY_CLEANUP] = hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, lambda _: async_stop_discovery(hass)
        )


@callback
@bind_hass
def async_stop_discovery(hass: HomeAssistant):
    """Stop Discovery"""
    domain_data: dict = hass.data.get(DOMAIN, None)
    discovery: CALLBACK_TYPE | None = (
        domain_data.pop(DISCOVERY, None) if domain_data is not None else None
    )
    if discovery is None:
        return
    discovery()


@bind_hass
async def async_discover_devices(
    hass: HomeAssistant, delay: float = 0, address: str = "0.0.0.0"
):
    """Discover Reolink devices"""

    domain_data: dict | None = hass.data.get(DOMAIN, None)
    listeners: dict[str, tuple[asyncio.BaseTransport, _Protocol]] | None = (
        domain_data.get(LISTENERS, None) if domain_data is not None else None
    )

    if listeners is None:
        return

    if address != "0.0.0.0":
        targets = [address]
    else:
        targets = [
            str(addr) for addr in await network.async_get_ipv4_broadcast_addresses(hass)
        ]

    for target in targets:
        _Protocol.ping(target)

    if delay > 0:
        await asyncio.sleep(delay)

    devices = (
        device
        for discovered in map(lambda t: t[1].discovered, listeners.values())
        for device in discovered
    )
    return tuple(devices)


@callback
@bind_hass
def async_trigger_discovery(hass: HomeAssistant, *discovered_devices: DiscoveredDevice):
    """Trigger config flow for discovered devices"""
    for device in discovered_devices:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_INTEGRATION_DISCOVERY},
                data=asdict(device),
            )
        )
