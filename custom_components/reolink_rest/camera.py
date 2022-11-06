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

from homeassistant.loader import async_get_integration

from homeassistant.components.camera import (
    Camera,
    CameraEntityFeature,
    CameraEntityDescription,
)

from homeassistant.const import CONF_USERNAME, CONF_PASSWORD

from async_reolink.api.system import capabilities

from async_reolink.api.errors import ReolinkResponseError

from async_reolink.api.typing import StreamTypes

from async_reolink.api.const import (
    DEFAULT_USERNAME,
    DEFAULT_PASSWORD,
)

from .api import ReolinkRestApi

from .entity import (
    ReolinkEntityDataUpdateCoordinator,
    ReolinkEntity,
)

from .const import DOMAIN, OPT_CHANNELS

_LOGGER = logging.getLogger(__name__)


class OutputStreamTypes(IntEnum):
    """Output stream Types"""

    JPEG = auto()
    RTMP = auto()
    RTSP = auto()


@dataclasses.dataclass
class ReolinkCameraEntityDescription(CameraEntityDescription):
    """Describe Reolink Camera Entity"""

    has_entity_name: bool = True
    output_type: OutputStreamTypes = OutputStreamTypes.JPEG
    stream_type: StreamTypes = StreamTypes.MAIN


_NO_FEATURE: Final[CameraEntityFeature] = 0

# need to unliteral STREAM so the typechecker thinks is a value
_STREAM: Final[CameraEntityFeature] = CameraEntityFeature.STREAM.value

CAMERAS: Final = [
    (
        _STREAM,
        [
            ReolinkCameraEntityDescription(
                "rtmp_main",
                name="RTMP Main",
                output_type=OutputStreamTypes.RTMP,
            ),
            ReolinkCameraEntityDescription(
                "rtmp_sub",
                name="RTMP Sub",
                output_type=OutputStreamTypes.RTMP,
                stream_type=StreamTypes.SUB,
            ),
            ReolinkCameraEntityDescription(
                "rtmp_ext",
                name="RTMP Extra",
                output_type=OutputStreamTypes.RTMP,
                stream_type=StreamTypes.EXT,
                has_entity_name=True,
            ),
            ReolinkCameraEntityDescription(
                "rtsp_main",
                name="RTSP Main",
                output_type=OutputStreamTypes.RTSP,
            ),
            ReolinkCameraEntityDescription(
                "rtsp_sub",
                name="RTSP Sub",
                output_type=OutputStreamTypes.RTSP,
                stream_type=StreamTypes.SUB,
            ),
            ReolinkCameraEntityDescription(
                "rtsp_ext",
                name="RTSP Extra",
                output_type=OutputStreamTypes.RTSP,
                stream_type=StreamTypes.EXT,
            ),
        ],
    ),
    (
        _NO_FEATURE,
        [
            ReolinkCameraEntityDescription("mjpeg_main", name="Snapshot Main"),
            ReolinkCameraEntityDescription(
                "mjpeg_sub",
                name="Snapshot Sub",
                stream_type=StreamTypes.SUB,
            ),
            ReolinkCameraEntityDescription(
                "mjpeg_ext",
                name="Snapshot Extra",
                stream_type=StreamTypes.EXT,
            ),
        ],
    ),
]

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

    domain_data: dict = hass.data[DOMAIN]
    api: ReolinkRestApi = domain_data[config_entry.entry_id]

    _LOGGER.debug("Setting up.")

    coordinator = api.coordinator

    can_stream = "stream" in hass.config.components

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

        features: int = 0

        otypes: list[OutputStreamTypes] = []
        if channel_capabilities.snap:
            otypes.append(OutputStreamTypes.JPEG)
        if can_stream:
            if _capabilities.rtmp:
                otypes.append(OutputStreamTypes.RTMP)
            if _capabilities.rtsp:
                otypes.append(OutputStreamTypes.RTSP)

        stypes: list[StreamTypes] = []
        if channel_capabilities.live in (
            capabilities.Live.MAIN_EXTERN_SUB,
            capabilities.Live.MAIN_SUB,
        ):
            stypes.append(StreamTypes.MAIN)
            stypes.append(StreamTypes.SUB)
        if channel_capabilities.live == capabilities.Live.MAIN_EXTERN_SUB:
            stypes.append(StreamTypes.EXT)

        if not otypes or not stypes:
            continue

        enabled = False
        main: OutputStreamTypes = None
        first: OutputStreamTypes = None
        name = info.device.get("name", info.device["default_name"])
        for camera_info in CAMERAS:
            for description in camera_info[1]:

                if (
                    description.output_type == OutputStreamTypes.RTMP
                    and description.stream_type == StreamTypes.MAIN
                    and channel_capabilities.main_encoding
                    == capabilities.EncodingType.H265
                ):
                    coordinator.logger.warning(
                        "Channel (%s) is H265 so skipping (%s) (%s) as it is not supported",
                        name,
                        description.output_type.name,
                        description.stream_type.name,
                    )
                    continue
                if (
                    not description.output_type in otypes
                    or not description.stream_type in stypes
                ):
                    continue

                if description.stream_type == StreamTypes.MAIN:
                    if not main:
                        main = description.output_type
                    else:
                        description.entity_registry_enabled_default = False
                    if not first:
                        first = description.output_type
                else:
                    if not first:
                        first = description.output_type
                        description.entity_registry_visible_default = False
                    elif description.output_type != first:
                        description.entity_registry_enabled_default = False
                    else:
                        description.entity_registry_visible_default = False

                if description.entity_registry_enabled_default and (
                    (
                        description.output_type == OutputStreamTypes.RTSP
                        and not api.ports.rtsp.enabled
                    )
                    or (
                        description.output_type == OutputStreamTypes.RTMP
                        and not api.ports.rtmp.enabled
                    )
                ):
                    description = dataclasses.replace(
                        description, entity_registry_enabled_default=False
                    )
                    coordinator.logger.warning(
                        "(%s) is disabled on device (%s) so (%s) stream will be disabled",
                        description.output_type.name,
                        coordinator.data.device["name"],
                        description.stream_type.name,
                    )

                if not enabled and description.entity_registry_enabled_default:
                    enabled = True
                entities.append(
                    ReolinkCamera(
                        coordinator,
                        camera_info[0] | features,
                        description,
                        status.channel_id,
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


class ReolinkCamera(ReolinkEntity, Camera):
    """Reolink Camera Entity"""

    entity_description: ReolinkCameraEntityDescription

    def __init__(
        self,
        coordinator: ReolinkEntityDataUpdateCoordinator,
        supported_features: int,
        description: ReolinkCameraEntityDescription,
        channel_id: int,
    ) -> None:
        Camera.__init__(self)
        ReolinkEntity.__init__(self, coordinator, channel_id)
        self.entity_description = description
        self._attr_supported_features = supported_features
        self._attr_extra_state_attributes["output_type"] = description.output_type.name
        self._attr_extra_state_attributes["stream_type"] = description.output_type.name

        self._snapshot_task: Task[bytes | None] = None
        self._port_disabled_warn = False

    async def stream_source(self) -> str | None:
        client = self._api.client
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
        client = self._api.client
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
        _capabilities = self._api.capabilities.channels[self._channel_id]
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
        if self.entity_description.output_type == OutputStreamTypes.RTSP:
            self._attr_available = self._api.ports.rtsp.enabled
        elif self.entity_description.output_type == OutputStreamTypes.RTMP:
            self._attr_available = self._api.ports.rtmp.enabled
        if not self._attr_available and not self._port_disabled_warn:
            self._port_disabled_warn = True
            self.coordinator.logger.error(
                "(%s) is disabled on device (%s) so (%s) stream will be unavailable",
                self.entity_description.output_type.name,
                self._api.device.name,
                self.entity_description.stream_type.name,
            )

        return super()._handle_coordinator_update()
