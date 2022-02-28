""""Base Components"""

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import EntityDescription

from . import ReolinkEntityData

from .const import DATA_ENTRY, DOMAIN


class ReolinkEntity(CoordinatorEntity):
    """Base class for Reolink Entities"""

    def __init__(
        self,
        hass: HomeAssistant,
        channel_id: int,
        config_entry: ConfigEntry,
        description: EntityDescription = None,
    ):
        self.hass = hass
        self._channel_id = channel_id
        self._data_key = config_entry.entry_id
        super().__init__(self._data.update_coordinator)
        self.entity_description = description
        self._enabled = False
        self._attr_device_info = self._data.ha_device_info

    @property
    def _data(self) -> ReolinkEntityData:
        return self.hass.data[DOMAIN][self._data_key][DATA_ENTRY]

    @property
    def _channel_ability(self):
        return self._data.abilities.channel[self._channel_id]

    @property
    def _channel_status(self):
        return self._data.channels[self._channel_id]

    async def async_added_to_hass(self):
        self._enabled = True
        return await super().async_added_to_hass()

    async def async_will_remove_from_hass(self) -> None:
        self._enabled = False
        return await super().async_will_remove_from_hass()
