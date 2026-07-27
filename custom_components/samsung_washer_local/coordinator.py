"""Coordinator - polls the appliance and tolerates it being offline."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    SamsungWasherClient,
    WasherAuthError,
    WasherError,
    WasherOfflineError,
    WasherState,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class SamsungWasherCoordinator(DataUpdateCoordinator[WasherState | None]):
    """Keeps the latest state of one appliance."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: SamsungWasherClient,
        host: str,
        poll_interval: int,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=poll_interval) if poll_interval else None,
        )
        self.client = client
        self.host = host
        self.information: dict[str, str] = {}

    async def _async_update_data(self) -> WasherState | None:
        """Read the state.

        An unreachable appliance is expected behaviour - it leaves the Wi-Fi within
        minutes of going idle unless Remote Control is on - so it is logged at debug
        level and merely makes the entities unavailable.
        """
        try:
            return await self.client.async_read_state()
        except WasherAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except WasherOfflineError as err:
            _LOGGER.debug("%s is offline: %s", self.host, err)
            raise UpdateFailed(str(err)) from err
        except WasherError as err:
            raise UpdateFailed(str(err)) from err

    async def async_set_operation_state(self, state: str) -> None:
        """Start, pause or resume, then publish the resulting state."""
        try:
            await self.client.async_set_operation_state(state)
        except WasherOfflineError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="offline",
                translation_placeholders={"host": self.host},
            ) from err
        except WasherError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_failed",
                translation_placeholders={"details": str(err)},
            ) from err
        await self.async_request_refresh()

    async def async_set_laundry_out_time(self, minutes: str) -> None:
        """Set the Laundry Out reminder, then publish the resulting state."""
        try:
            await self.client.async_set_laundry_out_time(minutes)
        except WasherOfflineError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="offline",
                translation_placeholders={"host": self.host},
            ) from err
        except WasherError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_failed",
                translation_placeholders={"details": str(err)},
            ) from err
        await self.async_request_refresh()
