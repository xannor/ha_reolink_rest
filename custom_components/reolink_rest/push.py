"""Simple Push Subscription Manager"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import asdict, dataclass
from datetime import timedelta, datetime
from enum import Enum
import hashlib

import logging
from typing import Callable, Final, TypeVar, overload

import secrets

from xml.etree import ElementTree as et

from aiohttp import ClientSession, TCPConnector, client_exceptions
from aiohttp.web import Request


from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.storage import Store
from homeassistant.helpers.singleton import singleton
from homeassistant.util import dt

from homeassistant.const import CONF_HOST, CONF_USERNAME, CONF_PASSWORD

from async_reolink.api.const import DEFAULT_USERNAME, DEFAULT_PASSWORD

from .const import DOMAIN, OPT_DISCOVERY

from .typing import EntityData, ReolinkDomainData

DATA_MANAGER: Final = "push_manager"
DATA_STORE: Final = "push_store"

STORE_VERSION: Final = 1

_LOGGER = logging.getLogger(__name__)


class _Namespaces(str, Enum):

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


def _duration_isoformat(value: timedelta):
    if value is None:
        return None
    if value.days:
        res = f"P{value.days}D"
    else:
        res = ""
    if value.seconds or value.microseconds:
        res += "T"
        minutes = value.seconds // 60
        seconds = value.seconds % 60
        if value.microseconds:
            seconds += value.microseconds / 1000
        hours = minutes // 60
        minutes = minutes % 60
        if hours:
            res += f"{hours}H"
        if minutes:
            res += f"{minutes}M"
        if seconds:
            res += f"{seconds}S"
    if not res:
        res = "P0D"

    return res


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
        ).text = _duration_isoformat(expires)
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
        ).text = _duration_isoformat(new_expires)

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

DEFAULT_EXPIRES: Final = timedelta(hours=1)

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
    _e = coalesce(*elements, __default=None)
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


@dataclass(frozen=True)
class PushSubscription:
    """Push Subscription Token"""

    manager_url: str
    timestamp: datetime
    expires: timedelta | None

    def __post_init__(self):
        if self.timestamp and not isinstance(self.timestamp, datetime):
            object.__setattr__(self, "timestamp", dt.parse_datetime(self.timestamp))
        if self.expires and not isinstance(self.expires, timedelta):
            object.__setattr__(self, "expires", dt.parse_duration(self.expires))


class PushManager:
    """Push Manager"""

    def __init__(
        self,
        storage: Store,
    ) -> None:
        self._storage = storage
        self._subscriptions: dict[str, PushSubscription] = None
        self._renew_id = None
        self._next_renewal = None
        self._renew_task = None
        self._on_failure: list[Callable[[str], None]] = []

    async def _ensure_subscriptions(self):
        if self._subscriptions is not None:
            return

        data = await self._storage.async_load()
        if isinstance(data, dict):
            self._subscriptions = {
                _k: PushSubscription(**_v) for _k, _v, in data.items()
            }
        else:
            self._subscriptions = {}

    async def _save_subscriptions(self):
        def _fix_expires(sub: dict):
            if "expires" in sub:
                sub["expires"] = _duration_isoformat(sub["expires"])
            return sub

        data = {_k: _fix_expires(asdict(_v)) for _k, _v in self._subscriptions.items()}
        await self._storage.async_save(data)

    async def _send(self, url: str, headers, data):

        async with ClientSession(connector=TCPConnector(verify_ssl=False)) as client:
            _LOGGER.debug("%s->%r", url, data)

            headers.setdefault("content-type", "application/soap+xml;charset=UTF-8")
            async with client.post(
                url, data=data, headers=headers, allow_redirects=False
            ) as response:
                if "xml" not in response.content_type:
                    _LOGGER.warning("bad response")
                    return None

                text = await response.text()
                _LOGGER.debug("%s<-%r, %r", url, response.status, text)
                return (response.status, et.fromstring(text))

    def _get_onvif_base(self, config_entry: ConfigEntry, device_data: EntityData):
        if not device_data.ports.onvif.enabled:
            return None
        discovery: dict = config_entry.options.get(OPT_DISCOVERY, {})
        host = config_entry.data.get(CONF_HOST, discovery.get("ip", None))
        return f"http://{host}:{device_data.ports.onvif.value}"

    def _get_service_url(self, config_entry: ConfigEntry, device_data: EntityData):
        base = self._get_onvif_base(config_entry, device_data)
        if base is None:
            return None
        return base + EVENT_SERVICE

    def _get_wsse(self, config_entry: ConfigEntry):
        return _create_wsse(
            username=config_entry.data.get(CONF_USERNAME, DEFAULT_USERNAME),
            password=config_entry.data.get(CONF_PASSWORD, DEFAULT_PASSWORD),
        )

    def _handle_failed_subscription(
        self,
        url: str,
        entry_id: str,
        save: bool,
    ):
        # def _retry():
        #    cleanup()
        #    self._storage.hass.create_task(self._subscribe(url, entry_id, save))

        # domain_data: ReolinkDomainData = self._storage.hass.data[DOMAIN]
        # entry_data = domain_data[entry_id]
        # coordinator = entry_data["coordinator"]
        # attempt resubscribe on next coordinator retrieval success
        # cleanup = coordinator.async_add_listener(_retry)

        for handler in self._on_failure:
            handler(entry_id)

    def _cancel_renew(self):
        if self._renew_task and not self._renew_task.cancelled():
            self._renew_task.cancel()
        self._renew_task = None

    def _schedule_next_renew(
        self, loop: asyncio.AbstractEventLoop, entry_id: str | None = None
    ):
        sub = None
        if entry_id is not None:
            sub = self._subscriptions[entry_id]
        else:
            expires = None
            for _id, _sub in self._subscriptions.items():
                if _sub.expires is not None and (
                    expires is None or _sub.expires < expires
                ):
                    expires = _sub.expires
                    sub = _sub
                    entry_id = _id
                    break

        if sub is None or sub.expires is None:
            return
        domain_data: ReolinkDomainData = self._storage.hass.data[DOMAIN]
        entry_data = domain_data.get(entry_id, None)
        if entry_data is None:
            # entry was removed so we need to bail
            self._cancel_renew()
            return
        expires = (
            sub.timestamp + sub.expires + entry_data["coordinator"].data.time_difference
        )

        if self._next_renewal is not None and expires > self._next_renewal:
            return
        self._cancel_renew()

        delay = max((expires - dt.utcnow()).total_seconds(), 0)

        def _task():
            loop.create_task(self._renew(entry_id))

        self._renew_id = entry_id
        self._renew_task = loop.call_later(delay, _task)

    async def _process_subscription(
        self,
        response: et.Element,
        reference: str | None,
        entry_id: str,
        save: bool = True,
    ):
        if reference is None:
            reference = _find(_Namespaces.WSNT.tag("SubscriptionReference"), response)
            reference = _text(
                _find(_Namespaces.WSA.tag("Address"), reference), reference
            )
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

        sub = PushSubscription(reference, time, expires)
        self._subscriptions[entry_id] = sub

        self._schedule_next_renew(asyncio.get_event_loop(), entry_id)

        if save:
            await self._save_subscriptions()
        return sub

    async def _subscribe(
        self,
        url: str,
        entry_id: str,
        save: bool = True,
    ):
        config_entry = self._storage.hass.config_entries.async_get_entry(entry_id)

        wsse = self._get_wsse(config_entry)
        message = _create_subscribe(url, DEFAULT_EXPIRES)

        headers = {"action": message[0]}

        data = _create_envelope(message[2], wsse, *message[1])
        domain_data: ReolinkDomainData = self._storage.hass.data[DOMAIN]
        entry_data = domain_data[entry_id]
        entity_data = entry_data["coordinator"].data
        service_url = self._get_service_url(config_entry, entity_data)
        response = None
        if service_url is not None:
            try:
                response = await self._send(
                    service_url,
                    headers,
                    et.tostring(data),
                )
            except client_exceptions.ServerDisconnectedError:
                raise
        if response is None:
            self._handle_failed_subscription(url, entry_id, save)
            return None

        status, response = response
        if status != 200:
            (code, reason) = _process_error_response(response)

            # error respons is kinda useless so we just assume
            _LOGGER.warning(
                "Camera (%s) refused subscription request, probably needs a reboot.",
                entity_data.device_info.name,
            )
            self._handle_failed_subscription(url, entry_id, save)
            return None

        response = response.find(f".//{_Namespaces.WSNT.tag('SubscribeResponse')}")
        return await self._process_subscription(response, None, entry_id, save)

    async def _renew(
        self,
        entry_id: str,
        url: str | None = None,
        save: bool = True,
    ):
        sub = self._subscriptions.get(entry_id, None)
        if sub is None:
            return

        domain_data: ReolinkDomainData = self._storage.hass.data[DOMAIN]
        entry_data = domain_data[entry_id]
        coordinator = entry_data["coordinator"]
        manager_url = self._get_onvif_base(coordinator.config_entry, coordinator.data)
        if manager_url is None:
            return None
        manager_url += sub.manager_url

        if sub.expires:
            if url is not None:
                data = coordinator.data
                camera_now = dt.utcnow() + data.time_difference
                expires = sub.timestamp + sub.expires
                if (expires - camera_now).total_seconds() < 2:
                    return await self._subscribe(url, entry_id, save)
                return await self._unsubscribe(entry_id, save)
            return None

        wsse = self._get_wsse(coordinator.config_entry)
        message = _create_renew(manager_url, sub.expires)

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
            _LOGGER.warning(
                "Camera (%s) refused subscription renewal, probably was rebooted.",
                coordinator.data.device_info.name,
            )
            if url is not None:
                return await self._subscribe(url, entry_id, save)
            self._handle_failed_subscription(url, entry_id, save)
            return None

        response = response.find(f".//{_Namespaces.WSNT.tag('RenewResponse')}")
        return await self._process_subscription(response, manager_url, entry_id, save)

    async def _unsubscribe(self, entry_id: str, save: bool = True):
        if entry_id == self._renew_id:
            self._cancel_renew()
            self._schedule_next_renew(asyncio.get_event_loop())

        sub = self._subscriptions.pop(entry_id, None)
        if sub is None:
            return

        domain_data: ReolinkDomainData = self._storage.hass.data[DOMAIN]
        entry_data = domain_data[entry_id]
        coordinator = entry_data["coordinator"]

        send = True
        if sub.expires:
            data = coordinator.data
            camera_now = dt.utcnow() + data.time_difference
            expires = sub.timestamp + sub.expires
            send = (expires - camera_now).total_seconds() < 1

        # no need to unsubscribe an expiring/expired subscription
        if send:
            url = self._get_onvif_base(coordinator.config_entry, coordinator.data)
            if url is None:
                send = False

        if send:
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
                _LOGGER.warning(
                    "Got disconnected on attempt to unsubscribe, assuming invalid subscription"
                )

            if response is None:
                return

            status, response = response
            if status != 200:
                (code, reason) = _process_error_response(response)
                _LOGGER.warning("bad response")

        if save:
            await self._save_subscriptions()

    async def async_subscribe(self, url: str, config_entry: ConfigEntry):
        """Subcribe"""
        await self._ensure_subscriptions()

        if config_entry.entry_id in self._subscriptions:
            return await self._renew(config_entry.entry_id, url)
        return await self._subscribe(url, config_entry.entry_id)

    async def async_unsubscribe(self, subscription: PushSubscription):
        """Unsubscribe"""
        await self._ensure_subscriptions()
        entry_id = next(
            (
                entry_id
                for (entry_id, sub) in self._subscriptions.items()
                if sub == subscription
            ),
            None,
        )
        if entry_id is None:
            return False
        await self._unsubscribe(entry_id)
        return True

    def async_on_subscription_failure(self, callback: Callable[[str], None]):
        self._on_failure.append(callback)

        def _remove():
            self._on_failure.remove(callback)

        return _remove


async def async_parse_notification(request: Request):
    """Push Motion Event Handler"""

    if "xml" not in request.content_type:
        return None

    text = await request.text()
    _LOGGER.debug("processing notification<-%r", text)
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

    return motion.attrib["Value"][0:1].lower() == "t"


@singleton(f"{DOMAIN}-push-manager")
@callback
def async_get_push_manager(
    hass: HomeAssistant,
) -> PushManager:
    """Get Push Manager"""

    storage = Store(hass, STORE_VERSION, f"{DOMAIN}_push")
    manager = PushManager(storage)
    return manager
