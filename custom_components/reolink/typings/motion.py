"""Motion typings"""

from __future__ import annotations

from typing import TypedDict

from reolinkapi.helpers.ai import GetAiStateResponseValue


class SimpleMotionData(TypedDict):
    """Simple motion data"""

    motion: bool


class SimpleChannelMotionData(SimpleMotionData):
    """Multi-channel simple motion data"""

    channel: int


class MultiChannelMotionData(TypedDict):
    """Multi-channel motion data"""

    channels: list[SimpleChannelMotionData | GetAiStateResponseValue]
