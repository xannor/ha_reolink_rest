"""Services Helpers"""

from __future__ import annotations
from asyncio import sleep
import logging

from typing import Literal, cast
from aiohttp import ClientConnectionError, ClientSession, ContentTypeError
import async_timeout
from homeassistant.const import EVENT_HOMEASSISTANT_STOP,     CONF_HOST, CONF_USERNAME,    CONF_PASSWORD

from homeassistant.core import HomeAssistant, Event
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.singleton import singleton
from urllib.parse import urlparse

from homeassistant.loader import bind_hass
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, Debouncer

from reolinkapi.helpers.abilities.ability import NO_ABILITY
from ..typings.services import AddonServiceEventData

from ..const import DOMAIN, EMAIL_SERVICE, ONVIF_SERVICE, ONVIF_SERVICE_TYPE, EMAIL_SERVICE_TYPE
from ..typings.component import EntryData, HassDomainData

DATA_TRACKER = f"{DOMAIN}-service-tracker"
VERSION = 1

EVENT_SERVICE = "reolink_motion_addon"

_LOGGER = logging.getLogger(__name__)


@singleton(DATA_TRACKER)
@bind_hass
async def async_get(hass: HomeAssistant):
    """Setup services"""

    store = Store(hass, VERSION, f'{DOMAIN}_services')
    domain_data = cast(HassDomainData, hass.data)[DOMAIN]
    tracker: dict[ONVIF_SERVICE_TYPE | EMAIL_SERVICE_TYPE,
                  dict[str, Literal[False] | str]] = {}

    async def save_trackers(_: Event):
        await store.async_save(tracker)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, save_trackers)

    async def service_notice(event: Event):
        data: AddonServiceEventData = event.data
        if not "addon" in data or not isinstance(data["addon"], str):
            return

        if "type" in data and data["type"] in (ONVIF_SERVICE, EMAIL_SERVICE) and "url" in data and urlparse(str(data["url"])).scheme == "http":
            addons = tracker.setdefault(data["type"], {})
            addons[data["addon"]] = data["url"]
            return

        for addons in tracker.values():
            if data["addon"] in addons:
                addons[data["addon"]] = False

    hass.bus.async_listen(EVENT_SERVICE, service_notice)

    store_data = await store.async_load()
    if isinstance(store_data, dict):
        tracker.update(store_data)

    return tracker

MAX_RETRIES = 3


async def async_setup(hass: HomeAssistant, event_id: str, entry_data: EntryData, config_entry: ConfigEntry, event_coordinator: DataUpdateCoordinator):
    """Setup addons services for device"""

    services = await async_get(hass)
    if services is None:
        return

    update_interval = event_coordinator.update_interval
    addon: str | None = None
    unregister: str | None = None

    async def cleanup():
        nonlocal unregister, addon
        if unregister is None:
            return
        addon = None
        _unregister = unregister
        unregister = None
        try:
            async with async_timeout.Timeout(2):
                client: ClientSession = hass.helpers.aiohttp_client.async_get_clientsession(
                    False)
                async with client.get(_unregister):
                    return
        except:
            # eat all errors as then unregister does not matter
            pass

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, cleanup)

    async def register():
        nonlocal addon, unregister

        match = False
        for type in (EMAIL_SERVICE, ONVIF_SERVICE):
            if match:
                break
            if entry_data.coordinator.data.abilities.get(type, NO_ABILITY)["ver"] and type in services:
                for _addon, status in services[type].items():
                    if status == False:
                        continue
                    if _addon == addon:
                        match = True
                        break

                    reg_data = {"event_type": event_id}
                    if type == ONVIF_SERVICE:
                        if not entry_data.coordinator.data.ports.get("onvifEnable", False) or entry_data.coordinator.data.ports.get("onvifPort", 0) == 0:
                            continue
                        reg_data["port"] = entry_data.coordinator.data.ports["onvifPort"]
                        reg_data["host"] = config_entry.data[CONF_HOST]
                        reg_data["username"] = config_entry.data[CONF_USERNAME]
                        reg_data["password"] = config_entry.data[CONF_PASSWORD]
                    elif type == EMAIL_SERVICE:
                        # TODO : configure email
                        continue

                    await cleanup()
                    try:
                        client: ClientSession = hass.helpers.aiohttp_client.async_get_clientsession(
                            False)
                        attempt = 1
                        data = None
                        while attempt <= MAX_RETRIES and data is None:
                            try:
                                # addons should respond fast, so even 2 seconds is probably too long (except when debugging :)
                                async with async_timeout.timeout(2):
                                    async with client.post(status, json=reg_data) as response:
                                        try:
                                            data = await response.json()
                                        except ContentTypeError:
                                            _LOGGER.error(
                                                "Addon %s did not respond to register with expected json", _addon)
                                            _LOGGER.debug("Register Got: %s", response.content_type, await response.text())
                            except (ClientConnectionError, ConnectionRefusedError):
                                # sometimes the hassos dns does not update quick enough so we will retry in connection errors
                                _LOGGER.debug(
                                    "Addon %s could not be contacted: attempts %s", _addon, attempt)
                                attempt += 1
                                await sleep(1)
                                continue
                            break

                        if data is None and attempt > MAX_RETRIES:
                            _LOGGER.warn(
                                "Failed to contact addon %s after %s attempts, giving up.", _addon, attempt-1)
                    except:
                        _LOGGER.error(
                            "Failed to register with addon %s", _addon, exc_info=1)
                        continue

                    if data is None:
                        continue

                    unregister = str(
                        response.url.with_path(data["unregister"]))
                    match = True
                    event_coordinator.update_interval = None
                    addon = _addon

                    if type == EMAIL_SERVICE:
                        pass
                    break

        if not match:
            await cleanup()

        if addon is None:
            event_coordinator.update_interval = update_interval
            await event_coordinator.async_refresh()

    debouncer = Debouncer(hass, _LOGGER, immediate=False,
                          cooldown=0.5, function=register)

    async def addon_notice(_event: Event):
        hass.async_add_job(debouncer.async_call())

    hass.bus.async_listen(EVENT_SERVICE, addon_notice)

    await debouncer.async_call()
