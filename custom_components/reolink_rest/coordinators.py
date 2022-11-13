"""Custom Update Coordinators"""

from datetime import timedelta
import logging
from typing import Callable, Generator, Generic, TypeVar

from homeassistant.core import HomeAssistant, CALLBACK_TYPE, callback
from homeassistant.config_entries import ConfigEntry

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

_C = TypeVar("_C")
_T = TypeVar("_T")


class SubUpdateCoordinator(Generic[_C, _T]):
    """Sub Update Coordinator"""

    update_interval: timedelta = None
    last_exception: Exception | None = None
    _coordinator: DataUpdateCoordinator[_C] = None
    hass: HomeAssistant
    logger: logging.Logger
    config_entry: ConfigEntry

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[_C],
        update_method: Callable[[_C], _T] | None = None,
        context: any = None,
    ):
        self.context = context
        self.update_method = update_method
        self.last_update_success = True
        self._set_coordinator(coordinator)

        self._listeners: dict[CALLBACK_TYPE, tuple[CALLBACK_TYPE, object | None]] = {}
        self.data: _T = None
        self._remove_listener: CALLBACK_TYPE = None

    @property
    def coordinator(self):
        """coordinator"""
        return self._coordinator

    def _set_coordinator(self, coordinator: DataUpdateCoordinator[_C]):
        self._coordinator = coordinator
        self.hass = coordinator.hass
        self.logger = coordinator.logger
        self.config_entry = coordinator.config_entry

    @coordinator.setter
    def coordinator(self, value: DataUpdateCoordinator[_C]):
        if self._coordinator is value:
            return
        self._unbind_parent()
        self._set_coordinator(value)
        if len(self._listeners):
            self._bind_parent()
        self._refresh_data()

    def _update_data(self) -> _T:
        if self.update_method is None:
            raise NotImplementedError("Update method not implemented")
        return self.update_method(self._coordinator.data)

    @callback
    def _refresh_data(self):
        self.data = self._update_data()
        self.async_update_listeners()

    @callback
    def async_set_updated_data(self, data: _T) -> None:
        """Manually update data, notify listeners and reset refresh interval."""
        self.data = data
        self.async_update_listeners()

    @callback
    def async_update_listeners(self) -> None:
        """Update all registered listeners."""
        for update_callback, _ in list(self._listeners.values()):
            update_callback()

    def _bind_parent(self):
        self._remove_listener = self._coordinator.async_add_listener(
            self._refresh_data, self.context
        )

    def _unbind_parent(self):
        if self._remove_listener is not None:
            self._remove_listener()
        self._remove_listener = None

    def async_contexts(self) -> Generator[any, None, None]:
        """Return all registered contexts."""
        yield from (
            context for _, context in self._listeners.values() if context is not None
        )

    @callback
    def async_add_listener(
        self, update_callback: CALLBACK_TYPE, context: any = None
    ) -> Callable[[], None]:
        """Listen for data updates."""

        bind_parent = not self._listeners

        @callback
        def remove_listener() -> None:
            """Remove update listener."""
            self._listeners.pop(remove_listener)
            if not self._listeners:
                self._unbind_parent()

        self._listeners[remove_listener] = (update_callback, context)

        if bind_parent:
            self._bind_parent()

        return remove_listener

    async def async_request_refresh(self):
        """Request a refresh.

        Refresh will wait a bit to see if it can batch them.
        """
        await self.coordinator.async_request_refresh()

    async def async_config_entry_first_refresh(self):
        """Refresh data for the first time when a coordinator is setup."""

        self.data = self._update_data()

    @callback
    def async_set_update_error(self, err: Exception) -> None:
        """Stub to emulate DataUpdateCoordinator signature, does not do anyting"""
