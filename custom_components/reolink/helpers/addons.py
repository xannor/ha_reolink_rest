"""Addon Helpers"""
from __future__ import annotations
from typing import cast

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.components.hassio.const import (
    SupervisorEntityModel,
    DOMAIN as HASSIO_DOMAIN,
)
from homeassistant.core import HomeAssistant, Event
from homeassistant.helpers.singleton import singleton
from homeassistant.helpers import device_registry as dr
from homeassistant.loader import bind_hass

from ..const import DOMAIN
from ..typings.component import HassDomainData


@singleton(f"{DOMAIN}-tracked-addons")
@bind_hass
def async_get_addon_tracker(hass: HomeAssistant):
    """setup the event monitor and do inital addon scan"""

    if not hass.components.hassio.is_hassio():
        return None

    domain_data = cast(HassDomainData, hass.data)[DOMAIN]
    # tracker_data: dict = domain_data.setdefault(DATA_MOTION_ADDONS, {})

    def handle_shutdown(event: Event):
        pass

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, handle_shutdown)

    async def scan_addons():
        dev_reg = await dr.async_get_registry(hass)
        for entry in (
            entry
            for entry in dev_reg.devices.values()
            if entry.model == SupervisorEntityModel.ADDON
            and entry.entry_type == dr.DeviceEntryType.SERVICE
        ):
            slug = next((slug for d, slug in entry.identifiers if d == HASSIO_DOMAIN))
            if slug is None:
                continue
            addon_info: dict = await hass.components.hassio.async_get_addon_info(slug)
            if not "reolink_motion:provide" in addon_info.get("services_role", []):
                continue

    return hass.async_add_job(scan_addons)
