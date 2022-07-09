"""Constants"""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "reolink_rest"
DISCOVERY_EVENT: Final = "reolink_discovery"

DEFAULT_PORT: Final = None
DEFAULT_USE_HTTPS: Final = False
DEFAULT_PREFIX_CHANNEL: Final = True
DEFAULT_SCAN_INTERVAL: Final = 60
DEFAULT_MOTION_INTERVAL: Final = 2

CONF_USE_HTTPS: Final = "use_https"
OPT_DISCOVERY: Final = "discovery"
OPT_CHANNELS: Final = "channels"
OPT_PREFIX_CHANNEL: Final = "prefix_channel"
OPT_MOTION_INTERVAL: Final = "motion_interval"

DATA_COORDINATOR: Final = "coordinator"

# keep? ---\/


# LIGHT_TYPE: Final[dict[LightTypes, LightEntityDescription]] = {
#    LightTypes.IR: LightEntityDescription(
#        key="LightTyps.IR", name="IR", entity_category=EntityCategory.CONFIG
#    ),
#    LightTypes.POWER: LightEntityDescription(
#        key="LightTypes.Power", name="Power", entity_category=EntityCategory.CONFIG
#    ),
#    LightTypes.WHITE: LightEntityDescription(
#        key="LightTypes.White", name="Floodlight", entity_category=EntityCategory.CONFIG
#    ),
# }
