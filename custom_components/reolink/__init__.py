""" Reolink Intergration """

from dataclasses import dataclass, field
from datetime import timedelta
import logging

from attr import asdict
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
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo

from reolinkapi.rest import Client
from reolinkapi.rest.typings import system as rs, network as rn, abilities as ra
from reolinkapi.const import DEFAULT_TIMEOUT
from reolinkapi.exceptions import ReolinkError

import async_timeout

from .utility import astypeddict

from .const import (
    CONF_CHANNELS,
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
    uid: str = field(default="")
    update_coordinator: DataUpdateCoordinator = field(default=None)
    ha_device_info: DeviceInfo = field(default=None)
    device_info: rs.DeviceInfo = field(default=None)
    abilities: ra.Abilities = field(default=None)
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

    try:
        with async_timeout.timeout(10):
            if not await entry_data.client.login(
                config_entry.data.get(CONF_USERNAME),
                config_entry.data.get(CONF_PASSWORD),
            ):
                raise ConfigEntryAuthFailed()
    except ReolinkError as _re:
        raise ConfigEntryNotReady(_re) from None

    device: dr.DeviceEntry = None

    async def _update_data():
        nonlocal device
        if not entry_data.client.authenticated:
            if not await entry_data.client.login(
                config_entry.data.get(CONF_USERNAME),
                config_entry.data.get(CONF_PASSWORD),
            ):
                raise ConfigEntryAuthFailed()

        commands = []
        abils = entry_data.abilities
        if entry_data.abilities is None:
            abils = entry_data.abilities = await entry_data.client.get_ability()
            if entry_data.abilities is None:
                await entry_data.client.disconnect()
                raise ConfigEntryNotReady()
        else:
            commands.append(Client.create_get_ability())

        commands.append(Client.create_get_network_ports())

        if entry_data.abilities["p2p"]["ver"]:
            commands.append(Client.create_get_p2p())

        if entry_data.abilities["localLink"]["ver"]:
            commands.append(Client.create_get_local_link())

        if entry_data.abilities["devInfo"]["ver"]:
            commands.append(Client.create_get_device_info())
            if (
                entry_data.device_info is not None
                and entry_data.device_info["channelNum"] > 1
            ):
                commands.append(Client.create_get_channel_status())

        responses = await entry_data.client.batch(commands)
        entry_data.abilities = next(Client.get_ability_responses(responses), abils)
        if entry_data.abilities is None:
            await entry_data.client.disconnect()
            raise ConfigEntryNotReady()
        entry_data.ports = next(Client.get_network_ports_responses(responses), None)
        if entry_data.ports is None:
            await entry_data.client.disconnect()
            raise ConfigEntryNotReady()
        p2p = next(Client.get_p2p_responses(responses), None)
        if p2p is not None:
            entry_data.uid = p2p["uid"]
        link = next(Client.get_local_link_responses(responses), None)
        entry_data.device_info = next(Client.get_device_info_responses(responses), None)
        channels = next(Client.get_channel_status_responses(responses), None)
        entry_data.channels = channels["status"] if channels is not None else None

        if entry_data.device_info is not None:
            if entry_data.device_info["channelNum"] > 1 and entry_data.channels is None:
                entry_data.channels = await entry_data.client.get_channel_status()
            if entry_data.uid is None:
                entry_data.uid = f'{entry_data.device_info["type"]}-{entry_data.device_info["serial"]}'
            connections = (
                {(dr.CONNECTION_NETWORK_MAC, link["mac"])} if link is not None else None
            )

            device_registry = dr.async_get(hass)
            if device is None:
                device = device_registry.async_get_or_create(
                    config_entry_id=config_entry.entry_id,
                    default_manufacturer="Reolink",
                    default_name=entry_data.device_info["name"],
                    identifiers={(DOMAIN, entry_data.uid)},
                    connections=connections,
                    sw_version=entry_data.device_info["firmVer"],
                    hw_version=entry_data.device_info["hardVer"],
                    default_model=entry_data.device_info["model"],
                    configuration_url=entry_data.client.base_url,
                )
                entry_data.ha_device_info = DeviceInfo(astypeddict(device, DeviceInfo))
            else:
                device = device_registry.async_update_device(
                    device.id,
                    name=entry_data.device_info["name"],
                    configuration_url=entry_data.client.base_url,
                )
                entry_data.ha_device_info.update(astypeddict(device, DeviceInfo))
        elif entry_data.uid is None:
            entry_data.uid = config_entry.entry_id

        entry_data.update_coordinator.name = f"Reolink-device-{entry_data.uid}"

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
        config_entry.async_on_unload(
            config_entry.add_update_listener(async_update_options)
        )

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


async def async_update_options(hass: HomeAssistant, config_entry: ConfigEntry):
    """Update options."""
    if CONF_CHANNELS in config_entry.options:
        data = config_entry.data.copy()
        options = config_entry.options.copy()
        data[CONF_CHANNELS] = options.pop(CONF_CHANNELS)
        if hass.config_entries.async_update_entry(
            config_entry, data=data, options=options
        ):
            return
    await hass.config_entries.async_reload(config_entry.entry_id)
