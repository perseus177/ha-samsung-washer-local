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
import contextlib
import json
import logging
import os
import ssl
import tempfile
from dataclasses import dataclass, field
from typing import Any

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15


class WasherError(Exception):
    """Any failure while talking to the appliance."""


class WasherAuthError(WasherError):
    """The certificate or the device token was rejected."""


class WasherOfflineError(WasherError):
    """The appliance is not reachable.

    This is an everyday occurrence, not a defect: the Wi-Fi module only stays
    associated while Remote Control is enabled on the appliance.
    """


@dataclass(frozen=True)
class WasherState:
    """A snapshot of everything the local API exposes."""

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
    alarms: list[Any] = field(default_factory=list)
    model_id: str | None = None
    serial_number: str | None = None
    software_version: str | None = None


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
        """Read every resource that carries state."""
        operation = (await self._async_get("/devices/0/operation")).get("Operation", {})
        washer = (await self._async_get("/devices/0/washer")).get("Washer", {})
        mode = (await self._async_get("/devices/0/mode")).get("Mode", {})
        configuration = (await self._async_get("/devices/0/configuration")).get(
            "Configuration", {}
        )
        alarms = (await self._async_get("/devices/0/alarms")).get("Alarms", [])

        options: list[str] = mode.get("options", [])
        course_code = _option(options, "Course_")
        add_wash = _option(options, "AddWashAvailable_")
        progress = operation.get("progress")

        from .const import COURSE_MAP  # local import keeps const free of api imports

        return WasherState(
            state=operation.get("state"),
            progress=progress.lower() if isinstance(progress, str) else None,
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
        )

    async def async_read_information(self) -> dict[str, Any]:
        """Read the identity of the appliance (model, serial, firmware)."""
        return (await self._async_get("/devices/0/information")).get("Information", {})

    async def async_set_operation_state(self, state: str) -> None:
        """Start, pause or resume the programme selected on the dial.

        The write is verified by reading the state back, because the appliance
        answers 204 whether or not it applied anything.
        """
        status, body = await self._async_request(
            "PUT", "/devices/0/operation", {"Operation": {"state": state}}
        )
        if status not in (200, 204):
            raise WasherError(f"setting state={state} returned {status}: {body}")
        await asyncio.sleep(3)
        current = (await self._async_get("/devices/0/operation")).get("Operation", {})
        if current.get("state") != state:
            raise WasherError(
                f"the appliance ignored state={state} (it reports {current.get('state')})"
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
