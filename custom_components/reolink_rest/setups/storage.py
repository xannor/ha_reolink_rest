"""Storage sensors"""

import dataclasses
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, cast

from homeassistant.core import HomeAssistant
from homeassistant.const import DATA_MEGABYTES
from homeassistant.components.sensor import SensorStateClass, SensorEntity
from homeassistant.helpers.entity import EntityCategory

from async_reolink.api.system import typing as system_typing
from async_reolink.rest.system import command as system_command

from ..entity import ReolinkEntity

from ..const import DATA_API, DOMAIN
from ..typing import DomainDataType, RequestHandler, ResponseCoordinatorType

from ..sensor_typing import SensorEntityConfig, SensorEntityDescription

_ICONS: Final = MappingProxyType(
    {
        system_typing.StorageTypes.HDD: "mdi:harddisk",
        system_typing.StorageTypes.SDC: "mdi:micro-sd",
    }
)


@dataclasses.dataclass
class ReolinkStorageSensorEntityDescription(SensorEntityDescription):
    """Reolink Storage Sensor Entity Description"""

    storage_id: int = 0
    native_unit_of_measurement: str | None = DATA_MEGABYTES
    entity_category: EntityCategory | None = EntityCategory.DIAGNOSTIC
    state_class: SensorStateClass | str | None = SensorStateClass.MEASUREMENT

    def from_storage(self, info: system_typing.StorageInfo):
        """Create a new instance from merging storage data"""
        return dataclasses.replace(
            self,
            storage_id=info.id,
            icon=_ICONS[info.type],
            key=f"{self.key}_{info.id}",
            name=f"{info.type.name} {self.name}",
        )


async def _capacity_init(self: SensorEntity):
    # pylint: disable=protected-access
    if not isinstance(self, ReolinkEntity):
        raise ValueError()

    description: ReolinkStorageSensorEntityDescription = self.entity_description

    def data_handler(response: system_command.GetHddInfoResponse):
        data = response.info[description.storage_id]
        if not data.mounted:
            self._attr_available = False
        else:
            self._attr_available = True
            self._attr_native_value = data.capacity

    self.coordinator_context = (RequestHandler(system_command.GetHddInfoRequest(), data_handler),)


_CAPACITY: Final = SensorEntityConfig.create(
    ReolinkStorageSensorEntityDescription(
        "storage_capacity",
        name="Capacity",
        state_class=SensorStateClass.TOTAL,
    ),
    lambda _self, device, _: device.sd_card,
    init_handler=_capacity_init,
)


async def _remaining_init(self: SensorEntity):
    # pylint: disable=protected-access
    if not isinstance(self, ReolinkEntity):
        raise ValueError()

    description = self.entity_description
    if TYPE_CHECKING:
        description = cast(ReolinkStorageSensorEntityDescription, description)

    def data_handler(response: system_command.GetHddInfoResponse):
        data = response.info[description.storage_id]
        if not data.mounted:
            self._attr_available = False
        else:
            self._attr_available = True
            self._attr_native_value = data.free_space

    self.coordinator_context = (RequestHandler(system_command.GetHddInfoRequest(), data_handler),)


_SENSORS: Final = (
    _CAPACITY,
    SensorEntityConfig.create(
        ReolinkStorageSensorEntityDescription("storage_remaining", name="Remaining"),
        _CAPACITY.device_supported,
        init_handler=_remaining_init,
    ),
)


async def async_get_sensors(hass: HomeAssistant, entry_id: str):
    """Load dynamic entity descriptions for device"""

    domain_data: DomainDataType = hass.data[DOMAIN]
    entry_data = domain_data[entry_id]
    api = entry_data[DATA_API]

    if not api.data.device_info.disks:
        return

    info = await api.client.get_storage_info()

    for sensor in _SENSORS:
        for storage_id in info:
            yield dataclasses.replace(
                sensor, description=sensor.description.from_storage(info[storage_id])
            )
