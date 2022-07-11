"""Onvif Typings"""

from typing import Literal, Protocol, overload

from .events import NotificationService, SubscriptionManager


class ONVIFCamera(Protocol):
    """Typed ONFIVCamera"""

    @overload
    def create_notification_service(self) -> NotificationService:
        ...

    @overload
    def create_subscription_service(
        self,
        port_type: Literal["NotificationSubscription"],
    ) -> SubscriptionManager:
        ...
