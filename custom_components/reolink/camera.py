""" Camera Platform """
from __future__ import annotations

import logging
from time import time
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.camera import (
    Camera,
    SUPPORT_STREAM,
    DOMAIN as CAMERA_DOMAIN,
)

from reolinkapi.rest.const import StreamTypes
from reolinkapi.rest.abilities.channel import LiveAbilitySupport

from .base import ReolinkEntity

from . import ReolinkEntityData
from .const import (
    CONF_CHANNELS,
    CONF_PREFIX_CHANNEL,
    CONF_STREAM_TYPE,
    CONF_USE_RTSP,
    DATA_ENTRY,
    DEFAULT_STREAM_TYPE,
    DEFAULT_USE_RTSP,
    DOMAIN,
    CAMERA_TYPES,
    OutputStreamTypes,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Setup camera platform"""

    entry_data: ReolinkEntityData = hass.data[DOMAIN][config_entry.entry_id][DATA_ENTRY]

    entities = []

    def _create_entities(channel: int):
        live = entry_data.abilities.channel[channel].live.supported
        if live in (LiveAbilitySupport.MAIN_SUB, LiveAbilitySupport.MAIN_EXTERN_SUB):
            entities.append(
                ReolinkCameraEntity(hass, channel, StreamTypes.MAIN, config_entry)
            )
            entities.append(
                ReolinkCameraEntity(hass, channel, StreamTypes.SUB, config_entry)
            )
        if live == LiveAbilitySupport.MAIN_EXTERN_SUB:
            entities.append(
                ReolinkCameraEntity(hass, channel, StreamTypes.EXT, config_entry)
            )

    if entry_data.channels is not None and CONF_CHANNELS in config_entry.data:
        for _c in config_entry.data.get(CONF_CHANNELS, []):
            if not next((ch for ch in entry_data.channels if ch.channel == _c)) is None:
                _create_entities(_c)
    else:
        _create_entities(0)

    if len(entities) > 0:
        async_add_entities(entities)


class ReolinkCameraEntity(ReolinkEntity, Camera):
    """Reolink Camera Entity"""

    def __init__(
        self,
        hass: HomeAssistant,
        channel_id: int,
        stream_type: StreamTypes,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(hass, channel_id, config_entry, CAMERA_TYPES[stream_type])
        Camera.__init__(
            self
        )  # explicitly call Camera init since UpdateCoordinatorEntity does not super()
        self._stream_type = stream_type
        self._attr_brand = "Reolink"
        self._connection_id: int = 0
        self._stream_url: str = None
        self._prefix_channel: bool = config_entry.data.get(CONF_PREFIX_CHANNEL)
        self._attr_model = self._channel_status.type_info
        self._attr_unique_id = f"{self._attr_brand}.{self._data.device_info.serial}.{CAMERA_DOMAIN}.{self._channel_id}.{self._stream_type.name}"
        _output_types = config_entry.data.get(CONF_STREAM_TYPE, DEFAULT_STREAM_TYPE)
        self._output_type: OutputStreamTypes = _output_types.get(stream_type)
        self._attr_supported_features |= SUPPORT_STREAM
        self._snap_spam_buffer: bytes | None = None
        self._snap_spam_timeout: float = 0
        self._additional_updates()

    def _additional_updates(self):
        _valid_types: list[OutputStreamTypes] = []
        if self._channel_ability.snap.supported:
            _valid_types.append(OutputStreamTypes.MJPEG)
        if self._data.abilities.rtmp:
            _valid_types.append(OutputStreamTypes.RTMP)
        if self._data.abilities.rtsp:
            _valid_types.append(OutputStreamTypes.RTSP)
        if self._output_type is None or self._output_type not in _valid_types:
            self._output_type = _valid_types[0]

        if self._prefix_channel and self._data.device_info.channels > 1:
            self._attr_name = f"{self._data.device_info.name} {self._channel_status.name} {self._stream_type.name.title()}"

    @callback
    def _handle_coordinator_update(self):
        if self._connection_id != self._data.connection_id:
            self._connection_id = self._data.connection_id
            self._stream_url = None

        self._additional_updates()

        super()._handle_coordinator_update()

    async def stream_source(self):
        if self._stream_url is None:
            if self._output_type == OutputStreamTypes.RTSP:
                self._stream_url = await self._data.client.get_rtsp_url(
                    self._channel_id, self._stream_type
                )
            elif self._output_type == OutputStreamTypes.RTMP:
                self._stream_url = await self._data.client.get_rtmp_url(
                    self._channel_id, self._stream_type
                )

        if self._stream_url is not None:
            return self._stream_url

        return await super().stream_source()

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ):
        if not self._channel_ability.snap:
            return await super().async_camera_image(width, height)

        if time() < self._snap_spam_timeout:
            return self._snap_spam_buffer

        result = await self._data.client.get_snap(self._channel_id)
        if result is None:
            return None
        buffer = b""
        async for data, end_of_http_chunk in result[0].iter_chunks():
            buffer += data
            if end_of_http_chunk:
                pass
        self._snap_spam_buffer = buffer
        self._snap_spam_timeout = time() + 1000
        return buffer

    async def async_added_to_hass(self):
        self._handle_coordinator_update()
        return await super().async_added_to_hass()
