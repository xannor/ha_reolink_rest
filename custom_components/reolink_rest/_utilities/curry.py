"""partial wrapper"""

from functools import partial
from typing import Callable, TypeVar

T = TypeVar("T")


def curry(__func: Callable[..., T], *args, **kwargs):
    """Wrapper for partial"""

    return partial(__func, *args, **kwargs)
