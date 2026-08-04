"""Selects: the Laundry Out reminder, and the choice of what to start.

Two different kinds live here. The Laundry Out reminder writes straight to the appliance -
it is one of the few Mode settings this firmware accepts on its own. The other four hold a
choice inside Home Assistant and write nothing until the Start selected programme button is
pressed, because the appliance takes a programme only together with a start: a select that
wrote would mean one tap on a dashboard starts a wash.

All five are unavailable while Remote Control is switched off at the appliance: that is the
appliance declining to be driven, so a dropdown that cannot be applied should not be offered.

The three setting selects offer exactly what the chosen programme allows, decoded from the
appliance's own supportedOptions - so picking Drum Clean leaves one temperature to choose
from, and Rinse + Spin has no temperature at all, in which case that select goes
unavailable rather than pretending. Switching programme drops a value the new one does not
allow, the way the app's dialogs do. Changing any of them republishes to all four at
once, so the narrowed lists appear immediately rather than at the next poll.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SamsungWasherConfigEntry
from .const import COURSE_MAP, LAUNDRY_OUT_VALUES
from .coordinator import SamsungWasherCoordinator
from .entity import SamsungWasherConfigEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SamsungWasherConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the selects."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            WasherLaundryOutSelect(coordinator),
            WasherProgrammeSelect(coordinator),
            WasherSettingSelect(coordinator, "temperature"),
            WasherSettingSelect(coordinator, "rinse"),
            WasherSettingSelect(coordinator, "spin"),
        ]
    )


class WasherLaundryOutSelect(SamsungWasherConfigEntity, SelectEntity):
    """The Laundry Out reminder interval in minutes, 0 meaning off.

    Numbered and categorised with the four above so the five read as the sequence the app
    presents - it is the last thing chosen before starting a wash. It differs from them in
    one way worth knowing: this one writes to the appliance as soon as it is changed, since
    the reminder is a setting the appliance accepts on its own, rather than part of a start.
    """

    _attr_translation_key = "laundry_out_time"
    _attr_options = LAUNDRY_OUT_VALUES
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SamsungWasherCoordinator) -> None:
        """Initialise the select."""
        super().__init__(coordinator, "laundry_out_time")

    @property
    def current_option(self) -> str | None:
        """Return the interval reported by the appliance."""
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.laundry_out_time
        return value if value in LAUNDRY_OUT_VALUES else None

    async def async_select_option(self, option: str) -> None:
        """Write the interval."""
        await self.coordinator.async_set_laundry_out_time(option)


class WasherProgrammeSelect(SamsungWasherConfigEntity, SelectEntity):
    """Which programme the Start selected programme button will run.

    Its option list is the programmes the appliance itself advertises, so a model with a
    different dial offers its own set. Selecting changes nothing on the appliance.
    """

    _attr_translation_key = "selected_programme"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SamsungWasherCoordinator) -> None:
        """Initialise the select."""
        super().__init__(coordinator, "selected_programme")

    @property
    def options(self) -> list[str]:
        """Return the programmes this appliance offers, named where the code is known."""
        codes = list((self.coordinator.data.course_options if self.coordinator.data else {}))
        return [COURSE_MAP.get(code, code) for code in codes] or list(COURSE_MAP.values())

    @property
    def current_option(self) -> str | None:
        """Return the chosen programme, defaulting to the one on the dial."""
        code = self.coordinator.selected_programme()
        if code is None:
            return None
        name = COURSE_MAP.get(code.upper(), code.upper())
        return name if name in self.options else None

    async def async_select_option(self, option: str) -> None:
        """Remember the programme, and reset the settings to its own defaults."""
        for code, name in COURSE_MAP.items():
            if name == option:
                self.coordinator.set_pending_programme(code)
                break
        else:
            self.coordinator.set_pending_programme(option)


class WasherSettingSelect(SamsungWasherConfigEntity, SelectEntity):
    """Temperature, rinse count or spin speed for the next start.

    Unavailable when the chosen programme does not offer the setting at all - Rinse + Spin
    has no temperature, and saying so is better than showing an empty dropdown.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SamsungWasherCoordinator, field: str) -> None:
        """Initialise the select for one setting."""
        super().__init__(coordinator, f"selected_{field}")
        self._field = field
        self._attr_translation_key = f"selected_{field}"

    @property
    def available(self) -> bool:
        """Return whether the chosen programme offers this setting."""
        return super().available and bool(self.coordinator.allowed_for(self._field))

    @property
    def options(self) -> list[str]:
        """Return only what the chosen programme allows."""
        return self.coordinator.allowed_for(self._field)

    @property
    def current_option(self) -> str | None:
        """Return the chosen value, or the programme's default."""
        return self.coordinator.pending(self._field)

    async def async_select_option(self, option: str) -> None:
        """Remember the value for the next start."""
        self.coordinator.set_pending(self._field, option)
