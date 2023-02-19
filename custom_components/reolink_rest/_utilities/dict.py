"""dictionary helpers"""

from typing import (
    TYPE_CHECKING,
    Callable,
    Generic,
    Iterator,
    KeysView,
    Mapping,
    TypeVar,
    ValuesView,
    overload,
)

_KT = TypeVar("_KT")
_VT_co = TypeVar("_VT_co", covariant=True)

if TYPE_CHECKING:
    from typing import cast
    from _typeshed import SupportsKeysAndGetItem

    @overload
    def slice_keys(
        __iterable: SupportsKeysAndGetItem[_KT, _VT_co], *__keys: _KT
    ) -> Iterator[tuple[_KT, _VT_co]]:
        ...

    @overload
    def slice_keys(
        source: Mapping[_KT, _VT_co], *keys: _KT
    ) -> Iterator[tuple[_KT, _VT_co], None, None]:
        ...

    @overload
    def slice_keys(
        source: Iterator[tuple[_KT, _VT_co]], *keys: _KT
    ) -> Iterator[tuple[_KT, _VT_co], None, None]:
        ...


def slice_keys(__iterable: any, *__keys: any):
    if __iterable is None:
        return _empty()
    if isinstance(__iterable, Mapping):
        return _map_slice_keys(__iterable, *__keys)
    if callable(getattr(__iterable, "keys", None)) and callable(
        getattr(__iterable, "__getitem__", None)
    ):
        return _getitem_slice_keys(__iterable, *__keys)
    return _tuple_slice_keys(__iterable, *__keys)


def _empty():
    yield from ()


def _tuple_slice_keys(__iterator: Iterator[tuple], *__keys):
    for t in __iterator:
        if t[0] in __keys:
            yield t


def _map_slice_keys(__map: Mapping, *__keys):
    return _tuple_slice_keys(__map.items(), *__keys)


def _getitem_slice_keys(__map, *__keys):
    if TYPE_CHECKING:
        __map = cast(SupportsKeysAndGetItem, __map)
    for k in __map.keys():
        if k in __keys:
            yield tuple(k, __map[k])


# _TKT = TypeVar("_TKT")

# _TVT_co = TypeVar("_TVT_co", covariant=True)

# class TransformingMapping(Mapping[_TKT, _TVT_co], Generic[_KT, _VT_co, _TKT, _TVT_co]):

#     __slots__ = ("__map", "__factory")

#     def __init__(
#         self,
#         __map: Mapping[_KT, _VT_co],
#         /,
#         __factory: Callable[[_KT, _VT_co], tuple[_TKT, _TVT_co]]|None = None,
#         __keyFactory: Callable[[_KT], _TKT]|None = None,
#         __valueFactory: Callable[[_VT_co], _TVT_co]|None = None,
#     ) -> None:
#         super().__init__()
#         self.__map = __map
#         if not __factory:
#             self.__factory = tuple(__keyFactory, __valueFactory)
#         else:
#             self.__factory = __factory


# class TransformingValueMapping(Mapping[_KT, _TVT_co], Generic[_KT, _VT_co, _TVT_co]):

#     __slots__ = ("__map", "__factory")

#     def __init__(self, __map: Mapping[_KT, _VT_co], __factory=Callable[[_KT], _TKT]) -> None:
#         super().__init__()
#         self.__map = __map
#         self.__factory = __factory
