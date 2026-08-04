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
| **Select a programme and start it, with temperature / spin / rinse** | ✅ `samsung_washer_local.start_cycle` |
| Start / pause / resume the programme selected on the dial | ✅ |
| Cancel a running cycle | ✅ from a running cycle only — see below |
| Set the Laundry Out reminder (0/30/60/90 min) | ✅ |
| Switch AddWash on and off | ✅ |
| Set the programme *without* starting it | ❌ the appliance only accepts the two together |

### Choosing a programme on a dashboard

The app's own flow — pick programme, temperature, rinse, spin, then press start — exists as
entities, so no YAML is needed for the common case:

| Entity | |
|---|---|
| `select` **1. Programme** | The programmes *this* appliance advertises. Starts on whatever the dial says, so it means something untouched. |
| `select` **2. Water temperature**, **3. Rinse cycles**, **4. Spin speed** | **Only what the chosen programme allows.** Pick Drum Clean and the temperature list becomes `60` alone; pick Rinse + Spin and the temperature select goes unavailable, because that programme has none. |
| `select` **5. Laundry Out reminder** | The one that writes straight away, since the appliance takes this reminder on its own. Numbered with the rest because it is the last thing chosen before a wash. |
| `switch` **6. AddWash** | Writes straight away, like 5. Whether AddWash can actually be *used* depends on the programme — the appliance reports that separately, and it is not a gate: the switch was verified writable while a Drum Clean ran and availability read `0`. |
| `button` Start selected programme | Sends 1–4. |

The numbers are there because Home Assistant lists entities alphabetically and there is no
ordering to set; without them the spin speed sorts first and the programme third, which reads
backwards for something meant to be filled in from the top. All five share the configuration
category for the same reason: entities in different categories are listed in separate blocks,
so numbering alone would not have put them together.

Switching programme re-narrows the three lists and drops a value the new programme does not
allow: 60 °C chosen under Cotton does not survive a switch to Super Eco, which is cold only —
it becomes `Cold`, that programme's own default. **None of the four selects touch the
appliance**; nothing is sent until the button is pressed. The reason is the same one that
makes this whole section interesting: a programme is only accepted together with a start, so
a select that wrote would mean one tap on a dashboard starts a wash.

### Starting a cycle from a script

`samsung_washer_local.start_cycle` does the same in one action, for automations, because the
appliance accepts programme and start only together — a programme written on its own is answered
`204 No Content` and discarded, and so are isolated temperature, spin and rinse writes. This
is the request the official app makes, and the one this service makes:

```json
{"Device": {"Mode": {"options": ["Course_63"]},
            "Operation": {"state": "Run"},
            "Washer": {"waterTemperature": "60", "spinLevel": "400"}}}
```

```yaml
action: samsung_washer_local.start_cycle
target:
  device_id: <your washer>
data:
  programme: drum_clean      # or a raw course code, e.g. "63"
  temperature: "60"          # optional; omitted means the programme's own default
```

**Which settings a programme allows is read from the appliance, not guessed.**
`supportedOptions` carries, per programme, a bitmap of allowed temperatures, rinse counts and
spin speeds plus its own default, and the Programme sensor exposes the decoded set for the
currently selected programme as `allowed_temperature`, `allowed_rinse`, `allowed_spin` and
the matching `default_*` attributes. The service refuses a value the programme does not
allow, naming the ones it does — worth doing, because the appliance itself would answer an
impossible combination with a silent `204`. On a TP6X_WW6500 Drum Clean allows exactly 60 °C,
400 rpm and 2 rinses, while Rinse + Spin has no temperature at all.

Two conditions are checked before anything is sent, because in both cases the appliance's
own refusal would point at the wrong thing:

- **Remote Control has to be on.** It is the appliance's consent to being driven, and
  without it a start is either refused with `403 SHE-001` or dropped silently with a `204`
  — which would read as "the appliance ignored the programme" when the cause is a button on
  the panel. The service says which button. (An unknown Remote Control state is not treated
  as a refusal.)
- **The appliance has to be idle**, or a start would resume the programme already loaded
  rather than the one asked for.

### Cancelling

Cancel is a `Ready` write, and what that does depends entirely on the state it is sent from —
all three measured on a TP6X_WW6500:

| From | Result |
|---|---|
| **Run** | lands in `Ready`. The cycle is cancelled. |
| **Pause** | accepted and ignored. The button reports that the cycle has to be resumed first, or ended at the panel. |
| **Ready** | **harmful** — it moves the appliance to `Pause` and resets the hand-dialled temperature and rinse count to the programme's defaults. Nothing is written in this case. |

`Stop`, the other candidate value, is not one this firmware knows: it answers
`400 "Control fail"`, so it is not attempted.

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
| `sensor` Programme | Enum; attributes carry the raw `course_code` and, decoded from the appliance, the `allowed_*` / `default_*` settings for that programme |
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
| `select` 5. Laundry Out reminder | `0` / `30` / `60` / `90` minutes — writes straight to the appliance |
| `select` Programme / temperature / rinse / spin to start | The choice for the next start; see above. Writes nothing on its own |
| `button` Start selected programme | Starts that choice |
| `switch` 6. AddWash | Writable. A three-bit mask underneath (`0`–`7`), so the raw value is kept in an attribute; on writes `7`. `available_phases` carries which phases the current programme allows it in |
| `sensor` Quick wash | Read-only — the appliance reports whether it has a quick-wash preset, and the app has no write for it either |

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
