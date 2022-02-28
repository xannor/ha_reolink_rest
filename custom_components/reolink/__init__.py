""" Reolink Intergration """

from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Literal, TypedDict
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_TIMEOUT,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
    CONF_SCAN_INTERVAL,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo

from reolinkapi.rest import Client, system as rs, network as rn
from reolinkapi.const import DEFAULT_TIMEOUT

from .const import (
    CONF_USE_HTTPS,
    DATA_ENTRY,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_HTTPS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CAMERA]


@dataclass
class ReolinkEntityData:
    """Entity Data"""

    client: Client = field(default_factory=Client)
    update_coordinator: DataUpdateCoordinator = field(default=None)
    ha_device_info: DeviceInfo = field(default=None)
    device_info: rs.DeviceInfo = field(default=None)
    local_link: rn.LinkInfo = field(default=None)
    abilities: rs.Abilities = field(default=None)
    channels: list[rn.ChannelStatus] = field(default=None)
    ports: rn.NetworkPorts = field(default=None)

    @property
    def connection_id(self):
        """Connection ID"""
        return self.client.connection_id


def get_poll_interval(config_entry: ConfigEntry):
    """Get the poll interval"""
    return timedelta(
        seconds=config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Setup Device"""

    if not config_entry.options:
        hass.config_entries.async_update_entry(
            config_entry, options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL}
        )

    entry_data = ReolinkEntityData()
    await entry_data.client.connect(
        config_entry.data.get(CONF_HOST),
        config_entry.data.get(CONF_PORT),
        config_entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
        use_https=config_entry.data.get(CONF_USE_HTTPS, DEFAULT_USE_HTTPS),
    )

    hass.data.setdefault(DOMAIN, {})

    device: dr.DeviceEntry = None

    async def _update_data():
        nonlocal device
        if not entry_data.client.authenticated:
            if not await entry_data.client.login(
                config_entry.data.get(CONF_USERNAME),
                config_entry.data.get(CONF_PASSWORD),
            ):
                return False  # TODO : mark device as needing update

        entry_data.abilities = await entry_data.client.get_ability()
        if entry_data.abilities is None:
            await entry_data.client.disconnect()
            return False

        entry_data.ports = await entry_data.client.get_ports()
        if entry_data.ports is None:
            await entry_data.client.disconnect()
            return False

        entry_data.device_info = (
            await entry_data.client.get_device_info()
            if entry_data.abilities.device_info.supported
            else None
        )
        entry_data.local_link = (
            await entry_data.client.get_local_link()
            if entry_data.abilities.local_link.supported
            else None
        )

        if entry_data.device_info is not None:
            entry_data.channels = (
                await entry_data.client.get_channel_status()
                if entry_data.device_info.channels > 0
                else None
            )
            connections = (
                {(dr.CONNECTION_NETWORK_MAC, entry_data.local_link.mac_address)}
                if entry_data.local_link is not None
                else None
            )

            device_registry = dr.async_get(hass)
            if device is None:
                device = device_registry.async_get_or_create(
                    config_entry_id=config_entry.entry_id,
                    name=entry_data.device_info.name,
                    identifiers={(DOMAIN, entry_data.device_info.serial)},
                    connections=connections,
                    sw_version=entry_data.device_info.versions.firmware,
                    hw_version=entry_data.device_info.versions.hardware,
                    model=entry_data.device_info.exact_type,
                    manufacturer="Reolink",
                    configuration_url=entry_data.client.base_url,
                )
                entry_data.ha_device_info = DeviceInfo(
                    configuration_url=device.configuration_url,
                    connections=device.connections,
                    default_manufacturer=device.manufacturer,
                    default_model=device.model,
                    default_name=device.name,
                    entry_type=device.entry_type,
                    hw_version=device.hw_version,
                    identifiers=device.identifiers,
                    suggested_area=device.area_id,
                    sw_version=device.sw_version,
                )
            else:
                device_registry.async_update_device(
                    device.id,
                    name=entry_data.device_info.name,
                    configuration_url=entry_data.client.base_url,
                )

        entry_data.update_coordinator.name = (
            f"Reolink-device-{entry_data.device_info.name}"
        )

    entry_data.update_coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="Reolink-device",
        update_interval=get_poll_interval(config_entry),
        update_method=_update_data,
    )

    await entry_data.update_coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][config_entry.entry_id] = {DATA_ENTRY: entry_data}
    hass.config_entries.async_setup_platforms(config_entry, PLATFORMS)

    if not config_entry.update_listeners:
        config_entry.add_update_listener(async_update_options)

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Unload Device"""
    unload_ok = await hass.config_entries.async_unload_platforms(
        config_entry, PLATFORMS
    )
    if unload_ok:
        entry = hass.data[DOMAIN][config_entry.entry_id]
        entry_data: ReolinkEntityData = entry[DATA_ENTRY]
        await entry_data.client.disconnect()
        hass.data[DOMAIN].pop(config_entry.entry_id)

    return unload_ok


async def async_update_options(hass, config_entry):
    """Update options."""
    await hass.config_entries.async_reload(config_entry.entry_id)
