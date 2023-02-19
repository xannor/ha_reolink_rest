"""lazy helpers"""

from types import EllipsisType
from typing import Callable, Final, overload
from typing_extensions import TypeVar, Never

_T = TypeVar("_T", infer_variance=True)
_R = TypeVar("_R", infer_variance=True)

USE_DEFAULT: Final = ...


@overload
def lazy(value: _T | EllipsisType) -> Never:
    ...


@overload
def lazy(value: _T | EllipsisType, /, default: _R) -> _T | _R:
    ...


@overload
def lazy(value: _T | EllipsisType, /, factory: Callable[[], _R]) -> _T | _R:
    ...


def lazy(value: any, default=..., factory: Callable[[], any] = None):
    if default is not ... and factory is not None:
        raise ValueError("cannot provide both default value and factory value")
    if value is not ...:
        return value
    if factory:
        return factory()
    if default is ...:
        raise ValueError("no value provided")
    return default
