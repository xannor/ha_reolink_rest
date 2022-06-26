"""Services typings"""

from __future__ import annotations

from typing import Literal, TypedDict, Union

from ..const import EMAIL_SERVICE_TYPE, ONVIF_SERVICE_TYPE


class AddonServiceEventData(TypedDict, total=False):
    """Addon Service Event Data"""

    addon: str
    type: ONVIF_SERVICE_TYPE | EMAIL_SERVICE_TYPE
    url: str
