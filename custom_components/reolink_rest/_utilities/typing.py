"""Typing Helpers"""

__all__ = ("CallableKwArgs", "bind")

from typing import Callable, Concatenate, Protocol, TypeAlias, overload
from typing_extensions import TypeVar, ParamSpec

P = ParamSpec("P")
R = TypeVar("R", infer_variance=True)
T = TypeVar("T", infer_variance=True)


@overload
def bind(__callable: Callable[Concatenate[T, P], R], __self: T) -> Callable[P, R]:
    ...


def bind(__callable: Callable, __self: any = None):
    if __callable is None:
        return None
    if __self is None:
        return __callable
    return __callable.__get__(__self)
