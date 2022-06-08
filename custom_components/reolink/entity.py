""""Base Components"""

from __future__ import annotations

from typing import cast

from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.helpers.entity import EntityDescription

from .typings.motion import ChannelMotionState

from .const import DOMAIN
from .typings import component


class ReolinkEntity(CoordinatorEntity[component.EntityData]):
    """Base class for Reolink Entities"""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[component.EntityData],
        channel_id: int,
        description: EntityDescription = None,
    ):
        self._channel_id = channel_id
        super().__init__(coordinator)
        self.entity_description = description
        self._enabled = False
        self._attr_device_info = self.coordinator.data.device_info
        self.coordinator = coordinator

    @property
    def _client(self):
        return cast(component.HassDomainData, self.hass.data)[DOMAIN][
            self.coordinator.config_entry.entry_id
        ].client

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


class ReolinkMotionEntity(ReolinkEntity):
    "Base class for Reolink Motion Entities"

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[component.EntityData],
        channel_id: int,
        motion_coordinator: DataUpdateCoordinator[dict[int, ChannelMotionState]],
        description: EntityDescription = None,
    ):
        super().__init__(coordinator, channel_id, description)
        self.motion_coordinator = motion_coordinator

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return super().available and self.motion_coordinator.last_update_success

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.motion_coordinator.async_add_listener(self._handle_coordinator_update)
        )
