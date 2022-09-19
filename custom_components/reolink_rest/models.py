"""Common Models"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping

from homeassistant.helpers.entity import EntityDescription

from async_reolink.api import ai, ptz

from async_reolink.rest.models import MinMaxRange


@dataclass
class ReolinkEntityDescription(EntityDescription):
    """Describe Reolink Entity"""

    channel: int = 0


class Motion(Mapping[ai.AITypes, bool], ABC):
    """
    Motion data

    is true if motion detected, otherwise false, also provides flags for if the motion was ai triggered
    """

    @property
    @abstractmethod
    def detected(self) -> bool:
        """detected"""


class PTZ(ABC):
    """PTZ Data"""

    @property
    @abstractmethod
    def pan(self) -> int:
        """pan"""

    @property
    @abstractmethod
    def tilt(self) -> int:
        """tilt"""

    @property
    @abstractmethod
    def zoom(self) -> int:
        """zoom"""

    @property
    @abstractmethod
    def zoom_range(self) -> MinMaxRange[int] | None:
        """zoom range"""

    @property
    @abstractmethod
    def focus(self) -> int:
        """focus"""

    @property
    @abstractmethod
    def focus_range(self) -> MinMaxRange[int] | None:
        """zoom range"""

    @property
    @abstractmethod
    def autofocus(self) -> bool:
        """autofocus"""

    @property
    @abstractmethod
    def presets(self) -> Mapping[int, ptz.Preset]:
        """presets"""

    @property
    @abstractmethod
    def patrol(self) -> Mapping[int, ptz.Patrol]:
        """patrol"""

    @property
    @abstractmethod
    def tattern(self) -> Mapping[int, ptz.Track]:
        """patrol"""
