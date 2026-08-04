"""Binary sensors for Samsung Washer Local."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SamsungWasherConfigEntry
from .api import WasherState
from .coordinator import SamsungWasherCoordinator
from .entity import SamsungWasherEntity

PARALLEL_UPDATES = 0


def _has_stage(state: WasherState, stage: str) -> bool | None:
    """Return whether a stage is in supportedProgress (case-insensitively).

    None while the list is empty, which means the appliance has not been read yet -
    reporting False then would claim the option is off rather than unknown.
    """
    if not state.supported_progress:
        return None
    return stage in [entry.lower() for entry in state.supported_progress]


@dataclass(frozen=True, kw_only=True)
class WasherBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a binary sensor and how to read it from the state."""

    value_fn: Callable[[WasherState], bool | None]


BINARY_SENSORS: tuple[WasherBinarySensorDescription, ...] = (
    WasherBinarySensorDescription(
        key="power",
        translation_key="power",
        device_class=BinarySensorDeviceClass.POWER,
        value_fn=lambda state: None if state.power is None else state.power == "On",
    ),
    # This is the condition for every writable entity - with it off the appliance takes no
    # setting and starts nothing - so it was briefly given the configuration category to sit
    # above them. Home Assistant refuses that outright: "cannot be added as the entity
    # category is set to config", because that category is reserved for entities a user can
    # change, and this one only follows the panel button. So it stays an ordinary sensor, and
    # the name carries the meaning instead.
    WasherBinarySensorDescription(
        key="remote_control",
        translation_key="remote_control",
        value_fn=lambda state: state.remote_control_enabled,
    ),
    WasherBinarySensorDescription(
        key="kids_lock",
        translation_key="kids_lock",
        value_fn=lambda state: (
            None if state.kids_lock is None else state.kids_lock != "Ready"
        ),
    ),
    # Three different things about Add wash, easy to confuse, so: this one is whether the
    # programme the appliance currently has loaded permits adding at all (7 on Cotton, 0
    # during a Drum Clean, and it moves as rinse and spin options are changed).
    WasherBinarySensorDescription(
        key="add_wash_available",
        translation_key="add_wash_available",
        value_fn=lambda state: state.add_wash_available,
    ),
    WasherBinarySensorDescription(
        key="alarm",
        translation_key="alarm",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda state: bool(state.alarms),
    ),
    # Options selected on the panel are mostly invisible from outside, but these two
    # do surface: the appliance adds the matching stage to supportedProgress.
    WasherBinarySensorDescription(
        key="prewash",
        translation_key="prewash",
        value_fn=lambda state: _has_stage(state, "prewash"),
    ),
    WasherBinarySensorDescription(
        key="delay_wash",
        translation_key="delay_wash",
        value_fn=lambda state: _has_stage(state, "delaywash"),
    ),
    # ...this one is whether the alarm is switched on at all - the read-only view of what
    # the Add wash alarm switch writes, kept because an automation reading a state should not
    # have to read a switch...
    WasherBinarySensorDescription(
        key="add_wash_set",
        translation_key="add_wash_set",
        value_fn=lambda state: (
            None if state.add_wash_set is None else state.add_wash_set != "0"
        ),
    ),
    # ...and this one is the live signal: right now, laundry may be added. The plugin's
    # wording for it is "Add wash available now. Put additional laundry or softener into the
    # washer." Disabled by default deliberately, because it is a *blinking* lamp - observed
    # flipping thirty times in six minutes with nobody touching the appliance - so it fills
    # the recorder, and an automation using it wants a `for:` of a few seconds.
    WasherBinarySensorDescription(
        key="add_wash_indicator",
        translation_key="add_wash_indicator",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: (
            None
            if state.add_wash_indicator is None
            else state.add_wash_indicator.lower() == "on"
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SamsungWasherConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        WasherBinarySensor(coordinator, description) for description in BINARY_SENSORS
    )


class WasherBinarySensor(SamsungWasherEntity, BinarySensorEntity):
    """A single binary sensor."""

    entity_description: WasherBinarySensorDescription

    def __init__(
        self,
        coordinator: SamsungWasherCoordinator,
        description: WasherBinarySensorDescription,
    ) -> None:
        """Initialise the binary sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the value from the last poll."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Expose the raw values behind the simplified on/off state."""
        state = self.coordinator.data
        if state is None:
            return None
        key = self.entity_description.key
        if key == "alarm":
            # The raw list carries the appliance's error codes.
            return (
                {"alarms": ", ".join(str(alarm) for alarm in state.alarms)}
                if state.alarms
                else None
            )
        if key == "add_wash_available":
            # Not a boolean at the source: the value moves between 0, 3, 4, 6 and 7 as
            # stages are enabled, which looks like a bitmask over the stages where
            # AddWash is allowed. Exposed raw rather than guessed at.
            raw = state.add_wash_available_raw
            return {"raw": raw} if raw is not None else None
        if key in ("prewash", "delay_wash"):
            return {"supported_progress": ", ".join(state.supported_progress)}
        return None
