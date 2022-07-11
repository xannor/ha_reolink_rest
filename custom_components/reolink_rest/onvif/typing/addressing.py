"""Addressing Protocols"""

from typing import Protocol, TypedDict, runtime_checkable


@runtime_checkable
class AttributedURI(Protocol):
    """Attributed URI"""

    _value_1: str


class AttributedURIType(TypedDict):
    """Attributed URI"""

    _value_1: str


class EndpointReference(Protocol):
    """Endpoint Reference Type"""

    Address: str | AttributedURI
    ReferenceProperties: None
    ReferenceParameters: None
    PortType: None
    ServiceName: None


class EndpointReferenceType(TypedDict, total=False):
    """Endpoint Reference Type"""

    Address: str
    ReferenceProperties: None
    ReferenceParameters: None
    PortType: None
    ServiceName: None
