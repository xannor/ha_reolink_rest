"""Common data models"""
from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.helpers.entity import DeviceInfo

from reolinkapi.typings import system as rs, network as rn, abilities as ra
from reolinkapi.rest.ai import GetAiStateResponseValue


@dataclass
class ReolinkEntityData:
    """Common data for Reolink Entities"""

    connection_id: int
    uid: str
    abilities: ra.Abilities
    channels: list[rn.ChannelStatus] | None
    ports: rn.NetworkPorts
    abilities: ra.Abilities
    client_device_info: rs.DeviceInfo | None
    device_info: DeviceInfo


@dataclass
class ReolinkMotionState:
    """Motion State"""

    state: int
    ai: GetAiStateResponseValue


@dataclass
class ReolinkMotionData:
    """Motion Data for Reolink Entities"""

    channels: dict[int, ReolinkMotionState] = field(default_factory=dict)
