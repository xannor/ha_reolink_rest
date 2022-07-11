"""Onvif motion support"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, fields
import logging
import os
from time import time
from typing import TYPE_CHECKING, Final, cast, ForwardRef
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt

from homeassistant.const import CONF_HOST, CONF_USERNAME, CONF_PASSWORD

from reolinkapi.const import DEFAULT_USERNAME, DEFAULT_PASSWORD

from .models import Subscription

from ..entity import ReolinkDomainData

from ..const import DATA_COORDINATOR, DOMAIN, OPT_DISCOVERY

import onvif

TIMESTAMP_OFFSET: Final[timedelta] = timedelta(seconds=10)


class Onvif:
    """ONVIF Manager"""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        entry: ConfigEntry,
        subscription: Subscription | None = None,
    ) -> None:
        self._logger = logger
        domain_data: ReolinkDomainData = hass.data[DOMAIN]
        entity_data = domain_data[entry.entry_id]
        self._coordinator = entity_data[DATA_COORDINATOR]
        self._camera = None
        self._renewal = None
        self._subscription = None
        if subscription:
            if subscription.manager_url and subscription.timestamp:
                if (
                    subscription.expires.timestamp()
                    >= time() + TIMESTAMP_OFFSET.total_seconds()
                ):
                    self._subscription = subscription
                    window = subscription.expires - subscription.timestamp

                    self._schedule_renewal(
                        asyncio.get_event_loop(), window - TIMESTAMP_OFFSET
                    )

    @property
    def supported(self):
        """is supported"""
        return (
            onvif is not None
            and self._coordinator.data.abilities.onvif.value
            and bool(self._coordinator.data.ports["onvifPort"])
        )

    @property
    def connected(self):
        """is connected"""
        return self._camera is not None

    @property
    def subscription(self):
        """current subscription"""
        return self._subscription

    async def _connect(self):
        if self._camera is not None:
            return self._camera
        host = self._coordinator.config_entry.data.get(CONF_HOST, None)
        if not host:
            host = self._coordinator.config_entry.options[OPT_DISCOVERY]["ip"]

        self._camera = onvif.ONVIFCamera(
            host,
            self._coordinator.data.ports["onvifPort"],
            self._coordinator.config_entry.data.get(CONF_USERNAME, DEFAULT_USERNAME),
            self._coordinator.config_entry.data.get(CONF_PASSWORD, DEFAULT_PASSWORD),
            f"{os.path.dirname(onvif.__file__)}/wsdl/",
            no_cache=True,
        )
        await self._camera.update_xaddrs()
        return self._camera

    async def async_connect(self):
        """Open connection to onvif service"""
        if not onvif:
            return False
        if self.connected:
            return True
        return await self._connect() is not None

    async def _disconnect(self):
        if self._camera is None:
            return False
        await self._camera.close()
        self._camera = None
        return True

    async def async_disconnect(self):
        """Cleanup resources and close connection"""
        await self._unsubcribe()
        await self._disconnect()

    def _cancel_renewal(self):
        if self._renewal is None:
            return
        if not self._renewal.cancelled:
            self._renewal.cancel()
        self._renewal = None

    def _schedule_renewal(self, loop: asyncio.AbstractEventLoop, when: timedelta):
        self._cancel_renewal()
        delay = (datetime.now() + when).timestamp() - time()

        def _task():
            loop.create_task(self._renew())

        self._renewal = loop.call_later(delay, _task)

    async def _subscribe(self, url: str):
        _dc = not self.connected
        camera: TypedONVIFCamera = await self._connect()

        await self._unsubcribe()

        notifications = camera.create_notification_service()
        params = NotificationSubscribeRequestType(
            ConsumerReference=EndpointReferenceType(Address=url)
        )

        response = None
        # with suppress(SERVICE_ERRORS):
        response = await notifications.Subscribe(params)

        if response is None:
            self._logger.warning("Could not get subscription from camera")
            return

        ref = response.SubscriptionReference
        if ref is None:
            pass

        addr = ref.Address
        if isinstance(addr, AttributedURI):
            manager_url = addr._value_1  # pylint: disable=protected-access
        else:
            manager_url = addr

        timestamp = dt.as_utc(response.CurrentTime)
        diff_time = dt.utcnow() - timestamp
        timestamp += diff_time
        expires = response.TerminationTime
        if expires is not None:
            expires = dt.as_utc(expires) + diff_time
            window = expires - timestamp
            self._schedule_renewal(
                asyncio.get_event_loop(), window - timedelta(seconds=10)
            )
        token = f"{self._camera.host}:{self._camera.port}"
        idx = manager_url.index(token)
        manager_url = manager_url[idx + len(token) :]
        self._subscription = Subscription(manager_url, timestamp, expires)

        if _dc:
            await self._disconnect()

    async def _renew(self):
        sub = self._subscription
        if sub is None:
            return False

        self._cancel_renewal()

        _dc = not self.connected
        camera = await self._connect()
        typed_camera: TypedONVIFCamera = camera

        if self._camera.host.startswith("http://") or self._camera.host.startswith(
            "https://"
        ):
            url = self._camera.host
        else:
            url = f"http://{self._camera.host}"
        url = f"{url}:{self._camera.port}{sub.manager_url}"
        camera.xaddrs[
            "http://docs.oasis-open.org/wsn/bw-2/NotificationSubscription"
        ] = url
        manager: SubscriptionManager = typed_camera.create_subscription_service(
            "NotificationSubscription"
        )

        params = None
        # if sub.expires is not None:
        #    params.TerminationTime = sub.expires - sub.timestamp
        # response = None
        with suppress(SERVICE_ERRORS):
            response = await manager.Renew(params)

        if not response:
            self._logger.warning("Could not renew subscription with camera")
            return False

        timestamp = dt.as_utc(response.CurrentTime)
        diff_time = dt.utcnow() - timestamp
        timestamp += diff_time
        expires = response.TerminationTime
        if expires is not None:
            expires = dt.as_utc(expires) + diff_time

        if expires is not None and expires == sub.expires:
            expires = timestamp + (sub.expires - sub.timestamp)
        if expires is not None:
            window = expires - timestamp
            self._schedule_renewal(asyncio.get_event_loop(), window - 10)

        self._subscription = Subscription(sub.manager_url, timestamp, expires)

        if _dc:
            await self._disconnect()

    async def _unsubcribe(self):
        sub = self._subscription
        if sub is None:
            return True

        self._cancel_renewal()

        _dc = not self.connected
        camera = await self._connect()
        typed_camera: TypedONVIFCamera = camera

        if self._camera.host.startswith("http://") or self._camera.host.startswith(
            "https://"
        ):
            url = self._camera.host
        else:
            url = f"http://{self._camera.host}"
        url = f"{url}:{self._camera.port}{sub.manager_url}"
        # camera.xaddrs[
        #    "http://www.onvif.org/ver10/events/wsdl/NotificationSubscription"
        # ] = url
        manager: SubscriptionManager = typed_camera.create_subscription_service(
            # "NotificationSubscription"
        )

        response = None
        try:
            with suppress(SERVICE_ERRORS):
                response = await manager.Unsubscribe(
                    {"_soapheaders": {"_raw_elements": f"<To>{url}</To>"}}
                )

        except Exception as e:
            pass

        self._subscription = None

        if _dc:
            await self._disconnect()

        return True

    async def async_register_notify(self, url: str):
        """Register callback url for notifications"""
        await self._subscribe(url)

        async def _unsubscribe():
            await self._unsubcribe()

        return _unsubscribe
