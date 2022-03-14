"""Constants"""

from enum import IntEnum
from typing import Final
from reolinkapi.rest.const import StreamTypes as CameraStreamTypes
from reolinkapi.const import DetectionTypes, LightTypes
from homeassistant.helpers.entity import EntityCategory
from homeassistant.components.camera import CameraEntityDescription
from homeassistant.components.binary_sensor import (
    BinarySensorEntityDescription,
    BinarySensorDeviceClass,
)
from homeassistant.components.light import LightEntityDescription


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
    CameraStreamTypes.MAIN: OutputStreamTypes.RTMP,
    CameraStreamTypes.SUB: OutputStreamTypes.RTMP,
    CameraStreamTypes.EXT: OutputStreamTypes.RTMP,
}

CONF_USE_HTTPS = "use_https"
CONF_CHANNELS = "channels"
CONF_PREFIX_CHANNEL = "prefix_channel"
CONF_USE_RTSP = "use_rtsp"

CAMERA_TYPES: Final[dict[CameraStreamTypes, CameraEntityDescription]] = {
    CameraStreamTypes.MAIN: CameraEntityDescription(
        key="StreamType.main",
        name="Main",
    ),
    CameraStreamTypes.SUB: CameraEntityDescription(
        key="StreamType.sub",
        name="Sub",
        entity_registry_enabled_default=False,
    ),
    CameraStreamTypes.EXT: CameraEntityDescription(
        key="StreamType.ext", name="Ext", entity_registry_enabled_default=False
    ),
}


MOTION_TYPE: Final[dict[DetectionTypes, BinarySensorEntityDescription]] = {
    DetectionTypes.NONE: BinarySensorEntityDescription(
        key="DetectionType.None",
        name="Motion",
        device_class=BinarySensorDeviceClass.MOTION,
    ),
    DetectionTypes.PEOPLE: BinarySensorEntityDescription(
        key="DetetctionTypes.Person",
        name="Person",
        device_class=BinarySensorDeviceClass.MOTION,
    ),
    DetectionTypes.VEHICLE: BinarySensorEntityDescription(
        key="DetetctionTypes.Vehicle",
        name="Vehicle",
        device_class=BinarySensorDeviceClass.MOTION,
    ),
    DetectionTypes.ANIMAL: BinarySensorEntityDescription(
        key="DetetctionTypes.Animal",
        name="Animal",
        device_class=BinarySensorDeviceClass.MOTION,
    ),
    DetectionTypes.PET: BinarySensorEntityDescription(
        key="DetetctionTypes.Pet",
        name="Pet",
        device_class=BinarySensorDeviceClass.MOTION,
    ),
}

LIGHT_TYPE: Final[dict[LightTypes, LightEntityDescription]] = {
    LightTypes.IR: LightEntityDescription(
        key="LightTyps.IR", name="IR", entity_category=EntityCategory.CONFIG
    ),
    LightTypes.POWER: LightEntityDescription(
        key="LightTypes.Power", name="Power", entity_category=EntityCategory.CONFIG
    ),
    LightTypes.WHITE: LightEntityDescription(
        key="LightTypes.White", name="Floodlight", entity_category=EntityCategory.CONFIG
    ),
}
