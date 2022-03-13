"""Component Typings"""

from typing import TypedDict

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from reolinkapi.rest import Client

from ..models import ReolinkEntityData


class DomainData(TypedDict, total=False):
    """Domain Data"""


class EntryData(TypedDict, total=False):
    """Entry Data"""

    client: Client
    coordinator: DataUpdateCoordinator[ReolinkEntityData]
