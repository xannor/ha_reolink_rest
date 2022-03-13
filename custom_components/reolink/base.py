""""Base Components"""

from __future__ import annotations

from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.helpers.entity import EntityDescription

from .models import ReolinkEntityData

from .const import DOMAIN
from .typings import component


class ReolinkEntity(CoordinatorEntity[ReolinkEntityData]):
    """Base class for Reolink Entities"""

    def __init__(
        self,
        update_coordinator: DataUpdateCoordinator,
        channel_id: int,
        description: EntityDescription = None,
    ):
        self._channel_id = channel_id
        super().__init__(update_coordinator)
        self.entity_description = description
        self._enabled = False
        self._attr_device_info = self.coordinator.data.device_info

    @property
    def _client(self):
        domain_data: dict[str, component.EntryData] = self.coordinator.hass.data[DOMAIN]
        return domain_data[self.coordinator.config_entry.entry_id]["client"]

    @property
    def _channel_ability(self):
        return self.coordinator.data.abilities["abilityChn"][self._channel_id]

    @property
    def _channel_status(self):
        if self.coordinator.data.channels is None:
            return None
        return next(
            (
                channel
                for channel in self.coordinator.data.channels
                if channel["channel"] == self._channel_id
            ),
            None,
        )
