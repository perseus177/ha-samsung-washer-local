"""Coordinator - polls the appliance and tolerates it being offline."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import timedelta
from typing import Any

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
from .const import DOMAIN, ENERGY_INTERVAL

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
        # The usage database is a ~21 kB base64 payload for a counter that only moves
        # once an hour, so it is fetched on its own slow cadence and carried over
        # between polls rather than being pulled on every one.
        self._energy: dict[str, Any] = {}
        self._energy_read: float = 0.0

    async def _async_update_data(self) -> WasherState | None:
        """Read the state.

        An unreachable appliance is expected behaviour - it leaves the Wi-Fi within
        minutes of going idle unless Remote Control is on - so it is logged at debug
        level and merely makes the entities unavailable.
        """
        try:
            state = await self.client.async_read_state()
            return replace(state, **await self._async_energy())
        except WasherAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except WasherOfflineError as err:
            _LOGGER.debug("%s is offline: %s", self.host, err)
            raise UpdateFailed(str(err)) from err
        except WasherError as err:
            raise UpdateFailed(str(err)) from err

    async def _async_energy(self) -> dict[str, Any]:
        """Return the energy fields, refreshing them at most every 15 minutes.

        A failure here is never fatal - the counter is a nice-to-have and the last
        known values are kept - so it is logged at debug level and swallowed.
        """
        now = self.hass.loop.time()
        if not self._energy or now - self._energy_read > ENERGY_INTERVAL:
            try:
                usage = await self.client.async_read_energy()
            except WasherError as err:
                _LOGGER.debug("Could not read the usage database: %s", err)
            else:
                self._energy_read = now
                if usage:
                    self._energy = {
                        "energy_counter": usage["counter"],
                        "energy_last_record": usage["last_record"],
                        "energy_first_record": usage["first_record"],
                        "energy_records": usage["records"],
                    }
        return self._energy

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

    async def async_cancel(self) -> None:
        """Cancel the running cycle, then publish the resulting state."""
        try:
            await self.client.async_cancel()
        except WasherOfflineError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="offline",
                translation_placeholders={"host": self.host},
            ) from err
        except WasherError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cancel_failed",
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
