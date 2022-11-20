"""MethodType wrapper"""

from types import MethodType
from typing import Callable, Concatenate, Final, ParamSpec, TypeVar, overload


class _Missing:
    pass


_MISSING: Final = _Missing()

C = TypeVar("C")
P = ParamSpec("P")
R = TypeVar("R")


@overload
def bind(obj: C, method: Callable[Concatenate[C, P], R]) -> Callable[P, R]:
    ...


@overload
def bind(obj: C, method: Callable[Concatenate[C, P], R], as_name: str) -> None:
    ...


def bind(obj: any, method: Callable, as_name: str = _MISSING):
    """wrapper for MethodType"""

    if as_name is None:
        as_name = method.__name__

    bound = MethodType(method, obj)
    if isinstance(as_name, str):
        setattr(obj, as_name, bound)
        return
    return bound
