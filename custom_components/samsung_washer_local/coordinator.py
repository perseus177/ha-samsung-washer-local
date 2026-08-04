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
    WasherControlDisabledError,
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
        level and merely makes the entities unavailable. An appliance that refuses to
        serve while its Wi-Fi control function is off is the same kind of everyday
        condition, only self-reported; both make the entities unavailable, which is
        honest, but the message has to say what the owner can do about it rather than
        quote a 403 body.
        """
        try:
            state = await self.client.async_read_state()
            return replace(state, **await self._async_energy())
        except WasherAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except WasherControlDisabledError as err:
            _LOGGER.debug("%s has its Wi-Fi control switched off: %s", self.host, err)
            raise UpdateFailed(str(err)) from err
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

    def _user_error(self, err: WasherError, failure_key: str) -> HomeAssistantError:
        """Turn a failed write into the message the user should see.

        The two reasons the appliance is simply not listening - it left the network, or
        it refuses to serve while its Wi-Fi control function is off - get their own
        wording, because "the appliance did not apply the change" would send the owner
        looking for a fault that is not there.
        """
        if isinstance(err, WasherControlDisabledError):
            return HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="control_disabled",
                translation_placeholders={"host": self.host},
            )
        if isinstance(err, WasherOfflineError):
            return HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="offline",
                translation_placeholders={"host": self.host},
            )
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=failure_key,
            translation_placeholders={"details": str(err)},
        )

    async def async_set_operation_state(self, state: str) -> None:
        """Start, pause or resume, then publish the resulting state."""
        try:
            await self.client.async_set_operation_state(state)
        except WasherError as err:
            raise self._user_error(err, "write_failed") from err
        await self.async_request_refresh()

    async def async_cancel(self) -> None:
        """Cancel the running cycle, then publish the resulting state."""
        try:
            await self.client.async_cancel()
        except WasherError as err:
            raise self._user_error(err, "cancel_failed") from err
        await self.async_request_refresh()

    async def async_set_laundry_out_time(self, minutes: str) -> None:
        """Set the Laundry Out reminder, then publish the resulting state."""
        try:
            await self.client.async_set_laundry_out_time(minutes)
        except WasherError as err:
            raise self._user_error(err, "write_failed") from err
        await self.async_request_refresh()
