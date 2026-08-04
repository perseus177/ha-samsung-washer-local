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
# a fault. A short interval is still the right default, because the case that matters
# is a running cycle: the appliance stays reachable for the whole wash whether Remote
# Control is on or not, and that is when the progress and remaining time move.
DEFAULT_POLL_INTERVAL = 30

# The usage database holds an hourly counter, so re-reading it more often than
# this only wastes a 21 kB transfer on an appliance with a fragile Wi-Fi link.
ENERGY_INTERVAL = 900

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
# ⚠️ Writing STATE_READY is only safe on an appliance that is *not* already idle: on an
# idle one it moves the appliance to Pause and resets the temperature, spin and rinse
# selections back to the programme's defaults. async_cancel therefore reads the state
# first and writes nothing when it is already Ready.
CANCEL_STATES = (STATE_READY, "Stop")

# Operation.progress and Operation.state feed enum sensors, and an enum sensor raises
# if it is handed a value outside its option list - which takes the entity down rather
# than showing something odd. Both lists are therefore kept wide, in cycle order, and
# anything still unrecognised is mapped to "unknown" with the raw value in an attribute
# (see _enum in sensor.py). The extra stages are the ones Samsung appliances have been
# seen to report: Prewash and Delaywash appear on this washer as options are selected,
# Weightsensing and Predrain on other washers, Steaming on steam models, and
# Drying/Cooling on washer-dryer combos.
UNKNOWN = "unknown"

PROGRESS_OPTIONS = [
    "none",
    "delaywash",
    "weightsensing",
    "prewash",
    "predrain",
    "wash",
    "rinse",
    "spin",
    "steaming",
    "drying",
    "cooling",
    "finish",
    UNKNOWN,
]
STATE_OPTIONS = ["ready", "run", "pause", UNKNOWN]

# "Laundry Out" reminder: the drum tumbles periodically after the cycle ends until
# the laundry is taken out. 0 disables it. Writable through /devices/0/mode.
LAUNDRY_OUT_VALUES = ["0", "30", "60", "90"]

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.SENSOR,
]
