"""Buttons for Samsung Washer Local - start, pause, resume and cancel.

The programme itself cannot be selected remotely: the appliance accepts a write to
Course_XX and silently discards it (Samsung keeps programme selection on its private
cloud channel). What is started is therefore always whatever is set on the dial,
which is why the course sensor is worth checking before automating a start.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SamsungWasherConfigEntry
from .const import STATE_PAUSE, STATE_RUN
from .coordinator import SamsungWasherCoordinator
from .entity import SamsungWasherControlEntity

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class WasherButtonDescription(ButtonEntityDescription):
    """Describes a button and the operation state it writes.

    ``target_state`` is None for cancel, which needs its own routine: the value that
    cancels a cycle is not known, and a cancel lands in Ready rather than echoing back
    whatever was written.
    """

    target_state: str | None = None


BUTTONS: tuple[WasherButtonDescription, ...] = (
    WasherButtonDescription(
        key="start",
        translation_key="start",
        target_state=STATE_RUN,
    ),
    WasherButtonDescription(
        key="pause",
        translation_key="pause",
        target_state=STATE_PAUSE,
    ),
    WasherButtonDescription(
        key="cancel",
        translation_key="cancel",
        target_state=None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SamsungWasherConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons."""
    coordinator = entry.runtime_data
    async_add_entities(
        WasherButton(coordinator, description) for description in BUTTONS
    )


class WasherButton(SamsungWasherControlEntity, ButtonEntity):
    """A single button."""

    entity_description: WasherButtonDescription

    def __init__(
        self,
        coordinator: SamsungWasherCoordinator,
        description: WasherButtonDescription,
    ) -> None:
        """Initialise the button."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Write the operation state, or cancel the cycle."""
        target = self.entity_description.target_state
        if target is None:
            await self.coordinator.async_cancel()
        else:
            await self.coordinator.async_set_operation_state(target)
