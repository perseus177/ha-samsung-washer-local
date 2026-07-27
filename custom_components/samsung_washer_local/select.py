"""Select for the Laundry Out reminder.

This is the one setting in the Mode resource that the appliance really does accept
over the local API - verified by writing 30, 60 and 90 and reading each back.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SamsungWasherConfigEntry
from .const import LAUNDRY_OUT_VALUES
from .coordinator import SamsungWasherCoordinator
from .entity import SamsungWasherControlEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SamsungWasherConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the select."""
    async_add_entities([WasherLaundryOutSelect(entry.runtime_data)])


class WasherLaundryOutSelect(SamsungWasherControlEntity, SelectEntity):
    """The Laundry Out reminder interval in minutes, 0 meaning off."""

    _attr_translation_key = "laundry_out_time"
    _attr_options = LAUNDRY_OUT_VALUES

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
