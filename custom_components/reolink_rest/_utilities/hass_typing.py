"""Home Assistant Helpers/Components Typing Helpers"""

from typing import Callable, Concatenate, ParamSpec
from typing_extensions import TypeVar

from homeassistant.core import HomeAssistant

_P = ParamSpec("_P")
_R = TypeVar("_R", infer_variance=True)


def hass_bound(func: Callable[Concatenate[HomeAssistant, _P], _R]) -> Callable[_P, _R]:
    """return function bound through helper/components, for typing purposes only"""
    return func
