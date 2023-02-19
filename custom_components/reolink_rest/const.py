"""Constants"""
from __future__ import annotations
from enum import auto

from typing import Final

from homeassistant.backports.enum import StrEnum

BRAND: Final = "Reolink"
DOMAIN: Final = "reolink_rest"
DISCOVERY_EVENT: Final = "reolink_discovery"

DEFAULT_PORT: Final = None
DEFAULT_USE_HTTPS: Final = False
DEFAULT_PREFIX_CHANNEL: Final = True
DEFAULT_SCAN_INTERVAL: Final = 10
DEFAULT_HISPEED_INTERVAL: Final = 2

CONF_USE_HTTPS: Final = "use_https"
OPT_CHANNELS: Final = "channels"
OPT_PREFIX_CHANNEL: Final = "prefix_channel"
OPT_HISPEED_INTERVAL: Final = "hispeed_interval"
OPT_BATCH_CAPABILITIES: Final = "batch_capabilities"
OPT_SSL: Final = "ssl_setting"


class SSLMode(StrEnum):
    """SSL Mode"""

    NORMAL = "normal"
    WEAK = "weak"
    INSECURE = "insecure"


DATA_API: Final = "api"
DATA_COORDINATOR: Final = "coordinator"
DATA_HISPEED_COORDINDATOR: Final = "hispeed_coordinator"
DATA_ONVIF: Final = "onvif"
DATA_WEBHOOK: Final = "webhook"
