"""Switches for Samsung Washer Local - the Add wash alarm and the moments it fires at.

Add wash is the little door for a forgotten sock, and this setting is the *reminder* for it,
not permission to use it. The official app words it: "If you need to add clothes just for a
rinse or spin, or want to add a special softener during the wash, the Add wash alarm reminds
you when to add your laundry or softener during the wash cycle." When it fires, the appliance
says "The rinse is about to start. Please press the Pause button to add laundry."

The appliance stores the whole thing in one place - ``AddWashSet``, a three-bit mask over the
moments the alarm fires at, with the app's own labels for the bits:

    bit 0  when rinsing starts          (WEBMOB_device_washer_add_wash_when_start_rinse)
    bit 1  when the final rinse starts  (..._add_wash_when_start_last_rinse)
    bit 2  when spinning starts         (..._add_wash_when_start_spin)

The app shows that as a master switch with three checkboxes underneath, and the checkboxes are
greyed out while the master is off. Mirrored here: the alarm switch is on whenever the mask is
non-zero, and the three moment switches are unavailable while it is off. Since the mask *is*
the state, switching the alarm off means zero - so turning it back on enables all three
moments again rather than restoring a previous combination, which the appliance does not
remember either.

Writable on its own, verified on a TP6X_WW6500 by writing 7, reading it back and restoring 0.
``AddWashAvailable`` is a different value - what the appliance permits for the programme it
currently has loaded, 7 on Cotton and 0 during a Drum Clean - and it does not gate any of
this: that write went through while a Drum Clean was running and availability read 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SamsungWasherConfigEntry
from .const import ADD_WASH_ALL, ADD_WASH_DESCRIPTION, ADD_WASH_NONE
from .coordinator import SamsungWasherCoordinator
from .entity import SamsungWasherConfigEntity

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class WasherSwitchDescription(SwitchEntityDescription):
    """Describes a switch. ``bit`` is None for the alarm's own master switch."""

    bit: int | None = None


SWITCHES: tuple[WasherSwitchDescription, ...] = (
    WasherSwitchDescription(
        key="add_wash_set",
        translation_key="add_wash_set",
        entity_category=EntityCategory.CONFIG,
    ),
    WasherSwitchDescription(
        key="add_wash_rinse",
        translation_key="add_wash_rinse",
        entity_category=EntityCategory.CONFIG,
        bit=0,
    ),
    WasherSwitchDescription(
        key="add_wash_last_rinse",
        translation_key="add_wash_last_rinse",
        entity_category=EntityCategory.CONFIG,
        bit=1,
    ),
    WasherSwitchDescription(
        key="add_wash_spin",
        translation_key="add_wash_spin",
        entity_category=EntityCategory.CONFIG,
        bit=2,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SamsungWasherConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switches."""
    coordinator = entry.runtime_data
    async_add_entities(
        WasherAddWashSwitch(coordinator, description) for description in SWITCHES
    )


class WasherAddWashSwitch(SamsungWasherConfigEntity, SwitchEntity):
    """The Add wash alarm, or one of the moments it fires at."""

    entity_description: WasherSwitchDescription

    def __init__(
        self, coordinator: SamsungWasherCoordinator, description: WasherSwitchDescription
    ) -> None:
        """Initialise the switch."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def _mask(self) -> int | None:
        """Return the mask as a number, or None while it has not been read."""
        state = self.coordinator.data
        if state is None or state.add_wash_set is None:
            return None
        try:
            return int(state.add_wash_set)
        except ValueError:
            return None

    @property
    def available(self) -> bool:
        """Return whether this switch can be used.

        ``super()`` covers the two conditions all four share - the appliance has to be
        reachable and willing to be driven. On top of that the moment switches follow the
        alarm, the way the app greys its checkboxes out while the master is off.
        """
        if not super().available:
            return False
        if self.entity_description.bit is None:
            return True
        mask = self._mask
        return mask is not None and mask != 0

    @property
    def is_on(self) -> bool | None:
        """Return whether the alarm is on, or whether this moment is one of its own."""
        mask = self._mask
        if mask is None:
            return None
        bit = self.entity_description.bit
        return mask != 0 if bit is None else bool(mask >> bit & 1)

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Expose the mask, what the feature is, and where the appliance permits it.

        The description is here because Home Assistant has nowhere else to put one - there is
        no tooltip for an entity - and the more-info dialog shows attributes.
        ``available_phases`` belongs to the programme the appliance currently has loaded, not
        to these switches; it is reported, never enforced.
        """
        state = self.coordinator.data
        if state is None or state.add_wash_set is None:
            return None
        attributes = {"raw": state.add_wash_set}
        if self.entity_description.bit is None:
            attributes["description"] = ADD_WASH_DESCRIPTION
        if state.add_wash_available_raw is not None:
            attributes["available_phases"] = state.add_wash_available_raw
        return attributes

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch the alarm on for every moment, or add this moment to it."""
        bit = self.entity_description.bit
        if bit is None:
            await self.coordinator.async_set_add_wash(ADD_WASH_ALL)
        else:
            await self.coordinator.async_set_add_wash(str((self._mask or 0) | 1 << bit))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch the alarm off entirely, or drop this one moment from it."""
        bit = self.entity_description.bit
        if bit is None:
            await self.coordinator.async_set_add_wash(ADD_WASH_NONE)
        else:
            await self.coordinator.async_set_add_wash(str((self._mask or 0) & ~(1 << bit)))
