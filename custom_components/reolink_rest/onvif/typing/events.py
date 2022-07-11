"""Events Protocols"""

from datetime import datetime, timedelta
from typing import Literal, Protocol, TypedDict, overload

from .addressing import (
    EndpointReference,
    EndpointReferenceType,
)


class Capabilities(Protocol):
    """Event Capabilities"""


class GetServiceCapabilitiesRequest(Protocol):
    """Even Service Capabilities Request Parameters"""


class GetServiceCapabilitiesResponse(Protocol):
    """Event Service Capabilites Response"""

    Capabilities: Capabilities


class EventService(Protocol):
    """Event Service"""

    async def GetServiceCapabilities(
        self, parameters: GetServiceCapabilitiesRequest
    ) -> GetServiceCapabilitiesResponse:
        """Get Service Capabilities"""
        ...


class NotificationSubscribeRequest(Protocol):
    """Subscribe Parameters"""

    ConsumerReference: EndpointReference
    InitialTerminationTime: datetime | timedelta | None


class NotificationSubscribeRequestType(TypedDict, total=False):
    """Subscribe Parameters"""

    ConsumerReference: EndpointReferenceType
    InitialTerminationTime: datetime | timedelta | None


class NotificationSubscribeParamsType(TypedDict):
    """Subscribe Parameters"""

    ConsumerReference: EndpointReferenceType
    InitialTerminationTime: datetime | timedelta | None


class NotificationSubscriptionResponse(Protocol):
    """Subscription Response"""

    SubscriptionReference: EndpointReference
    CurrentTime: datetime
    TerminationTime: datetime | None


class NotificationService(Protocol):
    """Notification Producer"""

    async def Subscribe(
        self,
        parameters: NotificationSubscribeRequest | NotificationSubscribeRequestType,
    ) -> NotificationSubscriptionResponse:
        """Subscribe to producer"""
        ...

    @overload
    def create_type(
        self, create_type: Literal["Subscribe"]
    ) -> NotificationSubscribeRequest:
        """Create Subscribe Type"""
        ...


class SubscriptionRenewRequest(Protocol):
    """Subscription Renwal Parameters"""

    TerminationTime: datetime | timedelta | None


class SubscriptionRenewRequestType(TypedDict, total=False):
    """Subscription Renwal Parameters"""

    TerminationTime: datetime | timedelta | None


class SubscriptionRenewResponse(Protocol):
    """Supscription Renew Response"""

    CurrentTime: datetime
    TerminationTime: datetime | None


class UnsubscribeRequest(Protocol):
    """Subscription Unsubscribe Parameters"""

    To: str


class SubscriptionUnsubscribeResponse(Protocol):
    """Subscription Unsubscribe Response"""


class SubscriptionManager(Protocol):
    """Subscription Manager"""

    async def Renew(
        self, parameters: SubscriptionRenewRequest
    ) -> SubscriptionRenewResponse:
        """Renew subscription"""
        ...

    @overload
    def create_type(self, create_type: Literal["Renew"]) -> SubscriptionRenewRequest:
        """Create Renew Type"""
        ...

    @overload
    def create_type(
        self, create_type: Literal["UnsubscribeRequest"]
    ) -> UnsubscribeRequest:
        """Create Unusbscribe Type"""
        ...

    async def Unsubscribe(
        self, parameters: UnsubscribeRequest
    ) -> SubscriptionUnsubscribeResponse:
        """Unsubscribe"""
        ...
