"""Constants"""
from __future__ import annotations

from enum import IntEnum
from typing import Final, Literal
from reolinkapi.const import StreamTypes as CameraStreamTypes
from reolinkapi.models.ai import AITypes
from homeassistant.components.camera import CameraEntityDescription
from homeassistant.components.binary_sensor import (
    BinarySensorEntityDescription,
    BinarySensorDeviceClass,
)


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
DEFAULT_MOTION_INTERVAL = 10
DEFAULT_STREAM_TYPE = {
    CameraStreamTypes.MAIN: OutputStreamTypes.RTMP,
    CameraStreamTypes.SUB: OutputStreamTypes.RTMP,
    CameraStreamTypes.EXT: OutputStreamTypes.RTMP,
}
DEFAULT_USE_AES = False

CONF_USE_HTTPS = "use_https"
CONF_CHANNELS = "channels"
CONF_PREFIX_CHANNEL = "prefix_channel"
CONF_MOTION_INTERVAL = "motion_interval"
CONF_USE_AES = "use_aes"

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

ONVIF_SERVICE = "onvif"
ONVIF_SERVICE_TYPE = Literal["onvif"]

EMAIL_SERVICE = "email"
EMAIL_SERVICE_TYPE = Literal["email"]


class _AITypeNone:
    pass


AI_TYPE_NONE = _AITypeNone

MOTION_TYPE: Final[dict[AITypes | AI_TYPE_NONE, BinarySensorEntityDescription]] = {
    AI_TYPE_NONE: BinarySensorEntityDescription(
        key="DetectionType.None",
        name="Motion",
        device_class=BinarySensorDeviceClass.MOTION,
    ),
    AITypes.PEOPLE: BinarySensorEntityDescription(
        key="DetetctionTypes.Person",
        name="Person",
        device_class=BinarySensorDeviceClass.MOTION,
    ),
    AITypes.VEHICLE: BinarySensorEntityDescription(
        key="DetetctionTypes.Vehicle",
        name="Vehicle",
        device_class=BinarySensorDeviceClass.MOTION,
    ),
    AITypes.ANIMAL: BinarySensorEntityDescription(
        key="DetetctionTypes.Animal",
        name="Animal",
        device_class=BinarySensorDeviceClass.MOTION,
    ),
    AITypes.PET: BinarySensorEntityDescription(
        key="DetetctionTypes.Pet",
        name="Pet",
        device_class=BinarySensorDeviceClass.MOTION,
    ),
}

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
