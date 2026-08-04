"""Base entities for Samsung Washer Local."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import SamsungWasherCoordinator


class SamsungWasherEntity(CoordinatorEntity[SamsungWasherCoordinator]):
    """Common base class for the appliance entities."""

    _attr_has_entity_name = True

    @property
    def available(self) -> bool:
        """Return whether the appliance answered and there is something to show.

        Availability cannot come from last_update_success alone here: the coordinator
        treats the appliance being away as an expected outcome rather than a failed
        update, so that flag stays true while the appliance is off the network. The
        reachable flag carries that instead. The data check covers the window before the
        first successful poll, where there is genuinely nothing to report yet.
        """
        return (
            super().available
            and self.coordinator.reachable
            and self.coordinator.data is not None
        )

    def __init__(self, coordinator: SamsungWasherCoordinator, key: str) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        information = coordinator.information
        # Identity comes from the config entry, never from the identity read: that read is
        # allowed to fail, because the appliance is off the network most of the time, and
        # anything derived from it therefore changes between restarts. It used to fall back
        # to the host, which meant a restart while the appliance was away produced a whole
        # second device with a second set of entities. The entry's unique id is the serial
        # number captured when the appliance was demonstrably answering (the config flow
        # cannot complete otherwise), so it is stable whatever the appliance is doing now.
        identity = coordinator.identity
        self._attr_unique_id = f"{identity}_{key}"
        model = information.get("modelID", "").split("|")[0] or None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identity)},
            manufacturer=MANUFACTURER,
            model=model,
            name="Washing machine",
            serial_number=information.get("serialNumber"),
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
