"""Config flow for Samsung Washer Local."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    SamsungWasherClient,
    WasherAuthError,
    WasherControlDisabledError,
    WasherError,
    WasherOfflineError,
)
from .const import (
    CONF_CERT_PEM,
    CONF_KEY_PEM,
    CONF_POLL_INTERVAL,
    CONF_TOKEN,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

MULTILINE = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT, multiline=True))

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_TOKEN): str,
        vol.Required(CONF_CERT_PEM): MULTILINE,
        vol.Required(CONF_KEY_PEM): MULTILINE,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
    }
)


async def _async_validate(data: Mapping[str, Any]) -> dict[str, Any]:
    """Connect to the appliance and read its identity.

    Raises the API errors unchanged so the caller can map them onto form errors.
    """
    client = SamsungWasherClient(
        host=data[CONF_HOST],
        token=data[CONF_TOKEN],
        port=data.get(CONF_PORT, DEFAULT_PORT),
    )
    await client.async_prepare(data[CONF_CERT_PEM], data[CONF_KEY_PEM])
    try:
        return await client.async_read_information()
    finally:
        await client.async_close()


class SamsungWasherConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the host and the credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                information = await _async_validate(user_input)
            except WasherAuthError:
                errors["base"] = "invalid_auth"
            except WasherControlDisabledError:
                # Before cannot_connect, and well before invalid_certificate: the
                # credentials are fine, the appliance just will not serve yet.
                errors["base"] = "control_disabled"
            except WasherOfflineError:
                errors["base"] = "cannot_connect"
            except (WasherError, OSError, ValueError) as err:
                # Debug, not an error: the usual cause is a mistyped credential, and
                # the user already gets a message on the form.
                _LOGGER.debug("Validation failed: %s", err, exc_info=True)
                errors["base"] = "invalid_certificate"
            else:
                serial = information.get("serialNumber")
                if serial:
                    await self.async_set_unique_id(serial)
                    self._abort_if_unique_id_configured(
                        updates={CONF_HOST: user_input[CONF_HOST]}
                    )
                model = information.get("modelID", "").split("|")[0]
                return self.async_create_entry(
                    title=model or "Samsung washing machine", data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input or {}
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle a rejected token or certificate."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a fresh token, keeping the certificate."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **user_input}
            try:
                await _async_validate(data)
            except WasherAuthError:
                errors["base"] = "invalid_auth"
            except WasherControlDisabledError:
                # Before cannot_connect, and well before invalid_certificate: the
                # credentials are fine, the appliance just will not serve yet.
                errors["base"] = "control_disabled"
            except WasherOfflineError:
                errors["base"] = "cannot_connect"
            except (WasherError, OSError, ValueError) as err:
                # Debug, not an error: the usual cause is a mistyped credential, and
                # the user already gets a message on the form.
                _LOGGER.debug("Validation failed: %s", err, exc_info=True)
                errors["base"] = "invalid_certificate"
            else:
                return self.async_update_reload_and_abort(entry, data_updates=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            errors=errors,
            description_placeholders={CONF_HOST: entry.data[CONF_HOST]},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SamsungWasherOptionsFlow:
        """Return the options flow."""
        return SamsungWasherOptionsFlow()


class SamsungWasherOptionsFlow(OptionsFlowWithReload):
    """Handle the poll interval."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_POLL_INTERVAL: int(user_input[CONF_POLL_INTERVAL])}
            )

        current = self.config_entry.options.get(
            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_POLL_INTERVAL, default=current): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=600, step=5, mode=NumberSelectorMode.BOX
                        )
                    )
                }
            ),
        )
