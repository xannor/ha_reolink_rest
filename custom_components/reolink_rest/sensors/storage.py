"""Storage sensors"""

import dataclasses
from types import MappingProxyType
from typing import Final, Mapping, Protocol

from async_reolink.api.system import typing as system_typing

from async_reolink.rest.connection.model import ResponseTypes
from async_reolink.rest.system import command as system_command

from homeassistant.const import DATA_MEGABYTES
from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers.entity import EntityCategory

from .._utilities.curry import curry

from ..api import EntryData, RequestQueue

from ..entity import ReolinkEntity, ChannelSupportedMixin

from .. import sensor

from .model import ReolinkSensorEntityDescription

_ICONS: Final = MappingProxyType(
    {
        system_typing.StorageTypes.HDD: "mdi:harddisk",
        system_typing.StorageTypes.SDC: "mdi:micro-sd",
    }
)


@dataclasses.dataclass
class ReolinkStorageSensorEntityDescription(
    ChannelSupportedMixin, ReolinkSensorEntityDescription
):
    """Reolink Storage Sensor Entity Description"""

    storage_id: int = 0
    native_unit_of_measurement: str | None = DATA_MEGABYTES
    entity_category: EntityCategory | None = EntityCategory.DIAGNOSTIC
    state_class: SensorStateClass | str | None = SensorStateClass.MEASUREMENT

    def set_storage_id(self, storage_id: int, info: system_typing.StorageInfo):
        """Set the storage id and key"""

        return dataclasses.replace(
            self,
            storage_id=storage_id,
            icon=_ICONS[info.type],
            key=f"{self.key}_{storage_id}",
            name=f"{info.type.name} {self.name}",
            data_handler_fn=curry(self.data_handler_fn, storage_id=storage_id),
        )


class StorageData(Protocol):
    """Storage info"""

    storage: Mapping[int, system_typing.StorageInfo]


def _handle_capacity(entity: ReolinkEntity, /, storage_id: int):
    if not isinstance(entity, sensor.ReolinkSensorEntity):
        return

    # pylint: disable=protected-access

    storage_data: StorageData = entity._client_data
    data = storage_data.storage[storage_id]
    if not data.mounted:
        entity._attr_available = False
    else:
        entity._attr_available = True
        entity._attr_native_value = data.capacity


def _handle_free_space(entity: ReolinkEntity, /, storage_id: int):
    if not isinstance(entity, sensor.ReolinkSensorEntity):
        return

    # pylint: disable=protected-access

    storage_data: StorageData = entity._client_data
    data = storage_data.storage[storage_id]
    if not data.mounted:
        entity._attr_available = False
    else:
        entity._attr_available = True
        entity._attr_native_value = data.free_space


SENSORS: Final = (
    ReolinkStorageSensorEntityDescription(
        "storage_capacity",
        name="Capacity",
        state_class=SensorStateClass.TOTAL,
        data_handler_fn=_handle_capacity,
        channel_supported_fn=ChannelSupportedMixin.simple_test2(
            lambda _, data: data.channel_id == 0
        ),
    ),
    ReolinkStorageSensorEntityDescription(
        "storage_remaining",
        name="Remaining",
        data_handler_fn=_handle_free_space,
        channel_supported_fn=ChannelSupportedMixin.simple_test2(
            lambda _, data: data.channel_id == 0
        ),
    ),
)

_EMPTY: Final[tuple[ReolinkStorageSensorEntityDescription, ...]] = tuple()


async def get_sensors(entry_data: EntryData):
    """get sensors"""
    client = entry_data["client"]
    client_data = entry_data["client_data"]

    if not client_data.capabilities.sd_card:
        return _EMPTY

    storage_data: StorageData = client_data
    storage_data.storage = await client.get_storage_info()

    queue: RequestQueue = entry_data["coordinator_global_queue"]

    def handle_response(response):
        if isinstance(response, system_command.GetHddInfoResponse):
            queue.append(system_command.GetHddInfoRequest(), handle_response)
            storage_data.storage = response.info

    queue.append(system_command.GetHddInfoRequest(), handle_response)

    return tuple(
        (
            sensor.set_storage_id(storage_id, storage_data.storage[storage_id])
            for storage_id in storage_data.storage
            for sensor in SENSORS
        )
    )
