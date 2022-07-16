"""Common Models"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Mapping, Sequence

from homeassistant.helpers.entity import DeviceInfo, EntityDescription
from homeassistant.util import dt

from async_reolink.api import system, network, ai


@dataclass
class ReolinkEntityDescription(EntityDescription):
    """Describe Reolink Entity"""

    channel: int = 0


@dataclass
class ChannelMotionData:
    """Reolink Motion Data"""

    motion: bool = field(default=False)
    detected: Mapping[ai.AITypes, bool] = field(default_factory=dict)


@dataclass
class DeviceData:
    """Reolink Base Entity Data"""

    time: datetime
    drift: timedelta
    abilities: system.abilities.Abilities
    device_info: system.DeviceInfoType
    channels: dict[int, DeviceInfo]
    ports: network.NetworkPortsType


@dataclass
class MotionData:
    """Reolink Base Motion Data"""

    updated: frozenset[int]
    channel: Mapping[int, ChannelMotionData]


@dataclass(frozen=True)
class PushSubscription:
    """Push Subscription Token"""

    manager_url: str
    timestamp: datetime
    expires: timedelta | None

    def __post_init__(self):
        if self.timestamp and not isinstance(self.timestamp, datetime):
            object.__setattr__(self, "timestamp", dt.parse_datetime(self.timestamp))
        if self.expires and not isinstance(self.expires, timedelta):
            object.__setattr__(self, "expires", dt.parse_duration(self.expires))
