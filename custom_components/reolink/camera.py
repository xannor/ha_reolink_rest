""" Camera Platform """

import logging
from typing import Optional
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
    DATA_ENTRY,
    DOMAIN,
    CAMERA_TYPES,
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
        if (
            live == LiveAbilitySupport.MAIN_SUB
            or live == LiveAbilitySupport.MAIN_EXTERN_SUB
        ):
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
        for c in config_entry.data.get(CONF_CHANNELS, []):
            if not next((ch for ch in entry_data.channels if ch.channel == c)) is None:
                _create_entities(c)
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
    ):
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
        self._additional_updates()

    def _additional_updates(self):
        if self._prefix_channel and self._data.device_info.channels > 1:
            self._attr_name = f"{self._data.device_info.name} {self._channel_status.name} {self._stream_type.name.title()}"

    @callback
    def _handle_coordinator_update(self):
        if self._connection_id != self._data.connection_id:
            self._connection_id = self._data.connection_id
            if self._data.abilities.rtsp and self._data.ports.rtsp.enabled:
                schema = "rtsp"
                port = (
                    self._data.ports.rtsp.port
                    if self._data.ports.rtsp.port != 554
                    else None
                )
                template = (
                    f"Preview_{self._channel_id}_{self._stream_type.name.lower()}"
                )
            elif self._data.abilities.rtmp and self._data.ports.rtmp.enabled:
                schema = "rtmp"
                port = (
                    self._data.ports.rtsp.port
                    if self._data.ports.rtsp.port != 1935
                    else None
                )
                template = f"bcs/channel{self._channel_id}_{self._stream_type.name.lower()}.bcs?channel={self._channel_id}&stream={self._stream_type}"
            else:
                schema = None
                port = None

            if schema is not None:
                hostname = self._data.client.base_url
                st = hostname.index("://") + 3
                ed = hostname.find("://", st)
                if ed < 0:
                    ed = len(hostname)
                hostname = hostname[st:ed]

                port = f":{port}" if port is not None else ""
                self._stream_url = f"{schema}://{hostname}{port}/{template}"
                self._attr_supported_features |= SUPPORT_STREAM

        self._additional_updates()

        super()._handle_coordinator_update()

    async def stream_source(self):
        if self._stream_url is not None:
            url = self._stream_url
            token = self._data.client.token
            if token is not None:
                url += ("?" if url.find("?") < 0 else "&") + "token=" + token
            return url
        return await super().stream_source()

    async def async_camera_image(
        self, width: Optional[int] = None, height: Optional[int] = None
    ):
        if not self._channel_ability.snap:
            return await super().async_camera_image(width, height)

        result = await self._data.client.get_snap(self._channel_id)
        if result is None:
            return None
        buffer = b""
        async for data, end_of_http_chunk in result[0].iter_chunks():
            buffer += data
            if end_of_http_chunk:
                pass
        return buffer

    async def async_added_to_hass(self):
        self._handle_coordinator_update()
        return await super().async_added_to_hass()
