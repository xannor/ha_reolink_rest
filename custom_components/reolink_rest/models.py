"""Common Models"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.helpers.entity import DeviceInfo, EntityDescription
from homeassistant.util import dt

from reolinkapi import system, network, ai


@dataclass
class ReolinkEntityDescription(EntityDescription):
    """Describe Reolink Entity"""

    channel: int = 0


@dataclass
class ChannelMotionData:
    """Reolink Motion Data"""

    motion: bool = field(default=False)
    detected: dict[ai.AITypes, bool] = field(default_factory=dict)


@dataclass
class EntityData:
    """Reolink Base Entity Data"""

    time: datetime
    drift: timedelta
    abilities: system.abilities.Abilities
    device_info: system.DeviceInfoType
    channels: dict[int, DeviceInfo]
    ports: network.NetworkPortsType


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
