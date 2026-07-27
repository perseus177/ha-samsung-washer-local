"""Base entities for Samsung Washer Local."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import SamsungWasherCoordinator


class SamsungWasherEntity(CoordinatorEntity[SamsungWasherCoordinator]):
    """Common base class for the appliance entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SamsungWasherCoordinator, key: str) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        information = coordinator.information
        serial = information.get("serialNumber")
        # The serial number is the only stable identifier the appliance offers; the IP
        # can change and the model ID is not unique. If it could not be read (an idle
        # appliance is usually offline) the host is used, which still keeps the unique
        # IDs stable for this installation.
        self._attr_unique_id = f"{serial or coordinator.host}_{key}"
        model = information.get("modelID", "").split("|")[0] or None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial or coordinator.host)},
            manufacturer=MANUFACTURER,
            model=model,
            name="Washing machine",
            serial_number=serial,
            sw_version=next(
                (
                    version.get("number")
                    for version in information.get("Versions", [])
                    if version.get("type") == "Software"
                ),
                None,
            ),
            configuration_url=f"http://{coordinator.host}",
        )


class SamsungWasherControlEntity(SamsungWasherEntity):
    """A control entity.

    Controls stay available even when the last poll failed. The appliance is off the
    network most of the time it is idle, and hiding the buttons then would be more
    confusing than letting a press report a clear "not reachable" error.
    """

    @property
    def available(self) -> bool:
        """Return True - controls are always offered."""
        return True
