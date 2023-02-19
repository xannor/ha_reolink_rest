"""Motion Typings"""


from typing import TypedDict

from ..entity import UpdateMethods


class MotionEventData(TypedDict, total=False):
    """Motion Event Data"""

    detected: bool
    ai: dict[str, bool]


class ChannelMotionEventData(MotionEventData):
    """Channel motion event"""

    channel_id: int


class MotionEvent(MotionEventData, total=False):
    """All Motion Events"""

    method: UpdateMethods
    channels: list[ChannelMotionEventData]
