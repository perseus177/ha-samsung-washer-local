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
| **Select the wash programme remotely** | ❌ not possible |
| Set temperature / spin / rinse remotely | ❌ not possible |
| Cancel a running cycle | ❌ not implemented |

Programme selection is genuinely unavailable, not merely unimplemented. The appliance
accepts a write to `Course_XX` with `204 No Content` and then discards it; the official
app changes the programme over Samsung's private cloud channel
(`ocfclientcon…samsungiotcloud.com`), which a local client cannot reach. The same is
true of temperature, spin and rinse.

Because of that, **a start always runs whatever is set on the physical dial.** If you
automate starting, read the programme sensor first and check it is what you expect.

## ⚠️ The appliance only stays on Wi-Fi with Remote Control on

This trips up everyone. With Remote Control switched off, the appliance associates for
two to four minutes after being switched on and then leaves the network entirely — the
router stops listing it, the cloud reports it offline, and this integration's entities
go unavailable. It is not a fault and not a signal problem.

**Switch Remote Control on at the appliance** (the door must be closed) and it stays
connected. Do *not* hold the button down: a long press starts Wi-Fi onboarding (`AP`
blinks on the display) and disconnects the machine from your network.

Also give the appliance a **fixed IP** in your DHCP server. It does not send a
hostname, and the integration addresses it by IP.

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
| `sensor` Phase | `none` / `wash` / `rinse` / `spin` / `finish` |
| `sensor` Progress | Percent. Tracks elapsed time, not actual washing progress |
| `sensor` Remaining time | Minutes |
| `sensor` Finishes at | Timestamp, derived from the remaining time; only while running |
| `sensor` Water temperature, Spin speed, Rinse cycles | Read-only |
| `binary_sensor` Power, Remote control, Child lock, AddWash available, Alarm | Alarm carries the appliance's error codes in an attribute |
| `button` Start, Pause | Start also resumes from pause |
| `select` Laundry Out reminder | `0` / `30` / `60` / `90` minutes |

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
