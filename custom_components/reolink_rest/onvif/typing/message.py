"""Messages Protocols"""

from typing import Protocol

from .addressing import EndpointReference


class Message(Protocol):
    """Message"""

    # TODO : figure this out


class NotificationMessage(Protocol):
    """Notification Message"""

    SubscriptionReference: EndpointReference
    Topic: str
    ProducerReference: EndpointReference
    Message: Message
