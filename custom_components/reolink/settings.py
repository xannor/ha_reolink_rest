"""Configuration"""
from __future__ import annotations

from typing import Iterator, Mapping, Sequence, SupportsIndex, cast
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.singleton import singleton
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_KEY = DOMAIN + ".settings"

VERSION = 1


@singleton(STORAGE_KEY)
@callback
async def async_get_settings(hass: HomeAssistant) -> Settings:
    """Get settings"""
    settings = Settings(hass)
    await settings.async_load()

    return settings


async def async_set_setting(obj: any, key: str | SupportsIndex, value: any) -> None:
    """Store a setting"""
    if isinstance(obj, SettingsDict):
        parent = obj._parent or obj
        obj._data[cast(str, key)] = value
    elif isinstance(obj, SettingsList):
        parent = obj._parent or obj
        obj._data[cast(SupportsIndex, key)] = value
    while isinstance(parent, _Settings) and not isinstance(parent, Settings):
        parent = parent._parent
    if isinstance(parent, Settings):
        await parent._async_save()


class _Settings:
    def __init__(self, parent: _Settings, key: str | SupportsIndex) -> None:
        self._parent = parent
        self._key = key

    def _wrap_value(self, key: str | SupportsIndex, value: any):
        if isinstance(value, dict):
            return SettingsDict(self, key, value)
        if isinstance(value, list):
            return SettingsList(self, key, value)
        return value


class SettingsDict(_Settings, Mapping[str, any]):
    """Settings Dictionary"""

    def __init__(self, parent: _Settings, key: str, data: dict[str, any]) -> None:
        super().__init__(parent, key)
        self._data = data

    def __getitem__(self, __k: str) -> any:
        return self._wrap_value(__k, self._data[__k])

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[str]:
        return self._data.__iter__()


class SettingsList(_Settings, Sequence[any]):
    """Settings List"""

    def __init__(self, parent: _Settings, key: str | SupportsIndex, data: list) -> None:
        super().__init__(parent, key)
        self._data = data

    def __getitem__(self, __k: SupportsIndex) -> any:
        return self._wrap_value(__k, self._data[__k])

    def __len__(self) -> int:
        return len(self._data)


class Settings(SettingsDict):
    """Settings"""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(None, None, {})
        self._store = Store(hass, VERSION, STORAGE_KEY)

    async def async_load(self):
        """Load settings"""
        if stored := await self._store.async_load():
            self._data = cast(dict, stored)

    async def _async_save(self):
        await self._store.async_save(self._data)
