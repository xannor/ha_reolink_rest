"""Onvif models with no future"""

from dataclasses import dataclass
from datetime import datetime

from homeassistant.util import dt


@dataclass(frozen=True)
class Subscription:
    """Subscription Token"""

    manager_url: str
    timestamp: datetime
    expires: datetime

    def __post_init__(self):
        if self.timestamp and not isinstance(self.timestamp, datetime):
            object.__setattr__(self, "timestamp", dt.parse_datetime(self.timestamp))
        if self.expires and not isinstance(self.expires, datetime):
            object.__setattr__(self, "expires", dt.parse_datetime(self.expires))
