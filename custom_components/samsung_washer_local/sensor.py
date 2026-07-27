"""Sensor entities for Samsung Washer Local."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, REVOLUTIONS_PER_MINUTE, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SamsungWasherConfigEntry
from .api import WasherState
from .const import COURSE_OPTIONS, PROGRESS_OPTIONS, STATE_OPTIONS
from .coordinator import SamsungWasherCoordinator
from .entity import SamsungWasherEntity

PARALLEL_UPDATES = 0


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
        value_fn=lambda state: state.state.lower() if state.state else None,
    ),
    WasherSensorDescription(
        key="progress",
        translation_key="progress",
        device_class=SensorDeviceClass.ENUM,
        options=PROGRESS_OPTIONS,
        value_fn=lambda state: state.progress,
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
        value_fn=lambda state: state.water_temperature,
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
            # Keeps an unmapped programme usable on another model.
            return {"course_code": state.course_code} if state.course_code else None
        if key == "water_temperature":
            # The appliance also reports "Cold" and "None", which a temperature sensor
            # cannot hold; without this the setting would simply vanish from the UI.
            raw = state.water_temperature_raw
            return {"raw": raw} if raw is not None else None
        if key == "spin_level":
            # Likewise "NoSpin" and "RinseHold" (stop with the water left in).
            raw = state.spin_level_raw
            return {"raw": raw} if raw is not None else None
        if key == "progress":
            # supportedProgress is dynamic - it reflects which stages the selected
            # options will actually run.
            return {"supported_progress": ", ".join(state.supported_progress)}
        return None
