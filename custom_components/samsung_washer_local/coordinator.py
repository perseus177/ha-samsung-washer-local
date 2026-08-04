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
    shown_temperature,
    written_temperature,
)
from .const import COURSE_MAP, DOMAIN, ENERGY_INTERVAL, STATE_READY

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
        # What the programme/temperature/rinse/spin selects are pointing at. Held here and
        # not on the appliance - see the pending-selection section below.
        self._pending_programme: str | None = None
        self._pending_settings: dict[str, str] = {}

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

    # ------------------------------------------------------------------ pending selection
    #
    # The appliance takes a programme only together with a start, so the dashboard cannot
    # simply have a "programme" select that writes: one tap would run a wash. What it has
    # instead is this - a choice held in Home Assistant, mirroring how the app works, where
    # you pick programme, temperature, rinse and spin and *then* press start. Nothing here
    # touches the appliance until the Start selected programme button is pressed.

    def selected_programme(self) -> str | None:
        """Return the course code the selects are pointing at.

        Defaults to whatever is on the dial, so the dropdowns mean something the moment the
        appliance is first read, rather than being empty until touched.
        """
        if self._pending_programme:
            return self._pending_programme
        return (self.data.course_code or None) if self.data else None

    def allowed_for(self, field: str) -> list[str]:
        """Return the values the selected programme allows for one setting."""
        code = (self.selected_programme() or "").upper()
        options = (self.data.course_options if self.data else {}).get(code, {})
        return list((options.get(field) or {}).get("allowed") or [])

    def supported_temperatures(self) -> list[str]:
        """Return the temperature tokens the appliance says it has."""
        return list(self.data.supported_water_temperature if self.data else [])

    def default_for(self, field: str) -> str | None:
        """Return the selected programme's own default for one setting."""
        code = (self.selected_programme() or "").upper()
        options = (self.data.course_options if self.data else {}).get(code, {})
        return (options.get(field) or {}).get("default")

    def pending(self, field: str) -> str | None:
        """Return the chosen value for a setting, falling back to the programme's default.

        Clamped to what the programme allows: choosing 60 degrees under Cotton and then
        switching to Super Eco, which is cold only, must not leave 60 selected.
        """
        allowed = self.allowed_for(field)
        chosen = self._pending_settings.get(field)
        if chosen is not None and chosen in allowed:
            return chosen
        default = self.default_for(field)
        return default if default in allowed else (allowed[0] if allowed else None)

    def set_pending_programme(self, code: str) -> None:
        """Point the selects at another programme, dropping settings it does not allow.

        Listeners are notified straight away. The three setting selects derive their whole
        option list from this choice, and without the push they would keep showing the old
        programme's values until the next poll came round - up to the poll interval of
        staleness for something that changed instantly and locally.
        """
        self._pending_programme = code.upper()
        self._pending_settings = {}
        self.async_update_listeners()

    def set_pending(self, field: str, value: str) -> None:
        """Record a chosen setting, and republish so every select agrees at once."""
        self._pending_settings[field] = value
        self.async_update_listeners()

    async def async_start_selected(self) -> None:
        """Start the programme the selects are pointing at.

        A setting still sitting on the programme's own default is not sent. The appliance
        applies that default itself when nothing arrives for it, so the request would say
        nothing new - and it is not free: a start whose body carries settings at all loads
        the programme but leaves the appliance Ready (see async_start_cycle), which costs a
        second write to recover from. Someone starting a programme without touching the
        three settings therefore gets the plain, measured-good request.
        """
        code = self.selected_programme()
        if not code:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="nothing_selected",
            )
        chosen = {field: self.pending(field) for field in ("temperature", "rinse", "spin")}
        await self.async_start_cycle(
            code,
            **{
                field: value
                for field, value in chosen.items()
                if value is not None and value != self.default_for(field)
            },
        )

    def _resolve_programme(self, programme: str) -> str:
        """Turn what the user asked for into a course code the appliance knows.

        Both spellings are accepted: the name the Programme sensor shows (``drum_clean``)
        and the raw code behind it (``63``), because the code is what a model outside the
        calibrated map has to be driven by.
        """
        wanted = programme.strip().lower()
        for code, name in COURSE_MAP.items():
            if wanted in (name, code.lower()):
                return code
        known = self.data.course_options if self.data else {}
        if wanted.upper() in known:
            return wanted.upper()
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="unknown_programme",
            translation_placeholders={
                "programme": programme,
                "known": ", ".join(sorted(COURSE_MAP.values())),
            },
        )

    def _validate(self, code: str, settings: dict[str, str | None]) -> dict[str, str]:
        """Drop unset settings and refuse ones this programme does not allow.

        The appliance publishes what each programme allows in supportedOptions, so this is
        checked against the device rather than a hardcoded table - and it is checked at all
        because the appliance answers an impossible combination with a silent 204.
        """
        allowed_for_course = (self.data.course_options if self.data else {}).get(code, {})
        checked: dict[str, str] = {}
        for field, value in settings.items():
            if value is None:
                continue
            if field == "temperature":
                # 70 is what the panel, the app and this integration call Drum Clean's
                # temperature; the appliance calls it 60. Accept either from a script.
                value = written_temperature(
                    code, str(value), self.supported_temperatures()
                )
            allowed = (allowed_for_course.get(field) or {}).get("allowed") or []
            if allowed and str(value) not in allowed:
                offered = allowed
                if field == "temperature":
                    # Name them the way the rest of the integration does, or the message
                    # would tell someone to use a 60 the dropdown does not even show.
                    offered = [
                        str(shown_temperature(code, value, self.supported_temperatures()))
                        for value in allowed
                    ]
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="option_not_allowed",
                    translation_placeholders={
                        "field": field,
                        "value": str(value),
                        "programme": COURSE_MAP.get(code, code),
                        "allowed": ", ".join(offered),
                    },
                )
            checked[field] = str(value)
        return checked

    async def async_start_cycle(
        self,
        programme: str,
        temperature: str | None = None,
        rinse: str | None = None,
        spin: str | None = None,
    ) -> None:
        """Select a programme and start it, then publish the resulting state."""
        code = self._resolve_programme(programme)
        settings = self._validate(
            code, {"temperature": temperature, "rinse": rinse, "spin": spin}
        )
        # Remote Control is the appliance's own consent to being driven, and starting a
        # wash is the one action where getting this wrong matters. Checked here rather
        # than left to the appliance because refusal is not consistent: some firmware
        # answers 403 SHE-001, and some simply drops the write and reports 204 - which
        # would surface as "the appliance ignored the programme", pointing at the wrong
        # thing entirely when the cause is a button on the panel. Only an explicit False
        # blocks; an unknown value is not treated as a refusal.
        if self.data and self.data.remote_control_enabled is False:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="remote_control_off",
                translation_placeholders={"host": self.host},
            )
        if self.data and self.data.state not in (None, STATE_READY):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_ready",
                translation_placeholders={"state": self.data.state or "unknown"},
            )
        try:
            await self.client.async_start_cycle(code, settings)
        except WasherError as err:
            raise self._user_error(err, "write_failed") from err
        await self.async_request_refresh()

    async def async_set_add_wash(self, value: str) -> None:
        """Set the AddWash token, then publish the resulting state."""
        try:
            await self.client.async_set_add_wash(value)
        except WasherError as err:
            raise self._user_error(err, "write_failed") from err
        await self.async_request_refresh()

    async def async_set_laundry_out_time(self, minutes: str) -> None:
        """Set the Laundry Out reminder, then publish the resulting state."""
        try:
            await self.client.async_set_laundry_out_time(minutes)
        except WasherError as err:
            raise self._user_error(err, "write_failed") from err
        await self.async_request_refresh()
