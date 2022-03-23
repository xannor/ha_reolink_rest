"""Common data models"""
from __future__ import annotations
from typing import Protocol


class DataUpdateCoordinatorStop(Protocol):
    """Stop method support on Data Update Coordinators"""

    async def async_stop(self) -> None:
        """Shutdown coordinator"""
        ...
