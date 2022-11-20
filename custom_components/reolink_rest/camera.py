"""Reolink Camera Platform"""

from __future__ import annotations
from asyncio import Task
import dataclasses
from enum import IntEnum, auto
import logging
from typing import Final
from urllib.parse import quote


from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback,
    # async_get_current_platform,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from homeassistant.loader import async_get_integration

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

from .api import async_get_entry_data

from .entity import (
    ReolinkEntity,
    ChannelMixin,
    DeviceSupportedMixin,
)

from .const import DOMAIN, OPT_CHANNELS

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
    ChannelMixin,
    DeviceSupportedMixin,
    CameraEntityDescription,
    ReolinkCameraEntityDescriptionMixin,
):
    """Describe Reolink Camera Entity"""

    key: str = None
    features: CameraEntityFeature = _NO_FEATURE
    has_entity_name: bool = True

    def __post_init__(self):
        if self.key is None:
            self.key = (
                f"{self.output_type.name.lower()}_{self.stream_type.name.lower()}"
            )
        if self.name is None:
            self.name = f"{self.output_type.name} {self.stream_type.name.title()}"


CAMERAS: Final = (
    ReolinkCameraEntityDescription(
        OutputStreamTypes.RTMP,
        StreamTypes.MAIN,
        features=CameraEntityFeature.STREAM,
        device_supported_fn=DeviceSupportedMixin.simple_test(
            lambda device: device.rtmp
        ),
        channel_supported_fn=ChannelMixin.simple_test_and_set(
            lambda channel: channel.main_encoding == channel.main_encoding.value.H264
        ),
    ),
    ReolinkCameraEntityDescription(
        OutputStreamTypes.RTSP,
        StreamTypes.MAIN,
        features=CameraEntityFeature.STREAM,
        device_supported_fn=DeviceSupportedMixin.simple_test(
            lambda device: device.rtsp
        ),
    ),
    ReolinkCameraEntityDescription(
        OutputStreamTypes.RTMP,
        StreamTypes.SUB,
        features=CameraEntityFeature.STREAM,
        device_supported_fn=DeviceSupportedMixin.simple_test(
            lambda device: device.rtmp
        ),
        channel_supported_fn=ChannelMixin.simple_test_and_set(
            lambda channel: channel.live
            in (channel.live.value.MAIN_SUB, channel.live.value.MAIN_EXTERN_SUB)
        ),
    ),
    ReolinkCameraEntityDescription(
        OutputStreamTypes.RTMP,
        StreamTypes.EXT,
        features=CameraEntityFeature.STREAM,
        device_supported_fn=DeviceSupportedMixin.simple_test(
            lambda device: device.rtmp
        ),
        channel_supported_fn=ChannelMixin.simple_test_and_set(
            lambda channel: channel.live == channel.live.value.MAIN_EXTERN_SUB
        ),
    ),
    ReolinkCameraEntityDescription(
        OutputStreamTypes.RTSP,
        StreamTypes.SUB,
        features=CameraEntityFeature.STREAM,
        device_supported_fn=DeviceSupportedMixin.simple_test(
            lambda device: device.rtsp
        ),
        channel_supported_fn=ChannelMixin.simple_test_and_set(
            lambda channel: channel.live
            in (channel.live.value.MAIN_SUB, channel.live.value.MAIN_EXTERN_SUB)
        ),
    ),
    ReolinkCameraEntityDescription(
        OutputStreamTypes.RTSP,
        StreamTypes.EXT,
        features=CameraEntityFeature.STREAM,
        device_supported_fn=DeviceSupportedMixin.simple_test(
            lambda device: device.rtsp
        ),
        channel_supported_fn=ChannelMixin.simple_test_and_set(
            lambda channel: channel.live == channel.live.value.MAIN_EXTERN_SUB
        ),
    ),
    ReolinkCameraEntityDescription(
        OutputStreamTypes.JPEG,
        StreamTypes.MAIN,
        channel_supported_fn=ChannelMixin.simple_test_and_set(
            lambda channel: channel.snap
        ),
    ),
    ReolinkCameraEntityDescription(
        OutputStreamTypes.JPEG,
        StreamTypes.SUB,
        channel_supported_fn=ChannelMixin.simple_test_and_set(
            lambda channel: channel.snap
            and channel.live
            in (channel.live.value.MAIN_SUB, channel.live.value.MAIN_EXTERN_SUB)
        ),
    ),
    ReolinkCameraEntityDescription(
        OutputStreamTypes.JPEG,
        StreamTypes.EXT,
        channel_supported_fn=ChannelMixin.simple_test_and_set(
            lambda channel: channel.snap
            and channel.live == channel.live.value.MAIN_EXTERN_SUB
        ),
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

    entry_data = async_get_entry_data(hass, config_entry.entry_id, False)
    api = entry_data["client_data"]

    _LOGGER.debug("Setting up.")

    coordinator = entry_data["coordinator"]

    # can_stream = "stream" in hass.config.components

    entities: list[ReolinkCamera] = []
    _capabilities = api.capabilities
    channels: list[int] = config_entry.options.get(OPT_CHANNELS, None)
    for status in api.channel_statuses.values():
        if not status.online or (
            channels is not None and not status.channel_id in channels
        ):
            continue
        channel_capabilities = _capabilities.channels[status.channel_id]
        info = api.channel_info[status.channel_id]

        enabled = False
        main: OutputStreamTypes = None
        first: OutputStreamTypes = None
        name = info.device.get("name", info.device["default_name"])
        for camera in CAMERAS:
            description = camera
            if (device_supported := description.device_supported_fn) and not (
                description := device_supported(description, _capabilities, api)
            ):
                continue
            if (channel_supported := description.channel_supported_fn) and not (
                description := channel_supported(
                    description, channel_capabilities, info
                )
            ):
                continue

            if (
                description.output_type == OutputStreamTypes.RTSP
                and not api.ports.rtsp.enabled
            ) or (
                description.output_type == OutputStreamTypes.RTMP
                and not api.ports.rtmp.enabled
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
                    coordinator,
                    description,
                )
            )

        if not enabled:
            url = info.device.get(
                "configuration_url",
                api.device_entry.name if api.device_entry is not None else None,
            )
            # platform = async_get_current_platform()
            self = await async_get_integration(hass, DOMAIN)
            async_create_issue(
                hass,
                DOMAIN,
                "no_enabled_cameras",
                is_fixable=True,
                # issue_domain=platform.domain,
                severity=IssueSeverity.WARNING,
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
        coordinator: DataUpdateCoordinator,
        description: ReolinkCameraEntityDescription,
    ) -> None:
        Camera.__init__(self)
        self.entity_description = description
        ReolinkEntity.__init__(self, coordinator)
        self._attr_supported_features = description.features
        self._attr_extra_state_attributes = {
            "output_type": description.output_type.name,
            "stream_type": description.output_type.name,
        }

        self._snapshot_task: Task[bytes | None] = None
        self._port_disabled_warn = False

    async def stream_source(self) -> str | None:
        client = self._entry_data["client"]
        if self.entity_description.output_type == OutputStreamTypes.RTSP:
            try:
                url = await client.get_rtsp_url(
                    self._channel_id, self.entity_description.stream_type
                )
            except Exception:
                self.hass.create_task(self.coordinator.async_request_refresh())
                raise

            # rtsp uses separate auth handlers so we have to "inject" the auth with http basic
            data = self.coordinator.config_entry.data
            auth = quote(data.get(CONF_USERNAME, DEFAULT_USERNAME))
            auth += ":"
            auth += quote(data.get(CONF_PASSWORD, DEFAULT_PASSWORD))
            idx = url.index("://")
            url = f"{url[:idx+3]}{auth}@{url[idx+3:]}"
        elif self.entity_description.output_type == OutputStreamTypes.RTMP:
            try:
                url = await client.get_rtmp_url(
                    self._channel_id, self.entity_description.stream_type
                )
            except Exception:
                self.hass.create_task(self.coordinator.async_request_refresh())
                raise
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
        client = self._entry_data["client"]
        try:
            image = await client.get_snap(self._channel_id)
        except ReolinkResponseError as resperr:
            _LOGGER.exception(
                "Failed to capture snapshot (%s: %s)", resperr.code, resperr.details
            )
            image = None
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Failed to capture snapshot")
            image = None
        if image is None:
            # have the coordinator upate on error so we can reconnect or disable
            self.hass.create_task(self.coordinator.async_request_refresh())
        self._snapshot_task = None
        return image

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        _capabilities = self._entry_data["client_data"].capabilities.channels[
            self._channel_id
        ]
        if not _capabilities.snap:
            return await super().async_camera_image(width, height)

        # throttle calls to one per channel at a time
        if not self._snapshot_task:
            self._snapshot_task = self.hass.async_create_task(
                self._async_camera_image()
            )

        return await self._snapshot_task

    @property
    def available(self) -> bool:
        if not self._attr_available:
            return False
        return super().available

    def _handle_coordinator_update(self) -> None:
        api = self._client_data

        if self.entity_description.output_type == OutputStreamTypes.RTSP:
            self._attr_available = api.ports.rtsp.enabled
        elif self.entity_description.output_type == OutputStreamTypes.RTMP:
            self._attr_available = api.ports.rtmp.enabled
        if not self._attr_available and not self._port_disabled_warn:
            self._port_disabled_warn = True
            self.coordinator.logger.error(
                "(%s) is disabled on device (%s) so (%s) stream will be unavailable",
                self.entity_description.output_type.name,
                api.device_entry.name_by_user or api.device_entry.name,
                self.entity_description.stream_type.name,
            )

        return super()._handle_coordinator_update()
