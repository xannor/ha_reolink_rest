"""Reolink Sensor Platform"""

import logging

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.sensor import (
    SensorEntity,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, CoordinatorEntity

from .const import DATA_API, DATA_COORDINATOR, DOMAIN, OPT_CHANNELS

from .typing import (
    DomainDataType,
    AsyncEntityInitializedCallback,
    EntityDataHandlerCallback,
    ChannelEntityConfig,
    ResponseCoordinatorType,
)

from .api import ReolinkDeviceApi

from .entity import ChannelDescriptionMixin, ReolinkEntity

from .sensor_typing import SensorEntityDescription

from ._utilities.typing import bind

_LOGGER = logging.getLogger(__name__)

from .setups import wifi, storage

# async def async_setup_platform(
#     hass: HomeAssistant,
#     config_entry: ConfigEntry,
#     async_add_entities: AddEntitiesCallback,
#     discovery_info: DiscoveryInfoType | None = None,
# ):
#     """Setup Sensor platform"""
#
#     platform = async_get_current_platform()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Setup Sensor Entities"""

    _LOGGER.debug("Setting up.")

    entities = []

    domain_data: DomainDataType = hass.data[DOMAIN]
    entry_data = domain_data[config_entry.entry_id]

    api = entry_data[DATA_API]
    device_data = api.data

    _capabilities = device_data.capabilities

    channels: list[int] = config_entry.options.get(OPT_CHANNELS, None)

    setups = wifi.SENSORS + tuple(
        [entry async for entry in storage.async_get_sensors(hass, config_entry.entry_id)]
    )

    for init in setups:
        first_channel = True
        for status in device_data.channel_statuses.values():
            if not status.online or (channels is not None and not status.channel_id in channels):
                continue
            channel_capabilities = _capabilities.channels[status.channel_id]
            info = device_data.channel_info[status.channel_id]

            description = init.description
            if (device_supported := init.device_supported) and not device_supported(
                description, _capabilities, device_data
            ):
                continue

            if isinstance(init, ChannelEntityConfig):
                channel_supported = init.channel_supported
            elif not first_channel:
                # if this is not a channel based sensor, but we have a multi-channel device
                # we need to ensure we dont create multiple entities
                continue
            else:
                channel_supported = None

            if first_channel:
                first_channel = False

            if channel_supported:
                # pylint: disable=not-callable
                if not channel_supported(description, channel_capabilities, info):
                    continue
                if isinstance(description, ChannelDescriptionMixin):
                    description = description.from_channel(info)

            entities.append(
                ReolinkSensorEntity(
                    api,
                    entry_data[DATA_COORDINATOR],
                    description,
                    init.data_handler,
                    init.init_handler,
                )
            )

    if entities:
        async_add_entities(entities)

    _LOGGER.debug("Finished setup")


# async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
#     """Unload Sensor Entities"""

#     return True


class ReolinkSensorEntity(ReolinkEntity, CoordinatorEntity[ResponseCoordinatorType], SensorEntity):
    """Reolink Sensor Entity"""

    entity_description: SensorEntityDescription

    def __init__(
        self,
        api: ReolinkDeviceApi,
        coordinator: DataUpdateCoordinator[ResponseCoordinatorType],
        description: SensorEntityDescription,
        data_handler: EntityDataHandlerCallback["ReolinkSensorEntity"] | None = None,
        init_handler: AsyncEntityInitializedCallback["ReolinkSensorEntity"] | None = None,
    ) -> None:
        self.entity_description = description
        super().__init__(api, coordinator.config_entry.unique_id, coordinator=coordinator)
        self._data_handler = bind(data_handler, self)
        self._init_handler = bind(init_handler, self)

    def _handle_coordinator_update(self) -> None:
        if self._data_handler:
            self._data_handler()
        return super()._handle_coordinator_update()

    async def async_added_to_hass(self):
        """update"""
        if self._init_handler:
            await self._init_handler()
        return await super().async_added_to_hass()
