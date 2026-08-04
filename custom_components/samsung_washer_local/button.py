"""Buttons for Samsung Washer Local - start a chosen programme, start, pause and cancel.

Two ways to start, because the appliance draws the line in an unusual place. A programme
cannot be written on its own - that is answered 204 and discarded - but it *is* accepted
together with the start, which is what Start selected programme does: it runs whatever the
Programme to start dropdown points at, with the settings chosen beside it.

Plain Start is the other way: it resumes, or runs whatever is set on the dial, and does not
touch the programme at all. Worth reading the Programme sensor first if a script uses it.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SamsungWasherConfigEntry
from .const import STATE_PAUSE, STATE_RUN
from .coordinator import SamsungWasherCoordinator
from .entity import SamsungWasherWritableEntity

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class WasherButtonDescription(ButtonEntityDescription):
    """Describes a button and the operation state it writes.

    ``target_state`` is None for the two buttons that need their own routine: cancel,
    because it is judged by the appliance landing in Ready rather than by an echo, and
    Start selected programme, which sends a programme along with the start.
    """

    target_state: str | None = None
    starts_selection: bool = False


BUTTONS: tuple[WasherButtonDescription, ...] = (
    # Starts what the selects are pointing at, which is the only way to choose a programme
    # remotely: the appliance accepts the programme and the start only together.
    WasherButtonDescription(
        key="start_selected",
        translation_key="start_selected",
        # Categorised with the selects it belongs to, not with the plain controls. It is an
        # action, so this is not what the category is for - but it is the last step of the
        # sequence those five make, and entities are listed per category, so keeping it in
        # Controls would have left the numbering pointing at nothing.
        entity_category=EntityCategory.CONFIG,
        target_state=None,
        starts_selection=True,
    ),
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


class WasherButton(SamsungWasherWritableEntity, ButtonEntity):
    """One button: start the selection, start or resume, pause, or cancel."""

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
        """Start the selection, write the operation state, or cancel the cycle."""
        if self.entity_description.starts_selection:
            await self.coordinator.async_start_selected()
            return
        target = self.entity_description.target_state
        if target is None:
            await self.coordinator.async_cancel()
        else:
            await self.coordinator.async_set_operation_state(target)
