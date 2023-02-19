"""Reolink Camera Platform"""

from __future__ import annotations
from asyncio import Task
import dataclasses
from enum import IntEnum, auto
import logging
from typing import TYPE_CHECKING, Final, Mapping
from urllib.parse import quote


from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback,
    # async_get_current_platform,
)

from .typing import DomainDataType

if TYPE_CHECKING:
    from homeassistant.helpers import (
        dispatcher as helper_dispatcher,
        issue_registry as helper_issue_registry,
    )
from ._utilities.hass_typing import hass_bound

from homeassistant.loader import async_get_integration

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.camera import (
    Camera,
    CameraEntityFeature,
    CameraEntityDescription,
)

from homeassistant.const import CONF_USERNAME, CONF_PASSWORD


from async_reolink.api.errors import ReolinkResponseError

from async_reolink.api.typing import StreamTypes

from async_reolink.api.const import (
    DEFAULT_USERNAME,
    DEFAULT_PASSWORD,
)

from .typing import DeviceSupportedCallback, ChannelSupportedCallback

from .api import ReolinkDeviceApi

from .entity import (
    ReolinkEntity,
    ChannelDescriptionMixin,
)

from .const import DATA_COORDINATOR, DOMAIN, OPT_CHANNELS, DATA_API

_LOGGER = logging.getLogger(__name__)


class OutputStreamTypes(IntEnum):
    """Output stream Types"""

    JPEG = auto()
    RTMP = auto()
    RTSP = auto()


_NO_FEATURE: Final[CameraEntityFeature] = 0


@dataclasses.dataclass
class ReolinkCameraEntityDescriptionMixin:
    """Mixin for required keys"""

    output_type: OutputStreamTypes
    stream_type: StreamTypes


@dataclasses.dataclass
class ReolinkCameraEntityDescription(
    ChannelDescriptionMixin,
    CameraEntityDescription,
    ReolinkCameraEntityDescriptionMixin,
):
    """Describe Reolink Camera Entity"""

    key: str = None
    features: CameraEntityFeature = _NO_FEATURE
    has_entity_name: bool = True

    def __post_init__(self):
        if self.key is None:
            self.key = f"{self.output_type.name.lower()}_{self.stream_type.name.lower()}"
        if self.name is None:
            self.name = f"{self.output_type.name} {self.stream_type.name.title()}"


_DEVICE_SUPPORTED: Final[
    Mapping[OutputStreamTypes, DeviceSupportedCallback[ReolinkCameraEntityDescription]]
] = {
    OutputStreamTypes.RTMP: lambda _self, device, _: device.rtmp,
    OutputStreamTypes.RTSP: lambda _self, device, _: device.rtsp,
    OutputStreamTypes.JPEG: lambda _self, device, _: device.media_port,
}

_STREAM_SUPPORTED: Final[
    Mapping[StreamTypes, ChannelSupportedCallback[ReolinkCameraEntityDescription]]
] = {
    StreamTypes.MAIN: lambda _self, channel, _: channel.live
    in (channel.live.value.MAIN_SUB, channel.live.value.MAIN_EXTERN_SUB),
    StreamTypes.EXT: lambda _self, channel, _: channel.live == channel.live.value.MAIN_EXTERN_SUB,
}
_STREAM_SUPPORTED[StreamTypes.SUB] = _STREAM_SUPPORTED[StreamTypes.MAIN]

_CAP_SUPPORTED: ChannelSupportedCallback[
    ReolinkCameraEntityDescription
] = lambda _self, channel, _: channel.snap

_CHANNEL_SUPPORTED: Final[
    Mapping[
        tuple[OutputStreamTypes, StreamTypes],
        ChannelSupportedCallback[ReolinkCameraEntityDescription],
    ]
] = {}
for __type, callback in _STREAM_SUPPORTED.items():
    _CHANNEL_SUPPORTED[OutputStreamTypes.RTMP, __type] = callback
    _CHANNEL_SUPPORTED[OutputStreamTypes.RTSP, __type] = callback

    def _jpeg_support(
        __callback: ChannelSupportedCallback[ReolinkCameraEntityDescription],
    ) -> ChannelSupportedCallback[ReolinkCameraEntityDescription]:
        return lambda self, channel, data: __callback(self, channel, data) and _CAP_SUPPORTED(
            self, channel, data
        )

    _CHANNEL_SUPPORTED[OutputStreamTypes.JPEG, __type] = _jpeg_support(callback)


CAMERAS: Final = (
    ReolinkCameraEntityDescription(
        OutputStreamTypes.RTMP,
        StreamTypes.MAIN,
        features=CameraEntityFeature.STREAM,
    ),
    ReolinkCameraEntityDescription(
        OutputStreamTypes.RTSP,
        StreamTypes.MAIN,
        features=CameraEntityFeature.STREAM,
    ),
    ReolinkCameraEntityDescription(
        OutputStreamTypes.RTMP,
        StreamTypes.SUB,
        features=CameraEntityFeature.STREAM,
    ),
    ReolinkCameraEntityDescription(
        OutputStreamTypes.RTMP,
        StreamTypes.EXT,
        features=CameraEntityFeature.STREAM,
    ),
    ReolinkCameraEntityDescription(
        OutputStreamTypes.RTSP,
        StreamTypes.SUB,
        features=CameraEntityFeature.STREAM,
    ),
    ReolinkCameraEntityDescription(
        OutputStreamTypes.RTSP,
        StreamTypes.EXT,
        features=CameraEntityFeature.STREAM,
    ),
    ReolinkCameraEntityDescription(
        OutputStreamTypes.JPEG,
        StreamTypes.MAIN,
    ),
    ReolinkCameraEntityDescription(
        OutputStreamTypes.JPEG,
        StreamTypes.SUB,
    ),
    ReolinkCameraEntityDescription(
        OutputStreamTypes.JPEG,
        StreamTypes.EXT,
    ),
)

# async def async_setup_platform(
#     _hass: HomeAssistant,
#     _config_entry: ConfigEntry,
#     _async_add_entities: AddEntitiesCallback,
#     _discovery_info: DiscoveryInfoType | None = None,
# ):
#     """Setup camera platform"""

#     platform = async_get_current_platform()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Setup camera entities"""

    domain_data: DomainDataType = hass.data[DOMAIN]
    entry_data = domain_data[config_entry.entry_id]
    api = entry_data[DATA_API]

    _LOGGER.debug("Setting up.")

    coordinator = entry_data[DATA_COORDINATOR]

    # can_stream = "stream" in hass.config.components

    entities: list[ReolinkCamera] = []
    device_data = api.data
    _capabilities = device_data.capabilities
    channels: list[int] = config_entry.options.get(OPT_CHANNELS)
    for status in device_data.channel_statuses.values():
        if not status.online or (channels is not None and not status.channel_id in channels):
            continue
        channel_capabilities = _capabilities.channels[status.channel_id]
        info = device_data.channel_info[status.channel_id]

        enabled = False
        main: OutputStreamTypes = None
        first: OutputStreamTypes = None
        name = info.device.get("name", info.device["default_name"])
        for camera in CAMERAS:
            if not _DEVICE_SUPPORTED[camera.output_type](camera, _capabilities, api):
                continue
            if not _CHANNEL_SUPPORTED[(camera.output_type, camera.stream_type)](
                camera, channel_capabilities, info
            ):
                continue
            description = camera.from_channel(info)

            if (
                description.output_type == OutputStreamTypes.RTSP
                and not device_data.ports.rtsp.enabled
            ) or (
                description.output_type == OutputStreamTypes.RTMP
                and not device_data.ports.rtmp.enabled
            ):
                coordinator.logger.warning(
                    "(%s) is disabled on device (%s) so (%s) stream will be skipped",
                    description.output_type.name,
                    name,
                    description.stream_type.name,
                )

            if description.stream_type == StreamTypes.MAIN:
                if not main:
                    main = description.output_type
                else:
                    if description is camera:
                        description = dataclasses.replace(camera)
                    description.entity_registry_enabled_default = False
                if not first:
                    first = description.output_type
            else:
                if not first:
                    first = description.output_type
                    if description is camera:
                        description = dataclasses.replace(camera)
                    description.entity_registry_visible_default = False
                elif description.output_type != first:
                    if description is camera:
                        description = dataclasses.replace(camera)
                    description.entity_registry_enabled_default = False
                else:
                    if description is camera:
                        description = dataclasses.replace(camera)
                    description.entity_registry_visible_default = False

            if not enabled and description.entity_registry_enabled_default:
                enabled = True
            entities.append(
                ReolinkCamera(
                    api,
                    config_entry,
                    description,
                )
            )

        if not enabled:
            url = info.device.get("configuration_url")
            # platform = async_get_current_platform()
            self = await async_get_integration(hass, DOMAIN)
            issue_registry: helper_issue_registry = hass.helpers.issue_registry
            issue_registry.async_create_issue(
                hass,
                DOMAIN,
                "no_enabled_cameras",
                is_fixable=True,
                # issue_domain=platform.domain,
                severity=issue_registry.IssueSeverity.WARNING,
                translation_key="no_enabled_cameras",
                translation_placeholders={
                    "entry_id": config_entry.entry_id,
                    "channel": status.channel_id,
                    "name": name,
                    "configuration_url": url,
                },
                learn_more_url=self.documentation + "/Camera-Stream",
            )

    if entities:
        async_add_entities(entities)

    _LOGGER.debug("Finished setup")


# async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
#     """Unload Camera Entities"""

#     return True


class ReolinkCamera(ReolinkEntity, Camera):
    """Reolink Camera Entity"""

    entity_description: ReolinkCameraEntityDescription

    def __init__(
        self,
        api: ReolinkDeviceApi,
        config_entry: ConfigEntry,
        description: ReolinkCameraEntityDescription,
    ) -> None:
        self.entity_description = description
        super().__init__(api)
        self._attr_supported_features = description.features
        self._attr_extra_state_attributes = {
            "output_type": description.output_type.name,
            "stream_type": description.output_type.name,
        }

        self._snapshot_task: Task[bytes | None] = None
        self._port_disabled_warn = False

    async def stream_source(self) -> str | None:
        if not (client := self._client):
            return await super().stream_source()
        if self.entity_description.output_type == OutputStreamTypes.RTSP:
            url = await client.get_rtsp_url(self._channel_id, self.entity_description.stream_type)
            # rtsp uses separate auth handlers so we have to "inject" the auth with http basic
            data = self.hass.config_entries.async_get_entry(self._entry_id).data
            auth = quote(data.get(CONF_USERNAME, DEFAULT_USERNAME))
            auth += ":"
            auth += quote(data.get(CONF_PASSWORD, DEFAULT_PASSWORD))
            idx = url.index("://")
            url = f"{url[:idx+3]}{auth}@{url[idx+3:]}"
        elif self.entity_description.output_type == OutputStreamTypes.RTMP:
            url = await client.get_rtmp_url(self._channel_id, self.entity_description.stream_type)
        else:
            return await super().stream_source()
        return url

    # async def async_enable_motion_detection(self) -> None:
    #     domain_data: DomainData = self.hass.data[DOMAIN]
    #     client = domain_data[self.coordinator.config_entry.entry_id].client

    #     return await super().async_enable_motion_detection()

    # async def async_disable_motion_detection(self) -> None:
    #     domain_data: DomainData = self.hass.data[DOMAIN]
    #     client = domain_data[self.coordinator.config_entry.entry_id].client

    #     return await super().async_disable_motion_detection()

    async def _async_use_rtsp_to_webrtc(self) -> bool:
        # Force false since the RTMP stream does not seem to work with webrtc
        if self.entity_description.output_type != OutputStreamTypes.RTSP:
            return False
        return await super()._async_use_rtsp_to_webrtc()

    async def _async_camera_image(self):
        if not (client := self._client):
            return None
        try:
            image = await client.get_snap(self._channel_id)
        except ReolinkResponseError as resperr:
            _LOGGER.exception("Failed to capture snapshot (%s: %s)", resperr.code, resperr.details)
            image = None
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Failed to capture snapshot")
            image = None
        if image is None:
            domain_data: DomainDataType = self.hass.data[DOMAIN]
            # have the coordinator upate on error so we can reconnect or disable
            self.hass.create_task(
                domain_data[self._entry_id][DATA_COORDINATOR].async_request_refresh()
            )
        self._snapshot_task = None
        return image

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        if (
            not (client_data := self._device_data)
            or not client_data.capabilities.channels[self._channel_id].snap
        ):
            return await super().async_camera_image(width, height)

        # throttle calls to one per channel at a time
        if not self._snapshot_task:
            self._snapshot_task = self.hass.async_create_task(self._async_camera_image())

        return await self._snapshot_task

    # def _handle_coordinator_update(self) -> None:
    #     if api := self._device_data:
    #         if self.entity_description.output_type == OutputStreamTypes.RTSP:
    #             self._attr_available = api.ports.rtsp.enabled
    #         elif self.entity_description.output_type == OutputStreamTypes.RTMP:
    #             self._attr_available = api.ports.rtmp.enabled
    #         if not self._attr_available and not self._port_disabled_warn:
    #             self._port_disabled_warn = True
    #             self.coordinator.logger.error(
    #                 "(%s) is disabled on device (%s) so (%s) stream will be unavailable",
    #                 self.entity_description.output_type.name,
    #                 self._attr_device_info.get("name", self._attr_device_info.get("default_name")),
    #                 self.entity_description.stream_type.name,
    #             )
    #     else:
    #         self._attr_available = False

    #     return super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        signal = f"{DOMAIN}_{self._entry_id}_ch_{self._channel_id}_motion"

        def on_motion(sensor: BinarySensorEntity):
            self._attr_is_recording = sensor.is_on
            self.schedule_update_ha_state()

        channel = self._device_data.capabilities.channels[self._channel_id]
        if channel.alarm.motion or channel.supports.motion_detection:
            self._attr_motion_detection_enabled = True
            dispatcher: helper_dispatcher = self.hass.helpers.dispatcher
            self.async_on_remove(hass_bound(dispatcher.async_dispatcher_connect)(signal, on_motion))
        return await super().async_added_to_hass()
