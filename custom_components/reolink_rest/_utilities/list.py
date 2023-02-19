"""List utilities"""

from typing import Callable, Iterable, Sequence, TypeGuard, TypeVar, overload

_P = TypeVar("_P")
_T = TypeVar("_T")


@overload
def partition(
    predicate: Callable[[any], TypeGuard[_P]], __iter: Iterable[_T]
) -> tuple[Sequence[_P], Sequence[_T]]:
    ...


@overload
def partition(predicate: Callable[[any], bool], __iter: Iterable) -> tuple[Sequence, Sequence]:
    ...


def partition(predicate: Callable[[any], bool], __iter: Iterable):
    trues = []
    falses = []
    for _i in __iter:
        if predicate(_i):
            trues.append(_i)
        else:
            falses.append(_i)

    return trues, falses
