"""ReoLink ONVIF service"""

from abc import ABC, abstractmethod
import base64
from datetime import datetime, timedelta
import hashlib
import logging
import secrets
from os.path import basename
from time import time
from types import MappingProxyType
from aiohttp.web import Request, Response
from typing import (
    TYPE_CHECKING,
    Callable,
    Final,
    Generic,
    Mapping,
    NamedTuple,
    Sequence,
    TypeVar,
    TypedDict,
)
from typing_extensions import Unpack
from xml.etree import ElementTree as et
from aiohttp import ClientSession, TCPConnector, client_exceptions
from homeassistant.core import HomeAssistant, callback, CALLBACK_TYPE
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import storage
from homeassistant.loader import bind_hass
from homeassistant.util import dt

from homeassistant.backports.enum import StrEnum

from homeassistant.const import CONF_USERNAME, CONF_PASSWORD

from async_reolink.api.const import DEFAULT_USERNAME, DEFAULT_PASSWORD

from ..setups.motion_typing import MotionEvent

from .._utilities.hass_typing import hass_bound

from ..typing import DeviceData, EntryId, DomainDataType

from ..const import DATA_ONVIF, DATA_API, DOMAIN

from .webhook import async_get as async_get_webhook

_LOGGER = logging.getLogger(__name__)

STORE_VERSION: Final = 1


class Error(NamedTuple):
    """Error Response"""

    code: str | None
    reason: str | None


class Subscription(NamedTuple):
    """Push Subscription Token"""

    manager_url: tuple[str, ...]
    timestamp: float
    expires: float | None


def _decode_subscription(
    manager_url: Sequence[str], timestamp: float, expires: float | None = None, **_kwargs: any
):
    return Subscription(tuple(manager_url), timestamp, expires)


def _encode_subscription(value: Subscription):
    return value._asdict()


def _duration_isoformat(value: timedelta):
    if value is None:
        return None
    if value.days:
        res = f"P{value.days}D"
    else:
        res = ""
    if value.seconds or value.microseconds:
        if not res:
            res = "P"
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


def _find(path: str, *elements: et.Element, namespaces: dict[str, str] = None):
    return next((i.find(path, namespaces) for i in elements if i is not None))


def _text(*elements: et.Element):
    return next((i.text for i in elements if i is not None), None)


class _NS(StrEnum):
    SOAP_ENV = "http://www.w3.org/2003/05/soap-envelope"
    WSNT = "http://docs.oasis-open.org/wsn/b-2"
    WSA = "http://www.w3.org/2005/08/addressing"
    WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
    WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
    TT = "http://www.onvif.org/ver10/schema"

    def tag(self, name: str):
        return f"{{{self}}}{name}"

    def Element(self, name: str, attrib: dict[str, str] = None, /, **extra: str):
        if attrib is None:
            return et.Element(self.tag(name), **extra)
        return et.Element(self.tag(name), attrib, **extra)

    def SubElement(
        self, parent: et.Element, name: str, attrib: dict[str, str] = None, /, **extra: str
    ):
        if attrib is None:
            return et.SubElement(parent, self.tag(name), **extra)
        return et.SubElement(parent, self.tag(name), attrib, **extra)


EVENT_SERVICE: Final = "/onvif/event_service"

DEFAULT_EXPIRES: Final = timedelta(hours=1)

_NOTIFICATION_PFX: Final = "/onvif/Notification?Idx=00_"


def _get_onvif_base(hostname: str, device_data: DeviceData):

    if device_data.capabilities.supports.onvif_enable and not device_data.ports.onvif.enabled:
        return None
    return f"http://{hostname}:{device_data.ports.onvif.value}"


def _get_service_url(hostname: str, device_data: DeviceData):
    base = _get_onvif_base(hostname, device_data)
    if base is None:
        return None
    return base + EVENT_SERVICE


async def _send(url: str, headers: dict[str, str], data):
    async with ClientSession(connector=TCPConnector(verify_ssl=False)) as client:
        _LOGGER.debug("%s->%r", url, data)

        headers.setdefault("content-type", "application/soap+xml;charset=UTF-8")
        async with client.post(url, data=data, headers=headers, allow_redirects=False) as response:
            if "xml" not in response.content_type:
                _LOGGER.warning("bad response")
                return None

            text = await response.text()
            _LOGGER.debug("%s<-%r, %r", url, response.status, text)
            return (response.status, et.fromstring(text))


def _process_error_response(response: et.Element):
    fault = _find(f".//{_NS.SOAP_ENV.tag('Fault')}", response)
    code = _find(
        _NS.SOAP_ENV.tag("Value"),
        _find(_NS.SOAP_ENV.tag("Code"), fault),
    )
    reason = _find(
        _NS.SOAP_ENV.tag("Text"),
        _find(_NS.SOAP_ENV.tag("Reason"), fault),
    )
    return Error(_text(code), _text(reason))


def _process_subscription(
    response: et.Element,
    reference: str | None,
):
    if reference is None:
        reference = _find(_NS.WSNT.tag("SubscriptionReference"), response)
        reference = _text(_find(_NS.WSA.tag("Address"), reference), reference)
    time = _text(_find(_NS.WSNT.tag("CurrentTime"), response))
    expires = _text(_find(_NS.WSNT.tag("TerminationTime"), response))
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
    expires = (expires - time).total_seconds() if expires else None

    return Subscription((reference,), time.timestamp(), expires)


class _WSSE_Args(TypedDict, total=False):

    username: str
    password: str


def _create_envelope(body: et.Element, *headers: et.Element):
    envelope = _NS.SOAP_ENV.Element("Envelope")
    if headers:
        _headers = _NS.SOAP_ENV.SubElement(envelope, "Header")
        for header in headers:
            _headers.append(header)
    _NS.SOAP_ENV.SubElement(envelope, "Body").append(body)
    return envelope


_NO_HEADERS: Mapping[str, str] = MappingProxyType({})


class _Envelope(ABC):
    @property
    def headers(self):
        return _NO_HEADERS

    @abstractmethod
    def _create_body(self, **kwargs: any) -> et.Element:
        ...

    def xml_string(self, **kwargs: any):
        return et.tostring(self._create_body(**kwargs))

    @abstractmethod
    def process_response(
        self, result: tuple[int, et.Element], **kwargs: any
    ) -> Error | Subscription | None:
        ...


def _generate_wsse(**config: Unpack[_WSSE_Args]):
    wsse = _NS.WSSE.Element("Security", {_NS.SOAP_ENV.tag("mustUnderstand"): "true"})
    _token = _NS.WSSE.SubElement(wsse, "UsernameToken")
    _NS.WSSE.SubElement(_token, "Username").text = config.get(CONF_USERNAME, DEFAULT_USERNAME)

    created = dt.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    nonce = secrets.token_bytes(16)
    digest = hashlib.sha1()
    digest.update(
        nonce
        + created.encode("utf-8")
        + str(config.get(CONF_PASSWORD, DEFAULT_PASSWORD)).encode("utf-8")
    )
    _NS.WSSE.SubElement(
        _token,
        "Password",
        {
            "Type": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest"
        },
    ).text = base64.b64encode(digest.digest()).decode("utf-8")
    _NS.WSSE.SubElement(_token, "Nonce").text = base64.b64encode(nonce).decode("utf-8")
    _NS.WSU.SubElement(_token, "Created").text = created

    return wsse


class _Message(_Envelope):
    def __init__(self, action: str, headers: list[et.Element], body: et.Element):
        self._body = body
        self._request_headers = {"action": action}
        self._headers = headers

    @property
    def headers(self):
        return MappingProxyType(self._request_headers)

    def _create_body(self, **kwargs: any):
        return _create_envelope(self._body, _generate_wsse(**kwargs), *self._headers)


class _SubscriptionMessage(_Message):
    def process_response(
        self, result: tuple[int, et.Element], /, reference: str | None = None, **kwargs: any
    ):
        status, response = result
        if status != 200:
            return _process_error_response(response)
        return _process_subscription(response, reference)


class _Subscribe(_SubscriptionMessage):

    ACTION: Final = "http://docs.oasis-open.org/wsn/bw-2/NotificationProducer/SubscribeRequest"

    def __init__(self, address: str, expires: timedelta = None) -> None:
        subscribe = _NS.WSNT.Element("Subscribe")
        _NS.WSA.SubElement(
            _NS.WSNT.SubElement(subscribe, "ConsumerReference"),
            "Address",
        ).text = address
        if expires is not None:
            _NS.WSNT.SubElement(subscribe, "InitialTerminationTime").text = _duration_isoformat(
                expires
            )
        super().__init__(self.ACTION, [], subscribe)

    def process_response(
        self, result: tuple[int, et.Element], /, reference: str | None = None, **kwargs: any
    ):
        status, response = result
        if status == 200:
            result = (status, response.find(f".//{_NS.WSNT.tag('SubscribeResponse')}"))
        return super().process_response(result, reference, **kwargs)


class _Renew(_SubscriptionMessage):

    ACTION: Final = "http://docs.oasis-open.org/wsn/bw-2/SubscriptionManager/RenewRequest"

    def __init__(self, manager: str, new_expires: timedelta = None) -> None:
        renew = _NS.WSNT.Element("Renew")
        if new_expires is not None:
            _NS.WSNT.SubElement(renew, "TerminationTime").text = _duration_isoformat(new_expires)

        headers = [_NS.WSA.Element("Action")]
        headers[0].text = self.ACTION
        headers.append(_NS.WSA.Element("To"))
        headers[1].text = manager
        super().__init__(self.ACTION, headers, renew)

    def process_response(
        self, result: tuple[int, et.Element], /, reference: str | None = None, **kwargs: any
    ):
        status, response = result
        if status == 200:
            result = (status, response.find(f".//{_NS.WSNT.tag('RenewResponse')}"))
        return super().process_response(result, reference, **kwargs)


class _UnSubscribe(_Message):
    ACTION: Final = "http://docs.oasis-open.org/wsn/bw-2/SubscriptionManager/UnsubscribeRequest"

    def __init__(self, manager: str) -> None:
        unsubscribe = _NS.WSNT.Element("Unsubscribe")

        headers = [_NS.WSA.Element("Action")]
        headers[0].text = self.ACTION
        headers.append(_NS.WSA.Element("To"))
        headers[1].text = manager
        super().__init__(self.ACTION, headers, unsubscribe)

    def process_response(self, result: tuple[int, et.Element], **kwargs: any):
        return None


_T = TypeVar("_T")


class _NoStore(Generic[_T]):

    __slots__ = ("hass",)

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def async_load(self) -> _T | None:
        return None

    def async_delay_save(self, data_func: Callable[[], _T], delay: float = 0):
        return


class OnvifService:
    """ONVIF Service"""

    __slots__ = (
        "_hook_subs",
        "_listeners",
        "_store",
        "_subs",
        "_sub_load_task",
        "_pending_renewal",
        "_pending_renewal_id",
    )

    def __init__(self, hass: HomeAssistant, track=True) -> None:
        self._hook_subs: dict[EntryId, CALLBACK_TYPE] = {}
        self._listeners: dict[EntryId, list[Callable[[Error | bool | None], None]]] = {}
        if track:
            self._store: storage.Store[dict[EntryId, dict[str, any]]] = storage.Store(
                hass, STORE_VERSION, f"{DOMAIN}_onvif_tokens", True
            )
        else:
            self._store = _NoStore[dict[EntryId, dict[str, any]]](hass)
        self._subs = None
        self._pending_renewal = None
        self._pending_renewal_id = None
        self._sub_load_task = None

    @property
    def _hass(self):
        return self._store.hass

    def _get_api(self, entry_id: EntryId):
        domain_data: DomainDataType
        if (domain_data := self._hass.data.get(DOMAIN)) and (entry_data := domain_data[entry_id]):
            return entry_data.get(DATA_API)
        return None

    def _get_device_time_offset(self, entry_id: EntryId):
        return api.data.time_diff if (api := self._get_api(entry_id)) else None

    def _schedule_renewal(self, entry_id: EntryId | None, no_check=False):
        if not self._subs:
            return
        if not entry_id:
            sub = None

            _time = time() + 2
            for _entry_id, _sub in self._subs.items():
                if _sub.expires is None or _sub.timestamp + _sub.expires <= _time:
                    continue
                if sub is None or (_sub.timestamp + _sub.expires) < (sub.timestamp + sub.expires):
                    entry_id = _entry_id
                    sub = _sub

            if not entry_id or not sub:
                if self._pending_renewal_id:
                    self._pending_renewal_id = None
                if self._pending_renewal:
                    self._pending_renewal.cancel()
                    self._pending_renewal = None

                return
        elif not (sub := self._subs.get(entry_id)) or not sub.expires:
            return

        if not no_check and self._pending_renewal_id:
            if (
                rsub := self._subs.get(self._pending_renewal_id)
            ) and rsub.timestamp + rsub.expires <= sub.timestamp + sub.expires:
                return
        if self._pending_renewal:
            self._pending_renewal.cancel()
        ttl = (sub.timestamp + sub.expires) - time() - 2
        if ttl < 1:
            ttl = 1

        def run_renewal():
            self._pending_renewal = None
            self._hass.create_task(self._renew(entry_id))

        self._pending_renewal_id = entry_id
        self._pending_renewal = self._hass.loop.call_later(ttl, run_renewal)

    async def _load_subs(self):
        subs = await self._store.async_load()
        self._subs: dict[EntryId, Subscription] = {}
        if subs:
            next_id = None
            next_sub: Subscription = None

            _time = time() + 2
            for entry_id, rsub in subs.items():
                sub = _decode_subscription(**rsub)
                ts = sub.timestamp + sub.expires if sub.expires is not None else None
                if ts and ts <= _time:
                    continue
                self._subs[entry_id] = sub
                if sub.expires is None:
                    continue
                if next_sub is None or (sub.timestamp + sub.expires) < (
                    next_sub.timestamp + next_sub.expires
                ):
                    next_id = entry_id
                    next_sub = sub

            if next_id:
                self._schedule_renewal(next_id, True)
        return self._subs

    async def _get_subs(self):
        if self._subs is None or self._sub_load_task is not None:
            if self._sub_load_task is None:
                self._sub_load_task = self._hass.async_create_task(self._load_subs())
                await self._sub_load_task
                self._sub_load_task = None
                return self._subs
            else:
                return await self._sub_load_task
        return self._subs

    def _save_subs(self):
        def save():
            if not self._subs:
                return None
            _time = time() + 2
            return {
                entry_id: _encode_subscription(sub)
                for entry_id, sub in self._subs.items()
                if sub.timestamp + sub.expires >= _time
            }

        self._store.async_delay_save(save, 1)

    async def _send(
        self, entry_id: EntryId, soap: _Message, reference: str | None = None
    ) -> Error | Subscription | None:
        if not (api := self._get_api(entry_id)) or not api.client.connection_id:
            return None

        kwargs = {}
        if reference:
            kwargs["reference"] = reference

        url = _get_service_url(api.client.hostname, api.data)
        data = soap.xml_string(**kwargs)
        _LOGGER.debug("%s->%r", url, data)
        kwargs.update(self._hass.config_entries.async_get_entry(entry_id).data)
        headers = {"content-type": "application/soap+xml;charset=UTF-8"}
        headers.update(soap.headers)
        result = None
        try:
            session = async_get_clientsession(self._hass, False)
            async with session.post(
                url, data=data, headers=headers, allow_redirects=False
            ) as response:
                if "xml" not in response.content_type:
                    _LOGGER.warning("bad response")
                    return None

                text = await response.text()
                _LOGGER.debug("%s<-%r, %r", url, response.status, text)
                result = (response.status, et.fromstring(text))

        except client_exceptions.ServerDisconnectedError:
            # this could mean our subscription is invalid for now log and ignore
            _LOGGER.warning(
                "Got disconnected on attempt to unsubscribe, assuming invalid subscription"
            )

        if result is None:
            return

        return soap.process_response(result)

    async def _subscribed(self, entry_id: EntryId, sub: Subscription, is_new=True):
        if is_new:
            await self._get_subs()
            time_diff = self._get_device_time_offset(entry_id)
            if time_diff is not None:
                sub = Subscription(
                    (sub.manager_url,), sub.timestamp + time_diff.total_seconds(), sub.expires
                )
            self._subs[entry_id] = sub
            self._save_subs()
        self._schedule_renewal(entry_id)
        self._notify(entry_id, None)

    async def _unsubscribed(self, entry_id: EntryId):
        self._listeners.pop(entry_id, None)
        cleanup = self._hook_subs.pop(entry_id, None)
        if cleanup:
            cleanup()
        if self._subs:
            self._subs.pop(entry_id, None)
        if self._pending_renewal_id == entry_id or not self._pending_renewal_id:
            self._schedule_renewal(None, True)
        self._save_subs()

    async def _subscribe(self, entry_id: EntryId, count=1):
        if entry_id in (await self._get_subs()):
            _LOGGER.debug("Found existing subscription")
            self._notify(entry_id, None)
            return
        webhooks = async_get_webhook(self._hass)
        url = webhooks.async_get_url(self._hook_subs[entry_id])
        sub = None
        for _ in range(count):
            response = await self._send(entry_id, _Subscribe(url, DEFAULT_EXPIRES))
            if not response:
                _LOGGER.warning("Got no response from onvif subscription to %s", entry_id)
                return await self._unsubscribed(entry_id)
            if isinstance(response, Error):
                self._notify(entry_id, response)
                return await self._unsubscribed(entry_id)
            if sub is None:
                sub = response
            else:
                sub = Subscription(
                    sub.manager_url + response.manager_url, sub.timestamp, sub.expires
                )
        if not sub:
            return
        return await self._subscribed(entry_id, sub)

    async def _flush_and_subscribe(self, entry_id: EntryId, count=1):
        await self.async_flush_subscriptions(entry_id, count)
        await self._subscribe(entry_id, count)

    async def _renew(self, entry_id: EntryId):
        if not (sub := (await self._get_subs()).get(entry_id)):
            return await self._subscribe(entry_id)
        if not (api := self._get_api(entry_id)):
            del self._subs[entry_id]
            if self._pending_renewal_id == entry_id:
                self._schedule_renewal(None, True)
            self._notify(entry_id, Error("500", "Entry not loaded"))
            return

        manager = _get_service_url(api.client.hostname, api.data)
        renewal = None
        for reference in sub.manager_url:
            response = await self._send(
                entry_id, _Renew(manager + reference, DEFAULT_EXPIRES), manager
            )
            if not response:
                _LOGGER.warning("Got no response from onvif subscription to %s", entry_id)
                await self._send(entry_id, _UnSubscribe(manager))
                return await self._unsubscribed(entry_id)
            if isinstance(response, Error):
                await self._send(entry_id, _UnSubscribe(manager))
                return await self._subscribe(entry_id)
            if renewal is None:
                renewal = response
            else:
                renewal = Subscription(
                    renewal.manager_url + response.manager_url, renewal.timestamp, renewal.expires
                )
        if not renewal:
            return
        return await self._subscribed(entry_id, renewal)

    async def _unsubscribe(self, entry_id: EntryId):
        sub = (await self._get_subs()).pop(entry_id, None)
        if not sub:
            return
        if api := self._get_api(entry_id):
            manager = _get_service_url(api.client.hostname, api.data)
            for reference in sub.manager_url:
                await self._send(entry_id, _UnSubscribe(manager + reference))
        return await self._unsubscribed(entry_id)

    async def async_flush_subscriptions(self, entry_id: EntryId, count=3):
        sub = (await self._get_subs()).pop(entry_id, None)
        if api := self._get_api(entry_id):
            manager = _get_service_url(api.client.hostname, api.data)
            for i in range(count):
                notification = _NOTIFICATION_PFX + str(i)
                if sub and sub.manager_url == notification:
                    sub = None
                await self._send(entry_id, _UnSubscribe(manager + notification))
            if sub:
                await self._send(entry_id, manager + sub.manager_url)

    def _ensure_hook(self, entry_id: EntryId):
        if entry_id not in self._hook_subs:

            @callback
            def can_handle_webhook(request: Request):
                if "xml" in request.content_type and request.body_exists and request.can_read_body:
                    return True
                return False

            async def handle_webhook(request: Request):
                return await self._handle_webhook(entry_id, request)

            service = async_get_webhook(self._hass)
            self._hook_subs[entry_id] = service.async_listen(
                entry_id,
                handle_webhook,
                can_handle_webhook,
                key="onvif",
                local_only=True,
            )

    @callback
    def async_subscribe(
        self, entry_id: EntryId, handler: Callable[[Error | MotionEvent | None], None]
    ) -> CALLBACK_TYPE:
        if entry_id not in self._hook_subs:
            self._ensure_hook(entry_id)
            if isinstance(self._store, _NoStore):
                self._hass.create_task(self._flush_and_subscribe(entry_id))
            else:
                self._hass.create_task(self._subscribe(entry_id))

        listeners = self._listeners.setdefault(entry_id, [])
        listeners.append(handler)

        def unsubscribe():
            try:
                listeners.remove(handler)
            except ValueError:
                _LOGGER.exception("Unable to remove unknown onvif listener %s", handler)

            if not listeners:
                self._hass.create_task(self._unsubscribe(entry_id))

        return unsubscribe

    def _notify(self, entry_id: EntryId, value: Error | MotionEvent | None):
        if listeners := self._listeners.get(entry_id):
            for listener in listeners:
                try:
                    listener(value)
                except Exception:
                    _LOGGER.exception("Error executing onvif callback: %s", listener)
                    continue

    async def _handle_webhook(self, entry_id: EntryId, request: Request):
        text = await request.text()
        _LOGGER.debug("processing notification<-%r", text)

        try:
            root = et.fromstring(text)
        except Exception:
            return Response(status=500, reason="Invalid Message Format")

        event = MotionEvent()

        for message in root.iter(_NS.WSNT.tag("NotificationMessage")):
            if (
                topic := message.find(
                    _NS.WSNT.tag(
                        "Topic[@Dialect='http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet']"
                    )
                )
            ) is None or not topic.text:
                continue
            if not (rule := basename(topic.text)):
                continue

            channel = None
            if (
                (source := message.find(".//" + _NS.TT.tag("SimpleItem[@Name='Source']")))
                is not None
                or (
                    source := message.find(
                        ".//" + _NS.TT.tag("SimpleItem[@Name='VideoSourceConfigurationToken']")
                    )
                )
                is not None
                and "Value" in source.attrib
            ):
                try:
                    channel = int(source.attrib["Value"])
                except ValueError:
                    pass

            key = "IsMotion" if rule == "Motion" else "State"
            if (
                data := message.find(".//" + _NS.TT.tag(f"SimpleItem[@Name='{key}']"))
            ) is None or "Value" not in data.attrib:
                continue
            if channel is not None:
                channels = event.setdefault("channels", [])
                while len(channels) < channel:
                    channels.append({})
                channel = channels[channel]
            else:
                channel = event
            state = data.attrib["Value"][0:1].lower() == "t"
            if rule in ("Motion", "MotionAlarm"):
                channel["detected"] = state
            elif rule == "FaceDetect":
                channel.setdefault("ai", {})["face"] = state
            elif rule == "PeopleDetect":
                channel.setdefault("ai", {})["people"] = state
            elif rule == "VehicleDetect":
                channel.setdefault("ai", {})["vehicle"] = state
            elif rule == "DogCatDetect":
                channel.setdefault("ai", {})["dog_cat"] = state
            elif rule == "Visitor":
                channel.setdefault("ai", {})["visitor"] = state

        if not event:
            return Response(status=500, reason="Invalid Message Format")

        self._notify(entry_id, event)
        return Response(status=200)

        # if not (env := et.fromstring(text)) or env.tag != _NS.SOAP_ENV.tag("Envelope"):
        #     return Response(status=500, reason="Invalid Message Format")

        # if not (notify := env.find(f".//{_NS.WSNT.tag('Notify')}")):
        #     return Response(status=500, reason="Invalid Message Format")

        # if not (data := notify.find(f".//{_NS.TT.tag('Data')}")):
        #     return Response(status=500, reason="Invalid Message Format")

        # if not (motion := data.find(_NS.TT.tag('SimpleItem[@Name="IsMotion"][@Value]'))):
        #     return Response(status=500, reason="Invalid Message Format")

        # self._notify(entry_id, motion.attrib["Value"][0:1].lower() == "t")


@callback
@bind_hass
def async_get(hass: HomeAssistant, tracking=True) -> OnvifService:
    domain_data: dict[str, any] = hass.data.setdefault(DOMAIN, {})
    return domain_data.setdefault(DATA_ONVIF, OnvifService(hass, tracking))
