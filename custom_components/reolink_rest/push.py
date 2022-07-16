"""Simple Push Subscription Manager"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import asdict
from datetime import timedelta
import hashlib

import logging
from typing import Final, TypeVar, overload

import secrets

from xml.etree import ElementTree as et

from aiohttp import ClientSession, TCPConnector, client_exceptions
from aiohttp.web import Request


from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.loader import bind_hass
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.storage import Store
from homeassistant.util import dt

from homeassistant.backports.enum import StrEnum

from homeassistant.const import CONF_HOST, CONF_USERNAME, CONF_PASSWORD
import isodate

from async_reolink.api.const import DEFAULT_USERNAME, DEFAULT_PASSWORD

from .const import DOMAIN, OPT_DISCOVERY

from .models import DeviceData, PushSubscription
from .typing import ReolinkDomainData, ReolinkEntryData, WebhookManager

DATA_MANAGER: Final = "push_manager"
DATA_STORE: Final = "push_store"

STORE_VERSION: Final = 1


class _Namespaces(StrEnum):

    SOAP_ENV = "http://www.w3.org/2003/05/soap-envelope"
    WSNT = "http://docs.oasis-open.org/wsn/b-2"
    WSA = "http://www.w3.org/2005/08/addressing"
    WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
    WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
    TT = "http://www.onvif.org/ver10/schema"

    def tag(self, name: str):
        """Create ElementTree tag"""
        return f"{{{self.value}}}{name}"


def _create_envelope(body: et.Element, *headers: et.Element):
    envelope = et.Element(_Namespaces.SOAP_ENV.tag("Envelope"))
    if headers:
        _headers = et.SubElement(envelope, _Namespaces.SOAP_ENV.tag("Header"))
        for header in headers:
            _headers.append(header)
    et.SubElement(envelope, _Namespaces.SOAP_ENV.tag("Body")).append(body)
    return envelope


def _create_wsse(*, username: str, password: str):
    wsse = et.Element(
        _Namespaces.WSSE.tag("Security"),
        {_Namespaces.SOAP_ENV.tag("mustUnderstand"): "true"},
    )
    _token = et.SubElement(wsse, _Namespaces.WSSE.tag("UsernameToken"))
    et.SubElement(_token, _Namespaces.WSSE.tag("Username")).text = username

    created = dt.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    nonce = secrets.token_bytes(16)
    digest = hashlib.sha1()
    digest.update(nonce + created.encode("utf-8") + str(password).encode("utf-8"))
    et.SubElement(
        _token,
        _Namespaces.WSSE.tag("Password"),
        {
            "Type": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest"
        },
    ).text = base64.b64encode(digest.digest()).decode("utf-8")
    et.SubElement(_token, _Namespaces.WSSE.tag("Nonce")).text = base64.b64encode(
        nonce
    ).decode("utf-8")
    et.SubElement(_token, _Namespaces.WSU.tag("Created")).text = created

    return wsse


def _create_subscribe(
    address: str, expires: timedelta = None
) -> tuple[str, list[et.Element], et.Element]:
    subscribe = et.Element(_Namespaces.WSNT.tag("Subscribe"))
    et.SubElement(
        et.SubElement(subscribe, _Namespaces.WSNT.tag("ConsumerReference")),
        _Namespaces.WSA.tag("Address"),
    ).text = address
    if expires is not None:
        et.SubElement(
            subscribe, _Namespaces.WSNT.tag("InitialTerminationTime")
        ).text = isodate.duration_isoformat(expires)
    return (
        "http://docs.oasis-open.org/wsn/bw-2/NotificationProducer/SubscribeRequest",
        [],
        subscribe,
    )


def _create_renew(manager: str, new_expires: timedelta = None):
    _ACTION: Final = (
        "http://docs.oasis-open.org/wsn/bw-2/SubscriptionManager/RenewRequest"
    )
    renew = et.Element(_Namespaces.WSNT.tag("Renew"))
    if new_expires is not None:
        et.SubElement(
            renew, _Namespaces.WSNT.tag("TerminationTime")
        ).text = isodate.duration_isoformat(new_expires)

    headers = [et.Element(_Namespaces.WSA.tag("Action"))]
    headers[0].text = _ACTION
    headers.append(et.Element(_Namespaces.WSA.tag("To")))
    headers[1].text = manager
    return (_ACTION, headers, renew)


def _create_unsubscribe(manager: str):
    _ACTION: Final = (
        "http://docs.oasis-open.org/wsn/bw-2/SubscriptionManager/UnsubscribeRequest"
    )
    unsubscribe = et.Element(_Namespaces.WSNT.tag("Unsubscribe"))

    headers = [et.Element(_Namespaces.WSA.tag("Action"))]
    headers[0].text = _ACTION
    headers.append(et.Element(_Namespaces.WSA.tag("To")))
    headers[1].text = manager
    return (_ACTION, headers, unsubscribe)


EVENT_SERVICE: Final = "/onvif/event_service"

DEFAULT_EXPIRES: Final = timedelta(days=1)

_T = TypeVar("_T")
_VT = TypeVar("_VT")


@overload
def coalesce(*args: _T) -> _T:
    ...


@overload
def coalesce(*args: _T, __default=_VT) -> _T | _VT:
    ...


def coalesce(*args, **kwargs):
    """Coalesce None"""
    _iter = (i for i in args if i is not None)
    if "__default" in kwargs:
        return next(_iter, kwargs["__default"])
    return next(_iter)


def _find(path: str, *elements: et.Element, namespaces: dict[str, str] = None):
    return coalesce(
        *(e.find(path, namespaces) for e in elements if e is not None), __default=None
    )


def _text(*elements: et.Element):
    if not elements:
        return None
    if len(elements) == 1:
        return elements[0].text
    _e = coalesce(*elements)
    if _e is None:
        return None
    return _e.text


def _process_error_response(response: et.Element):
    fault = _find(f".//{_Namespaces.SOAP_ENV.tag('Fault')}", response)
    code = _find(
        _Namespaces.SOAP_ENV.tag("Value"),
        _find(_Namespaces.SOAP_ENV.tag("Code"), fault),
    )
    reason = _find(
        _Namespaces.SOAP_ENV.tag("Text"),
        _find(_Namespaces.SOAP_ENV.tag("Reason"), fault),
    )
    return (_text(code), _text(reason))


class PushManager:
    """Push Manager"""

    def __init__(
        self,
        logger: logging.Logger,
        url: str,
        storage: Store,
        entry_id: str,
    ) -> None:
        self._logger = logger
        self._url = url
        self._storage = storage
        self._subscription: PushSubscription = None
        self._renew_task = None
        self._entry_id = entry_id
        self._motion_interval: timedelta = None

    async def async_start(self):
        """start up manager"""
        data = await self._storage.async_load()
        if isinstance(data, dict) and self._entry_id in data:
            self._subscription = PushSubscription(**data[self._entry_id])
            # if we retrieve a sub we must have crashed so we will
            # "renew" it incase the camera was reset inbetween

        domain_data: ReolinkDomainData = self._storage.hass.data[DOMAIN]
        entry_data = domain_data[self._entry_id]
        await self._subscribe(entry_data)

    async def async_stop(self):
        """shutdown manager"""
        domain_data: ReolinkDomainData = self._storage.hass.data[DOMAIN]
        entry_data = domain_data[self._entry_id]
        await self._unsubscribe(entry_data)

    async def async_reftresh(self):
        """force refresh of manage incase device state changed"""
        domain_data: ReolinkDomainData = self._storage.hass.data[DOMAIN]
        entry_data = domain_data[self._entry_id]
        self._renew(entry_data)

    async def _store_subscription(self):
        data = await self._storage.async_load()
        if self._subscription:
            sub = asdict(self._subscription)
            if "expires" in sub:
                sub["expires"] = isodate.duration_isoformat(sub["expires"])
        else:
            sub = None

        if isinstance(data, dict):
            if not sub and self._entry_id in data:
                data.pop(self._entry_id)
            else:
                data[self._entry_id] = sub
        elif sub:
            data = {self._entry_id: sub}
        if data is not None:
            await self._storage.async_save(data)

    async def _send(self, url: str, headers, data):

        async with ClientSession(connector=TCPConnector(verify_ssl=False)) as client:
            self._logger.debug("sending data")

            headers.setdefault("content-type", "application/soap+xml;charset=UTF-8")
            async with client.post(
                url, data=data, headers=headers, allow_redirects=False
            ) as response:
                if "xml" not in response.content_type:
                    self._logger.warning("bad response")
                    return None

                text = await response.text()
                return (response.status, et.fromstring(text))

    def _get_onvif_base(self, config_entry: ConfigEntry, device_data: DeviceData):
        discovery: dict = config_entry.options.get(OPT_DISCOVERY, {})
        host = config_entry.data.get(CONF_HOST, discovery.get("ip", None))
        return f"http://{host}:{device_data.ports['onvifPort']}"

    def _get_service_url(self, config_entry: ConfigEntry, device_data: DeviceData):
        return self._get_onvif_base(config_entry, device_data) + EVENT_SERVICE

    def _get_wsse(self, config_entry: ConfigEntry):
        return _create_wsse(
            username=config_entry.data.get(CONF_USERNAME, DEFAULT_USERNAME),
            password=config_entry.data.get(CONF_PASSWORD, DEFAULT_PASSWORD),
        )

    def _handle_failed_subscription(
        self,
        coordinator: DataUpdateCoordinator,
        entry_data: ReolinkEntryData,
        save: bool,
    ):
        def _retry():
            cleanup()
            self._storage.hass.create_task(self._subscribe(entry_data, save))

        cleanup = coordinator.async_add_listener(_retry)

        if self._motion_interval:
            entry_data["motion_coordinator"].update_interval = self._motion_interval
            self._motion_interval = None

    async def _subscribe(self, entry_data: ReolinkEntryData, save: bool = True):
        await self._unsubscribe(entry_data, False)

        coordinator = entry_data["coordinator"]

        wsse = self._get_wsse(coordinator.config_entry)
        message = _create_subscribe(self._url)

        headers = {"action": message[0]}

        data = _create_envelope(message[2], wsse, *message[1])
        response = None
        try:
            response = await self._send(
                self._get_service_url(coordinator.config_entry, coordinator.data),
                headers,
                et.tostring(data),
            )
        except client_exceptions.ServerDisconnectedError:
            raise
        if response is None:
            return

        status, response = response
        if status != 200:
            (code, reason) = _process_error_response(response)

            # error respons is kinda useless so we just assume
            self._logger.warning(
                f"Camera ({coordinator.data.device_info['name']}) refused subscription request, probably needs a reboot."
            )
            self._handle_failed_subscription(coordinator, entry_data, save)
            return

        response = response.find(f".//{_Namespaces.WSNT.tag('SubscribeResponse')}")
        await self._process_subscription(response, entry_data, save)

    async def _process_subscription(
        self, response: et.Element, entry_data: ReolinkEntryData, save: bool = True
    ):
        reference = _find(_Namespaces.WSNT.tag("SubscriptionReference"), response)
        reference = _text(_find(_Namespaces.WSA.tag("Address"), reference), reference)
        time = _text(_find(_Namespaces.WSNT.tag("CurrentTime"), response))
        expires = _text(_find(_Namespaces.WSNT.tag("TerminationTime"), response))
        if not reference or not time:
            return

        if not (reference and time):
            return

        # we trim of the device info incase that changes before we renew or unsub
        idx = reference.index("://")
        idx = reference.index("/", idx + 3)
        reference = reference[idx:]

        time = dt.parse_datetime(time)
        if not time:
            return
        expires = dt.parse_datetime(expires) if expires else None
        expires = expires - time if expires else None

        self._subscription = PushSubscription(reference, time, expires)
        if self._motion_interval is None:
            coordinator = entry_data["motion_coordinator"]
            self._motion_interval = coordinator.update_interval
            coordinator.update_interval = None

        self._schedule_renew(entry_data, asyncio.get_event_loop())

        if save:
            await self._store_subscription()

    async def _renew(self, entry_data: ReolinkEntryData, save: bool = True):
        sub = self._subscription
        if sub and not sub.expires:
            return

        coordinator = entry_data["coordinator"]

        if sub and sub.expires:
            data = coordinator.data
            camera_now = dt.utcnow() + data.drift
            expires = sub.timestamp + sub.expires
            if (expires - camera_now).total_seconds() < 1:
                return await self._subscribe(entry_data, save)

        if not sub:
            return await self._subscribe(entry_data, save)

        url = (
            self._get_onvif_base(coordinator.config_entry, coordinator.data)
            + sub.manager_url
        )

        wsse = self._get_wsse(coordinator.config_entry)
        message = _create_renew(url)

        headers = {"action": message[0]}
        data = _create_envelope(message[2], wsse, *message[1])
        response = await self._send(
            self._get_service_url(coordinator.config_entry, coordinator.data),
            headers,
            et.tostring(data),
        )
        if not response:
            return

        status, response = response
        if status != 200:
            (code, reason) = _process_error_response(response)

            # error respons is kinda useless so we just assume
            self._logger.warning(
                f"Camera ({coordinator.data.device_info['name']}) refused subscription renewal, probably was rebooted."
            )
            self._handle_failed_subscription(coordinator, entry_data, save)
            return

        response = response.find(f".//{_Namespaces.WSNT.tag('SubscribeResponse')}")
        await self._process_subscription(response, entry_data, save)

    async def _unsubscribe(self, entry_data: ReolinkEntryData, save: bool = True):
        sub = self._subscription
        if not sub:
            return

        self._cancel_renew()

        coordinator = entry_data["coordinator"]

        send = True
        if sub.expires:
            data = coordinator.data
            camera_now = dt.utcnow() + data.drift
            expires = sub.timestamp + sub.expires
            send = (expires - camera_now).total_seconds() > 1

        # no need to unsubscribe an expiring/expired subscription
        if send:
            url = self._get_onvif_base(coordinator.config_entry, coordinator.data)
            url += sub.manager_url

            wsse = self._get_wsse(coordinator.config_entry)
            message = _create_unsubscribe(url)

            headers = {"action": message[0]}
            data = _create_envelope(message[2], wsse, *message[2])
            response = None
            try:
                response = await self._send(
                    self._get_service_url(coordinator.config_entry, coordinator.data),
                    headers,
                    et.tostring(data),
                )
            except client_exceptions.ServerDisconnectedError:
                # this could mean our subscription is invalid for now log and ignore
                self._logger.warning(
                    "Got disconnected on attempt to unsubscribe, assuming invalid subscription"
                )

            if response is None:
                return

            status, response = response
            if status != 200:
                (code, reason) = _process_error_response(response)
                self._logger.warning("bad response")

        self._subscription = None
        if save:
            await self._store_subscription()

    def _cancel_renew(self):
        if self._renew_task and not self._renew_task.cancelled():
            self._renew_task.cancel()
        self._renew_task = None

    def _schedule_renew(
        self, entry_data: ReolinkEntryData, loop: asyncio.AbstractEventLoop
    ):
        self._cancel_renew()
        sub = self._subscription
        if not sub or not sub.expires:
            return

        data = entry_data["coordinator"].data
        camera_now = dt.utcnow() + data.drift
        expires = sub.timestamp + sub.expires
        delay = max((expires - camera_now).total_seconds(), 0)

        def _task():
            loop.create_task(self._renew(entry_data))

        self._renew_task = loop.call_later(delay, _task)


async def async_parse_notification(request: Request):
    """Push Motion Event Handler"""

    if "xml" not in request.content_type:
        return None

    text = await request.text()
    env = et.fromstring(text)
    if env is None or env.tag != f"{{{_Namespaces.SOAP_ENV}}}Envelope":
        return None

    notify = env.find(f".//{{{_Namespaces.WSNT}}}Notify")
    if notify is None:
        return None

    data = notify.find(f".//{{{_Namespaces.TT}}}Data")
    if data is None:
        return None

    motion = data.find(f'{{{_Namespaces.TT}}}SimpleItem[@Name="IsMotion"][@Value]')
    if motion is None:
        return None

    return motion.attrib["Value"]


@callback
@bind_hass
def async_get_push_manager(
    hass: HomeAssistant,
    logger: logging.Logger,
    entry: ConfigEntry,
    webhook: WebhookManager,
) -> PushManager:
    """Get Push Manager"""

    domain_data: dict = hass.data[DOMAIN]
    entry_data: dict = domain_data[entry.entry_id]

    if DATA_MANAGER in entry_data:
        return entry_data[DATA_MANAGER]

    storage: Store = domain_data.setdefault(
        DATA_STORE, Store(hass, STORE_VERSION, f"{DOMAIN}.push_subs")
    )

    entry_data[DATA_MANAGER] = manager = PushManager(
        logger, webhook.url, storage, entry.entry_id
    )

    def _unload():
        hass.create_task(manager.async_stop())

    entry.async_on_unload(_unload)
    hass.create_task(manager.async_start())

    return manager
