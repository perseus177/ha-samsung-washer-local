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
        # What the device and the entities are identified by. Deliberately not derived
        # from the appliance's identity read, which is allowed to fail - see
        # SamsungWasherEntity.__init__. The entry's unique id is the serial number; the
        # entry id is the fallback, because it cannot change either, whereas the host can.
        self.identity = entry.unique_id or entry.entry_id
        self.information: dict[str, str] = {}
        # Whether the appliance answered the last poll. Kept separately from
        # last_update_success because the appliance being away is not a failed update -
        # see _async_update_data - and the entities take their availability from this.
        self.reachable = True
        # The usage database is a ~21 kB base64 payload for a counter that only moves
        # once an hour, so it is fetched on its own slow cadence and carried over
        # between polls rather than being pulled on every one.
        self._energy: dict[str, Any] = {}
        self._energy_read: float = 0.0

    async def _async_update_data(self) -> WasherState | None:
        """Read the state.

        An absent appliance is expected behaviour, not a failure: between washes it is
        often switched off entirely, and even switched on it leaves the network within
        minutes of going idle unless Remote Control is on. An appliance that refuses to
        serve while its Wi-Fi control function is off (403 SHE-001) is the same everyday
        condition, only self-reported.

        Neither is raised as UpdateFailed, because the coordinator logs that at ERROR
        level, and an appliance behaving exactly as designed should not read as a fault.
        The appliance is marked unreachable instead - which is what makes the entities
        unavailable, see SamsungWasherEntity.available - and the last snapshot is kept so
        nothing is thrown away while it is gone. The disappearance is still recorded,
        once, at warning level: knowing when the appliance came and went is genuinely
        useful, while repeating it for every subsequent poll is not. The recovery is
        logged at the same level, so the pair is never half-visible.

        Anything that cannot be explained this way is a real failure and is raised, so it
        keeps the coordinator's own error handling.
        """
        try:
            state = await self.client.async_read_state()
        except WasherAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except WasherOfflineError as err:
            # WasherControlDisabledError is a subclass of it, hence the single branch.
            if self.reachable:
                _LOGGER.warning(
                    "The appliance at %s is not answering: %s", self.host, err
                )
                self.reachable = False
            else:
                _LOGGER.debug(
                    "The appliance at %s is still not answering: %s", self.host, err
                )
            return self.data
        except WasherError as err:
            raise UpdateFailed(str(err)) from err
        if not self.reachable:
            # Warning, not info, purely so it is visible wherever the disappearance was:
            # an installation whose log level hides info (`logger: default: warn`) would
            # otherwise see only the half that says something is wrong, and read a long
            # since-recovered outage as still going on.
            _LOGGER.warning("The appliance at %s is answering again", self.host)
            self.reachable = True
        return replace(state, **await self._async_energy())

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
