"""Common Models"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, MutableMapping

from homeassistant.helpers.entity import EntityDescription
from homeassistant.util import dt

from async_reolink.api import ai


@dataclass
class ReolinkEntityDescription(EntityDescription):
    """Describe Reolink Entity"""

    channel: int = 0


class MotionData(Mapping[ai.AITypes, bool], ABC):
    """
    Motion data

    is true if motion detected, otherwise false, also provides flags for if the motion was ai triggered
    """

    @property
    @abstractmethod
    def detected(self) -> bool:
        """detected"""


class MutableMotionData(MotionData, MutableMapping[ai.AITypes, bool]):
    """Motion Data"""

    def __init__(self) -> None:
        self._detected: bool = False
        self._ai: dict[ai.AITypes, bool] = {}

    def __bool__(self):
        return self._detected

    def __len__(self):
        return self._ai.__len__()

    def __getitem__(self, __k: ai.AITypes):
        return self._ai.__getitem__(__k)

    def __setitem__(self, __k: ai.AITypes, __v: bool):
        return self._ai.__setitem__(__k, __v)

    def __delitem__(self, __v: ai.AITypes):
        return self._ai.__delitem__(__v)

    def __iter__(self):
        return self._ai.__iter__()

    def __contains__(self, __o: object):
        return self._ai.__contains__(__o)

    @property
    def detected(self):
        """Detected"""
        return self._detected

    @detected.setter
    def detected(self, value: bool):
        self._detected = value


class PTZPosition(ABC):
    """
    PTZ Position data

    also can be used directly as an int
    """

    @property
    @abstractmethod
    def value(self) -> int:
        """value"""

    def __index__(self):
        return self.value


class MutablePTZPosition(PTZPosition):
    """PTZ Position"""

    def __init__(self) -> None:
        self._value = 0

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value: int):
        self._value = value


class PTZDisabled(ABC):
    """PTZ Disabled"""

    @property
    @abstractmethod
    def disabled(self) -> bool:
        """disabled"""

    def __bool__(self):
        return not self.disabled


class MutablePTZDisabled(PTZDisabled):
    """PTZ Disabled"""

    def __init__(self) -> None:
        self._value = True

    @property
    def disabled(self):
        return self._value

    @disabled.setter
    def disabled(self, value: bool) -> bool:
        self._value = value
