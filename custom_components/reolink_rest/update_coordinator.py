"""Data Update Coordinator"""

from datetime import timedelta
import logging
from typing import Callable, Coroutine, Sequence, TypeVar
from bleak import Awaitable
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.debounce import Debouncer

from async_reolink.api.connection.typing import CommandRequest, CommandResponse


class QueuedDataUpdateCoordinator(DataUpdateCoordinator[tuple[CommandResponse]]):
    """Class to manage fetching data from single endpoint."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        *,
        name: str,
        update_interval: timedelta | None = None,
        update_method: Callable[[], Awaitable[_T]] | None = None,
        request_refresh_debouncer: Debouncer[Coroutine[any, any, None]] | None = None
    ) -> None:
        super().__init__(
            hass,
            logger,
            name=name,
            update_interval=update_interval,
            update_method=update_method,
            request_refresh_debouncer=request_refresh_debouncer,
        )
        self._queue = []
        self._responses = []

    @property
    def client(self):
        """Client"""
        return self._client
