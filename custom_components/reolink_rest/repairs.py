"""Repairs platform"""

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers.issue_registry import async_get as async_get_issue_registry
from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow

from .const import CONF_USE_HTTPS, OPT_SSL, SSLMode


class FixRedirectFlow(RepairsFlow):
    """Handler for device redirection issues"""

    def __init__(self, use_ssl: bool):
        self._use_ssl = use_ssl

    async def async_step_init(self, user_input: dict[str, str] | None = None):
        """Handle first step"""
        issue_registry = async_get_issue_registry(self.hass)
        if issue := issue_registry.async_get_issue(self.handler, self.issue_id):
            self.context["placeholders"] = issue.translation_placeholders
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, str] | None = None):
        """Confirm choice"""
        if user_input is not None:
            config_entries = self.hass.config_entries
            entry = config_entries.async_get_entry(self.data["entry_id"])
            if entry is None:
                raise TypeError()
            data = entry.data.copy()
            if self._use_ssl:
                data[CONF_USE_HTTPS] = True
            else:
                try:
                    del data[CONF_USE_HTTPS]
                except KeyError:
                    pass
            config_entries.async_update_entry(entry, data=data)

            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders=self.context.get("placeholders", None),
        )


class FixSSLFlow(RepairsFlow):
    """Handler for SSL issues"""

    def __init__(self, ssl_mode: SSLMode) -> None:
        self._ssl_mode = ssl_mode

    async def async_step_init(self, user_input: dict[str, str] | None = None):
        """Handle first step"""
        issue_registry = async_get_issue_registry(self.hass)
        if issue := issue_registry.async_get_issue(self.handler, self.issue_id):
            self.context["placeholders"] = issue.translation_placeholders
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, str] | None = None):
        """Confirm choice"""
        if user_input is not None:
            config_entries = self.hass.config_entries
            entry = config_entries.async_get_entry(self.data["entry_id"])
            if entry is None:
                raise TypeError()
            options = entry.options.copy()
            options[OPT_SSL] = self._ssl_mode
            config_entries.async_update_entry(entry, options=options)

            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders=self.context.get("placeholders", None),
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None
):
    """Create flow"""

    if issue_id == "insecure_ssl":
        return FixSSLFlow(SSLMode.INSECURE)

    if issue_id == "weak_ssl":
        return FixSSLFlow(SSLMode.WEAK)

    if issue_id == "from_ssl_redirect":
        return FixRedirectFlow(False)

    if issue_id == "to_ssl_redirect":
        return FixRedirectFlow(True)

    return ConfirmRepairFlow()
