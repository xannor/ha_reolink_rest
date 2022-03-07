""" Camera Platform """
from __future__ import annotations
from asyncio import Task

import logging
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.camera import (
    Camera,
    SUPPORT_STREAM,
)

from reolinkapi.rest.const import StreamTypes
from reolinkapi.rest.typings.abilities.channel import LiveAbilityVers

from .base import ReolinkEntity

from . import ReolinkEntityData
from .const import (
    CONF_CHANNELS,
    CONF_PREFIX_CHANNEL,
    DATA_ENTRY,
    DEFAULT_STREAM_TYPE,
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
        channel_abilities = entry_data.abilities["abilityChn"][channel]
        live = channel_abilities["live"]["ver"]
        if live in (LiveAbilityVers.MAIN_SUB, LiveAbilityVers.MAIN_EXTERN_SUB):
            entities.append(
                ReolinkCameraEntity(hass, channel, StreamTypes.MAIN, config_entry)
            )
            entities.append(
                ReolinkCameraEntity(hass, channel, StreamTypes.SUB, config_entry)
            )
        if live == LiveAbilityVers.MAIN_EXTERN_SUB:
            entities.append(
                ReolinkCameraEntity(hass, channel, StreamTypes.EXT, config_entry)
            )

    if entry_data.channels is not None and CONF_CHANNELS in config_entry.data:
        for _c in config_entry.data.get(CONF_CHANNELS, []):
            if (
                not next((ch for ch in entry_data.channels if ch["channel"] == _c))
                is None
            ):
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
        self._attr_model = (
            self._channel_status["typeInfo"]
            if self._channel_status is not None
            else self._data.device_info["model"]
        )
        self._attr_unique_id = (
            f"{self._data.uid}.{self._channel_id}.{self._stream_type.name}"
        )
        key = f"channel_{channel_id}_{stream_type.name.lower()}_type"
        self._output_type = config_entry.options.get(
            key, DEFAULT_STREAM_TYPE[stream_type]
        )
        if self._output_type != OutputStreamTypes.MJPEG:
            self._attr_supported_features |= SUPPORT_STREAM
        self._snapshot_task: Task[bytes | None] | None = None
        self._additional_updates()

    def _additional_updates(self):
        _valid_types: list[OutputStreamTypes] = []
        if self._channel_ability["snap"]["ver"]:
            _valid_types.append(OutputStreamTypes.MJPEG)
        if self._data.abilities["rtmp"]:
            _valid_types.append(OutputStreamTypes.RTMP)
        if self._data.abilities["rtsp"]:
            _valid_types.append(OutputStreamTypes.RTSP)
        if self._output_type is None or self._output_type not in _valid_types:
            self._output_type = _valid_types[0]

        if self._prefix_channel and self._channel_status is not None:
            self._attr_name = f'{self._data.ha_device_info["name"]} {self._channel_status["name"]} {self.entity_description.name}'
        else:
            self._attr_name = (
                f'{self._data.ha_device_info["name"]} {self.entity_description.name}'
            )

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
        if not self._channel_ability["snap"]["ver"]:
            return await super().async_camera_image(width, height)

        if self._snapshot_task is not None:
            return await self._snapshot_task

        # create task for snapshot so camera does not get flooded
        # with requests, instead it will only grab them
        # "linearly" and multiple calls will return the
        # same pending picture
        self._snapshot_task = self.hass.async_create_task(
            self._data.client.get_snap(self._channel_id)
        )
        snap = await self._snapshot_task
        self._snapshot_task = None
        return snap

    async def async_added_to_hass(self):
        self._handle_coordinator_update()
        return await super().async_added_to_hass()
