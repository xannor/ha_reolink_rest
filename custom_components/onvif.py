"""Onvif helpers"""

DATA_STORAGE: Final = "onvif_storage"


async def _handle_onvif_notify(hass: HomeAssistant, request: Request):
    motion = await async_parse_notification(request)
    if motion is None:
        return None

    domain_data: DomainData = hass.data[DOMAIN]
    entry_data = domain_data[request["entry_id"]]

    # the onvif motion event is only a simple notice as the device detected motion
    # TODO : does the NVR provide a channel or does it just to this for any device?

    try:
        _cb: CALLBACK_TYPE = entry_data.onvif_notify_debounce
        del entry_data.onvif_notify_debounce
        if _cb:
            # clear debouce
            _cb()
    except AttributeError:
        pass

    data = entry_data.coordinator.data

    include_md = False
    # when we have channels or ai, we need to know what type of motion and where
    # we do this separatly from the data updaters because we do not want to delay
    async def _fetch_actual_motion():
        client = entry_data.client
        commands = client.commands
        queue = []
        for channel in data.channels:
            if include_md or len(data.channels) > 1:
                queue.append(commands.create_get_md_state(channel))
            if len(_get_ai_support(data.capabilities.channels[channel])) > 0:
                queue.append(commands.create_get_ai_state_request(channel))
        idx = -1
        async for response in client.batch(queue):
            idx += 1
            if commands.is_get_md_response(response):
                motion_data: ChannelMotionData = data.channels[response.channel_id]
                motion_data.motion_state.detected = response.state
            elif commands.is_get_ai_state_response(response):
                motion_data: ChannelMotionData = data.channels[response.channel_id]
                if response.can_update(motion_data.motion_state.ai):
                    motion_data.motion_state.ai.update(response.state)
                else:
                    motion_data.motion_state.ai = response.state
        for motion_data in data.channels.items():
            motion_data.motion_coordinator.async_set_updated_data(
                motion_data.motion_coordinator.data
            )
        entry_data.onvif_fetch_task = None

    # sometimes the cameras fail to send the IsMotion false (or possibly it gets lost)
    # so we will "debounce" a final refresh while IsMotion is true
    if motion:

        async def _force_refresh():
            nonlocal include_md
            del entry_data.onvif_notify_debounce
            include_md = True
            await _fetch_actual_motion()

        entry_data.onvif_notify_debounce = async_track_point_in_utc_time(
            hass, _force_refresh, dt.utcnow() + MOTION_DEBOUCE
        )

    if len(data.channels) == 1:
        motion_data: ChannelMotionData = data.channels[0]
        motion_data.motion_state.detected = motion
        motion_data.motion_coordinator.async_set_updated_data(
            motion_data.motion_coordinator.data
        )

    if len(filter(lambda c: len(_get_ai_support(c)) > 0, data.channels.values())) > 0:
        if motion:
            try:
                fetch_task = entry_data.onvif_fetch_task
            except AttributeError:
                fetch_task = None
            if fetch_task is not None:
                # if we have a task pending we will bail on a motion update
                # incase updates come in faster than the API will give us results
                # as spamming a camera is a good way to cause errors
                return None
        # we add the fetch as a task so we can return as quick as possible
        entry_data.onvif_fetch_task = hass.async_create_task(_fetch_actual_motion())

    return None

        if _capabilities.onvif:
            # platform = async_get_current_platform()
            self = await async_get_integration(hass, DOMAIN)

            webhooks = async_get_webhook_manager(hass)
            if webhooks is not None:
                webhook = webhooks.async_register(hass, config_entry)
                config_entry.async_on_unload(
                    webhook.async_add_handler(_handle_onvif_notify)
                )
                push = async_get_push_manager(hass)
                subscription = None

                async def _async_sub():
                    nonlocal subscription
                    subscription = await push.async_subscribe(webhook.url, config_entry)
                    update_coordinators()

                resub_cleanup = None
                onvif_warned = False

                def _sub_failure(entry_id: str, method: str, code: str, reason: str):
                    nonlocal subscription, resub_cleanup, onvif_warned
                    if entry_id != config_entry.entry_id or config_entry.data is None:
                        return

                    if subscription is not None:
                        subscription = None
                        update_coordinators()
                    subscription = None
                    if not coordinator.data.ports.onvif.enabled:
                        if not onvif_warned:
                            onvif_warned = True
                            coordinator.logger.warning(
                                "ONVIF not enabled for device %s, forcing polling mode",
                                coordinator.data.device["name"],
                            )
                        async_create_issue(
                            hass,
                            DOMAIN,
                            "onvif_disabled",
                            is_fixable=True,
                            severity=IssueSeverity.WARNING,
                            translation_key="onvif_disabled",
                            translation_placeholders={
                                "entry_id": config_entry.entry_id,
                                "name": data.device["name"],
                                "configuration_url": data.device["configuration_url"],
                            },
                            learn_more_url=self.documentation + "/ONVIF",
                        )

                    def _sub_resub():
                        nonlocal resub_cleanup
                        resub_cleanup()
                        resub_cleanup = None
                        hass.create_task(_async_sub())

                    resub_cleanup = coordinator.async_add_listener(_sub_resub)

                sub_fail_cleanup = push.async_on_subscription_failure(_sub_failure)

                await _async_sub()

                def _unsubscribe():
                    sub_fail_cleanup()
                    if resub_cleanup is not None:
                        resub_cleanup()  # pylint: disable=not-callable
                    if subscription is not None:
                        hass.create_task(push.async_unsubscribe(subscription))

                config_entry.async_on_unload(_unsubscribe)
