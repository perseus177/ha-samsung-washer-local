"""Constants for the Samsung Washer Local integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "samsung_washer_local"

CONF_TOKEN = "token"
CONF_CERT_PEM = "cert_pem"
CONF_KEY_PEM = "key_pem"
CONF_POLL_INTERVAL = "poll_interval"

DEFAULT_PORT = 8888
# The appliance drops off the network within minutes whenever it is idle and its
# Remote Control is switched off, so a failed poll is the normal case rather than
# a fault. A short interval keeps a running cycle responsive without being able
# to keep the Wi-Fi module awake anyway.
DEFAULT_POLL_INTERVAL = 30

MANUFACTURER = "Samsung"

# Wash programmes are identified by a one-byte course code that is specific to the
# model/firmware family. This map was calibrated empirically on a TP6X_WW6500 (EU,
# 14-position dial) by reading /devices/0/mode for every dial position; the full
# revolution closed on itself and the code set matched the dial exactly.
# An unknown code is surfaced as "unknown" with the raw value in an attribute, so a
# different model degrades gracefully instead of breaking the entity.
COURSE_MAP: dict[str, str] = {
    "5B": "cotton",
    "5C": "extra_quick",
    "5D": "super_eco",
    "5E": "delicates",
    "5F": "baby_care",
    "60": "outdoor",
    "61": "dark_garments",
    "63": "drum_clean",
    "64": "rinse_spin",
    "65": "wool",
    "66": "bedding",
    "67": "synthetics",
    "68": "eco_cotton",
    "6C": "denim",
}
COURSE_OPTIONS: list[str] = sorted(set(COURSE_MAP.values())) + ["unknown"]

# Operation.state values observed on the appliance. "Run" doubles as resume from
# "Pause".
STATE_READY = "Ready"
STATE_RUN = "Run"
STATE_PAUSE = "Pause"

# No value for "cancel the cycle" has been observed on this appliance, so both
# plausible ones are tried in order and success is judged by it landing in Ready.
CANCEL_STATES = (STATE_READY, "Stop")

PROGRESS_OPTIONS = ["none", "wash", "rinse", "spin", "finish"]
STATE_OPTIONS = ["ready", "run", "pause"]

# "Laundry Out" reminder: the drum tumbles periodically after the cycle ends until
# the laundry is taken out. 0 disables it. Writable through /devices/0/mode.
LAUNDRY_OUT_VALUES = ["0", "30", "60", "90"]

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.SENSOR,
]
