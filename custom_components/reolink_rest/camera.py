"""Reolink Camera Platform"""

from __future__ import annotations
from asyncio import Task
from dataclasses import asdict, dataclass
from enum import IntEnum, auto
import logging
from typing import Final

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.components.camera import (
    Camera,
    CameraEntityFeature,
    CameraEntityDescription,
)

from homeassistant.const import CONF_USERNAME, CONF_PASSWORD

from async_reolink.api.system import abilities

from async_reolink.api.errors import ReolinkResponseError

from async_reolink.api.const import IntStreamTypes as StreamTypes

from .entity import (
    ReolinkEntityDataUpdateCoordinator,
    ReolinkEntity,
    ReolinkEntityDescription,
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
class ReolinkCameraEntityDescription(ReolinkEntityDescription, CameraEntityDescription):
    """Describe Reolink Camera Entity"""

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
                has_entity_name=True,
            ),
            ReolinkCameraEntityDescription(
                "camera_rtmp_sub",
                name="RTMP Sub",
                output_type=OutputStreamTypes.RTMP,
                stream_type=StreamTypes.SUB,
                has_entity_name=True,
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
                has_entity_name=True,
            ),
            ReolinkCameraEntityDescription(
                "camera_rtsp_sub",
                name="RTSP Sub",
                output_type=OutputStreamTypes.RTSP,
                stream_type=StreamTypes.SUB,
                has_entity_name=True,
            ),
            ReolinkCameraEntityDescription(
                "camera_rtsp_ext",
                name="RTSP Extra",
                output_type=OutputStreamTypes.RTSP,
                stream_type=StreamTypes.EXT,
                has_entity_name=True,
            ),
        ],
    ),
    (
        _NO_FEATURE,
        [
            ReolinkCameraEntityDescription(
                "camera_mjpeg_main", name="Snapshot Main", has_entity_name=True
            ),
            ReolinkCameraEntityDescription(
                "camera_mjpeg_sub",
                name="Snapshot Sub",
                stream_type=StreamTypes.SUB,
                has_entity_name=True,
            ),
            ReolinkCameraEntityDescription(
                "camera_mjpeg_ext",
                name="Snapshot Extra",
                stream_type=StreamTypes.EXT,
                has_entity_name=True,
            ),
        ],
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Setup camera platform"""

    _LOGGER.debug("Setting up camera")
    domain_data: ReolinkDomainData = hass.data[DOMAIN]

    coordinator = domain_data[config_entry.entry_id][DATA_COORDINATOR]

    stream = "stream" in hass.config.components

    entities: list[ReolinkCamera] = []
    data = coordinator.data
    for channel in data.channels.keys():
        ability = coordinator.data.abilities.channels[channel]

        otypes: list[OutputStreamTypes] = []
        if ability.snap:
            otypes.append(OutputStreamTypes.JPEG)
        if stream:
            if data.abilities.rtmp:
                otypes.append(OutputStreamTypes.RTMP)
            if data.abilities.rtsp:
                otypes.append(OutputStreamTypes.RTSP)

        stypes: list[StreamTypes] = []
        if ability.live in (
            abilities.channel.LiveValues.MAIN_EXTERN_SUB,
            abilities.channel.LiveValues.MAIN_SUB,
        ):
            stypes.append(StreamTypes.MAIN)
            stypes.append(StreamTypes.SUB)
        if ability.live == abilities.channel.LiveValues.MAIN_EXTERN_SUB:
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
                    and ability.mainEncType == abilities.channel.EncodingTypeValues.H265
                ):
                    continue
                if (
                    not description.output_type in otypes
                    or not description.stream_type in stypes
                ):
                    continue

                description = ReolinkCameraEntityDescription(**asdict(description))
                description.channel = channel

                if description.stream_type != StreamTypes.MAIN:
                    if not first:
                        first = description.output_type
                    description.entity_registry_visible_default = False
                elif not main:
                    main = description.output_type
                    if not first:
                        first = main
                if description.output_type != first:
                    description.entity_registry_enabled_default = False

                entities.append(ReolinkCamera(coordinator, camera_info[0], description))

    if entities:
        async_add_entities(entities)


class ReolinkCamera(ReolinkEntity, Camera):
    """Reolink Camera Entity"""

    entity_description: ReolinkCameraEntityDescription

    def __init__(
        self,
        coordinator: ReolinkEntityDataUpdateCoordinator,
        supported_features: CameraEntityFeature,
        description: ReolinkCameraEntityDescription,
        context: any = None,
    ) -> None:
        Camera.__init__(self)
        ReolinkEntity.__init__(self, coordinator, description, context)
        self._attr_supported_features = supported_features
        self._snapshot_task: Task[bytes | None] = None

    async def stream_source(self) -> str | None:
        domain_data: ReolinkDomainData = self.hass.data[DOMAIN]
        client = domain_data[self.coordinator.config_entry.entry_id]["client"]

        if self.entity_description.output_type == OutputStreamTypes.RTSP:
            try:
                url = await client.get_rtsp_url(
                    self.entity_description.channel, self.entity_description.stream_type
                )
            except Exception:
                self.hass.create_task(self.coordinator.async_request_refresh())
                raise

            # rtsp uses separate auth handlers so we have to "inject" the auth with http basic
            idx = url.index("://")
            url = f"{url[:idx+3]}{self.coordinator.config_entry.data[CONF_USERNAME]}:{self.coordinator.config_entry.data[CONF_PASSWORD]}@{url[idx+3:]}"
        elif self.entity_description.output_type == OutputStreamTypes.RTMP:
            try:
                url = await client.get_rtmp_url(
                    self.entity_description.channel, self.entity_description.stream_type
                )
            except Exception:
                self.hass.create_task(self.coordinator.async_request_refresh())
                raise
        else:
            return await super().stream_source()
        return url

    async def _async_camera_image(self):
        domain_data: ReolinkDomainData = self.hass.data[DOMAIN]
        client = domain_data[self.coordinator.config_entry.entry_id]["client"]
        try:
            image = await client.get_snap(self.entity_description.channel)
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
        if not self.coordinator.data.abilities.channels[
            self.entity_description.channel
        ].snap:
            return await super().async_camera_image(width, height)

        # throttle calls to one per channel at a time
        if not self._snapshot_task:
            self._snapshot_task = self.hass.async_create_task(
                self._async_camera_image()
            )

        return await self._snapshot_task
