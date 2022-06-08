"""Utility Methods"""

from inspect import isclass
from attr import fields_dict


def astypeddict(instance: any, _type: type):
    """dataclass as typeddict"""

    cls = type(instance)
    _dict = {}
    if not isclass(cls) or not hasattr(_type, "__annotations__"):
        return _dict
    _fields = fields_dict(cls)

    keys = _type.__annotations__.keys()
    for key in _fields.keys():
        if key not in keys:
            continue
        _dict[key] = getattr(instance, key)
    return _dict
