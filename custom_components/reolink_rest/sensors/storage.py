"""Storage sensors"""

from dataclasses import dataclass
from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)

from ..entity import ReolinkEntity, ReolinkEntityDataUpdateCoordinator


@dataclass
class ReolinkStorageSensorEntityDescription(SensorEntityDescription):
    """Reolink Device Storage Sensor Entity Description"""

    state_class = SensorStateClass.TOTAL
    native_unit_of_measurement = "Gb"


class ReolinkStorageSensor(ReolinkEntity, SensorEntity):
    """Reolink Device Storage"""

    entity_description: ReolinkStorageSensorEntityDescription

    def __init__(
        self,
        coordinator: ReolinkEntityDataUpdateCoordinator,
        index: int,
        entity_description: ReolinkStorageSensorEntityDescription,
    ) -> None:
        SensorEntity.__init__(self)
        ReolinkEntity.__init__(self, coordinator)
        self.entity_description = entity_description
        self._index = index

    def _handle_coordinator_update(self) -> None:
        commands = self._api.client.commands
        self._queue.add(commands.create_get_hdd_info_request())
        if response := next(
            filter(commands.is_get_hdd_info_response, self.coordinator.data), None
        ):
            if info := response.info.get(self._index, None):
                pass

        return super()._handle_coordinator_update()
