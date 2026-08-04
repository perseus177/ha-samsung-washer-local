# Samsung Washer Local

Home Assistant integration for **local, cloud-free** control of Samsung washing
machines that expose the legacy REST API on port 8888 — the generation that speaks
token-authenticated HTTPS rather than CoAP-DTLS.

It reads things the SmartThings cloud integration does not expose at all, including
the **wash programme**, the **progress percentage** and the **water temperature, spin
speed and rinse count**, and it can **start, pause and resume** a cycle without the
cloud being involved.

> Relevant if you are looking at this because of Samsung's SmartThings API pricing:
> everything below works with the internet disconnected.

## What works and what does not

| | |
|---|---|
| Read state, programme, progress, parameters, error codes, identity | ✅ |
| Start / pause / resume the programme selected on the dial | ✅ |
| Set the Laundry Out reminder (0/30/60/90 min) | ✅ |
| Cancel a running cycle | ⚠️ implemented, unverified — see below |
| **Select the wash programme remotely** | ❌ not possible |
| Set temperature / spin / rinse remotely | ❌ not possible |

The Cancel button exists and is wired up, but it has never been confirmed against a
running cycle. It does, however, know to leave an idle appliance alone: writing `Ready` to
one that is already idle is not the no-op it looks like — measured on a TP6X_WW6500, a
single such write moved it to Pause and reset the hand-dialled temperature and rinse count
to the programme's defaults — so Cancel reads the state first and writes nothing when there
is nothing to cancel. The reason it is not simply a third state to write: `Operation.state` has
only ever been observed to hold `Ready`, `Run` and `Pause`, and no value for "cancel"
turned up anywhere in the appliance's own vocabulary. The button therefore writes `Ready`
and then, if that changes nothing, `Stop`, and judges the outcome by the appliance ending
up in `Ready` rather than by it echoing back what was written — a cancel naturally lands
in `Ready`, so comparing against the written value would report a false failure. If it
does not work on your appliance you will get a clear error saying what each attempt left
it in; please report that.

Programme selection is a different matter. A `Course_XX` write on its own is answered
`204 No Content` and then discarded — many shapes were tried, on both the resource and the
aggregate endpoint, wrapped and bare. What is now known is *why* that may be: the official
app never writes the programme on its own either. It always sends it in one body together
with `Operation.state`, alongside the temperature, spin and rinse settings:

```json
{"Device":{"Mode":{"options":["Course_5C"]},"Operation":{"state":"Run"},
           "Washer":{"rinseCycles":"2","waterTemperature":"40","spinLevel":"1400"}}}
```

So the appliance may well accept a programme change *as part of starting a cycle*, which
would also explain why isolated temperature, spin and rinse writes are discarded. Sending
that shape with a state that starts nothing (`Ready`) does **not** work — measured. Whether
it works with `Run` is untested here, because it would start a real wash. Until then:
**a start runs whatever is set on the physical dial**, so if you automate starting, read the
programme sensor first and check it is what you expect.

## ⚠️ When the appliance is on the network — and when it is not

This trips up everyone, so it is worth being precise. The appliance is not a device that
is simply "on the network"; it joins and leaves depending on what it is doing:

| Appliance | Reachable? |
|---|---|
| No power (switched off at the wall, or on a smart plug that is off) | **No** — no power, no Wi-Fi. Nothing in software helps. |
| Powered but switched off at the panel | **No.** It reports as offline; press the power button first. |
| Switched on, idle, Remote Control **off** | **Briefly.** It associates for a few minutes and then leaves the network again. |
| Switched on, idle, Remote Control **on** | **Yes**, it holds the connection. |
| **Running a cycle** | **Yes, for the whole cycle** — Remote Control is *not* needed. |

So entities going unavailable between washes is normal, and it is not a signal problem.
The integration treats it that way too: an appliance that stops answering is **recorded
once, as a warning** — not as an error, and not again on every following poll — and the
entities simply go unavailable until it answers again. Only a response that cannot be
explained by the appliance being away is reported as an error.

Two consequences worth knowing:

- **For continuous monitoring, switch Remote Control on** at the appliance (the door has
  to be closed). Do *not* hold the button down: a long press starts Wi-Fi onboarding
  (`AP` blinks on the display) and disconnects the machine from your network.
- **For following a wash you do not need it.** Once a programme is running, the appliance
  stays reachable until the cycle ends, whether Remote Control is on or not — so progress,
  phase and remaining time are readable without changing anything on the panel.

Also give the appliance a **fixed IP** in your DHCP server. It does not send a
hostname, and the integration addresses it by IP.

While Remote Control is off, some appliances go quiet and others answer every request
with `403 SHE-001 "current function of WiFi is disabled, please enable the function for
controlling"`. Both mean the same thing and have the same remedy; the credentials are
fine. The integration recognises that response and says so, in the log and on the
configuration form, rather than passing the error code on.

## Requirements

* A washing machine with **8888/tcp open**. Check with
  `nmap -Pn -p 8888 <ip>`, or simply `curl -k https://<ip>:8888/` — a reply of
  `400 No required SSL certificate was sent` from nginx is exactly the right answer.
  Newer appliances (Tizen RT 3.x / DAWIT 3.0+, 2023 and later) instead answer
  DTLS-CoAP on UDP 49152–49160 and are **not** supported here; see
  [localthings](https://github.com/mbillow/localthings) for those.
* A **client certificate** signed by a CA the appliance trusts, and a **device token**.
  Both are produced by the scripts in [`tools/`](tools).

### Obtaining the credentials

```bash
# 1) mint a client certificate (needs the openssl command line tool)
python tools/setup_cert.py
#    -> certs/client_fullchain.pem, certs/client.key

# 2) ask the appliance for a device token; run this on a machine on the same LAN,
#    with inbound TCP 8889 open, and with Remote Control switched on at the appliance
python tools/get_token.py 192.168.0.173 --listen-ip 192.168.0.10
#    -> Device token: xxxxxxxxxx
```

The certificate works because the appliance's own TLS handshake advertises which
client CAs it accepts — `DeviceCA`, `RemoteAccessCA(CE)`, `CECA`, `ROOTCA` — and one
intermediate under that chain, `AC14K_M`, is publicly mirrored **together with its
private key**. `setup_cert.py` fetches it and signs a leaf for the UUID that Samsung's
own infrastructure uses, which the appliance's factory ACL grants full access to.

The token flow is a callback: the appliance connects back to *you* on port 8889 and
posts the token. It takes the address from the **`Host` header** of your request, not
from the source address, so the request and the listener must be the same machine and
the header must be set — `get_token.py` handles both.

Keep the token and the private key secret. Together they grant full local control.

## Installation

### HACS

HACS → ⋮ → *Custom repositories* → add `https://github.com/perseus177/ha-samsung-washer-local`
as an *Integration*, install, restart Home Assistant, then
*Settings → Devices & services → Add integration → Samsung Washer Local*.

### Manual

Copy `custom_components/samsung_washer_local/` into your `config/custom_components/`
and restart Home Assistant.

## Entities

| Entity | Notes |
|---|---|
| `sensor` Programme | Enum; the raw code is kept in the `course_code` attribute |
| `sensor` State | `ready` / `run` / `pause` |
| `sensor` Phase | `none` / `delaywash` / `weightsensing` / `prewash` / `predrain` / `wash` / `rinse` / `spin` / `steaming` / `drying` / `cooling` / `finish` |
| `sensor` Progress | Percent. Tracks elapsed time, not actual washing progress |
| `sensor` Remaining time | Minutes |
| `sensor` Finishes at | Timestamp, derived from the remaining time; only while running |
| `sensor` Water temperature, Spin speed, Rinse cycles | Read-only |
| `binary_sensor` Power, Remote control, Child lock, AddWash available, Alarm | Alarm carries the appliance's error codes in an attribute |
| `binary_sensor` Prewash, Delayed start | Read from `supportedProgress`, see below |
| `binary_sensor` AddWash | The feature's own on/off (`AddWashSet`) |
| `binary_sensor` AddWash indicator | The blinking panel lamp — **disabled by default**, it flips every few seconds on its own |
| `sensor` Diagnosis | `Diagnosis.diagnosisStart`; also carries every raw `Mode.options` token as attributes |
| `sensor` Consumption counter | The appliance's own counter, **no unit** — see below |
| `button` Start, Pause, Cancel | Start also resumes from pause |
| `select` Laundry Out reminder | `0` / `30` / `60` / `90` minutes |

Everything the appliance exposes is covered: `resources` lists exactly eight
(`Alarms`, `Configuration`, `Diagnosis`, `EnergyConsumption`, `Information`, `Mode`,
`Operation`, `Washer`) and every field of each is either an entity or an attribute.
Values that do not fit their sensor's type — `Cold`/`None` for the temperature,
`NoSpin`/`RinseHold` for the spin — are kept in a `raw` attribute so nothing is lost.

Entities are identified by the appliance's serial number, taken from the config entry
rather than from a live read — a restart while the appliance is away must not change what
anything is called. **Up to and including 1.0.6 it could:** a restart during one of those
absences produced a second device with a duplicate set of entities (`..._2`). From 1.0.7
those duplicates are renamed back onto the serial where that is possible, and where both
sets exist the leftovers are named in the log and the duplicate device can be deleted from
its device page.

The wash programme, the state and the phase are enum sensors, and the vocabulary
differs between models and firmware families. A value this integration does not know
is shown as `unknown` with the appliance's own wording in a `raw` attribute (the
programme keeps its `course_code`) and logged once, rather than breaking the entity.
Please open an issue with that raw value so it can be added.

### Which panel options are readable

`Operation.supportedProgress` is **not** a static capability list: the appliance adds
and removes entries as options are selected, which is the only way some of them become
visible. `Prewash` and `Delaywash` appear when those options are chosen; `Rinse`
disappears when the rinse count is 0 and `Spin` when `RinseHold` is selected.

**Bubble Soak, stain wash and the intensive option are not readable on this
generation.** Newer appliances carry `BubbleSoak_On`/`_Off` tokens in the options
array; this firmware never reports them, and cycling those buttons changes nothing in
the API except the remaining-time estimate.

### The consumption counter has no unit

`EnergyConsumption` only holds a file path. The numbers are in `/files/usage.db`, a
base64-encoded SQLite database with one hourly row in
`power_usage_table(date, power_usage, running_time)` — `date` is `YYYYMMDDHH`,
`power_usage` is a monotonically increasing counter, `running_time` is unused.

The counter is exposed **without a unit or device class on purpose.** Its scale could
not be established: a day's rise measured against a metering plug on the same
appliance came out at roughly 7 Wh per count, which is not a round number, and the
database snapshot lags the running cycle. Publishing it as kWh would feed a
plausible-looking wrong number into the energy dashboard. If you want real energy
figures, meter the socket.

The database is re-read every 15 minutes rather than on every poll — it is a 21 kB
transfer for a counter that moves once an hour.

Controls stay available while the appliance is offline and report a clear error when
pressed, rather than disappearing — the machine is off the network most of the time it
is idle.

## Model compatibility

Developed and verified against a **TP6X_WW6500** (EU, 14-position dial). The protocol
is shared across the appliance family, but **the wash programme codes are
model-specific**. An unrecognised code shows as `unknown` with the raw value in the
`course_code` attribute, so nothing breaks — and that attribute is what you need in
order to extend the map in `const.py` for another model. Pull requests adding models
are welcome; please include the dial order and the code for each position.

## Notes on the API

The appliance emits a malformed response header — `X-API-Version : v1.0.0`, with a
space before the colon — which strict HTTP parsers reject. This integration therefore
speaks HTTP over raw asyncio streams and ignores header lines it cannot parse.

`204 No Content` from the appliance means "request accepted", **not** "setting
applied": several resources answer 204 and silently discard the value. Every write in
this integration is verified by reading the resource back.

## Licence

MIT — see [LICENSE](LICENSE). Attribution and prior work: [NOTICE](NOTICE).
