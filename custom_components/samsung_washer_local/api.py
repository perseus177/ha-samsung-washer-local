"""Client for the local REST API a Samsung washing machine exposes on port 8888.

The appliance runs an nginx that demands a client certificate signed by one of
Samsung's own CAs, plus a device token in an Authorization header. It speaks TLS 1.0
only and presents a 1024-bit RSA certificate, so the SSL context has to be relaxed
well below today's defaults - hence the explicit minimum version and SECLEVEL=0.

Response semantics that matter, learned the hard way:

* ``204 No Content`` means "request accepted", NOT "setting applied". Several
  resources answer 204 and silently discard the value. Every write therefore has to
  be verified by reading the resource back; this module never reports success on the
  strength of a 204 alone.
* ``400 {"errorDescription": "Control fail, <Mode.options=X>"}`` means the appliance
  really did try to apply the listed options and refused them.

HTTP is spoken over raw asyncio streams rather than through aiohttp on purpose: the
appliance emits a malformed response header - ``X-API-Version : v1.0.0``, with a space
before the colon - which strict parsers reject outright ("Invalid header token"). The
tiny parser below simply ignores header lines it cannot make sense of.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import sqlite3
import ssl
import tempfile
from dataclasses import dataclass, field
from typing import Any

from .const import (
    CANCEL_STATE,
    COURSE_MAP,
    MIN_MAX_OPTION_TYPES,
    OPTION_FIELDS,
    START_PATH,
    STATE_READY,
    STATE_RUN,
    WRITABLE_SETTINGS,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15


class WasherError(Exception):
    """Any failure while talking to the appliance."""


class WasherAuthError(WasherError):
    """The certificate or the device token was rejected."""


class WasherOfflineError(WasherError):
    """The appliance is not reachable.

    This is an everyday occurrence, not a defect. The appliance joins and leaves the
    network according to what it is doing: unpowered or switched off at the panel it is
    not there at all; switched on but idle it stays for a few minutes and then leaves
    again unless Remote Control is on; and while a cycle is running it stays reachable
    throughout, Remote Control or not.
    """


class WasherControlDisabledError(WasherOfflineError):
    """The appliance answered, but refuses to serve while its Wi-Fi control is off.

    Some firmware families reject every request with ``403 SHE-001 "current function
    of WiFi is disabled, please enable the function for controlling"`` until Remote
    Control (Smart Control) is switched on at the panel; others answer reads regardless
    and merely leave the network. Either way this is the appliance working as designed
    and something only the owner can change, so it is treated as the same everyday
    "not talking to us" condition as being offline - hence the subclass, which also
    keeps any handler that only knows about WasherOfflineError correct.
    """


@dataclass(frozen=True)
class WasherState:
    """A snapshot of everything the local API exposes."""

    # Both are raw, as the appliance words them ("Run", "Prewash"): the enum sensors
    # normalise them, and every other consumer compares against the raw values.
    state: str | None = None
    progress: str | None = None
    progress_percentage: int | None = None
    remaining_minutes: int | None = None
    power: str | None = None
    kids_lock: str | None = None
    water_temperature: int | None = None
    spin_level: int | None = None
    rinse_cycles: int | None = None
    course_code: str | None = None
    course: str | None = None
    laundry_out_time: str | None = None
    add_wash_available: bool | None = None
    remote_control_enabled: bool | None = None
    # Both settings can hold named values as well as numbers - "Cold"/"None" for the
    # temperature, "NoSpin"/"RinseHold" for the spin - which do not fit a numeric
    # sensor. The raw string is kept so nothing is lost.
    water_temperature_raw: str | None = None
    spin_level_raw: str | None = None
    add_wash_available_raw: str | None = None
    # Not a static capability list: the appliance adds and removes entries as options
    # are selected on the panel, which is the only way "Prewash" and "Delaywash"
    # become visible from outside.
    supported_progress: list[str] = field(default_factory=list)
    alarms: list[Any] = field(default_factory=list)
    model_id: str | None = None
    serial_number: str | None = None
    software_version: str | None = None
    # Remaining Mode.options tokens. AddWashSet is the AddWash feature's own on/off
    # (the naming mirrors Samsung's "<Feature>Set" / "<Feature>AvailableSet" pairs);
    # AddWashIndicator is the blinking panel lamp and flips on its own every few
    # seconds, so it is offered only as a disabled-by-default diagnostic.
    add_wash_set: str | None = None
    add_wash_indicator: str | None = None
    # Read-only. QuickWash_Not_Used means the appliance has no such feature; any other
    # value means it has. The official app only ever reads this - it has no write for it.
    quick_wash: str | None = None
    diagnosis: str | None = None
    # Per programme: which temperature/rinse/spin values it allows and its own default,
    # decoded from supportedOptions. Keyed by course code.
    course_options: dict[str, Any] = field(default_factory=dict)
    # Every token, so nothing the appliance reports is lost even where the meaning
    # is unknown (EnergyKW, NoCheck_SC, DeviceType, QuickWash, TimeSync, UsagesDB).
    mode_options: list[str] = field(default_factory=list)
    supported_options: list[str] = field(default_factory=list)
    supported_water_temperature: list[str] = field(default_factory=list)
    supported_spin_level: list[str] = field(default_factory=list)
    supported_rinse_cycles: list[str] = field(default_factory=list)
    # From /files/usage.db - see async_read_energy for why there is no unit.
    energy_counter: int | None = None
    energy_last_record: str | None = None
    energy_first_record: str | None = None
    energy_records: int | None = None


def _build_ssl_context(cert_pem: str, key_pem: str) -> ssl.SSLContext:
    """Build the SSL context for the appliance (blocking - run in an executor).

    ``load_cert_chain`` can only read from the filesystem, so the PEMs are written
    to a private temporary directory that is removed again immediately.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with contextlib.suppress(ValueError):
        # The appliance offers TLS 1.0 only. Some builds refuse to go this low, in
        # which case the handshake will fail later with a clearer error.
        context.minimum_version = ssl.TLSVersion.TLSv1
    context.set_ciphers("DEFAULT@SECLEVEL=0")
    with tempfile.TemporaryDirectory() as tmp:
        cert_path = os.path.join(tmp, "client.pem")
        key_path = os.path.join(tmp, "client.key")
        with open(cert_path, "w", encoding="utf-8") as handle:
            handle.write(cert_pem)
        with open(key_path, "w", encoding="utf-8") as handle:
            handle.write(key_pem)
        os.chmod(key_path, 0o600)
        context.load_cert_chain(cert_path, key_path)
    return context


def _parse_usage_db(encoded: str) -> dict[str, Any] | None:
    """Decode the base64 SQLite database and return the newest counter row.

    Blocking (base64 + sqlite + a temp file) - always call from an executor. A temp
    file is used rather than ``Connection.deserialize`` so this also works on Python
    releases that do not have it.
    """
    try:
        blob = base64.b64decode(encoded, validate=False)
    except (ValueError, TypeError):
        return None
    if not blob.startswith(b"SQLite format 3"):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "usage.sqlite")
        with open(path, "wb") as handle:
            handle.write(blob)
        try:
            connection = sqlite3.connect(path)
            try:
                cursor = connection.execute(
                    "SELECT date, power_usage, COUNT(*), MIN(date) FROM power_usage_table"
                    " WHERE date = (SELECT MAX(date) FROM power_usage_table)"
                )
                row = cursor.fetchone()
                cursor = connection.execute(
                    "SELECT COUNT(*), MIN(date) FROM power_usage_table"
                )
                total, first = cursor.fetchone()
            finally:
                connection.close()
        except sqlite3.Error:
            return None
    if not row or row[0] is None:
        return None
    return {
        "counter": row[1],
        "last_record": str(row[0]),
        "first_record": str(first),
        "records": total,
    }


def _parse_minutes(value: str | None) -> int | None:
    """Turn the appliance's ``HH:MM:SS`` into whole minutes."""
    if not value:
        return None
    parts = value.split(":")
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return None
    if len(numbers) == 3:
        hours, minutes, seconds = numbers
    elif len(numbers) == 2:
        hours, minutes, seconds = numbers[0], numbers[1], 0
    else:
        return None
    return hours * 60 + minutes + (1 if seconds >= 30 else 0)


def _parse_int(value: Any) -> int | None:
    """Return an int, or None for the appliance's non-numeric placeholders."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _option(options: list[str], prefix: str) -> str | None:
    """Return the suffix of the first ``prefix``-prefixed entry in Mode.options."""
    for option in options:
        if option.startswith(prefix):
            return option[len(prefix) :]
    return None


def _parse_course_options(
    blob: str, supported: dict[str, list[str]]
) -> dict[str, dict[str, Any]]:
    """Decode supportedOptions into what each programme allows.

    Returns ``{course_code: {field: {"default": value, "allowed": [values]}}}``. The layout
    is described in const.py; the values are produced by indexing into the appliance's own
    ``supported<X>`` lists, so a model with different settings decodes just as well. An
    unparsable blob returns nothing rather than raising - it is a convenience, and a poll
    must not fail over it.
    """
    if not blob or not blob[0].isdigit():
        return {}
    per_course = int(blob[0], 10)
    record_length = 2 + per_course * 4
    body = blob[1:]
    if not record_length or len(body) % record_length:
        _LOGGER.debug("supportedOptions does not divide into records: %r", blob)
        return {}

    courses: dict[str, dict[str, Any]] = {}
    for start in range(0, len(body), record_length):
        record = body[start : start + record_length]
        code = record[:2].upper()
        fields: dict[str, Any] = {}
        for offset in range(2, len(record), 4):
            try:
                raw = int(record[offset : offset + 4], 16)
            except ValueError:
                continue
            option_type = raw >> 12 & 0xF
            default_index = raw >> 8 & 0xF
            available = raw & 0xFF
            if option_type not in OPTION_FIELDS:
                continue
            field, attribute = OPTION_FIELDS[option_type]
            values = supported.get(attribute) or []
            if option_type in MIN_MAX_OPTION_TYPES:
                low, high = available & 0xF, available >> 4
                allowed = [values[i] for i in range(low, high + 1) if i < len(values)]
            else:
                allowed = [
                    values[bit]
                    for bit in range(8)
                    if available >> bit & 1 and bit < len(values)
                ]
            fields[field] = {
                "default": values[default_index] if default_index < len(values) else None,
                "allowed": allowed,
            }
        if fields:
            courses[code] = fields
    return courses


class SamsungWasherClient:
    """Talks to one appliance."""

    def __init__(self, host: str, token: str, port: int) -> None:
        """Initialise the client. The SSL context is built lazily in an executor."""
        self._host = host
        self._token = token
        self._port = port
        self._ssl_context: ssl.SSLContext | None = None
        self._lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        """Return the base URL of the appliance."""
        return f"https://{self._host}:{self._port}"

    async def async_prepare(self, cert_pem: str, key_pem: str) -> None:
        """Build the SSL context (file I/O, so it runs in an executor)."""
        loop = asyncio.get_running_loop()
        self._ssl_context = await loop.run_in_executor(
            None, _build_ssl_context, cert_pem, key_pem
        )

    async def async_close(self) -> None:
        """Nothing to close - every request uses its own short-lived connection."""

    async def _async_roundtrip(
        self, method: str, path: str, payload: dict[str, Any] | None
    ) -> tuple[int, bytes]:
        """Send one request over a fresh TLS connection and return status and body."""
        body = b"" if payload is None else json.dumps(payload).encode()
        lines = [
            f"{method} {path} HTTP/1.1",
            f"Host: {self._host}:{self._port}",
            f"Authorization: Bearer {self._token}",
            "Accept: */*",
            "Connection: close",
        ]
        if payload is not None:
            lines += ["Content-Type: application/json", f"Content-Length: {len(body)}"]
        request = ("\r\n".join(lines) + "\r\n\r\n").encode() + body

        reader, writer = await asyncio.open_connection(
            self._host, self._port, ssl=self._ssl_context
        )
        try:
            writer.write(request)
            await writer.drain()

            status_line = await reader.readline()
            if not status_line:
                raise WasherError("the appliance closed the connection without replying")
            parts = status_line.decode("latin1").split(None, 2)
            if len(parts) < 2 or not parts[1].isdigit():
                raise WasherError(f"unparsable status line: {status_line!r}")
            status = int(parts[1])

            length: int | None = None
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                # Header lines that do not parse are skipped rather than fatal - the
                # appliance sends "X-API-Version : v1.0.0", which is not valid HTTP.
                name, separator, value = line.decode("latin1").partition(":")
                if separator and name.strip().lower() == "content-length":
                    with contextlib.suppress(ValueError):
                        length = int(value.strip())

            if length is not None:
                payload_bytes = await reader.readexactly(length) if length else b""
            else:
                payload_bytes = await reader.read()
            return status, payload_bytes
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _async_request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        """Perform one request and return the status and the decoded body."""
        if self._ssl_context is None:
            raise WasherError("client is not prepared")
        try:
            async with self._lock, asyncio.timeout(REQUEST_TIMEOUT):
                status, raw = await self._async_roundtrip(method, path, payload)
        # These carry the reason only, not the host: every caller already knows which
        # appliance it asked, and naming it here made the log line say it twice.
        except (TimeoutError, asyncio.IncompleteReadError) as err:
            raise WasherOfflineError("it timed out") from err
        except (OSError, ConnectionError) as err:
            raise WasherOfflineError(f"it is not reachable: {err}") from err
        except ssl.SSLError as err:
            raise WasherAuthError(f"the TLS handshake failed: {err}") from err

        body: Any = None
        if raw:
            try:
                body = json.loads(raw)
            except ValueError:
                body = raw.decode("utf-8", "replace")

        if status in (400, 401) and isinstance(body, dict):
            description = body.get("errorDescription", "")
            if "Token" in description or status == 401:
                raise WasherAuthError(description or "token is not valid")
        if status >= 500:
            # Seen once right after Remote Control was switched on at the panel:
            # 500 {"errorCode": "0", "errorDescription": "InvokeGetDeviceListFunc fail"},
            # then it answered normally again. That is the appliance's API still coming up,
            # which belongs in the everyday "not answering yet" family rather than being
            # logged as an error - it recovers on the next poll.
            description = body.get("errorDescription") if isinstance(body, dict) else None
            raise WasherOfflineError(
                f"it is not ready yet: {description or f'{status} from the appliance'}"
            )
        if status == 403 and isinstance(body, dict):
            # SHE-001: "current function of WiFi is disabled, please enable the function
            # for controlling". Recognised here, before the callers turn a non-200 into
            # a bare WasherError, so the message the user ends up seeing says what to do
            # instead of quoting the appliance's error dict.
            description = body.get("errorDescription", "")
            if body.get("errorCode") == "SHE-001" or "WiFi is disabled" in description:
                raise WasherControlDisabledError(
                    "it is refusing requests because its Wi-Fi control function is"
                    " switched off - switch Remote Control (Smart Control) on at the"
                    " panel, with the door closed"
                )
        if status == 400 and isinstance(body, str) and "certificate" in body:
            raise WasherAuthError("the client certificate was rejected")
        return status, body

    async def _async_get(self, path: str) -> dict[str, Any]:
        """GET a resource and return its payload, or raise."""
        status, body = await self._async_request("GET", path)
        if status != 200 or not isinstance(body, dict):
            raise WasherError(f"GET {path} returned {status}: {body}")
        return body

    async def async_read_state(self) -> WasherState:
        """Read every resource that carries state.

        Two requests, not one per resource: ``/devices`` embeds Operation, Washer,
        Mode, Alarms and Diagnosis in a single response, which matters on an appliance
        whose Wi-Fi drops at the slightest excuse. Configuration and Information are
        the exception - ``/devices`` only carries links to them - so
        remoteControlEnabled still needs its own request.
        """
        devices = (await self._async_get("/devices")).get("Devices") or []
        if not devices:
            raise WasherError("/devices returned no device")
        device = devices[0]
        operation = device.get("Operation", {})
        washer = device.get("Washer", {})
        mode = device.get("Mode", {})
        alarms = device.get("Alarms", [])
        diagnosis = device.get("Diagnosis", {})
        configuration = (await self._async_get("/devices/0/configuration")).get(
            "Configuration", {}
        )

        options: list[str] = mode.get("options", [])
        course_code = _option(options, "Course_")
        add_wash = _option(options, "AddWashAvailable_")
        progress = operation.get("progress")
        supported_lists = {
            "supported_water_temperature": list(washer.get("supportedWaterTemperature", [])),
            "supported_rinse_cycles": list(washer.get("supportedRinseCycles", [])),
            "supported_spin_level": list(washer.get("supportedSpinLevel", [])),
            "supported_water_height": list(washer.get("supportedWaterHeight", [])),
            "supported_wash_time": list(washer.get("supportedWashTime", [])),
        }
        supported_options = list(mode.get("supportedOptions", []))

        return WasherState(
            state=operation.get("state"),
            # Kept exactly as reported; the sensor maps it onto its option list.
            progress=progress if isinstance(progress, str) else None,
            progress_percentage=_parse_int(operation.get("progressPercentage")),
            remaining_minutes=_parse_minutes(operation.get("remainingTime")),
            power=operation.get("power"),
            kids_lock=operation.get("kidsLock"),
            water_temperature=_parse_int(washer.get("waterTemperature")),
            spin_level=_parse_int(washer.get("spinLevel")),
            rinse_cycles=_parse_int(washer.get("rinseCycles")),
            course_code=course_code,
            course=COURSE_MAP.get(course_code or "", "unknown") if course_code else None,
            laundry_out_time=_option(options, "LaundryOutTime_"),
            add_wash_available=None if add_wash is None else add_wash != "0",
            remote_control_enabled=configuration.get("remoteControlEnabled"),
            alarms=alarms if isinstance(alarms, list) else [],
            water_temperature_raw=washer.get("waterTemperature"),
            spin_level_raw=washer.get("spinLevel"),
            add_wash_available_raw=add_wash,
            supported_progress=[
                str(stage) for stage in operation.get("supportedProgress", [])
            ],
            add_wash_set=_option(options, "AddWashSet_"),
            add_wash_indicator=_option(options, "AddWashIndicator_"),
            quick_wash=_option(options, "QuickWash_"),
            diagnosis=diagnosis.get("diagnosisStart"),
            mode_options=list(options),
            supported_options=supported_options,
            supported_water_temperature=supported_lists["supported_water_temperature"],
            supported_spin_level=supported_lists["supported_spin_level"],
            supported_rinse_cycles=supported_lists["supported_rinse_cycles"],
            course_options=(
                _parse_course_options(supported_options[0], supported_lists)
                if supported_options
                else {}
            ),
        )

    async def async_read_information(self) -> dict[str, Any]:
        """Read the identity of the appliance (model, serial, firmware)."""
        return (await self._async_get("/devices/0/information")).get("Information", {})

    async def async_read_energy(self) -> dict[str, Any] | None:
        """Read the appliance's own consumption counter.

        ``EnergyConsumption`` only carries a file path; the numbers live in
        /files/usage.db, a base64-encoded SQLite database with one hourly row in
        ``power_usage_table(date, power_usage, running_time)``. ``date`` is
        YYYYMMDDHH, ``power_usage`` is a monotonically increasing counter and
        ``running_time`` is unused (always 0) on this model.

        The unit of ``power_usage`` is deliberately not asserted anywhere: comparing
        a day's increase against a metering plug on the same appliance came out at
        roughly 7 Wh per count, which is not a round number, and the snapshot appears
        to lag behind the live cycle. It is therefore surfaced as a bare counter.
        """
        status, body = await self._async_request("GET", "/files/usage.db")
        if status != 200 or not isinstance(body, str):
            raise WasherError(f"reading the usage database returned {status}")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _parse_usage_db, body)

    async def _async_write_state(self, state: str) -> str | None:
        """Write Operation.state and return what the appliance reports afterwards."""
        status, body = await self._async_request(
            "PUT", "/devices/0/operation", {"Operation": {"state": state}}
        )
        if status not in (200, 204):
            raise WasherError(f"setting state={state} returned {status}: {body}")
        await asyncio.sleep(3)
        current = (await self._async_get("/devices/0/operation")).get("Operation", {})
        return current.get("state")

    async def async_set_operation_state(self, state: str) -> None:
        """Start, pause or resume the programme selected on the dial.

        The write is verified by reading the state back, because the appliance
        answers 204 whether or not it applied anything.
        """
        reported = await self._async_write_state(state)
        if reported != state:
            raise WasherError(
                f"the appliance ignored state={state} (it reports {reported})"
            )

    async def async_cancel(self) -> None:
        """Cancel the running cycle.

        There is no dedicated cancel value; a Ready write does it, and only from a running
        cycle. What each starting state does was measured on a TP6X_WW6500:

        * from Run   - lands in Ready. Cancelled.
        * from Pause - accepted and ignored. Nothing to do but resume it first, or end it
          at the panel; saying so is more use than a write that does nothing.
        * from Ready - actively harmful. It moves the appliance to Pause and resets the
          temperature, spin and rinse selections to the programme's defaults, throwing
          away whatever was dialled in at the panel. So an idle appliance is left alone.
        """
        state = (await self._async_get("/devices/0/operation")).get("Operation", {}).get(
            "state"
        )
        if state == STATE_READY:
            _LOGGER.debug("Nothing to cancel: %s is already idle", self._host)
            return
        if state != STATE_RUN:
            raise WasherError(
                f"the appliance is {state}, and a cancel only takes effect on a running"
                " cycle - resume it first, or end the cycle at the panel"
            )
        reported = await self._async_write_state(CANCEL_STATE)
        if reported != STATE_READY:
            raise WasherError(
                f"the appliance did not cancel the cycle: it reports {reported}"
            )

    async def async_start_cycle(
        self, course_code: str, settings: dict[str, str] | None = None
    ) -> None:
        """Start a programme, optionally with settings, exactly as the official app does.

        One PUT carrying the programme, the Run state and any settings. The programme
        cannot be written on its own - see START_PATH in const.py - and settings sent on
        their own are discarded too, which is why they ride along here. Anything left
        unspecified is not sent at all: the appliance then applies that programme's own
        defaults, which is what it does for the app as well.

        Verified by reading back, because a 204 means nothing on this transport.

        Measured on a TP6X_WW6500 on 2026-08-04: when the body carries a ``Washer`` block,
        the appliance loads the programme and the settings, beeps, and stays Ready - the
        Run is dropped. The same request without that block starts the cycle. Since a bare
        Operation.state write is the ordinary Start button and does work, a start that ends
        up selected-but-not-running is followed by exactly that, rather than reported as a
        failure the user can only fix by pressing again. It is not sent speculatively: on
        the appliance's own admission it is already running, and asking a running machine
        to Run again is not something to do on a guess.
        """
        body: dict[str, Any] = {
            "Mode": {"options": [f"Course_{course_code.upper()}"]},
            "Operation": {"state": STATE_RUN},
        }
        washer = {
            WRITABLE_SETTINGS[field]: str(value)
            for field, value in (settings or {}).items()
            if field in WRITABLE_SETTINGS and value is not None
        }
        if washer:
            body["Washer"] = washer

        status, response = await self._async_request("PUT", START_PATH, {"Device": body})
        if status not in (200, 204):
            raise WasherError(f"starting {course_code} returned {status}: {response}")

        await asyncio.sleep(3)
        device = (await self._async_get(START_PATH)).get("Device", {})
        options = device.get("Mode", {}).get("options", [])
        started = _option(options, "Course_")
        state = device.get("Operation", {}).get("state")
        if (started or "").upper() != course_code.upper():
            raise WasherError(
                f"the appliance ignored the programme (it reports {started},"
                f" state {state})"
            )
        if state == STATE_RUN:
            return

        _LOGGER.debug(
            "%s loaded %s but stayed %s; starting it with a state write",
            self._host,
            course_code,
            state,
        )
        reported = await self._async_write_state(STATE_RUN)
        if reported != STATE_RUN:
            raise WasherError(
                f"the programme was selected but the appliance did not start it"
                f" (it reports {reported})"
            )

    async def async_set_add_wash(self, value: str) -> None:
        """Set the AddWash feature's own on/off token, verifying it afterwards.

        Written on its own, like the Laundry Out reminder - AddWash is not part of a cycle,
        and the official app writes it standalone too.
        """
        status, body = await self._async_request(
            "PUT", "/devices/0/mode", {"Mode": {"options": [f"AddWashSet_{value}"]}}
        )
        if status not in (200, 204):
            raise WasherError(f"setting AddWashSet returned {status}: {body}")
        await asyncio.sleep(2)
        mode = (await self._async_get("/devices/0/mode")).get("Mode", {})
        current = _option(mode.get("options", []), "AddWashSet_")
        if current != value:
            raise WasherError(
                f"the appliance ignored AddWashSet={value} (it reports {current})"
            )

    async def async_set_laundry_out_time(self, minutes: str) -> None:
        """Set the Laundry Out reminder interval, verifying it afterwards."""
        status, body = await self._async_request(
            "PUT",
            "/devices/0/mode",
            {"Mode": {"options": [f"LaundryOutTime_{minutes}"]}},
        )
        if status not in (200, 204):
            raise WasherError(f"setting LaundryOutTime returned {status}: {body}")
        await asyncio.sleep(2)
        mode = (await self._async_get("/devices/0/mode")).get("Mode", {})
        current = _option(mode.get("options", []), "LaundryOutTime_")
        if current != minutes:
            raise WasherError(
                f"the appliance ignored LaundryOutTime={minutes} (it reports {current})"
            )
