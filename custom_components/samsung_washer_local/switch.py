"""Switches for Samsung Washer Local - currently just AddWash.

AddWash is one of the few things this appliance lets a local client write on its own,
without wrapping it in a start command: verified on a TP6X_WW6500 by writing 7, reading it
back and restoring 0, while a cycle was running.

Underneath it is a three-bit mask, not a boolean - the official app builds it from three
checkboxes (`parseInt(opt3 + opt2 + opt1, 2)`), so 0 through 7 are all valid. Only 0 and 7
have been seen on real appliances, and "all three" is what someone means by switching
AddWash on, so that is what this writes. The raw value stays visible as an attribute, and
any non-zero mask reads as on rather than being rounded away.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SamsungWasherConfigEntry
from .const import ADD_WASH_ALL, ADD_WASH_NONE
from .coordinator import SamsungWasherCoordinator
from .entity import SamsungWasherControlEntity

PARALLEL_UPDATES = 1

DESCRIPTION = SwitchEntityDescription(
    key="add_wash_set",
    translation_key="add_wash_set",
    # Numbered and categorised with the five selects: it is one more thing chosen before a
    # wash, and entities are listed per category, so a number alone would not group it.
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SamsungWasherConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switches."""
    async_add_entities([WasherAddWashSwitch(entry.runtime_data)])


class WasherAddWashSwitch(SamsungWasherControlEntity, SwitchEntity):
    """The AddWash feature's own on/off."""

    entity_description = DESCRIPTION

    def __init__(self, coordinator: SamsungWasherCoordinator) -> None:
        """Initialise the switch."""
        super().__init__(coordinator, DESCRIPTION.key)

    @property
    def is_on(self) -> bool | None:
        """Return whether AddWash is enabled for any phase."""
        state = self.coordinator.data
        if state is None or state.add_wash_set is None:
            return None
        return state.add_wash_set != ADD_WASH_NONE

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Expose the mask, and which phases the current programme allows it in.

        Two different things, deliberately kept apart. ``raw`` is this switch's own value.
        ``available_phases`` is what the appliance says AddWash can be used in *for the
        programme it currently has loaded* - 7 on Cotton, 0 on Drum Clean, and it moves as
        options are selected. It is not a gate on the switch: writing AddWashSet was
        verified to work on this appliance while a Drum Clean was running and the
        availability read 0. The AddWash available binary sensor carries the same value if
        an automation needs to act on it.
        """
        state = self.coordinator.data
        if state is None or state.add_wash_set is None:
            return None
        attributes = {"raw": state.add_wash_set}
        if state.add_wash_available_raw is not None:
            attributes["available_phases"] = state.add_wash_available_raw
        return attributes

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable AddWash for every phase."""
        await self.coordinator.async_set_add_wash(ADD_WASH_ALL)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable AddWash."""
        await self.coordinator.async_set_add_wash(ADD_WASH_NONE)
