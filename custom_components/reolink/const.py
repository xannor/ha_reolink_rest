"""Constants"""

from typing import Final
from reolinkapi.rest.const import StreamTypes
from homeassistant.components.camera import CameraEntityDescription

DOMAIN = "reolink"

DEFAULT_PORT = 0
DEFAULT_USE_HTTPS = False
DEFAULT_PREFIX_CHANNEL = True
DEFAULT_SCAN_INTERVAL = 60

PREFIX_CHAR = "-"

CONF_USE_HTTPS = "use_https"
CONF_CHANNELS = "channels"
CONF_PREFIX_CHANNEL = "prefix_channel"

DATA_ENTRY = "data"

CAMERA_TYPES: Final[dict[StreamTypes, CameraEntityDescription]] = {
    StreamTypes.MAIN: CameraEntityDescription(
        key="StreamType.main",
        name="Main",
    ),
    StreamTypes.SUB: CameraEntityDescription(
        key="StreamType.sub",
        name="Sub",
        entity_registry_enabled_default=False,
    ),
    StreamTypes.EXT: CameraEntityDescription(
        key="StreamType.ext", name="Ext", entity_registry_enabled_default=False
    ),
}
