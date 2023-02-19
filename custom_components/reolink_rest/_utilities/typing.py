"""Typing Helpers"""

__all__ = ("CallableKwArgs",)

from typing import Callable, Concatenate, ParamSpec, Protocol, TypeAlias, overload
from typing_extensions import TypeVar

P = ParamSpec("P")
R = TypeVar("R", infer_variance=True)


class CallableKwArgs(Protocol[P, R]):
    def __call__(self, *args: P.args, **kwds: P.kwargs) -> R:
        ...


T = TypeVar("T", infer_variance=True)


@overload
def bind(__callable: Callable[Concatenate[T, P], R], __self: T) -> Callable[P, R]:
    ...


@overload
def bind(__callable: Callable[Concatenate[T, P], R] | None, __self: T) -> Callable[P, R] | None:
    ...


@overload
def bind(__callable: Callable[P, R]) -> Callable[P, R]:
    ...


@overload
def bind(__callable: Callable[P, R] | None) -> Callable[P, R] | None:
    ...


@overload
def bind(__callable: Callable[P, R], __self: None) -> Callable[P, R]:
    ...


@overload
def bind(__callable: Callable[P, R] | None, __self: None) -> Callable[P, R] | None:
    ...


@overload
def bind(__callable: None, __self: any) -> None:
    ...


def bind(__callable: Callable, __self: any = None):
    if __callable is None:
        return None
    if __self is None:
        return __callable
    return __callable.__get__(__self)
