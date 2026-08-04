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

from .const import CANCEL_STATES, COURSE_MAP, STATE_READY

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
    diagnosis: str | None = None
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
        except (TimeoutError, asyncio.IncompleteReadError) as err:
            raise WasherOfflineError(f"{self._host} timed out") from err
        except (OSError, ConnectionError) as err:
            raise WasherOfflineError(f"{self._host} is not reachable: {err}") from err
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
        if status == 403 and isinstance(body, dict):
            # SHE-001: "current function of WiFi is disabled, please enable the function
            # for controlling". Recognised here, before the callers turn a non-200 into
            # a bare WasherError, so the message the user ends up seeing says what to do
            # instead of quoting the appliance's error dict.
            description = body.get("errorDescription", "")
            if body.get("errorCode") == "SHE-001" or "WiFi is disabled" in description:
                raise WasherControlDisabledError(
                    "the appliance is refusing requests because its Wi-Fi control"
                    " function is switched off - switch Remote Control (Smart Control)"
                    " on at the panel, with the door closed"
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
            diagnosis=diagnosis.get("diagnosisStart"),
            mode_options=list(options),
            supported_options=list(mode.get("supportedOptions", [])),
            supported_water_temperature=list(washer.get("supportedWaterTemperature", [])),
            supported_spin_level=list(washer.get("supportedSpinLevel", [])),
            supported_rinse_cycles=list(washer.get("supportedRinseCycles", [])),
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

        Unlike start and pause, the value that cancels a cycle has not been observed
        on this appliance - only Ready, Run and Pause are known. Both plausible ones
        are therefore tried in turn, and success is judged by the appliance ending up
        in Ready rather than by it echoing back what was written (a cancel naturally
        lands in Ready, so comparing against the written value would report a false
        failure).
        """
        errors: list[str] = []
        for candidate in CANCEL_STATES:
            reported = await self._async_write_state(candidate)
            if reported == STATE_READY:
                return
            errors.append(f"state={candidate} left it in {reported}")
        raise WasherError("the appliance did not cancel the cycle: " + "; ".join(errors))

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
