"""Constants"""

from enum import IntEnum
from typing import Final
from reolinkapi.rest.const import StreamTypes as CameraTreamTypes
from homeassistant.components.camera import CameraEntityDescription


class OutputStreamTypes(IntEnum):
    """Out stream Types"""

    MJPEG = 0
    RTMP = 1
    RTSP = 2


DOMAIN = "reolink"

DEFAULT_PORT = 0
DEFAULT_USE_HTTPS = False
DEFAULT_PREFIX_CHANNEL = True
DEFAULT_SCAN_INTERVAL = 60
DEFAULT_USE_RTSP = False
DEFAULT_STREAM_TYPE = {
    CameraTreamTypes.MAIN: OutputStreamTypes.RTMP,
    CameraTreamTypes.SUB: OutputStreamTypes.RTMP,
    CameraTreamTypes.EXT: OutputStreamTypes.RTMP,
}

CONF_USE_HTTPS = "use_https"
CONF_CHANNELS = "channels"
CONF_PREFIX_CHANNEL = "prefix_channel"
CONF_USE_RTSP = "use_rtsp"
CONF_STREAM_TYPE = "stream_type"

DATA_ENTRY = "data"

CAMERA_TYPES: Final[dict[CameraTreamTypes, CameraEntityDescription]] = {
    CameraTreamTypes.MAIN: CameraEntityDescription(
        key="StreamType.main",
        name="Main",
    ),
    CameraTreamTypes.SUB: CameraEntityDescription(
        key="StreamType.sub",
        name="Sub",
        entity_registry_enabled_default=False,
    ),
    CameraTreamTypes.EXT: CameraEntityDescription(
        key="StreamType.ext", name="Ext", entity_registry_enabled_default=False
    ),
}
