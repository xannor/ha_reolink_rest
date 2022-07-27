"""Common Typings"""

from datetime import timedelta
from typing import Mapping, Protocol, TypedDict

from aiohttp.web import Request, Response

from homeassistant.core import HomeAssistant, CALLBACK_TYPE
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.device_registry import DeviceEntry
from async_reolink.api import system, network, ptz
from async_reolink.rest import Client

from .models import MotionData, PTZPosition, PTZDisabled


class PTZData(Protocol):
    """PTZ Data"""

    pan: PTZPosition
    tilt: PTZPosition
    zoom: PTZPosition
    focus: PTZPosition
    autofocus: PTZDisabled
    presets: Mapping[ptz.PTZPresetId, ptz.PTZPreset] | None
    patrol: Mapping[ptz.PTZPatrolId, ptz.PTZPatrol] | None
    tattern: Mapping[ptz.PTZTrackId, ptz.PTZTrack] | None


class EntityData(Protocol):
    """Entity Data and API"""

    client: Client
    device: DeviceEntry
    time_difference: timedelta
    abilities: system.abilities.Abilities
    device_info: system.DeviceInfoType
    channels: Mapping[int, DeviceInfo]
    ports: network.NetworkPortsType
    updated_motion: frozenset[int]
    motion: Mapping[int, MotionData]
    updated_ptz: frozenset[int]
    ptz: Mapping[int, PTZData]

    def async_request_motion_update(self, channel: int = 0) -> None:
        """Request motion update for channel"""
        ...

    def async_request_ptz_update(self, channel: int = 0) -> None:
        """Request PTZ update for channel"""
        ...


class ReolinkEntryData(TypedDict, total=False):
    """Common entry data"""

    coordinator: DataUpdateCoordinator[EntityData]
    motion_coordinators: dict[int, DataUpdateCoordinator[EntityData]]


ReolinkDomainData = dict[str, ReolinkEntryData]


class AsyncWebhookHandler(Protocol):
    """Async Webhook Handler"""

    async def __call__(self, hass: HomeAssistant, request: Request) -> Response | None:
        ...


class WebhookManager(Protocol):
    """Webhook Manager"""

    @property
    def url(self) -> str:
        """full webhook url"""

    def async_add_handler(self, handler: AsyncWebhookHandler) -> CALLBACK_TYPE:
        """add callable handler returns removal callback"""
