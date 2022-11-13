"""Simple ONVIF Implementation"""


from asyncio import TimerHandle
import base64
from collections import deque
import dataclasses
from datetime import datetime, timedelta
import hashlib
import logging
import secrets
from types import MappingProxyType
from typing import Final
from xml.etree import ElementTree as et

from aiohttp import ClientSession, TCPConnector, client_exceptions

from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.storage import Store
from homeassistant.helpers.singleton import singleton
from homeassistant.util import dt

from homeassistant.const import CONF_USERNAME, CONF_PASSWORD

from async_reolink.api.const import DEFAULT_USERNAME, DEFAULT_PASSWORD

from .api import EntryData, async_get_entry_data

from .const import DOMAIN

STORE_VERSION: Final = 1

_LOGGER = logging.getLogger(__name__)

SOAP_ENV: Final = "http://www.w3.org/2003/05/soap-envelope"
WSNT: Final = "http://docs.oasis-open.org/wsn/b-2"
WSA: Final = "http://www.w3.org/2005/08/addressing"
WSSE: Final = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
)
WSU: Final = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
)
TT: Final = "http://www.onvif.org/ver10/schema"


def _ns(tag: str, namespace: str):
    return f"{{{namespace}}}{tag}"


def _create_envelope(body: et.Element, *headers: et.Element):
    envelope = et.Element(_ns("Envelope", SOAP_ENV))
    if headers:
        _headers = et.SubElement(envelope, _ns("Header", SOAP_ENV))
        for header in headers:
            _headers.append(header)
    et.SubElement(envelope, _ns("Body", SOAP_ENV)).append(body)
    return envelope


def _create_wsse(*, username: str, password: str):
    wsse = et.Element(
        _ns("Security", WSSE),
        {_ns("mustUnderstand", SOAP_ENV): "true"},
    )
    _token = et.SubElement(wsse, _ns("UsernameToken", WSSE))
    et.SubElement(_token, _ns("Username", WSSE)).text = username

    created = dt.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    nonce = secrets.token_bytes(16)
    digest = hashlib.sha1()
    digest.update(nonce + created.encode("utf-8") + str(password).encode("utf-8"))
    et.SubElement(
        _token,
        _ns("Password", WSSE),
        {
            "Type": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest"
        },
    ).text = base64.b64encode(digest.digest()).decode("utf-8")
    et.SubElement(_token, _ns("Nonce", WSSE)).text = base64.b64encode(nonce).decode(
        "utf-8"
    )
    et.SubElement(_token, _ns("Created", WSU)).text = created

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
    subscribe = et.Element(_ns("Subscribe", WSNT))
    et.SubElement(
        et.SubElement(subscribe, _ns("ConsumerReference", WSNT)),
        _ns("Address", WSA),
    ).text = address
    if expires is not None:
        et.SubElement(
            subscribe, _ns("InitialTerminationTime", WSNT)
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
    renew = et.Element(_ns("Renew", WSNT))
    if new_expires is not None:
        et.SubElement(renew, _ns("TerminationTime", WSNT)).text = _duration_isoformat(
            new_expires
        )

    headers = [et.Element(_ns("Action", WSA))]
    headers[0].text = _ACTION
    headers.append(et.Element(_ns("To", WSA)))
    headers[1].text = manager
    return (_ACTION, headers, renew)


def _create_unsubscribe(manager: str):
    _ACTION: Final = (
        "http://docs.oasis-open.org/wsn/bw-2/SubscriptionManager/UnsubscribeRequest"
    )
    unsubscribe = et.Element(_ns("Unsubscribe", WSNT))

    headers = [et.Element(_ns("Action", WSA))]
    headers[0].text = _ACTION
    headers.append(et.Element(_ns("To", WSA)))
    headers[1].text = manager
    return (_ACTION, headers, unsubscribe)


EVENT_SERVICE: Final = "/onvif/event_service"

DEFAULT_EXPIRES: Final = timedelta(hours=1)


@dataclasses.dataclass(frozen=True)
class Subscription:
    """Push Subscription Token"""

    manager_url: str
    timestamp: datetime
    expires: timedelta | None

    def __post_init__(self):
        if self.timestamp and not isinstance(self.timestamp, datetime):
            object.__setattr__(self, "timestamp", dt.parse_datetime(self.timestamp))
        if self.expires and not isinstance(self.expires, timedelta):
            object.__setattr__(self, "expires", dt.parse_duration(self.expires))


async def _send(url: str, headers: dict[str, str], data):
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


def _get_onvif_base(entry_data: EntryData):
    if not entry_data["client_data"].ports.onvif.enabled:
        return None
    return f"http://{entry_data['client'].hostname}:{entry_data['client_data'].ports.onvif.value}"


def _get_service_url(entry_data: EntryData):
    base = _get_onvif_base(entry_data)
    if base is None:
        return None
    return base + EVENT_SERVICE


def _get_wsse(config: MappingProxyType[str, any]):
    return _create_wsse(
        username=config.get(CONF_USERNAME, DEFAULT_USERNAME),
        password=config.get(CONF_PASSWORD, DEFAULT_PASSWORD),
    )


def _find(path: str, *elements: et.Element, namespaces: dict[str, str] = None):
    return next((i.find(path, namespaces) for i in elements if i is not None))


def _text(*elements: et.Element):
    return next((i.text for i in elements if i is not None), None)


def _process_subscription(
    response: et.Element,
    reference: str | None,
):
    if reference is None:
        reference = _find(_ns("SubscriptionReference", WSNT), response)
        reference = _text(_find(_ns("Address", WSA), reference), reference)
    time = _text(_find(_ns("CurrentTime", WSNT), response))
    expires = _text(_find(_ns("TerminationTime", WSNT), response))
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

    return Subscription(reference, time, expires)


def _process_error_response(response: et.Element):
    fault = _find(f".//{_ns('Fault', SOAP_ENV)}", response)
    code = _find(
        _ns("Value", SOAP_ENV),
        _find(_ns("Code", SOAP_ENV), fault),
    )
    reason = _find(
        _ns("Text", SOAP_ENV),
        _find(_ns("Reason", SOAP_ENV), fault),
    )
    return (_text(code), _text(reason))


async def _subscribe(url: str, config_entry: ConfigEntry, entry_data: EntryData):
    wsse = _get_wsse(config_entry.data)
    message = _create_subscribe(url, DEFAULT_EXPIRES)

    headers = {"action": message[0]}

    data = _create_envelope(message[2], wsse, *message[1])
    service_url = _get_service_url(entry_data)
    response = None
    if service_url is not None:
        try:
            response = await _send(
                service_url,
                headers,
                et.tostring(data),
            )
        except client_exceptions.ServerDisconnectedError:
            raise
    if response is None:
        return None

    status, response = response
    if status != 200:
        return _process_error_response(response)

    response = response.find(f".//{_ns('SubscribeResponse', WSNT)}")
    return _process_subscription(response, None)


def _expires(sub: Subscription, entry_data: EntryData):
    if not sub.expires:
        return None
    now = dt.utcnow() + entry_data["client_data"].time_diff
    expires = sub.timestamp + sub.expires
    return expires - now


async def _unsubscribe(
    sub: Subscription, config_entry: ConfigEntry, entry_data: EntryData
):
    if sub is None:
        return

    send = True
    if (ttl := _expires(sub, entry_data)) is not None:
        send = ttl.total_seconds() > 1

    # no need to unsubscribe an expiring/expired subscription
    if send:
        url = _get_onvif_base(entry_data)
        if url is None:
            send = False

    if send:
        url += sub.manager_url

        wsse = _get_wsse(config_entry.data)
        message = _create_unsubscribe(url)

        headers = {"action": message[0]}
        data = _create_envelope(message[2], wsse, *message[2])
        response = None
        try:
            response = await _send(
                _get_service_url(entry_data),
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
            return _process_error_response(response)


async def _renew(
    sub: Subscription,
    config_entry: ConfigEntry,
    entry_data: EntryData,
    url: str | None = None,
):
    if sub is None:
        return

    manager_url = _get_onvif_base(entry_data)
    if manager_url is None:
        return None
    manager_url += sub.manager_url

    if sub.expires:
        if url is not None:
            await _unsubscribe(sub, config_entry, entry_data)
            return await _subscribe(url, config_entry, entry_data)

    wsse = _get_wsse(config_entry.data)
    message = _create_renew(manager_url, sub.expires)

    headers = {"action": message[0]}
    data = _create_envelope(message[2], wsse, *message[1])
    response = await _send(
        _get_service_url(entry_data),
        headers,
        et.tostring(data),
    )
    if not response:
        return None

    status, response = response
    if status != 200:
        (code, reason) = _process_error_response(response)

        if url is not None:
            return await _subscribe(url, config_entry, entry_data)

        return (code, reason)

    response = response.find(f".//{_ns('RenewResponse', WSNT)}")
    return _process_subscription(response, manager_url)


@singleton(f"{DOMAIN}-onvif-store")
@callback
def _get_store(hass: HomeAssistant):
    return Store(hass, STORE_VERSION, f"{DOMAIN}_onvif", True)


def _fix_expires(data: dict):
    if "expires" in data:
        data["expires"] = _duration_isoformat(data["expires"])
    return data


def _schedule_renewal(sub: Subscription, hass: HomeAssistant, entry_id: str):
    if not sub.expires:
        return
    domain_data: dict = hass.data[DOMAIN]
    subs: deque[tuple[str, Subscription]] = domain_data.setdefault(
        "onvif_subs", deque()
    )
    i = 0
    while i < len(subs) and subs[0][1].expires <= sub.expires:
        # bring the next subscripton to the front
        subs.rotate(1)
        i += 1
    subs.appendleft((entry_id, subs))
    # reset deque top
    subs.rotate(-i)
    # if we added further down we are done as the most recent did not change
    if i > 0:
        return
    handle: TimerHandle = domain_data.get("onvif_timer", None)
    if handle:
        handle.cancel()

    def schedule_next():
        _t = next(iter(subs), None)
        if not _t:
            return
        entry_id, sub = _t
        entry_data = async_get_entry_data(hass, entry_id)
        ttl = _expires(sub, entry_data)
        if ttl is None:
            # this should NEVER happen
            raise SystemError()

        loop = hass.loop

        def timer_call():
            entry_id, sub = subs.popleft()
            loop.create_task(
                _renew(
                    sub,
                    hass.config_entries.async_get_entry(entry_id),
                    async_get_entry_data(hass, entry_id, False),
                )
            )
            schedule_next()

        delay = max(ttl.total_seconds() - 10, 0)
        # shave off some seconds for safety
        domain_data["onvif_timer"] = loop.call_later(delay, timer_call)

    schedule_next()


async def async_get_subscription(url: str, hass: HomeAssistant, entry_id: str):
    """Get existing or new ONVIF subscription"""

    config_entry = hass.config_entries.async_get_entry(entry_id)
    entry_data = async_get_entry_data(hass, entry_id)
    store = _get_store(hass)
    data = await store.async_load()
    if isinstance(data, dict):
        token: dict = data.get(entry_id, None)
        if token:
            sub = Subscription(**token)
            if (ttl := _expires(sub, entry_data)) is not None:
                if ttl.total_seconds() > 60:
                    _schedule_renewal(sub, hass, entry_id)
                    return sub
                await _unsubscribe(sub, config_entry, entry_data)
                del data[entry_id]
    else:
        data = {}

    sub = await _subscribe(url, config_entry, entry_data)
    store.async_delay_save(lambda: data, 1)
    if not isinstance(sub, Subscription):
        return sub
    _schedule_renewal(sub, config_entry, entry_data)
    data[entry_id] = _fix_expires(dataclasses.asdict(sub))
    return sub


def async_parse_notification(text: str):
    """Push Motion Event Handler"""

    _LOGGER.debug("processing notification<-%r", text)
    env = et.fromstring(text)
    if env is None or env.tag != f"{{{SOAP_ENV}}}Envelope":
        return None

    notify = env.find(f".//{{{WSNT}}}Notify")
    if notify is None:
        return None

    data = notify.find(f".//{{{TT}}}Data")
    if data is None:
        return None

    motion = data.find(f'{{{TT}}}SimpleItem[@Name="IsMotion"][@Value]')
    if motion is None:
        return None

    return motion.attrib["Value"][0:1].lower() == "t"
