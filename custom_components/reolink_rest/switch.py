"""Reolink Switch Platform"""

from dataclasses import dataclass
import logging
from typing import Final
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory

from homeassistant.components.switch import (
    SwitchEntity,
    SwitchEntityDescription,
    SwitchDeviceClass,
)

from async_reolink.api.system import capabilities

from .entity import (
    ReolinkEntity,
    ReolinkEntityDataUpdateCoordinator,
)

from .typing import ReolinkDomainData

from .const import DATA_COORDINATOR, DOMAIN


_LOGGER = logging.getLogger(__name__)


@dataclass
class ReolinkSwitchEntityDescription(SwitchEntityDescription):
    """Describe Reolink Switch Entity"""

    has_entity_name: bool = True


@dataclass
class ReolinkLEDSwitchEntityDescription(ReolinkSwitchEntityDescription):
    """Describe Reolink LED Switch Entity"""

    device_class: SwitchDeviceClass | str | None = SwitchDeviceClass.SWITCH
    led_type = None


@dataclass
class ReolinkPTZSwitchEntityDescription(ReolinkSwitchEntityDescription):
    """Describe Reolink PTZ Switch Entity"""

    ptz_type: capabilities.PTZType | None = None


PTZ_SWITCHES: Final = [
    ReolinkPTZSwitchEntityDescription(
        key="ptz_auto_focus",
        name="Auto Focus",
        icon="mdi:camera-iris",
        ptz_type=capabilities.PTZType.AF,
        entity_category=EntityCategory.CONFIG,
    )
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Setup switch platform"""

    _LOGGER.debug("Setting up switches")
    domain_data: ReolinkDomainData = hass.data[DOMAIN]
    entry_data = domain_data[config_entry.entry_id]
    coordinator = entry_data[DATA_COORDINATOR]

    entities = []
    data = coordinator.data
    abilities = data.abilities

    for channel in data.channels.keys():
        ability = abilities.channels[channel]

        for description in PTZ_SWITCHES:
            if description.ptz_type != ability.ptz.type:
                continue
            entities.append(ReolinkPTZSwitch(coordinator, description, channel))

    if entities:
        async_add_entities(entities)


class ReolinkLEDSwitch(ReolinkEntity, SwitchEntity):
    """Reolink LED Switch Entity"""

    entity_description: ReolinkLEDSwitchEntityDescription

    def __init__(
        self,
        coordinator: ReolinkEntityDataUpdateCoordinator,
        description: ReolinkLEDSwitchEntityDescription,
        channel_id: int,
        context: any = None,
    ) -> None:
        super().__init__(coordinator, channel_id, context)
        self.entity_description = description

    def _handle_coordinator_update(self) -> None:
        return super()._handle_coordinator_update()

    async def async_update(self) -> None:
        return await super().async_update()


class ReolinkPTZSwitch(ReolinkEntity, SwitchEntity):
    """Reolink PTZ Switch Entity"""

    entity_description: ReolinkPTZSwitchEntityDescription

    def __init__(
        self,
        coordinator: ReolinkEntityDataUpdateCoordinator,
        description: ReolinkPTZSwitchEntityDescription,
        channel_id: int,
        context: any = None,
    ) -> None:
        super().__init__(coordinator, channel_id, context)
        self.entity_description = description
        self._attr_available = False

    def _get_state(self):
        if self.entity_description.ptz_type == capabilities.PTZType.AF:
            return self.coordinator.data.ptz[self._channel_id].autofocus
        return None

    def _update_state(self, value: bool):
        if value is None:
            self._attr_available = False
        else:
            self._attr_available = True
            self._attr_is_on = value

    def _handle_coordinator_update(self) -> None:
        self._update_state(self._get_state())
        return super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        self._update_state(self._get_state())
        return await super().async_added_to_hass()

    async def async_update(self) -> None:
        return await super().async_update()

    async def async_turn_off(self, **kwargs: any) -> None:
        client = self.coordinator.data.client
        await client.set_ptz_autofocus(True, self._channel_id)
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: any) -> None:
        client = self.coordinator.data.client
        await client.set_ptz_autofocus(False, self._channel_id)
        await self.coordinator.async_request_refresh()
