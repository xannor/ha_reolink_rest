"""Webhook Service"""

import logging
from typing import TYPE_CHECKING, Callable, Coroutine, NamedTuple

from aiohttp.web import Request, Response

from .._utilities.hass_typing import hass_bound


if TYPE_CHECKING:
    import homeassistant.components.webhook as component_webhook
    import homeassistant.helpers.network as helpers_network

from homeassistant.core import (
    HomeAssistant,
    callback,
    is_callback,
    HomeAssistantError,
    HassJob,
    CALLBACK_TYPE,
)
from homeassistant.loader import bind_hass

from ..const import DOMAIN, DATA_WEBHOOK

from ..typing import EntryId

_LOGGER = logging.getLogger(__name__)


class _FilterableJob(NamedTuple):
    """Event listener job to be executed with optional filter."""

    job: HassJob[[Request], Coroutine[any, any, Response | None] | Response | None]
    request_filter: Callable[[Request], bool] | None
    run_immediately: bool


class WebHookService:

    __slots__ = ("_hass", "_lookups", "_listeners", "_url")

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._lookups: dict[EntryId, dict[str, str]] = {}
        self._listeners: dict[str, list[_FilterableJob]] = {}
        self._url = None

    @property
    def supported(self):
        return True

    def _get_url(self, force=False):
        if not force and self._url:
            return self._url
        network: helpers_network = self._hass.helpers.network
        get_url = hass_bound(network.get_url)
        try:
            self._url = get_url(prefer_external=False, prefer_cloud=False)
        except network.NoURLAvailableError:
            _LOGGER.warning(
                "Could not get internal url from system"
                ", will attempt external url but this is not preferred"
                ", please verify your installation."
            )

            try:
                self._url = get_url(allow_cloud=False)
            except network.NoURLAvailableError:
                self._url = None
                _LOGGER.warning("Could not get an addressable url, disabling webhook support")
                return
        return self._url

    def _get_webhook_id(
        self,
        entry_id: EntryId,
        /,
        key: str | None = None,
        name: str | None = None,
        local_only=False,
    ):
        if key:
            key = f"_{key}"
        else:
            key = ""
        if not (webhook_id := self._lookups.setdefault(entry_id, {}).get(key)):
            config_entry = self._hass.config_entries.async_get_entry(entry_id)
            _webhook_id = f"{DOMAIN}{key}_{config_entry.unique_id or entry_id}"
            if not name:
                name = f"{config_entry.title}{key.replace('_', ' ')}"
            webhooks: component_webhook = self._hass.components.webhook
            webhook_id = self._lookups[entry_id].setdefault(key, _webhook_id)
            if webhook_id == _webhook_id:
                hass_bound(webhooks.async_register)(
                    DOMAIN, name, webhook_id, self._handler, local_only=local_only
                )
        return webhook_id

    def async_listen(
        self,
        entry_id: EntryId,
        listener: Callable[[Request], Coroutine[any, any, None] | None],
        request_filter: Callable[[Request], bool] | None = None,
        run_immediately: bool = False,
        /,
        key: str | None = None,
        name: str | None = None,
        local_only=False,
    ):
        webhook_id = self._get_webhook_id(entry_id, key=key, name=name, local_only=local_only)
        if request_filter is not None and not is_callback(request_filter):
            raise HomeAssistantError(f"Event filter {request_filter} is not a callback")
        if run_immediately and not is_callback(listener):
            raise HomeAssistantError(f"Event listener {listener} is not a callback")
        return self._async_listen_filterable_job(
            webhook_id, _FilterableJob(HassJob(listener), request_filter, run_immediately)
        )

    @callback
    def _async_listen_filterable_job(
        self, webhook_id: str, filterable_job: _FilterableJob
    ) -> CALLBACK_TYPE:
        self._listeners.setdefault(webhook_id, []).append(filterable_job)

        def remove_listener() -> None:
            """Remove the listener."""
            self._async_remove_listener(webhook_id, filterable_job)

        setattr(remove_listener, "_webhook_id", webhook_id)
        return remove_listener

    @callback
    def _async_remove_listener(self, webhook_id: str, filterable_job: _FilterableJob) -> None:
        try:
            self._listeners[webhook_id].remove(filterable_job)

            if not self._listeners[webhook_id]:
                self._listeners.pop(webhook_id)
                webhooks: component_webhook = self._hass.components.webhook
                hass_bound(webhooks.async_unregister)(webhook_id)

        except (KeyError, ValueError):
            # ValueError if listener did not exist within event_type
            _LOGGER.exception("Unable to remove unknown job listener %s", filterable_job)

    @callback
    def async_get_url(self, webhook: CALLBACK_TYPE):
        if not (webhook_id := getattr(webhook, "_webhook_id", None)):
            return None
        url = self._get_url()
        if not url:
            return None
        webhooks: component_webhook = self._hass.components.webhook
        url += webhooks.async_generate_path(webhook_id)
        return url

    async def _handler(self, _: HomeAssistant, webhook_id: str, request: Request):
        if not (listeners := self._listeners.get(webhook_id)):
            return

        for job, request_filter, run_immediately in listeners:
            if request_filter is not None:
                try:
                    if not request_filter(request):
                        continue
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Error in request filter")
                    continue
            if run_immediately:
                try:
                    response = job.target(request)
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Error running job: %s", job)
                    continue
            else:
                response = self._hass.async_add_hass_job(job, request)

            if isinstance(response, Response):
                return response
            elif response is not None:
                return await response


@callback
@bind_hass
def async_get(hass: HomeAssistant) -> WebHookService:
    domain_data: dict[str, any] = hass.data.setdefault(DOMAIN, {})
    return domain_data.setdefault(DATA_WEBHOOK, WebHookService(hass))
