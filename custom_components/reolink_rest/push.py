"""Simple Push Subscription Manager"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import asdict
import hashlib

import logging
import re
from typing import Final, TypeVar, overload

import secrets

from xml.etree import ElementTree as et

from aiohttp import ClientSession, TCPConnector
from aiohttp.web import Request, Response


from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.loader import bind_hass
from homeassistant.helpers.storage import Store
from homeassistant.util import dt

from homeassistant.backports.enum import StrEnum

from homeassistant.const import CONF_HOST, CONF_PORT, CONF_USERNAME, CONF_PASSWORD
import isodate

from reolinkapi.const import DEFAULT_USERNAME, DEFAULT_PASSWORD

from .const import DATA_COORDINATOR, DOMAIN, OPT_DISCOVERY

from .models import PushSubscription
from .typing import ReolinkDataUpdateCoordinator, WebhookManager

DATA_MANAGER: Final = "push_manager"
DATA_STORE: Final = "push_store"

STORE_VERSION: Final = 1


class _Namespaces(StrEnum):

    soapenv = "http://www.w3.org/2003/05/soap-envelope"
    wsnt = "http://docs.oasis-open.org/wsn/b-2"
    wsa = "http://www.w3.org/2005/08/addressing"
    wsse = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
    wsu = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
    tt = "http://www.onvif.org/ver10/schema"


class _Actions(StrEnum):

    subscribe = (
        "http://docs.oasis-open.org/wsn/bw-2/NotificationProducer/SubscribeRequest"
    )
    renew = "http://docs.oasis-open.org/wsn/bw-2/SubscriptionManager/RenewRequest"
    unsubscribe = (
        "http://docs.oasis-open.org/wsn/bw-2/SubscriptionManager/UnsubscribeRequest"
    )


SOAP: Final = f"""<soapenv:Envelope xmlns:soapenv="{_Namespaces.soapenv}">
{{header}}
<soapenv:Body>{{body}}</soapenv:Body>
</soapenv:Envelope>"""

SOAP_HEADER: Final = "<soapenv:Header>{header}</soapenv:Header>"

CONF_NONCE: Final = "nonce"

CONF_CREATED: Final = "created"

WSS_SECURITY: Final = f"""<wsse:Security soap:mustUnderstand="true" xmlns:wsse="{_Namespaces.wsse}" xmlns:wsu="{_Namespaces.wsu}">
<wsse:UsernameToken>
<wsse:Username>{{{CONF_USERNAME}}}</wsse:Username>
<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{{{CONF_PASSWORD}_digest}}</wsse:Password>
<wsse:Nonce>{{{CONF_NONCE}}}</wsse:Nonce>
<wsu:Created>{{{CONF_CREATED}}}</wsu:Created>
</wsse:UsernameToken>
</wsse:Security>"""

CONF_ADDRESS: Final = "address"

CONF_EXPIRES: Final = "expires"

_NO_LIST: Final[list[str]] = []

ACTION_BODY: Final = {
    _Actions.subscribe: (
        _NO_LIST,
        f"""<wsnt:Subscribe xmlns:wsnt="{_Namespaces.wsnt}">
    <wsnt:ConsumerReference>
        <wsa:Address xmlns:wsa="{_Namespaces.wsa}">{{{CONF_ADDRESS}}}</wsa:Address>
    </wsnt:ConsumerReference>
    <wsnt:InitialTerminationTime>{{{CONF_EXPIRES}}}</wsnt:InitialTerminationTime>
</wsnt:Subscribe>""",
    ),
    _Actions.renew: (
        [
            f"""<wsa:Action xmlns:wsa="{_Namespaces.wsa}">{_Actions.unsubscribe}</wsa:Action>""",
            f"""<wsa:To xmlns:wsa="{_Namespaces.wsa}">{{{CONF_ADDRESS}}}</wsa:To>""",
        ],
        f"""<wsnt:Renew xmlns:wsnt="{_Namespaces.wsnt}">
<wsnt:TerminationTime>{{{CONF_EXPIRES}}}</wsnt:TerminationTime>
</wsnt:Renew>""",
    ),
    _Actions.unsubscribe: (
        [
            f"""<wsa:Action xmlns:wsa="{_Namespaces.wsa}">{_Actions.unsubscribe}</wsa:Action>""",
            f"""<wsa:To xmlns:wsa="{_Namespaces.wsa}">{{{CONF_ADDRESS}}}</wsa:To>""",
        ],
        f"""<wsnt:Unsubscribe xmlns:wsnt="{_Namespaces.wsnt}"/>""",
    ),
}

EVENT_SERVICE: Final = f"http://{{{CONF_HOST}}}:{{{CONF_PORT}}}/onvif/event_service"

DEFAULT_EXPIRES: Final = "P1D"


def _prepare_map(coordinator: ReolinkDataUpdateCoordinator):
    data = coordinator.config_entry.data.copy()
    if CONF_HOST not in data:
        data[CONF_HOST] = coordinator.config_entry.options[OPT_DISCOVERY]["ip"]
    data[CONF_PORT] = coordinator.data.ports["onvifPort"]
    if not CONF_USERNAME in data:
        data[CONF_USERNAME] = DEFAULT_USERNAME
    if not CONF_PASSWORD in data:
        data[CONF_PASSWORD] = DEFAULT_PASSWORD

    created = dt.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    nonce = secrets.token_bytes(16)
    digest = hashlib.sha1()
    digest.update(
        nonce + created.encode("utf-8") + str(data[CONF_PASSWORD]).encode("utf-8")
    )
    data[CONF_CREATED] = created
    data[f"{CONF_PASSWORD}_digest"] = base64.b64encode(digest.digest()).decode("utf-8")
    data[CONF_NONCE] = base64.b64encode(nonce).decode("utf-8")

    return data


def _build_soap(action: _Actions, __map: dict):
    body = ACTION_BODY[action]
    soap = {"body": body[1].format_map(__map), "header": ""}
    headers = []
    if CONF_USERNAME in __map:
        wsse = WSS_SECURITY.format_map(__map)
        headers.append(wsse)
    if body[0]:
        headers.extend((tmpl.format_map(__map) for tmpl in body[0]))
    if headers:
        soap["header"] = SOAP_HEADER.format_map({"header": "".join(headers)})

    return SOAP.format_map(soap)


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


class PushManager:
    """Push Manager"""

    def __init__(
        self,
        logger: logging.Logger,
        url: str,
        storage: Store,
        coordinator: ReolinkDataUpdateCoordinator,
    ) -> None:
        self._logger = logger
        self._url = url
        self._storage = storage
        self._coordinator = coordinator
        self._subscription: PushSubscription = None
        self._renew_task = None

    async def async_start(self):
        """start up manager"""
        data = await self._storage.async_load()
        entry_id = self._coordinator.config_entry.entry_id
        if isinstance(data, dict) and entry_id in data:
            self._subscription = PushSubscription(**data[entry_id])
            # if we retrieve a sub we must have crashed so we will
            # "renew" it incase the camera was reset inbetween
        await self._subscribe()

    async def async_stop(self):
        """shutdown manager"""
        await self._unsubscribe()

    async def _store_subscription(self):
        data = await self._storage.async_load()
        if self._subscription:
            sub = asdict(self._subscription)
            if "expires" in sub:
                sub["expires"] = isodate.duration_isoformat(sub["expires"])
        else:
            sub = None

        entry_id = self._coordinator.config_entry.entry_id
        if isinstance(data, dict):
            if not sub and entry_id in data:
                data.pop(entry_id)
            else:
                data[entry_id] = sub
        elif sub:
            data = {entry_id: sub}
        if data is not None:
            await self._storage.async_save(data)

    async def _send(self, url: str, headers, data):

        try:
            async with ClientSession(
                connector=TCPConnector(verify_ssl=False)
            ) as client:
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

        except Exception as e:
            raise

    async def _subscribe(self, save: bool = True):
        await self._unsubscribe(False)

        data_map = _prepare_map(self._coordinator)
        data_map[CONF_ADDRESS] = self._url
        data_map[CONF_EXPIRES] = DEFAULT_EXPIRES

        url = EVENT_SERVICE.format_map(data_map)

        headers = {"action": _Actions.subscribe.value}
        data = _build_soap(_Actions.subscribe, data_map)
        response = await self._send(url, headers, data)
        if response is None:
            return

        status, response = response
        if status != 200:
            # error respons is kinda useless so we just assume
            self._logger.warning(
                f"Camera ({self._coordinator.data.device_info['name']}) refused subscription request, probably needs a reboot."
            )
            return

        response = response.find(f".//{{{_Namespaces.wsnt}}}SubscribeResponse")
        reference = _find(f"{{{_Namespaces.wsnt}}}SubscriptionReference", response)
        reference = _text(_find(f"{{{_Namespaces.wsa}}}Address", reference), reference)
        time = _text(_find(f"{{{_Namespaces.wsnt}}}CurrentTime", response))
        expires = _text(_find(f"{{{_Namespaces.wsnt}}}TerminationTime", response))
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

        if save:
            await self._store_subscription()

    async def _renew(self, save: bool = True):
        sub = self._subscription
        if sub and not sub.expires:
            return

        if sub and sub.expires:
            data = self._coordinator.data
            camera_now = dt.utcnow() + data.drift
            expires = sub.timestamp + sub.expires
            if (expires - camera_now).total_seconds() < 1:
                return await self._subscribe(save)

        if not sub:
            return await self._subscribe(save)

        data_map = _prepare_map(self._coordinator)
        url = f"http://{{{CONF_HOST}}}:{{{CONF_PORT}}}".format_map(data_map)
        url += sub.manager_url

        data_map[CONF_ADDRESS] = url
        url = EVENT_SERVICE.format_map(data_map)

        headers = {"action": _Actions.renew.value}
        data = _build_soap(_Actions.renew, data_map)
        response = await self._send(url, headers, data)
        if not response:
            return

        status, response = response
        if status != 200:
            # error respons is kinda useless so we just assume
            self._logger.warning(
                f"Camera ({self._coordinator.data.device_info['name']}) refused subscription renewal, probably was rebooted."
            )
            return

        response = response.find(f".//{{{_Namespaces.wsnt}}}SubscribeResponse")

        if save:
            await self._store_subscription()

    async def _unsubscribe(self, save: bool = True):
        sub = self._subscription
        if not sub:
            return

        self._cancel_renew()

        send = True
        if sub.expires:
            data = self._coordinator.data
            camera_now = dt.utcnow() + data.drift
            expires = sub.timestamp + sub.expires
            send = (expires - camera_now).total_seconds() > 1

        # no need to unsubscribe an expiring/expired subscription
        if send:
            data_map = _prepare_map(self._coordinator)
            url = f"http://{{{CONF_HOST}}}:{{{CONF_PORT}}}".format_map(data_map)
            url += sub.manager_url

            data_map[CONF_ADDRESS] = url
            url = EVENT_SERVICE.format_map(data_map)

            headers = {"action": _Actions.unsubscribe.value}
            data = _build_soap(_Actions.unsubscribe, data_map)
            response = await self._send(url, headers, data)
            if response is None:
                return

            status, response = response
            if status != 200:
                self._logger.warning("bad response")

        self._subscription = None
        if save:
            await self._store_subscription()

    def _cancel_renew(self):
        if self._renew_task and not self._renew_task.cancelled():
            self._renew_task.cancel()
        self._renew_task = None

    def _schedule_renew(self, loop: asyncio.AbstractEventLoop):
        self._cancel_renew()
        sub = self._subscription
        if not sub or not sub.expires:
            return

        data = self._coordinator.data
        camera_now = dt.utcnow() + data.drift
        expires = sub.timestamp + sub.expires
        delay = max((expires - camera_now).total_seconds(), 0)

        def _task():
            loop.create_task(self._renew())

        self._renew_task = loop.call_later(delay, _task)


async def async_parse_notification(request: Request):
    """Push Motion Event Handler"""

    if "xml" not in request.content_type:
        return None

    text = await request.text()
    env = et.fromstring(text)
    if env is None or env.tag != f"{{{_Namespaces.soapenv}}}Envelope":
        return None

    notify = env.find(f".//{{{_Namespaces.wsnt}}}Notify")
    if notify is None:
        return None

    data = notify.find(f".//{{{_Namespaces.tt}}}Data")
    if data is None:
        return None

    motion = data.find(f'{{{_Namespaces.tt}}}SimpleItem[@Name="IsMotion"][@Value]')
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
        logger, webhook.url, storage, entry_data[DATA_COORDINATOR]
    )

    def _unload():
        hass.create_task(manager.async_stop())

    entry.async_on_unload(_unload)
    hass.create_task(manager.async_start())

    return manager
