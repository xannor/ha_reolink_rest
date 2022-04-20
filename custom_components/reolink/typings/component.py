"""Component Typings"""
from __future__ import annotations


from dataclasses import dataclass, field
from typing import Literal, Mapping, MutableMapping, overload

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from reolinkapi.rest import Client
from reolinkapi.typings.abilities import Abilities
from reolinkapi.typings.network import ChannelStatus, NetworkPorts
from reolinkapi.typings.system import DeviceInfo as Reolink_DeviceInfo

from .motion import ChannelMotionState


@dataclass
class EntityData:
    """Reolink Client Entity Data"""

    connection_id: int
    uid: str
    abilities: Abilities
    channels: list[ChannelStatus] | None
    ports: NetworkPorts
    client_device_info: Reolink_DeviceInfo | None
    device_info: DeviceInfo


@dataclass
class EntryData:
    """Entry Data"""

    client: Client
    coordinator: DataUpdateCoordinator[EntityData]
    data_motion_coordinator: DataUpdateCoordinator[
        dict[int, ChannelMotionState]
    ] = field(default=None, init=False)


@dataclass
class DomainData(Mapping[str, EntryData]):
    """Domain Data"""

    def __post_init__(self):
        self._entries: dict[str, EntryData] = {}

    def __getitem__(self, __k: str):
        return self._entries[__k]

    def __iter__(self):
        return self._entries.__iter__()

    def __len__(self) -> int:
        return self._entries.__len__()

    @overload
    def register_entry(self, entry_id: str, data: EntryData) -> None:
        ...

    @overload
    def register_entry(
        self,
        entry_id: str,
        client: Client,
        coordinator: DataUpdateCoordinator[EntryData],
    ) -> EntityData:
        ...

    def register_entry(self, entry_id: str, *args):
        """Register entry with integration"""

        if isinstance(args[0], EntryData):
            data = args[0]
        else:
            data = EntryData(*args)

        self._entries[entry_id] = data
        return data

    def remove_entry(self, entry_id: str):
        """Remove entry from inegration"""
        return self._entries.pop(entry_id, None)


HassDomainData = MutableMapping[Literal["reolink"], DomainData]
