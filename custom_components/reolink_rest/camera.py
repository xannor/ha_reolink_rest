"""Reolink Camera Platform"""

from __future__ import annotations
from asyncio import Task
from dataclasses import asdict, dataclass
from enum import IntEnum, auto
import logging
from typing import Final
from urllib.parse import quote


from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback,
    # async_get_current_platform,
)

from homeassistant.components.camera import (
    Camera,
    CameraEntityFeature,
    CameraEntityDescription,
)

from homeassistant.const import CONF_USERNAME, CONF_PASSWORD

from async_reolink.api.errors import ReolinkResponseError

from async_reolink.api.typings import StreamTypes

from async_reolink.api.const import (
    DEFAULT_USERNAME,
    DEFAULT_PASSWORD,
)

from async_reolink.api.system import capabilities

from .entity import (
    ReolinkEntityDataUpdateCoordinator,
    ReolinkEntity,
)

from .typing import ReolinkDomainData

from .const import DATA_COORDINATOR, DOMAIN

_LOGGER = logging.getLogger(__name__)


class OutputStreamTypes(IntEnum):
    """Output stream Types"""

    JPEG = auto()
    RTMP = auto()
    RTSP = auto()


@dataclass
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
                "camera_rtmp_main",
                name="RTMP Main",
                output_type=OutputStreamTypes.RTMP,
            ),
            ReolinkCameraEntityDescription(
                "camera_rtmp_sub",
                name="RTMP Sub",
                output_type=OutputStreamTypes.RTMP,
                stream_type=StreamTypes.SUB,
            ),
            ReolinkCameraEntityDescription(
                "camera_rtmp_ext",
                name="RTMP Extra",
                output_type=OutputStreamTypes.RTMP,
                stream_type=StreamTypes.EXT,
                has_entity_name=True,
            ),
            ReolinkCameraEntityDescription(
                "camera_rtsp_main",
                name="RTSP Main",
                output_type=OutputStreamTypes.RTSP,
            ),
            ReolinkCameraEntityDescription(
                "camera_rtsp_sub",
                name="RTSP Sub",
                output_type=OutputStreamTypes.RTSP,
                stream_type=StreamTypes.SUB,
            ),
            ReolinkCameraEntityDescription(
                "camera_rtsp_ext",
                name="RTSP Extra",
                output_type=OutputStreamTypes.RTSP,
                stream_type=StreamTypes.EXT,
            ),
        ],
    ),
    (
        _NO_FEATURE,
        [
            ReolinkCameraEntityDescription("camera_mjpeg_main", name="Snapshot Main"),
            ReolinkCameraEntityDescription(
                "camera_mjpeg_sub",
                name="Snapshot Sub",
                stream_type=StreamTypes.SUB,
            ),
            ReolinkCameraEntityDescription(
                "camera_mjpeg_ext",
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

    _LOGGER.debug("Setting up camera")
    domain_data: ReolinkDomainData = hass.data[DOMAIN]
    entry_data = domain_data[config_entry.entry_id]
    _entry_data: dict = entry_data

    coordinator = entry_data[DATA_COORDINATOR]

    stream = "stream" in hass.config.components

    entities: list[ReolinkCamera] = []
    data = coordinator.data
    _abilities = data.abilities
    for channel in data.channels.keys():
        ability = _abilities.channels[channel]

        features: int = 0

        otypes: list[OutputStreamTypes] = []
        if ability.snap:
            otypes.append(OutputStreamTypes.JPEG)
        if stream:
            if _abilities.rtmp:
                otypes.append(OutputStreamTypes.RTMP)
            if _abilities.rtsp:
                otypes.append(OutputStreamTypes.RTSP)

        stypes: list[StreamTypes] = []
        if ability.live in (
            capabilities.Live.MAIN_EXTERN_SUB,
            capabilities.Live.MAIN_SUB,
        ):
            stypes.append(StreamTypes.MAIN)
            stypes.append(StreamTypes.SUB)
        if ability.live == capabilities.Live.MAIN_EXTERN_SUB:
            stypes.append(StreamTypes.EXT)

        if not otypes or not stypes:
            continue

        main: OutputStreamTypes = None
        first: OutputStreamTypes = None
        for camera_info in CAMERAS:
            for description in camera_info[1]:
                if (
                    description.output_type == OutputStreamTypes.RTMP
                    and description.stream_type == StreamTypes.MAIN
                    and ability.main_encoding == capabilities.EncodingType.H265
                ):
                    coordinator.logger.warning(
                        "Channel (%s) is H265 so skipping (%s) (%s) as it is not supported",
                        coordinator.data.channels[channel]["name"],
                        description.output_type.name,
                        description.stream_type.name,
                    )
                    continue
                if (
                    not description.output_type in otypes
                    or not description.stream_type in stypes
                ):
                    continue

                description = ReolinkCameraEntityDescription(**asdict(description))

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
                        and not coordinator.data.ports.rtsp.enabled
                    )
                    or (
                        description.output_type == OutputStreamTypes.RTMP
                        and not coordinator.data.ports.rtmp.enabled
                    )
                ):
                    description.entity_registry_enabled_default = False
                    coordinator.logger.warning(
                        "(%s) is disabled on device (%s) so (%s) stream will be disabled",
                        description.output_type.name,
                        coordinator.data.device.name,
                        description.stream_type.name,
                    )

                entities.append(
                    ReolinkCamera(
                        coordinator, camera_info[0] | features, description, channel
                    )
                )

    if entities:
        async_add_entities(entities)


class ReolinkCamera(ReolinkEntity, Camera):
    """Reolink Camera Entity"""

    entity_description: ReolinkCameraEntityDescription

    def __init__(
        self,
        coordinator: ReolinkEntityDataUpdateCoordinator,
        supported_features: int,
        description: ReolinkCameraEntityDescription,
        channel_id: int,
        context: any = None,
    ) -> None:
        Camera.__init__(self)
        ReolinkEntity.__init__(self, coordinator, channel_id, context)
        self.entity_description = description
        self._attr_supported_features = supported_features
        self._attr_extra_state_attributes["output_type"] = description.output_type.name
        self._attr_extra_state_attributes["stream_type"] = description.output_type.name

        self._snapshot_task: Task[bytes | None] = None
        self._port_disabled_warn = False

    async def stream_source(self) -> str | None:
        domain_data: ReolinkDomainData = self.hass.data[DOMAIN]
        client = domain_data[self.coordinator.config_entry.entry_id][
            DATA_COORDINATOR
        ].data.client

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

    async def _async_use_rtsp_to_webrtc(self) -> bool:
        # Force falce since the RTMP stream does not seem to work with webrtc
        if self.entity_description.output_type != OutputStreamTypes.RTSP:
            return False
        return await super()._async_use_rtsp_to_webrtc()

    async def _async_use_rtsp_to_webrtc(self) -> bool:
        # Force falce since the RTMP stream does not seem to work with webrtc
        if self.entity_description.output_type != OutputStreamTypes.RTSP:
            return False
        return await super()._async_use_rtsp_to_webrtc()

    async def _async_camera_image(self):
        domain_data: ReolinkDomainData = self.hass.data[DOMAIN]
        client = domain_data[self.coordinator.config_entry.entry_id][
            DATA_COORDINATOR
        ].data.client
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
        ability = self.coordinator.data.abilities.channels[self._channel_id]
        if not ability.snap:
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
            self._attr_available = self.coordinator.data.ports.rtsp.enabled
        elif self.entity_description.output_type == OutputStreamTypes.RTMP:
            self._attr_available = self.coordinator.data.ports.rtmp.enabled
        if not self._attr_available and not self._port_disabled_warn:
            self._port_disabled_warn = True
            self.coordinator.logger.error(
                "(%s) is disabled on device (%s) so (%s) stream will be unavailable",
                self.entity_description.output_type.name,
                self.coordinator.data.device.name,
                self.entity_description.stream_type.name,
            )

        return super()._handle_coordinator_update()
