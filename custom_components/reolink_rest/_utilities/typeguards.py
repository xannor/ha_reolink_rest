"""typeguards"""

from typing import Final, NamedTuple, Protocol, TypeGuard, TypeVar, TypedDict, overload

_T = TypeVar("_T")

_TypedDictMeta: Final[type] = type(TypedDict("_TypedDict", {}))


@overload
def is_type(value: any, __type: type[_T]) -> TypeGuard[_T]:
    ...


def is_type(value: any, __type: type):
    """wrapper for isinstance to simply detect TypedDicts, NamedTuples, and Protocols"""
    if value is None or value is ...:
        return False
    if not isinstance(__type, type):
        return callable(__type)

    # pylint: disable=protected-access
    # pylint: disable=unidiomatic-typecheck
    if type(__type) == _TypedDictMeta:
        __type = dict
    # elif issubclass(__type, NamedTuple):
    #     __type = tuple
    elif issubclass(__type, Protocol) and not __type._is_runtime_protocol:
        __type = object
    return isinstance(value, __type)
