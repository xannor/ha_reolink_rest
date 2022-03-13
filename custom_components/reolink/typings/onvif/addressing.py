"""Addressing"""
from __future__ import annotations

from typing import Protocol


class AttributedURI(Protocol):
    """Attributed URI"""

    _value_1: str


class EndpointReferenceType(Protocol):
    """Endpoint Reference Type"""

    Address: str | AttributedURI
    ReferenceProperties: None
    ReferenceParameters: None
    PortType: None
    ServiceName: None
