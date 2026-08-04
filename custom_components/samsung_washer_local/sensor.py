"""Sensor entities for Samsung Washer Local."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SamsungWasherConfigEntry
from .api import WasherState, shown_temperature
from .const import COURSE_OPTIONS, PROGRESS_OPTIONS, STATE_OPTIONS, UNKNOWN
from .coordinator import SamsungWasherCoordinator
from .entity import SamsungWasherEntity

PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)

# Values already logged as unrecognised, so a stage that shows up on every poll is
# reported once instead of filling the log.
_SEEN_UNKNOWN: set[str] = set()


def _enum(value: str | None, options: list[str]) -> str | None:
    """Map a value the appliance reports onto an enum sensor's option list.

    An enum sensor raises if it is given a value outside its options, which takes the
    entity down for good rather than merely showing something unexpected. Since the
    appliance's vocabulary differs between models and firmware families - a washer was
    reported publishing the stage "Prewash" - anything unrecognised becomes "unknown",
    with the raw value kept in an attribute and logged once so it can be added.
    """
    if not value:
        return None
    lowered = value.lower()
    if lowered in options:
        return lowered
    if lowered not in _SEEN_UNKNOWN:
        _SEEN_UNKNOWN.add(lowered)
        _LOGGER.warning(
            "The appliance reported %r, which this integration does not know yet;"
            " showing it as 'unknown'. Please report it at"
            " https://github.com/perseus177/ha-samsung-washer-local/issues",
            value,
        )
    return UNKNOWN


def _finish_time(state: WasherState) -> datetime | None:
    """Return when the cycle will end, derived from the remaining time.

    The appliance only reports a countdown, so the absolute time is computed here.
    It is published while a cycle is active - running or paused - and withheld when
    the appliance is merely Ready, where the countdown is only an estimate for the
    selected programme and would produce a finish time that keeps sliding.

    Paused is included on purpose: dashboards that show a progress bar between the
    start and the finish time need the value to survive a pause.
    """
    if state.remaining_minutes is None or state.state not in ("Run", "Pause"):
        return None
    return dt_util.utcnow() + timedelta(minutes=state.remaining_minutes)


def _water_temperature(state: WasherState) -> int | None:
    """Return the wash temperature, as the panel and the official app state it.

    Which is 70 on Drum Clean, where the appliance can only say 60 - the reasoning and the
    source are in const.py. The reported value is unchanged for every other programme, and
    the appliance's own answer stays visible on the raw attribute either way.
    """
    shown = shown_temperature(
        state.course_code, state.water_temperature_raw, state.supported_water_temperature
    )
    if shown != state.water_temperature_raw:
        return int(shown) if shown is not None else None
    return state.water_temperature


def _attrs(**values: object) -> dict[str, str] | None:
    """Build an attribute dict, dropping empties and joining lists.

    Keeps unset values out of the state machine rather than publishing a wall of
    None/"" attributes while the appliance has not been read yet.
    """
    out: dict[str, str] = {}
    for name, value in values.items():
        if value in (None, "", [], ()):
            continue
        out[name] = ", ".join(str(v) for v in value) if isinstance(value, list) else str(value)
    return out or None


@dataclass(frozen=True, kw_only=True)
class WasherSensorDescription(SensorEntityDescription):
    """Describes a sensor and how to read it from the state."""

    value_fn: Callable[[WasherState], float | str | datetime | None]


SENSORS: tuple[WasherSensorDescription, ...] = (
    WasherSensorDescription(
        key="course",
        translation_key="course",
        device_class=SensorDeviceClass.ENUM,
        options=COURSE_OPTIONS,
        value_fn=lambda state: state.course,
    ),
    WasherSensorDescription(
        key="state",
        translation_key="state",
        device_class=SensorDeviceClass.ENUM,
        options=STATE_OPTIONS,
        value_fn=lambda state: _enum(state.state, STATE_OPTIONS),
    ),
    WasherSensorDescription(
        key="progress",
        translation_key="progress",
        device_class=SensorDeviceClass.ENUM,
        options=PROGRESS_OPTIONS,
        value_fn=lambda state: _enum(state.progress, PROGRESS_OPTIONS),
    ),
    WasherSensorDescription(
        key="progress_percentage",
        translation_key="progress_percentage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state.progress_percentage,
    ),
    WasherSensorDescription(
        key="remaining_time",
        translation_key="remaining_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda state: state.remaining_minutes,
    ),
    WasherSensorDescription(
        key="finish_time",
        translation_key="finish_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_finish_time,
    ),
    WasherSensorDescription(
        key="water_temperature",
        translation_key="water_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=_water_temperature,
    ),
    WasherSensorDescription(
        key="spin_level",
        translation_key="spin_level",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        value_fn=lambda state: state.spin_level,
    ),
    WasherSensorDescription(
        key="rinse_cycles",
        translation_key="rinse_cycles",
        value_fn=lambda state: state.rinse_cycles,
    ),
    WasherSensorDescription(
        key="diagnosis",
        translation_key="diagnosis",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.diagnosis,
    ),
    # Read-only: the appliance reports whether it has a quick-wash preset and whether one
    # is in use, but the official app has no write for it either - the panel owns it.
    WasherSensorDescription(
        key="quick_wash",
        translation_key="quick_wash",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.quick_wash,
    ),
    # The appliance's own consumption counter, out of /files/usage.db. No unit and no
    # device_class on purpose: the counter's scale could not be pinned down (a day's
    # rise measured against a metering plug on the same appliance came out at roughly
    # 7 Wh per count, and the snapshot lags the live cycle), so calling it kWh would
    # feed a plausible-looking wrong number into the energy dashboard.
    WasherSensorDescription(
        key="energy_counter",
        translation_key="energy_counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.energy_counter,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SamsungWasherConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        WasherSensor(coordinator, description) for description in SENSORS
    )


class WasherSensor(SamsungWasherEntity, SensorEntity):
    """A single sensor."""

    entity_description: WasherSensorDescription

    def __init__(
        self,
        coordinator: SamsungWasherCoordinator,
        description: WasherSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | datetime | None:
        """Return the value from the last poll."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Expose raw values that do not fit the sensor's own type."""
        state = self.coordinator.data
        if state is None:
            return None
        key = self.entity_description.key
        if key == "course":
            # The raw code keeps an unmapped programme usable on another model, and the
            # allowed settings - decoded from supportedOptions - say what start_cycle will
            # accept for the programme currently selected, without having to guess.
            attributes: dict[str, str] = {}
            if state.course_code:
                attributes["course_code"] = state.course_code
            for field, values in (
                state.course_options.get((state.course_code or "").upper(), {}) or {}
            ).items():
                allowed = [str(value) for value in (values.get("allowed") or [])]
                default = values.get("default")
                default = None if default is None else str(default)
                if field == "temperature":
                    # The same 60-is-really-70 mapping as the sensor's own value, so what
                    # these attributes list is what the selects offer and what start_cycle
                    # takes - one story, not two.
                    supported = state.supported_water_temperature
                    allowed = [
                        str(shown_temperature(state.course_code, value, supported))
                        for value in allowed
                    ]
                    if default is not None:
                        default = str(
                            shown_temperature(state.course_code, default, supported)
                        )
                if allowed:
                    attributes[f"allowed_{field}"] = ", ".join(allowed)
                if default is not None:
                    attributes[f"default_{field}"] = default
            return attributes or None
        if key == "water_temperature":
            # The appliance also reports "Cold" and "None", which a temperature sensor
            # cannot hold; without this the setting would simply vanish from the UI.
            return _attrs(
                raw=state.water_temperature_raw,
                supported=state.supported_water_temperature,
            )
        if key == "spin_level":
            # Likewise "NoSpin" and "RinseHold" (stop with the water left in).
            return _attrs(raw=state.spin_level_raw, supported=state.supported_spin_level)
        if key == "rinse_cycles":
            return _attrs(supported=state.supported_rinse_cycles)
        if key == "state":
            # The raw wording matters when the value fell through to "unknown".
            return _attrs(raw=state.state)
        if key == "progress":
            # supportedProgress is dynamic - it reflects which stages the selected
            # options will actually run.
            return _attrs(
                raw=state.progress, supported_progress=state.supported_progress
            )
        if key == "diagnosis":
            # Everything else the appliance reports, including the tokens whose meaning
            # is unknown (EnergyKW, NoCheck_SC, DeviceType, QuickWash, TimeSync,
            # UsagesDB) and the per-course availability bitmap in supportedOptions.
            return _attrs(
                mode_options=state.mode_options,
                supported_options=state.supported_options,
            )
        if key == "energy_counter":
            return _attrs(
                last_record=state.energy_last_record,
                first_record=state.energy_first_record,
                records=state.energy_records,
            )
        return None
