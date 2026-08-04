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

# Cancelling is a Ready write, and it only does anything from a running cycle - measured:
#   from Run    -> the appliance lands in Ready. The cycle is cancelled.
#   from Pause  -> accepted and ignored; it stays paused.
#   from Ready  -> harmful. It moves to Pause and resets the temperature, spin and rinse
#                  selections back to the programme's defaults, discarding the choice.
# "Stop" was the other candidate and is simply not a value this firmware knows: it answers
# 400 "Control fail", which is the appliance saying it tried and refused, so it is not
# attempted any more.
CANCEL_STATE = STATE_READY

# The programme is set by sending it *with* Operation.state - never on its own. The official
# app has no other path: every Course_ write it makes carries a state, alongside the washer
# settings, in one body on /devices/0. Course on its own is answered 204 and discarded, and
# so are isolated temperature/spin/rinse writes. This is the shape that works:
#   {"Device": {"Mode": {"options": ["Course_5C"]},
#               "Operation": {"state": "Run"},
#               "Washer": {"waterTemperature": "40", ...}}}
START_PATH = "/devices/0"

# supportedOptions carries, per programme, which temperature / rinse / spin values that
# programme allows and which one is its default. Layout, from the official plugin's own
# parser: one hex digit saying how many option fields each record has, then a record per
# programme of two hex chars of course code followed by that many four-char fields. Each
# field is 16 bits: type in 15-12, the default as an index in 11-8, and a bitmap of allowed
# entries in 7-0. The indices and bits point into the supported<X> lists the appliance
# reports for itself, so nothing here is model-specific.
OPTION_TYPE_TEMPERATURE = 0x8
OPTION_TYPE_RINSE = 0x9
OPTION_TYPE_SPIN = 0xA

# Drum Clean runs at 70 degrees, and both the panel and the official app say so - but the
# waterTemperature slot has no "70" token, so the appliance reports the nearest one it does
# have, 60. Samsung resolves this in the app rather than in the appliance, and the plugin
# spells the rule out: isExceptTempCondidion / isEcoDrumClean in washer.js (~8782-8817) turn
# a 60 into the 70 string when the programme is one of the Drum Clean family AND the
# appliance's supportedWaterTemperature carries any of these high tokens - a stand-in for
# "this model has a heater that goes there". The comment on isEcoDrumClean says as much:
# "온도 70도 표기를 위한 함수" - a function for displaying 70 degrees. Selecting the programme
# forces it too (setTemp = "70" at ~16701, and checkSpecialEcodrum by model code).
# Reported as 70 here for the same reason: 60 is the transport's limitation, not the wash.
# The raw value stays on the sensor's raw attribute, and a write still sends 60.
DRUM_CLEAN_PROGRAMMES = ("drum_clean",)
DRUM_CLEAN_HIGH_TOKENS = ("70", "75", "80", "90", "95")
DRUM_CLEAN_TEMPERATURE_RAW = "60"
DRUM_CLEAN_TEMPERATURE_SHOWN = "70"

# 95 degrees is a hygiene wash, and the appliance's own app will not run one on fewer than two
# rinses: checkSpecialCase in washer.js (~17083) sets four rinses when 95 is picked, and clamps
# a hand-lowered count back up to two. It does that for one class of appliance, and this is one
# of them - DeviceType_0167, "medium size drum washer, Europe". Mirrored so the dropdown offers
# what the app offers, and so start_cycle refuses a combination the app would never send.
# Detergent needs rinsing out, and at 95 there is more of it dissolved.
HYGIENE_TEMPERATURE = "95"
HYGIENE_MIN_RINSE = 2
HYGIENE_RINSE = "4"
# These two carry a min and a max index in the low byte instead of a bitmap. Only top
# loaders report them, so they are decoded but not offered as service fields.
OPTION_TYPE_WATER_HEIGHT = 0x6
OPTION_TYPE_WASH_TIME = 0x7

OPTION_FIELDS: dict[int, tuple[str, str]] = {
    OPTION_TYPE_TEMPERATURE: ("temperature", "supported_water_temperature"),
    OPTION_TYPE_RINSE: ("rinse", "supported_rinse_cycles"),
    OPTION_TYPE_SPIN: ("spin", "supported_spin_level"),
    OPTION_TYPE_WATER_HEIGHT: ("water_height", "supported_water_height"),
    OPTION_TYPE_WASH_TIME: ("wash_time", "supported_wash_time"),
}
MIN_MAX_OPTION_TYPES = (OPTION_TYPE_WATER_HEIGHT, OPTION_TYPE_WASH_TIME)

# What the appliance calls the settings in a write, keyed by the service's own field names.
WRITABLE_SETTINGS = {
    "temperature": "waterTemperature",
    "rinse": "rinseCycles",
    "spin": "spinLevel",
    "water_height": "waterHeight",
    "wash_time": "washTime",
}

# The Add wash alarm is stored as one three-bit mask over the moments it fires at, so 0-7
# are all valid: 0 is the alarm off, 7 is all three moments. See switch.py.
ADD_WASH_NONE = "0"
ADD_WASH_ALL = "7"
# The appliance's own explanation of the feature, from the plugin's translations
# (WEBMOB_device_washer_add_wash_desc). Carried as an attribute because Home Assistant has
# no tooltip for an entity and the more-info dialog does show attributes.
ADD_WASH_DESCRIPTION = (
    "If you need to add clothes just for a rinse or spin, or want to add a special softener"
    " during the wash, the Add wash alarm reminds you when to add your laundry or softener"
    " during the wash cycle."
)

SERVICE_START_CYCLE = "start_cycle"
ATTR_PROGRAMME = "programme"
ATTR_TEMPERATURE = "temperature"
ATTR_RINSE = "rinse"
ATTR_SPIN = "spin"

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
    Platform.SWITCH,
]
