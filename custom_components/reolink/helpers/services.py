"""Services Helpers"""

from typing import cast
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.components.hassio.const import (
    SupervisorEntityModel,
    DOMAIN as HASSIO_DOMAIN,
)
from homeassistant.core import HomeAssistant, Event
from homeassistant.helpers.singleton import singleton
from homeassistant.helpers import device_registry as dr
from homeassistant.loader import bind_hass

from ..const import DOMAIN
from ..typings.component import HassDomainData


@singleton(f"{DOMAIN}-service-tracker")
@bind_hass
def ensure_tracker(hass: HomeAssistant):
    """Setup services"""

    domain_data = cast(HassDomainData, hass.data)[DOMAIN]
