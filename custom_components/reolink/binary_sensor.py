"""ONVIF motion sensor"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from contextlib import suppress

import logging
import os
import onvif


from homeassistant.core import HomeAssistant, callback, CALLBACK_TYPE, HassJob
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import event
from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
)
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_USERNAME,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from httpx import Request, TransportError
from zeep.exceptions import Fault

from reolinkapi.rest import Client
from reolinkapi.const import DetectionTypes
from reolinkapi.helpers.ability import NO_ABILITY, NO_CHANNEL_ABILITIES

from .typings import component
from . import models

from .typings.onvif.events import (
    NotificationService,
    SubscriptionManager,
    SubscriptionRenewParams,
    SubscriptionUnsubscribeParams,
)

from .base import ReolinkEntity

from .const import CONF_CHANNELS, DOMAIN, MOTION_TYPE, CONF_PREFIX_CHANNEL


_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Setup binary sensor platform"""

    domain_data: component.DomainData | dict[str, component.EntryData] = hass.data[
        DOMAIN
    ]
    entry_data: component.EntryData = domain_data[config_entry.entry_id]
    entity_data = entry_data["coordinator"].data

    if (
        entity_data.abilities.get("onvif", NO_ABILITY)["ver"] == 0
        or entity_data.ports["onvifPort"] == 0
    ):
        return True

    device = onvif.ONVIFCamera(
        entry_data["client"].hostname,
        entity_data.ports["onvifPort"],
        config_entry.data.get(CONF_USERNAME),
        config_entry.data.get(CONF_PASSWORD),
        f"{os.path.dirname(onvif.__file__)}/wsdl/",
        no_cache=False,
    )
    await device.update_xaddrs()

    need_refresh: CALLBACK_TYPE | None = None

    async def _update_data():
        nonlocal need_refresh

        commands = []
        channel_state_index: dict[int, int] = {}

        def _build_commands(channel: int):
            _LOGGER.info("Updating Motion states for channel %s", channel)
            channel_state_index[channel] = len(commands)
            commands.append(entry_data["client"].create_get_md_state(channel))
            channel_abilities = entity_data.abilities["abilityChn"][channel]
            if (
                channel_abilities.get("supportAi", NO_ABILITY)["ver"]
                or channel_abilities.get("supportAiAnimal", NO_ABILITY)["ver"]
                or channel_abilities.get("supportAiDogCat", NO_ABILITY)["ver"]
                or channel_abilities.get("supportAiFace", NO_ABILITY)["ver"]
                or channel_abilities.get("supportAiPeople", NO_ABILITY)["ver"]
                or channel_abilities.get("supportAiVehicle", NO_ABILITY)["ver"]
            ):
                commands.append(entry_data["client"].create_get_ai_state(channel))

        def _retry():
            nonlocal need_refresh
            need_refresh()
            need_refresh = None
            update_coordinator.hass.async_add_job(update_coordinator.async_refresh)

        if entity_data.channels is not None and CONF_CHANNELS in config_entry.data:
            for _c in config_entry.data.get(CONF_CHANNELS, []):
                if (
                    not next((ch for ch in entity_data.channels if ch["channel"] == _c))
                    is None
                ):
                    _build_commands(_c)
        else:
            _build_commands(0)

        try:
            responses = await entry_data["client"].batch(commands)
        except Exception:
            if need_refresh is None:
                need_refresh = entry_data["coordinator"].async_add_listener(_retry)
            raise
        if Client.has_auth_failure(responses):
            await entry_data["client"].logout()
            if need_refresh is None:
                need_refresh = entry_data["coordinator"].async_add_listener(_retry)
            raise UpdateFailed()
        ai_states = list(Client.get_ai_state_responses(responses))
        channels: dict[int, models.ReolinkMotionState] = {}
        for channel, index in channel_state_index.items():
            state = next(Client.get_md_state_responses([responses[index]]), None)
            ai = next(
                (ai_state for ai_state in ai_states if ai_state["channel"] == channel),
                None,
            )
            channels[channel] = models.ReolinkMotionState(state, ai)

        return models.ReolinkMotionData(channels)

    update_coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="Reolink-device-motion-data",
        update_method=_update_data,
    )

    await update_coordinator.async_config_entry_first_refresh()

    manager: EventManager = hass.data[DOMAIN].get(EVENT_MANAGER, None)
    if manager is None:
        manager = EventManager()
        hass.data[DOMAIN][EVENT_MANAGER] = manager
    manager.async_add_device_and_coordinator(device, update_coordinator)

    entities = []

    def _create_entities(channel: int):
        channel_abilities = entity_data.abilities.get(
            "abilityChn", [NO_CHANNEL_ABILITIES]
        )[channel]
        entities.append(
            ReolinkMotionEntity(
                entry_data["coordinator"], manager, channel, DetectionTypes.NONE
            )
        )
        if channel_abilities.get("supportAiPeople", NO_ABILITY)["ver"]:
            entities.append(
                ReolinkMotionEntity(
                    entry_data["coordinator"],
                    manager,
                    channel,
                    DetectionTypes.PEOPLE,
                )
            )
        if channel_abilities.get("supportAiVehicle", NO_ABILITY)["ver"]:
            entities.append(
                ReolinkMotionEntity(
                    entry_data["coordinator"],
                    manager,
                    channel,
                    DetectionTypes.VEHICLE,
                )
            )
        if channel_abilities.get("supportAiAnimal", NO_ABILITY)["ver"]:
            entities.append(
                ReolinkMotionEntity(
                    entry_data["coordinator"],
                    manager,
                    device,
                    DetectionTypes.ANIMAL,
                )
            )
        if channel_abilities.get("supportAiDogCat", NO_ABILITY)["ver"]:
            entities.append(
                ReolinkMotionEntity(
                    entry_data["coordinator"],
                    manager,
                    channel,
                    DetectionTypes.PET,
                )
            )

    if entity_data.channels is not None and CONF_CHANNELS in config_entry.data:
        for _c in config_entry.data.get(CONF_CHANNELS, []):
            if (
                not next((ch for ch in entity_data.channels if ch["channel"] == _c))
                is None
            ):
                _create_entities(_c)
    else:
        _create_entities(0)

    if len(entities) > 0:
        async_add_entities(entities)

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """unload platform"""

    domain_data: component.DomainData | dict[str, component.EntryData] = hass.data[
        DOMAIN
    ]
    manager: EventManager = domain_data.get(EVENT_MANAGER, None)
    if manager is None:
        return

    await manager.async_remove_device_and_coordinator(config_entry.entry_id)


EVENT_MANAGER = "event-manager"

SERVICE_ERRORS = (
    Fault,
    asyncio.TimeoutError,
    TransportError,
)


@dataclass
class EventSubscription:
    """Event Subscription Information"""

    coordinator: DataUpdateCoordinator[models.ReolinkMotionData]
    device: onvif.ONVIFCamera
    webhook_id: str
    manager_url: str | None = field(default=None)
    client_time: datetime = field(default=datetime.min)
    termination_time: datetime = field(default=datetime.min)
    time_diff: float = field(default=0)
    lease_delta: timedelta | None = field(default=None)


DEFAULT_LEASE_TIME = timedelta(minutes=15)


class EventManager:
    """Global event Manager"""

    def __init__(self, lease_delta: timedelta = DEFAULT_LEASE_TIME) -> None:
        self._subscriptions: dict[str, EventSubscription] = {}
        self._lease_time = lease_delta
        self._job = HassJob(self._renew_subscriptions)
        self._next_renewal: datetime = datetime.max
        self._unsub_renewals: CALLBACK_TYPE | None = None

    async def _webhook_handler(
        self, hass: HomeAssistant, webhook_id: str, request: Request
    ):
        data = await request.text()
        if not data:
            return

        sub = next(
            (
                sub
                for sub in self._subscriptions.values()
                if sub.webhook_id == webhook_id
            ),
            None,
        )
        if sub is not None:
            _LOGGER.info("Received motion notification from %s", sub.device.host)
            hass.add_job(sub.coordinator.async_request_refresh())

        # the onvif notification is just a general one, it does not specify type or source

    def async_add_device_and_coordinator(
        self, device: onvif.ONVIFCamera, coordinator: DataUpdateCoordinator
    ):
        """register device and coordinator in manager"""
        sub = self._subscriptions[
            coordinator.config_entry.entry_id
        ] = EventSubscription(
            coordinator, device, coordinator.hass.components.webhook.async_generate_id()
        )
        coordinator.hass.components.webhook.async_register(
            DOMAIN,
            self.__class__.__name__,
            sub.webhook_id,
            self._webhook_handler,
        )

    async def async_remove_device_and_coordinator(self, entry_id: str):
        """shutdown listener and remove info"""
        sub = self._subscriptions.pop(entry_id, None)
        if sub is None:
            return

        if sub.webhook_id is not None:
            sub.coordinator.hass.components.webhook.async_unregister(sub.webhook_id)
        await self._stop_device(sub)

    async def _stop_device(self, sub: EventSubscription):
        svc = sub.device.create_subscription_service()
        subscription: SubscriptionManager = svc
        params: SubscriptionUnsubscribeParams = svc.create_type("Unsubscribe")
        params.To = sub.manager_url
        response = await subscription.Unsubscribe(params)
        await sub.device.close()

    def async_add_listener(self, entry_id: str, update_callback: CALLBACK_TYPE):
        """Listen for data updates"""
        sub = self._subscriptions.get(entry_id, None)
        if sub is None:
            return _cleanup

        listeners = sub.coordinator._listeners  # pylint: disable=protected-access
        cleanup = sub.coordinator.async_add_listener(update_callback)

        def _cleanup():
            if cleanup is None:
                return
            cleanup()
            if len(listeners):
                return
            sub.coordinator.hass.add_job(self._stop_device, sub)

        if len(listeners) == 1:
            sub.coordinator.hass.add_job(self._setup_subscription, sub)
        return _cleanup

    async def _setup_subscription(self, sub: EventSubscription):
        url = sub.coordinator.hass.components.webhook.async_generate_url(sub.webhook_id)
        local_time = datetime.utcnow()
        svc = sub.device.create_notification_service()
        notification: NotificationService = svc
        # params = svc.create_type("Subscribe")
        params = {}
        params["ConsumerReference"] = {"Address": url}
        # params.ConsumerReference.Address = url
        # params.InitialTerminationTime = "PT15M"
        response = None
        with suppress(SERVICE_ERRORS):
            response = await notification.Subscribe(params)

        if response is None:

            async def _retry(_: datetime):
                return await self._setup_subscription(sub)

            _LOGGER.warning(
                "could not get subscription from camera, will retry later, this this persists the camera will need to be restarted"
            )
            event.async_track_point_in_utc_time(
                sub.coordinator.hass, _retry, datetime.utcnow() + timedelta(minutes=5)
            )
            return

        sub.manager_url = (
            response.SubscriptionReference.Address._value_1  # pylint: disable=protected-access
        )

        sub.client_time = response.CurrentTime
        sub.termination_time = response.TerminationTime
        sub.time_diff = sub.client_time.timestamp() - local_time.timestamp()
        sub.lease_delta = (
            sub.client_time - sub.termination_time
            if sub.termination_time is not None
            else None
        )
        if sub.termination_time.timestamp() < self._next_renewal.timestamp():
            renew_window = timedelta(seconds=sub.lease_delta.total_seconds() * 0.08)
            self._next_renewal = sub.termination_time - renew_window
        self._schedule_renewals(sub.coordinator.hass)

    def _schedule_renewals(self, hass: HomeAssistant):
        if self._next_renewal.timestamp() == datetime.max.timestamp():
            return

        if self._unsub_renewals is not None:
            self._unsub_renewals()
            self._unsub_renewals = None

        self._unsub_renewals = event.async_track_point_in_utc_time(
            hass, self._job, self._next_renewal
        )

    async def _renew_subscriptions(self):
        local_time = datetime.utcnow()
        for sub in self._subscriptions.values():
            renew_window = timedelta(seconds=sub.lease_delta.total_seconds() * 0.08)

            if (
                sub.termination_time - renew_window
            ).timestamp() > local_time.timestamp() + sub.time_diff:
                sub.coordinator.hass.add_job(self._renew_subscription, sub)

    async def _renew_subscription(self, sub: EventSubscription):
        local_time = datetime.utcnow()
        svc = sub.device.create_subscription_service()
        subscription: SubscriptionManager = svc
        params: SubscriptionRenewParams = svc.create_type("Renew")
        params.To = sub.manager_url
        params.TerminationTime = sub.lease_delta
        with suppress(SERVICE_ERRORS):
            response = await subscription.Renew(params)
        if response is None:
            _LOGGER.warning("Could not renew subscription, will need to resubscribe")
            return await self._setup_subscription(sub)

        sub.client_time = response.CurrentTime
        sub.termination_time = response.TerminationTime
        sub.time_diff = sub.client_time.timestamp() - local_time.timestamp()
        sub.lease_delta = (
            sub.client_time - sub.termination_time
            if sub.termination_time is not None
            else None
        )

    def async_get_data(self, entry_id: str) -> models.ReolinkMotionData:
        """get motion data"""
        sub = self._subscriptions.get(entry_id, None)
        if not sub is None:
            return sub.coordinator.data


class ReolinkMotionEntity(ReolinkEntity, BinarySensorEntity):
    """Reolink Motion Entity"""

    def __init__(
        self,
        entity_coordinator: DataUpdateCoordinator,
        manager: EventManager,
        channel_id: int,
        detection_type: DetectionTypes,
    ) -> None:
        super().__init__(entity_coordinator, channel_id, MOTION_TYPE[detection_type])
        BinarySensorEntity.__init__(
            self
        )  # explicitly call Camera init since UpdateCoordinatorEntity does not super()
        self._detection_type = detection_type
        self._prefix_channel: bool = self.coordinator.config_entry.data.get(
            CONF_PREFIX_CHANNEL
        )
        self._attr_unique_id = (
            f"{self.coordinator.data.uid}.{self._channel_id}.{detection_type.name}"
        )
        self._manager = manager
        self._additional_updates()

    def _additional_updates(self):
        if self._prefix_channel and self._channel_status is not None:
            self._attr_name = f'{self.coordinator.data.device_info["name"]} {self._channel_status["name"]} {self.entity_description.name}'
        else:
            self._attr_name = f'{self.coordinator.data.device_info["name"]} {self.entity_description.name}'

    @callback
    def _handle_coordinator_update(self):
        self._additional_updates()

        super()._handle_coordinator_update()

    @callback
    def _handle_motion_update(self):
        data = self._manager.async_get_data(self.coordinator.config_entry.entry_id)
        _state = 0
        if self._detection_type == DetectionTypes.NONE:
            _state = data.channels[self._channel_id].state
        elif self._detection_type == DetectionTypes.PET:
            _state = data.channels[self._channel_id].ai["dog_cat"]["alarm_state"]
        elif self._detection_type == DetectionTypes.FACE:
            _state = data.channels[self._channel_id].ai["face"]["alarm_state"]
        elif self._detection_type == DetectionTypes.PEOPLE:
            _state = data.channels[self._channel_id].ai["people"]["alarm_state"]
        elif self._detection_type == DetectionTypes.VEHICLE:
            _state = data.channels[self._channel_id].ai["vehicle"]["alarm_state"]

        self._attr_is_on = _state != 0
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._handle_coordinator_update()
        self.async_on_remove(
            self._manager.async_add_listener(
                self.coordinator.config_entry.entry_id, self._handle_motion_update
            )
        )
        self._handle_motion_update()
