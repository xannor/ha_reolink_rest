""" Reolink Intergration """

from __future__ import annotations
import asyncio
from datetime import timedelta
from inspect import isawaitable

import logging
import ssl
from typing import TYPE_CHECKING, Callable, Final, Iterable, Mapping, ParamSpec, TypeVar
import aiohttp
from async_timeout import timeout

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.config_entries import ConfigEntry, ConfigEntryNotReady, ConfigEntryState
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType

if TYPE_CHECKING:
    from homeassistant.helpers import (
        service as helper_service,
        device_registry as helper_device_registry,
        entity_registry as helper_entity_registry,
        issue_registry as helper_issue_registry,
    )
from ._utilities.hass_typing import hass_bound
from ._utilities.asyncio import Busy

from homeassistant.const import (
    Platform,
    CONF_HOST,
    CONF_SCAN_INTERVAL,
)

from .typing import (
    AsyncResponseHandlerType,
    DiscoveredDevice,
    DomainDataType,
    EntryData,
    EntryId,
    RequestHandler,
    RequestType,
    ResponseHandlerType,
    is_request_handler_tuple,
)

from async_reolink.api.errors import ErrorCodes, ReolinkResponseError
from async_reolink.api.connection.model import ErrorResponse, Request, Response

from .api import ReolinkDeviceApi

from .const import (
    DATA_API,
    DATA_COORDINATOR,
    DATA_HISPEED_COORDINDATOR,
    DEFAULT_HISPEED_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    OPT_CHANNELS,
    OPT_HISPEED_INTERVAL,
)

from ._utilities.list import partition

_LOGGER = logging.getLogger(__name__)

PLATFORMS: Final = (
    Platform.CAMERA,
    Platform.BINARY_SENSOR,
    # Platform.NUMBER,
    Platform.SENSOR,
    # Platform.SWITCH,
    Platform.LIGHT,
    # Platform.BUTTON,
    # Platform.SIREN,
)


@callback
def _async_get_poll_interval(options: Mapping[str, any]):
    """Get the poll interval"""
    interval = options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    return timedelta(seconds=interval)


@callback
def _async_get_hispeed_poll_interval(options: Mapping[str, any]):
    """Get the high speed poll interval"""
    interval = options.get(OPT_HISPEED_INTERVAL, DEFAULT_HISPEED_INTERVAL)
    return timedelta(seconds=interval)


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    """Setup ReoLink Component"""

    # ensure data exists
    hass.data.setdefault(DOMAIN, {})

    async def _reboot_handler(call: ServiceCall):
        _LOGGER.debug("Reboot called.")
        domain_data: DomainDataType = hass.data.get(DOMAIN)
        if not domain_data:
            _LOGGER.warning("No domain data loaded")
            return
        service: helper_service = hass.helpers.service
        entries: set[str] = await hass_bound(service.async_extract_config_entry_ids)(call)
        for entry_id in entries:
            if entry_data := domain_data.get(entry_id):
                if api := entry_data.get(DATA_API):
                    await api.client.reboot()
                    hass.create_task(entry_data[DATA_COORDINATOR].async_request_refresh())

    hass.services.async_register(DOMAIN, "reboot", _reboot_handler)

    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up ReoLink Device from a config entry."""

    _LOGGER.debug("Setting up entry")

    domain_data: dict[str, any] = hass.data.setdefault(DOMAIN, {})
    entry_data: EntryData = domain_data.setdefault(config_entry.entry_id, {})

    config_entry.async_on_unload(config_entry.add_update_listener(_entry_updated))

    client_busy = Busy()
    entry_data["client_busy"] = client_busy

    api = entry_data.setdefault(DATA_API, new_api := ReolinkDeviceApi())
    coordinator = entry_data.get(DATA_COORDINATOR)
    if not coordinator:

        async def client_updater(self: DataUpdateCoordinator):
            async with client_busy:
                callbacks, requests = partition(
                    callable,
                    await _issue_trap(
                        self.hass,
                        api,
                        self.config_entry.entry_id,
                        api.async_get_client_update_requests,
                        hass,
                        self.config_entry.entry_id,
                        self.config_entry.unique_id,
                        **self.config_entry.options | self.config_entry.data,
                    ),
                )
                responses = await _execute_commands(
                    self.hass, api, self.config_entry, *requests, *self.async_contexts()
                )
                for _c in callbacks:
                    _c()
                return responses

        coordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name=f"{config_entry.title} Update Coordinator",
            update_method=client_updater,
            update_interval=_async_get_poll_interval(config_entry.options),
        )
        entry_data[DATA_COORDINATOR] = coordinator
        coordinator.update_method = client_updater.__get__(coordinator)

    if config_entry.state == ConfigEntryState.NOT_LOADED:
        # on an new run (hass initializing) we want to timeout so a dead/moved device
        # does not delay the startup for very long, a working one should respond quickly
        # anyway
        __timeout = 5
        try:
            async with timeout(__timeout):
                await coordinator.async_config_entry_first_refresh()
        except asyncio.TimeoutError:
            raise ConfigEntryNotReady(
                f"Failed to connect to device in {__timeout} seconds so delaying setup"
            )
    else:
        await coordinator.async_config_entry_first_refresh()

    coordinator = entry_data.get(DATA_HISPEED_COORDINDATOR)
    if not coordinator:

        async def simple_updater(self: DataUpdateCoordinator):
            async with client_busy:
                return await _execute_commands(
                    self.hass, api, self.config_entry, *self.async_contexts()
                )

        coordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            name=f"{config_entry.title} Frequent Update Coordinator",
            update_method=simple_updater,
            update_interval=_async_get_hispeed_poll_interval(config_entry.options),
        )
        entry_data[DATA_HISPEED_COORDINDATOR] = coordinator
        coordinator.update_method = simple_updater.__get__(coordinator)
    # this should never fail if the prior succeeded
    await coordinator.async_config_entry_first_refresh()

    hass.create_task(hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        domain_data: DomainDataType
        if domain_data := hass.data.get(DOMAIN):
            if entry_data := domain_data.get(entry.entry_id):
                if api := entry_data.get(DATA_API):
                    try:
                        await api.client.disconnect()
                    except Exception:  # pylint: disable=broad-except
                        _LOGGER.exception("Error ocurred while cleaning up entry")
    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
):
    """Remove device from configuration"""
    # TODO : cleanup storage/etc
    return True


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Migrate older entries"""

    _LOGGER.debug("Migrating from version %s", config_entry.version)
    device_registry: helper_device_registry = hass.helpers.device_registry
    devices = await hass_bound(device_registry.async_get_registry)()

    if config_entry.version == 1:
        unique_id = config_entry.unique_id
        data = {**config_entry.data}
        options = {**config_entry.options}
        discovery: DiscoveredDevice.JSON
        uuid = None
        mac = None
        ip: str = data.get(CONF_HOST)
        if discovery := options.pop("discovery", None):
            uuid = discovery.get(DiscoveredDevice.Keys.uuid)
            mac = discovery.get(DiscoveredDevice.Keys.mac)
            ip = discovery.get(DiscoveredDevice.Keys.ip, ip)

        if not uuid and unique_id and unique_id.startswith("uid_"):
            uuid = unique_id[4:]
        if not mac and unique_id and unique_id.startswith("device_mac_"):
            mac = unique_id[11 : unique_id.index("_", 12)]
        unique_id = None

        def entry_devices():
            for device in devices.devices.values():
                if config_entry.entry_id in device.config_entries:
                    yield device

        def scan_devices():
            nonlocal uuid, mac
            known_devices: list[DeviceEntry] = []
            for device in entry_devices():
                known_devices.append(device)
                if not uuid:
                    uuid = next(
                        map(
                            lambda t: t[1],
                            filter(lambda t: t[0] == f"{DOMAIN}_uuid", iter(device.identifiers)),
                        ),
                        None,
                    )
                if not mac:
                    mac = next(
                        map(
                            lambda t: t[1],
                            filter(
                                lambda t: t[0] == device_registry.CONNECTION_NETWORK_MAC,
                                iter(device.connections),
                            ),
                        ),
                        None,
                    )
            return tuple(known_devices)

        known_devices = None
        if not uuid or not mac:
            known_devices = scan_devices()

        if uuid:
            uuid = uuid.upper()

        if mac:
            mac = device_registry.format_mac(mac)

        if uuid:
            unique_id = uuid
        elif mac:
            unique_id = mac

        if ip:
            data[CONF_HOST] = ip

        if not known_devices:
            known_devices = tuple(scan_devices())

        for device in known_devices:
            via_device = None
            identifiers: set[tuple[str, str]] = set()
            if device.via_device_id:
                key = f"{DOMAIN}_{config_entry.unique_id or config_entry.entry_id}_channel"
                channel = next(
                    map(
                        lambda t: int(t[1]), filter(lambda t: t[0] == key, iter(device.identifiers))
                    ),
                    -1,
                )
                if channel >= 0:
                    identifiers.add(
                        (f"{DOMAIN}_{unique_id or config_entry.entry_id}_channel", str(channel))
                    )
            else:
                if unique_id:
                    identifiers.add((DOMAIN, unique_id))
                if uuid:
                    identifiers.add((f"{DOMAIN}_uuid", uuid))
            connections = device.connections.copy()
            if mac:
                connections.add((device_registry.CONNECTION_NETWORK_MAC, mac))
            if identifiers != device.identifiers or connections != device.connections:
                devices.async_update_device(
                    device.id, new_identifiers=identifiers, merge_connections=connections
                )

        if unique_id != config_entry.unique_id:

            def migrate_entity(entity: helper_entity_registry.RegistryEntry):
                new_unique_id = entity.unique_id.replace(
                    config_entry.unique_id or config_entry.entry_id,
                    unique_id or config_entry.entry_id,
                )
                if new_unique_id != entity.unique_id:
                    return {"new_unique_id": new_unique_id}
                return None

            entity_registry: helper_entity_registry = hass.helpers.entity_registry
            await entity_registry.async_migrate_entries(hass, config_entry.entry_id, migrate_entity)

        config_entry.version = 2
        if hass.config_entries.async_update_entry(
            config_entry, unique_id=unique_id, data=data, options=options
        ):
            _LOGGER.info("Migration to version %s successful", config_entry.version)

            return True
    return False


T = TypeVar("T")
P = ParamSpec("P")


def _issue_trap(
    hass: HomeAssistant,
    api: ReolinkDeviceApi,
    config_entry_id: EntryId,
    operation: Callable[P, T],
    *args: P.args,
    **kwargs: P.kwargs,
):
    issue_registry: helper_issue_registry = hass.helpers.issue_registry
    try:
        return operation(*args, **kwargs)
    except aiohttp.ClientResponseError as http_error:
        if http_error.status in (301, 302, 308) and "location" in http_error.headers:
            location = http_error.headers["location"]
            # TODO : verify redirect stays on device
            if api.client.secured and location.startswith("http://"):
                issue_registry.async_create_issue(
                    hass,
                    DOMAIN,
                    "from_ssl_redirect",
                    severity=issue_registry.IssueSeverity.ERROR,
                    is_fixable=True,
                    data={"entry_id": config_entry_id},
                    translation_key="from_ssl_redirect",
                    translation_placeholders={
                        "name": api.device_name,
                    },
                )
            elif not api.client.secured and location.startswith("https://"):
                issue_registry.async_create_issue(
                    hass,
                    DOMAIN,
                    "to_ssl_redirect",
                    severity=issue_registry.IssueSeverity.ERROR,
                    is_fixable=True,
                    data={"entry_id": config_entry_id},
                    translation_key="from_ssl_redirect",
                    translation_placeholders={
                        "name": api.device_name,
                    },
                )
        elif http_error.status == 500:
            if api.client.secured:
                # this error occurs when HTTPS is disabled on the camera but we try to connect to it.
                issue_registry.async_create_issue(
                    hass,
                    DOMAIN,
                    "from_ssl_redirect",
                    severity=issue_registry.IssueSeverity.ERROR,
                    is_fixable=True,
                    data={"entry_id": config_entry_id},
                    translation_key="from_ssl_redirect",
                    translation_placeholders={
                        "name": api.device_name,
                    },
                )
            else:
                issue_registry.async_create_issue(
                    hass,
                    DOMAIN,
                    "http_error",
                    severity=issue_registry.IssueSeverity.CRITICAL,
                    is_fixable=True,
                    data={"entry_id": config_entry_id},
                    translation_key="http_error",
                    translation_placeholders={
                        "name": api.device_name,
                    },
                )
        raise http_error
    except ssl.SSLError as ssl_error:
        if ssl_error.errno == 1:
            issue_registry.async_create_issue(
                hass,
                DOMAIN,
                "insecure_ssl",
                severity=issue_registry.IssueSeverity.ERROR,
                is_fixable=True,
                data={"entry_id": config_entry_id},
                translation_key="insecure_ssl",
                translation_placeholders={
                    "name": api.device_name,
                },
            )
        raise ssl_error


async def _execute_commands(
    hass: HomeAssistant,
    api: ReolinkDeviceApi,
    config_entry: ConfigEntry,
    *commands: RequestType | Iterable[RequestType],
):
    sync_responses: list[Response] = []
    handlers: dict[int, list[ResponseHandlerType | AsyncResponseHandlerType]] = {}

    def iter_commands(value: any, depth=0):
        if isinstance(value, Request):
            yield value
        elif isinstance(value, RequestHandler):
            if isinstance(value.request, type):
                _LOGGER.warning(
                    "Skiping invalid request (type instead of instance) of %s",
                    value.request,
                )
                return
            handlers.setdefault(value.request.id, []).append(value.handler)
            yield value.request
        elif is_request_handler_tuple(value):
            handlers.setdefault(value[0].id, []).append(value[1])
            yield value[0]
        elif depth < 2 and isinstance(value, Iterable):
            for _i in value:
                _r: Request
                for _r in iter_commands(_i, depth + 1):
                    yield _r

    await _issue_trap(
        hass,
        api,
        config_entry.entry_id,
        api.async_ensure_connection,
        hass,
        **config_entry.options | config_entry.data,
    )

    async for response in api.client.batch(iter_commands(commands)):
        if isinstance(response, ErrorResponse):
            response.throw()
        elif not isinstance(response, Response):
            raise ReolinkResponseError(
                response,
                code=ErrorCodes.PROTOCOL_ERROR,
                details="Did not get a valid response",
            )

        sync_responses.append(response)
        if response.request_id and (_handlers := handlers.get(response.request_id)):
            for handler in _handlers:
                result = handler(response)
                if isawaitable(result):
                    await result

    return tuple(sync_responses)


async def _entry_updated(hass: HomeAssistant, entry: ConfigEntry):
    domain_data: DomainDataType = hass.data.get(DOMAIN)
    if not domain_data:
        return
    entry_data = domain_data.get(entry.entry_id)
    if not entry_data:
        return
    interval = _async_get_poll_interval(entry.options)
    coordinator = entry_data.get(DATA_COORDINATOR)
    if coordinator and interval != coordinator.update_interval:
        coordinator.update_interval = interval
        hass.create_task(coordinator.async_request_refresh())
    interval = _async_get_hispeed_poll_interval(entry.options)
    coordinator = entry_data.get(DATA_HISPEED_COORDINDATOR)
    if coordinator and interval != coordinator.update_interval:
        coordinator.update_interval = interval
        hass.create_task(coordinator.async_request_refresh())

    if entry.state == ConfigEntryState.LOADED:
        channels = entry.options.get(OPT_CHANNELS, None)
        # TODO : detect channel count change for forced reload
        # hass.create_task(hass.config_entries.async_reload(entry.entry_id))

    if api := entry_data.get(DATA_API, None):
        if (
            api.has_client
            and (host := entry.data.get(CONF_HOST, None))
            and api.client.hostname != host
        ):
            client_busy: Busy = entry_data.get("client_busy")
            if client_busy:
                await client_busy.wait()
            await api.client.disconnect()
