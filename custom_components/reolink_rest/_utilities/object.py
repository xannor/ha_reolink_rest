"""Object helpers"""

from typing import Callable, TypeVar, overload, Any

_T = TypeVar("_T")


@overload
def setdefaultattr(__obj: object, __Name: str, __default: None) -> Any | None:
    ...


@overload
def setdefaultattr(__obj: object, __Name: str, __default: _T) -> Any | _T:
    ...


def setdefaultattr(__obj: object, __name: str, __default: Any):
    """get attribute value or set default if not present"""
    try:
        return getattr(__obj, __name)
    except AttributeError:
        setattr(__obj, __name, __default)
    return __default


@overload
def lazygetattr(
    __obj: object, __Name: str, __default: Callable[[], None]
) -> Any | None:
    ...


@overload
def lazygetattr(__obj: object, __Name: str, __default: Callable[[], _T]) -> Any | _T:
    ...


def lazygetattr(__obj: object, __name: str, __default: Callable[[], Any]):
    """get attribute value or return a default from the return of a factory method"""

    try:
        return getattr(__obj, __name)
    except AttributeError:
        return __default()


@overload
def lazysetdefaultattr(
    __obj: object, __Name: str, __default: Callable[[], None]
) -> Any | None:
    ...


@overload
def lazysetdefaultattr(
    __obj: object, __Name: str, __default: Callable[[], _T]
) -> Any | _T:
    ...


def lazysetdefaultattr(__obj: object, __name: str, __default: Callable[[], Any]):
    """get attribute value or set a default from the return of a factory method"""

    try:
        return getattr(__obj, __name)
    except AttributeError:
        if not callable(__default):
            raise ValueError("default value not a function")
        __value = __default()
        setattr(__obj, __name, __value)
    return __value
