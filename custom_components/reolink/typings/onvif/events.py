"""Events WSDL typings"""

# pragma pylint: disable=invalid-name

from __future__ import annotations
from datetime import datetime, timedelta

from typing import Protocol

from .addressing import EndpointReferenceType


class Capabilities(Protocol):
    """Event Service Capabilities"""


class GetServiceCapabilitiesParams(Protocol):
    """Even Service Capabilities Request Parameters"""


class GetServiceCapabilitiesResponse(Protocol):
    """Event Service Capabilites Response"""

    Capabilities: Capabilities


class EventService(Protocol):
    """Event Service"""

    async def GetServiceCapabilities(
        self, parameters: GetServiceCapabilitiesParams
    ) -> GetServiceCapabilitiesResponse:
        """Get Service Capabilities"""
        ...


class NotificationSubscribeParams(Protocol):
    """Subscribe Parameters"""

    ConsumerReference: EndpointReferenceType
    InitialTerminationTime: datetime | timedelta | None


class NotificationSubscriptionResponse(Protocol):
    """Subscription Response"""

    SubscriptionReference: EndpointReferenceType
    CurrentTime: datetime
    TerminationTime: datetime | None


class NotificationService(Protocol):
    """Notification Producer"""

    async def Subscribe(
        self, parameters: NotificationSubscribeParams
    ) -> NotificationSubscriptionResponse:
        """Subscribe to producer"""
        ...


class SubscriptionRenewParams(Protocol):
    """Subscription Renwal Parameters"""

    To: str
    TerminationTime: datetime | timedelta | None


class SubscriptionRenewResponse(Protocol):
    """Supscription Renew Response"""

    CurrentTime: datetime
    TerminationTime: datetime | None


class SubscriptionUnsubscribeParams(Protocol):
    """Subscription Unsubscribe Parameters"""

    To: str


class SubscriptionUnsubscribeResponse(Protocol):
    """Subscription Unsubscribe Response"""


class SubscriptionManager(Protocol):
    """Subscription Manager"""

    async def Renew(
        self, parameters: SubscriptionRenewParams
    ) -> SubscriptionRenewResponse:
        """Renew subscription"""
        ...

    async def Unsubscribe(
        self, parameters: SubscriptionUnsubscribeParams
    ) -> SubscriptionUnsubscribeResponse:
        """Unsubscribe"""
        ...
