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

# What the appliance's fault codes mean, so a code blinking on the panel does not send anyone
# to the manual. The grouping is the appliance's own: its app carries one dictionary of about
# 150 codes that collapse into these meanings, aliases and all - the same fault appears with
# an E and a C spelling (4E/4C), lower case (tE, oF, dS) and per-variant suffixes.
#
# The wording is deliberately ours. The app's own strings are Samsung's and are not shipped
# here; what a code means is a fact about the appliance, and that is what these describe. They
# are short on purpose: enough to know whether to open the tap, clean the filter or call
# someone. The codes are matched case-insensitively.
#
# "Needs a service visit" entries are the ones the app also gives up on: an internal sensor or
# board has failed and there is nothing to do at home beyond one power cycle. The rest are
# things the owner can act on.
_SERVICE = "Nothing to do at home beyond one power cycle: switch off, wait 2-3 minutes, switch on and try again. If it comes back, it needs a service visit."

FAULT_MEANINGS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    # Things the owner can fix.
    (
        ("4E", "4E1", "4C", "4C1", "NF"),
        "No water coming in - the fill timed out",
        "Check the tap is open, the inlet hose is not kinked and its filter is not blocked. In"
        " winter check the hose and tap are not frozen. Then press Start/Pause to carry on.",
    ),
    (
        ("4E2", "4C2", "NF1"),
        "Incoming water is too hot for the fabric",
        "Check the cold and hot inlet hoses are not swapped, then press Start/Pause.",
    ),
    (
        ("5E", "5C"),
        "The water is not draining",
        "Clean the drain filter and check the drain hose is not blocked, kinked or frozen. Then"
        " press Start/Pause.",
    ),
    (
        ("UE", "UB"),
        "The load is unbalanced, so it will not spin",
        "Redistribute the laundry, or add a little more if the drum is nearly empty, then press"
        " Start/Pause. Waterproof items cause this too - they trap water on one side.",
    ),
    (
        ("UB1",),
        "Waterproof items in the drum, so it will not spin",
        "Take them out: water cannot escape through them, so they stay on one side and the drum"
        " cannot balance.",
    ),
    (
        ("dS", "DS", "dE", "DE", "dC", "DC"),
        "The door is open",
        "Close it firmly, then press Start/Pause if the cycle does not carry on by itself.",
    ),
    (
        ("DDC",),
        "The AddWash door is open",
        "Close it, then press Start/Pause.",
    ),
    (
        ("DC4",),
        "The inner door is open",
        "Close it firmly, then press Start/Pause. If it keeps reporting this, it needs a"
        " service visit.",
    ),
    (
        ("SDE", "SDC"),
        "The automatic detergent drawer is open",
        "Close the detergent compartment.",
    ),
    (
        ("CL",),
        "Child lock is on",
        "Switch it off at the panel. Nothing can be started or cancelled while it is on.",
    ),
    (
        ("NC",),
        "The lint filter is missing",
        "Put it back in its slot.",
    ),
    (
        ("NC2",),
        "The heat exchanger filter is missing",
        "Put it back in its slot.",
    ),
    (
        ("LE", "LC", "LE1", "LC1"),
        "A water leak was detected",
        "Check around the drain filter and the hoses. Switch off and on after 2-3 minutes and"
        " try again; if it returns, it needs a service visit.",
    ),
    (
        ("OE", "oF", "OF", "OC"),
        "Overfilling - water kept coming in",
        "Close the tap and call service. Do not run it in this state.",
    ),
    (
        ("CE", "CC"),
        "The drum or the water is too hot for the next step",
        "Check the cold and hot inlet hoses are not swapped, then press Start/Pause.",
    ),
    (
        ("E2", "BE2", "bE2", "C2", "BC2"),
        "A button is stuck",
        "Check nothing is resting on the panel, then switch off and on.",
    ),
    (
        ("2E", "2C", "UC", "9E1", "9E2", "9C1", "9C2"),
        "Mains supply out of range",
        "Check the appliance is switched on at the socket and the supply is steady. If it"
        " persists, it needs a service visit.",
    ),
    (
        ("AE4", "AC4"),
        "The Wi-Fi module is not answering the appliance",
        "A network problem rather than a wash problem. Switch off and on after 2-3 minutes.",
    ),
    (
        ("4E3", "4C3"),
        "Diverter valve or cooling fan",
        "This can happen below -10 C: leave it at room temperature for half an hour and try"
        " again. Otherwise it needs a service visit.",
    ),
    # Sensors and boards - one power cycle, then service.
    (("1E", "1C"), "Water level sensor", _SERVICE),
    (
        ("tE", "tE1", "TE", "TE1", "tC", "tC1", "TC", "TC1"),
        "Wash temperature sensor",
        _SERVICE,
    ),
    (("TE2", "tE2", "TC2"), "Drying air temperature sensor", _SERVICE),
    (("TE3", "TC3"), "Drying duct temperature sensor", _SERVICE),
    (("TE4", "TC4"), "Motor temperature sensor", _SERVICE),
    (("TC5", "TE5"), "Compressor temperature sensor", _SERVICE),
    (("TC6",), "Water jet temperature sensor", _SERVICE),
    (("TC7",), "Heat exchanger inlet temperature sensor", _SERVICE),
    (("TC8",), "Heat exchanger outlet temperature sensor", _SERVICE),
    (("TCA",), "Compressor top temperature sensor", _SERVICE),
    (
        (
            "3E", "3E1", "3E2", "3E3", "3E4", "BE", "3C", "3C1", "3C2", "3C3", "3C4", "3C5",
            "3C6", "3C7", "3C8", "3C9", "BC", "3CP",
        ),
        "Motor",
        _SERVICE,
    ),
    (("3CA",), "Compressor", _SERVICE),
    (("PE", "PC"), "Clutch", _SERVICE),
    (("FE", "FC", "FC3"), "Drying fan motor", _SERVICE),
    (("HE", "HC"), "The heater is overheating", "Switch it off and call service."),
    (("HE1", "HC1"), "Wash heater", _SERVICE),
    (("HE2", "HC2"), "Drying heater", _SERVICE),
    (
        ("LO", "dE1", "DE1", "dC1", "DC1", "FL"),
        "Door lock switch",
        _SERVICE,
    ),
    (("dE2", "DE2", "dC2", "DC2"), "Door lock switch is reacting too often", _SERVICE),
    (("DF",), "Door lock switch", "Switch it off and call service."),
    (("DC3",), "AddWash door lock switch", _SERVICE),
    (
        (
            "8E", "8E1", "8E2", "8C", "8C1", "8C2", "8CA1", "8CA2", "8CA3", "8CA4", "8CB1",
            "8CB2", "8CB3", "8CB4",
        ),
        "Vibration sensor",
        _SERVICE,
    ),
    (("6E", "6C", "6C1", "6C2", "6C3", "6C4", "6C5", "6C6", "6C7"),
     "Automatic detergent dispenser motor", _SERVICE),
    (("AE", "AC"), "Communication with the display", _SERVICE),
    (("AE3", "AC3"), "Zigbee communication", _SERVICE),
    (("AE5", "AC5"), "Communication between the display and the main board", _SERVICE),
    (("AE6", "AC6"), "Communication between the inverter and the main board", _SERVICE),
    (("AC7",), "Communication between the boards", _SERVICE),
    (("ACA",), "Communication with the compressor inverter", _SERVICE),
    # The named alarms, which are reminders and warnings rather than faults.
    (
        ("DrumClean",),
        "Time to run the Drum Clean programme",
        "Run it on an empty drum. The appliance counts washes and asks about every 40.",
    ),
    (
        ("FilterAlarm",),
        "Water is left in the drain filter",
        "Open the flap at the bottom front and drain it, then clean the filter.",
    ),
    (
        ("FreezeProtection",),
        "Freezing risk - it is near 2 C where the appliance stands",
        "Empty the hoses and the residual water hose if it will stand unused, or run the"
        " appliance's own anti-freezing mode.",
    ),
    (
        ("AirwashWarning",),
        "The chosen programme cannot run with water in the drum",
        "Drain it first, then start the programme again.",
    ),
    (
        ("DispenserOpen",),
        "The detergent drawer is open",
        "Close it.",
    ),
    (
        ("DispenserAbnormal",),
        "The automatic dispenser is not working as expected",
        "Check it is clean and not empty. If it persists, it needs a service visit.",
    ),
    (
        ("HotWarning",),
        "The appliance raised its hot warning",
        "It clears itself. The app carries no explanation for this one, so treat it as"
        " information: something inside is hot.",
    ),
)
FAULT_CODES: dict[str, tuple[str, str]] = {
    code.upper(): (meaning, action)
    for codes, meaning, action in FAULT_MEANINGS
    for code in codes
}
# What the fault sensor reads when the appliance reports nothing wrong, chosen over "unknown"
# so a template can compare it without a guard.
FAULT_NONE = "none"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]
