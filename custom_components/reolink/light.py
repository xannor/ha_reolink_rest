"""Light Platform"""

from __future__ import annotations

import logging


from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.light import (
    LightEntity,
)

from reolinkapi.const import LightTypes
from reolinkapi.helpers.ability import NO_ABILITY, NO_CHANNEL_ABILITIES

from .typings import component


from .base import ReolinkEntity

from .const import CONF_CHANNELS, DOMAIN, LIGHT_TYPE, CONF_PREFIX_CHANNEL


_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Setup light platform"""

    domain_data: component.DomainData | dict[str, component.EntryData] = hass.data[
        DOMAIN
    ]
    entry_data: component.EntryData = domain_data[config_entry.entry_id]
    entity_data = entry_data["coordinator"].data

    if (
        entity_data.abilities.get("onvif", NO_ABILITY)["ver"] == 0
        or entity_data.ports["onvifPort"] == 0
    ):
        return True

    entities = []

    def _create_entities(channel: int):
        channel_abilities = entity_data.abilities.get(
            "abilityChn", [NO_CHANNEL_ABILITIES]
        )[channel]
        entities.append(
            ReolinkLightEntity(entry_data["coordinator"], channel, LightTypes.IR)
        )
        if channel_abilities.get("floodLight", NO_ABILITY)["ver"]:
            entities.append(
                ReolinkLightEntity(entry_data["coordinator"], channel, LightTypes.WHITE)
            )
        if channel_abilities.get("powerLed", NO_ABILITY)["ver"]:
            entities.append(
                ReolinkLightEntity(entry_data["coordinator"], channel, LightTypes.POWER)
            )

    if entity_data.channels is not None and CONF_CHANNELS in config_entry.data:
        for _c in config_entry.data.get(CONF_CHANNELS, []):
            if (
                not next((ch for ch in entity_data.channels if ch["channel"] == _c))
                is None
            ):
                _create_entities(_c)
    else:
        _create_entities(0)

    if len(entities) > 0:
        async_add_entities(entities)

    return True


# async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
#    """unload platform"""
#
#    domain_data: component.DomainData | dict[str, component.EntryData] = hass.data[
#        DOMAIN
#    ]


class ReolinkLightEntity(ReolinkEntity, LightEntity):
    """Reolink Light Entity"""

    def __init__(
        self,
        entity_coordinator: any,
        channel_id: int,
        light_type: LightTypes,
    ) -> None:
        super().__init__(entity_coordinator, channel_id, LIGHT_TYPE[light_type])
        LightEntity.__init__(
            self
        )  # explicitly call LightEntity init since UpdateCoordinatorEntity does not super()
        self._light_type = light_type
        self._prefix_channel: bool = self.coordinator.config_entry.data.get(
            CONF_PREFIX_CHANNEL
        )
        self._attr_unique_id = (
            f"{self.coordinator.data.uid}.{self._channel_id}.{light_type.name}"
        )
        self._additional_updates()

    def _additional_updates(self):
        if self._prefix_channel and self._channel_status is not None:
            self._attr_name = f'{self.coordinator.data.device_info["name"]} {self._channel_status["name"]} {self.entity_description.name}'
        else:
            self._attr_name = f'{self.coordinator.data.device_info["name"]} {self.entity_description.name}'

    @callback
    def _handle_coordinator_update(self):
        self._additional_updates()

        super()._handle_coordinator_update()
