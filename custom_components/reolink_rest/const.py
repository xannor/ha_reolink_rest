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
OPT_BATCH_ABILITY: Final = "batch_abilitiy"

DATA_COORDINATOR: Final = "coordinator"
DATA_MOTION_COORDINATORS: Final = "motion_coordinators"
DATA_ONVIF: Final = "onvif"

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
