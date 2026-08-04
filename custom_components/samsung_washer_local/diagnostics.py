"""Diagnostics for Samsung Washer Local."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from . import SamsungWasherConfigEntry
from .const import CONF_CERT_PEM, CONF_KEY_PEM, CONF_TOKEN

TO_REDACT = {CONF_TOKEN, CONF_CERT_PEM, CONF_KEY_PEM}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SamsungWasherConfigEntry
) -> dict[str, Any]:
    """Return diagnostics, with the credentials removed.

    The token and the private key are redacted rather than partially masked - they
    grant full local control of the appliance.
    """
    coordinator = entry.runtime_data
    return {
        "entry": {
            key: ("**REDACTED**" if key in TO_REDACT else value)
            for key, value in entry.data.items()
        },
        "options": dict(entry.options),
        "information": coordinator.information,
        # Both, because they mean different things here: the appliance being away is not
        # counted as a failed update, so last_update_success can be true while the
        # appliance is unreachable and the entities are unavailable.
        "last_update_success": coordinator.last_update_success,
        "reachable": coordinator.reachable,
        "state": asdict(coordinator.data) if coordinator.data else None,
    }
