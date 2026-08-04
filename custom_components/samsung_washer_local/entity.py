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


class SamsungWasherWritableEntity(SamsungWasherEntity):
    """Anything that writes to the appliance - the numbered configuration, and the buttons.

    Unavailable whenever the appliance is unreachable, and whenever Remote Control is switched
    off at the panel: that is the appliance declining to be driven at all. It will not take a
    setting, it will not start, and within minutes it leaves the network entirely.

    An earlier version kept the buttons always available, on the reasoning that they display
    no value to misrepresent and that a press could report a clear error. That is true as far
    as it goes, but a control that cannot possibly work is still noise on a dashboard, and the
    inconsistency - greyed-out dropdowns beside a pressable Start - was worse than either rule
    on its own.

    Only an explicit false disables. Before the first successful poll the flag is unknown, and
    unknown is not a refusal; SamsungWasherEntity withholds those entities anyway while there
    is no data.

    One thing this cannot know: whether a *running* cycle can still be paused with Remote
    Control off. The appliance stays on the network for a whole wash either way, but no write
    was ever tried in that state, so the assumption here is that a refusal to be driven means
    all of it. If a pause ever proves to work, this is the place to relax.
    """

    @property
    def available(self) -> bool:
        """Return whether the appliance is currently willing to be driven."""
        state = self.coordinator.data
        return super().available and (
            state is None or state.remote_control_enabled is not False
        )
