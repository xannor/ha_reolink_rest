"""API Interface"""

from datetime import timedelta
import logging
import ssl
from time import time
from typing import Mapping

import aiohttp
from async_reolink.api.const import DEFAULT_PASSWORD, DEFAULT_TIMEOUT, DEFAULT_USERNAME
from async_reolink.api.errors import ErrorCodes, ReolinkResponseError
from async_reolink.api.network.typing import ChannelStatus, NetworkPorts
from async_reolink.api.system.capabilities import Capabilities
from async_reolink.api.system.typing import (
    DeviceInfo as ReolinkDeviceInfo,
    DaylightSavingsTimeInfo,
    TimeInfo,
)
from async_reolink.rest.client import Client
from async_reolink.rest.connection.models import CommandRequest, CommandResponse
from async_reolink.rest.connection.typing import Encryption
from async_reolink.rest.network.models import (
    ChannelStatuses as UpdatableChannelStatuses,
)
from async_reolink.rest.system.capabilities import Capabilities as UpdatableCapabilities
from async_reolink.rest.system.models import DeviceInfo as UpdatableDeviceInfo
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_TIMEOUT,
    CONF_USERNAME,
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue
from homeassistant.helpers.update_coordinator import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    DataUpdateCoordinator,
)
from homeassistant.util import dt

from .discovery import DiscoveryDict

ChannelStatuses = Mapping[int, ChannelStatus]

from async_reolink.rest.errors import AUTH_ERRORCODES

from .const import (
    CONF_USE_HTTPS,
    DEFAULT_HISPEED_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    OPT_BATCH_CAPABILITIES,
    OPT_DISCOVERY,
    OPT_HISPEED_INTERVAL,
    OPT_SSL,
    SSLMode,
)


def _weak_ssl_context(__base_url: str):
    """Create a weak ssl context to work with self signed certs"""
    return False


def _insecure_ssl_context(__base_url: str):
    """Create an insecure ssl context to work with outdated hardware"""
    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT")
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _async_get_poll_interval(options: Mapping[str, any]):
    """Get the poll interval"""
    interval = options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    return timedelta(seconds=interval)


def _async_get_hispeed_poll_interval(options: Mapping[str, any]):
    """Get the high speed poll interval"""
    interval = options.get(OPT_HISPEED_INTERVAL, DEFAULT_HISPEED_INTERVAL)
    return timedelta(seconds=interval)


class ReolinkRestApi:
    """Reolink REST API"""

    _client: Client
    _coordinator: DataUpdateCoordinator[tuple[CommandResponse]]
    _hispeed_coordinator: DataUpdateCoordinator[tuple[CommandResponse]]
    _device: DeviceInfo
    _capabilities: Capabilities
    _device_info: ReolinkDeviceInfo
    _channel_statuses: ChannelStatuses
    _ports: NetworkPorts
    _dst: DaylightSavingsTimeInfo
    _time: TimeInfo
    _time_diff: timedelta

    def __init__(self, entry: ConfigEntry):
        self._entry_id = entry.entry_id
        self._entry_cleanup = entry.add_update_listener(self._update_config)
        self._config = entry.data
        self._options = entry.options
        self._unique_id = entry.unique_id
        self._connection_id = 0
        self._authentication_id = 0
        self._can_batch_capabilities: bool = entry.options.get(
            OPT_BATCH_CAPABILITIES, True
        )
        self._timestamp = time()

    async def _update_config(self, _hass: HomeAssistant, entry: ConfigEntry):
        self._config = entry.data
        self._options = entry.options
        self._unique_id = entry.unique_id
        if self._coordinator is not None:
            self._coordinator.update_interval = _async_get_poll_interval(self._options)
        if self._hispeed_coordinator is not None:
            self._hispeed_coordinator.update_interval = (
                _async_get_hispeed_poll_interval(self._options)
            )

    def _create_client(self):
        ssl_mode = SSLMode(self._options.get(OPT_SSL, SSLMode.NORMAL))
        if ssl_mode == SSLMode.WEAK:
            ssl_mode = _weak_ssl_context
        elif ssl_mode == SSLMode.INSECURE:
            ssl_mode = _insecure_ssl_context
        else:
            ssl_mode = None

        return Client(ssl=ssl_mode)

    @property
    def client(self):
        """Client"""

        if self._client is None:
            self._client = self._create_client()
        return self._client

    async def _ensure_data_init_queue(self, hass: HomeAssistant):
        client = self.client
        discovery: DiscoveryDict = self._options.get(OPT_DISCOVERY, None)
        if not client.is_connected or self._connection_id != client.connection_id:
            config = self._config
            host: str = config.get(
                CONF_HOST,
                discovery.get("ip", None) if discovery is not None else None,
            )
            if config.get(CONF_USE_HTTPS, False):
                encryption = Encryption.HTTPS
            else:
                encryption = Encryption.NONE

            if not host:
                raise ConfigEntryNotReady(
                    "No host configured, and none discovered (was device lost?)"
                )

            await client.connect(
                host,
                config.get(CONF_PORT, None),
                config.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
                encryption=encryption,
            )
            if self._connection_id != client.connection_id:
                self._connection_id = client.connection_id
                self._authentication_id = 0

        if (
            not client.is_authenticated
            or self._authentication_id != client.authentication_id
        ):
            config = self._config

            try:
                if not await client.login(
                    config.get(CONF_USERNAME, DEFAULT_USERNAME),
                    config.get(CONF_PASSWORD, DEFAULT_PASSWORD),
                ):
                    self._authentication_id = 0
                    await client.disconnect()
                    raise ConfigEntryAuthFailed()
            except aiohttp.ClientResponseError as http_error:
                if (
                    http_error.status in (301, 302, 308)
                    and "location" in http_error.headers
                ):
                    location = http_error.headers["location"]
                    # TODO : verify redirect stays on device
                    if client.secured and location.startswith("http://"):
                        async_create_issue(
                            hass,
                            DOMAIN,
                            "from_ssl_redirect",
                            severity=IssueSeverity.ERROR,
                            is_fixable=True,
                            data={"entry_id": self._entry_id},
                            translation_key="from_ssl_redirect",
                            translation_placeholders={
                                "name": self._device["name"]
                                if self._device is not None
                                else client.hostname,
                            },
                        )
                    elif not client.secured and location.startswith("https://"):
                        async_create_issue(
                            hass,
                            DOMAIN,
                            "to_ssl_redirect",
                            severity=IssueSeverity.ERROR,
                            is_fixable=True,
                            data={"entry_id": self._entry_id},
                            translation_key="from_ssl_redirect",
                            translation_placeholders={
                                "name": self._device["name"]
                                if self._device is not None
                                else client.hostname,
                            },
                        )
                elif http_error.status == 500:
                    if client.secured:
                        # this error occurs when HTTPS is disabled on the camera but we try to connect to it.
                        async_create_issue(
                            hass,
                            DOMAIN,
                            "from_ssl_redirect",
                            severity=IssueSeverity.ERROR,
                            is_fixable=True,
                            data={"entry_id": self._entry_id},
                            translation_key="from_ssl_redirect",
                            translation_placeholders={
                                "name": self._device["name"]
                                if self._device is not None
                                else client.hostname,
                            },
                        )
                    else:
                        async_create_issue(
                            hass,
                            DOMAIN,
                            "http_error",
                            severity=IssueSeverity.CRITICAL,
                            is_fixable=True,
                            data={"entry_id": self._entry_id},
                            translation_key="http_error",
                            translation_placeholders={
                                "name": self._device["name"]
                                if self._device is not None
                                else client.hostname,
                            },
                        )
                raise http_error
            except ssl.SSLError as ssl_error:
                if ssl_error.errno == 1:
                    async_create_issue(
                        hass,
                        DOMAIN,
                        "insecure_ssl",
                        severity=IssueSeverity.ERROR,
                        is_fixable=True,
                        data={"entry_id": self._entry_id},
                        translation_key="insecure_ssl",
                        translation_placeholders={
                            "name": self._device["name"]
                            if self._device is not None
                            else client.hostname,
                        },
                    )
                raise ssl_error
            except ReolinkResponseError as reoresp:
                if reoresp.code in AUTH_ERRORCODES:
                    await client.disconnect()
                    raise ConfigEntryAuthFailed() from reoresp
                raise reoresp
            self._authentication_id = client.authentication_id

        commands = client.commands

        queue: list[CommandRequest] = []
        caps: UpdatableCapabilities = self._capabilities
        if caps is None or not self._can_batch_capabilities:
            try:
                new_caps = await client.get_capabilities(
                    self._config.get(CONF_USERNAME, None)
                )
            except ReolinkResponseError as reoresp:
                if reoresp.code in AUTH_ERRORCODES:
                    self._authentication_id = 0
                    await client.disconnect()
                    queue = await self._ensure_data_init_queue(hass)
                    return queue
                if reoresp.code == ErrorCodes.PROTOCOL_ERROR:
                    # possible weird encryption bug or other invalid response so we will force a reconnect
                    self._connection_id = 0
                    self._authentication_id = 0
                raise reoresp

            if caps is None:
                caps = new_caps
                self._capabilities = caps
            else:
                caps.update(new_caps)
        else:
            queue.append(
                commands.create_get_capabilities_request(
                    self._config.get(CONF_USERNAME, None)
                )
            )

        if self._dst is None or self._time is None or time() - self._timestamp > 3600:
            queue.append(commands.create_get_time_request())

        if caps.device.info:
            if self._device_info is None:
                dev_info = await client.get_device_info()
                self._device_info = dev_info
            else:
                queue.append(commands.create_get_device_info_request())

            if dev_info.channels > 1:
                queue.append(commands.create_get_channel_status_request())

        if self._ports is None:
            queue.append(commands.create_get_ports_request())

        if self._device is None:
            if (discovery is None or "uuid" not in discovery) and caps.p2p:
                queue.append(commands.create_get_p2p_request())
            if (discovery is None or "mac" not in discovery) and caps.local_link:
                queue.append(commands.create_get_local_link_request())

        return queue

    async def _execute_queue(self, queue: list[CommandRequest]):
        responses: list[CommandResponse] = []
        if len(queue) < 1:
            return responses
        commands = self.client.commands
        async for response in self.client.batch(queue):
            if commands.is_error(response):
                response.throw()
            elif commands.is_get_capabilities_response(response):
                caps: UpdatableCapabilities = self._capabilities
                caps.update(response.capabilities)
            elif commands.is_get_device_info_response(response):
                dev_info: UpdatableDeviceInfo = self._device_info
                dev_info.update(response.info)
            elif commands.is_get_channel_status_response(response):
                channel_stats: UpdatableChannelStatuses = self._channel_statuses
                if channel_stats is None:
                    channel_stats = response.channels
                    self._channel_statuses = channel_stats
                else:
                    channel_stats.update(response.channels)
            elif commands.is_get_p2p_response(response):
                device = self._device
                if device is None:
                    device = DeviceInfo()
                ids = device.setdefault("identifiers", set())
                ids.add(("uuid", response.info.uid))
            elif commands.is_get_local_link_response(response):
                device = self._device
                if device is None:
                    device = DeviceInfo()
                cons = device.setdefault("connections", set())
                cons.add((CONNECTION_NETWORK_MAC, response.local_link.mac))
            elif commands.is_get_ports_response(response):
                self._ports = response.ports
            elif commands.is_get_time_response(response):
                self._dst = response.dst
                self._time = response.time
                self._time_diff = dt.utcnow() - dt.as_utc(response.to_datetime())
            responses.append(response)

        return responses

    def _get_requests(self, coordinator: DataUpdateCoordinator):
        if coordinator is None:
            return
        commands = self.client.commands
        for context in self._coordinator.async_contexts():
            try:
                itr = iter(context)
            except TypeError:
                continue
            else:
                for request in filter(commands.is_request, itr):
                    yield request

    async def _update_method(self):
        queue = await self._ensure_data_init_queue(self._coordinator.hass)
        queue.extend(self._get_requests(self._coordinator))
        return await self._execute_queue(queue)

    async def _hispeed_update_method(self):
        queue = []
        queue.extend(self._get_requests(self._hispeed_coordinator))
        return await self._execute_queue(queue)

    @property
    def coordinator(self):
        """Data Update Coordinator"""

        return self._coordinator

    @property
    def hispeed_coordinator(self):
        """Hi-Speed Data Update Coordinator"""
        return self._hispeed_coordinator

    async def async_initialize(self, hass: HomeAssistant, logger: logging.Logger):
        """Initialize API"""

        discovery: DiscoveryDict = self._options.get(OPT_DISCOVERY, None)
        if discovery is not None and (
            "name" in discovery or "uuid" in discovery or "mac" in discovery
        ):
            name: str = discovery.get(
                "name", discovery.get("uuid", discovery.get("mac", None))
            )
        else:
            name: str = self._config[CONF_HOST]

        first_run = False
        if self._coordinator is None:
            first_run = True
            self._coordinator = DataUpdateCoordinator(
                hass,
                logger,
                name=f"{DOMAIN}-{name}",
                update_method=self._update_method,
                update_interval=_async_get_poll_interval(self._options),
            )

        if self._hispeed_coordinator is None:
            self._hispeed_coordinator = DataUpdateCoordinator(
                hass,
                logger,
                name=f"{DOMAIN}-{name}-hispeed",
                update_method=self._hipseed_update_method,
                update_interval=_async_get_hispeed_poll_interval(self._options),
            )

        if first_run:
            await self._coordinator.async_config_entry_first_refresh()
