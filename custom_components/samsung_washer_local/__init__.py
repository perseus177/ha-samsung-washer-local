"""The Samsung Washer Local integration - cloud-free control over port 8888."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID, CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er

from .api import SamsungWasherClient, WasherError
from .const import (
    ATTR_PROGRAMME,
    ATTR_RINSE,
    ATTR_SPIN,
    ATTR_TEMPERATURE,
    CONF_CERT_PEM,
    CONF_KEY_PEM,
    CONF_POLL_INTERVAL,
    CONF_TOKEN,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DOMAIN,
    PLATFORMS,
    SERVICE_START_CYCLE,
)
from .coordinator import SamsungWasherCoordinator

_LOGGER = logging.getLogger(__name__)

type SamsungWasherConfigEntry = ConfigEntry[SamsungWasherCoordinator]


START_CYCLE_SCHEMA = vol.Schema(
    {
        # A device target arrives as a list, even for one device.
        vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_PROGRAMME): cv.string,
        vol.Optional(ATTR_TEMPERATURE): cv.string,
        vol.Optional(ATTR_RINSE): cv.string,
        vol.Optional(ATTR_SPIN): cv.string,
    }
)


def _coordinator_for_device(
    hass: HomeAssistant, device_id: str
) -> SamsungWasherCoordinator:
    """Return the coordinator behind a device id from a service call."""
    device = dr.async_get(hass).async_get(device_id)
    entries = device.config_entries if device else set()
    for entry_id in entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry and entry.domain == DOMAIN and hasattr(entry, "runtime_data"):
            return entry.runtime_data
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="unknown_device",
        translation_placeholders={"device_id": device_id},
    )


async def _async_register_services(hass: HomeAssistant) -> None:
    """Register the integration's services once.

    Starting a cycle is a service rather than an entity on purpose. Selecting a programme
    means starting a wash - the appliance accepts the two only together - and a select or
    a button that starts the machine on a single tap is a surprise nobody wants on a
    dashboard. A service has to be asked for.
    """
    if hass.services.has_service(DOMAIN, SERVICE_START_CYCLE):
        return

    async def start_cycle(call: ServiceCall) -> None:
        for device_id in call.data[ATTR_DEVICE_ID]:
            coordinator = _coordinator_for_device(hass, device_id)
            await coordinator.async_start_cycle(
                call.data[ATTR_PROGRAMME],
                temperature=call.data.get(ATTR_TEMPERATURE),
                rinse=call.data.get(ATTR_RINSE),
                spin=call.data.get(ATTR_SPIN),
            )

    hass.services.async_register(
        DOMAIN, SERVICE_START_CYCLE, start_cycle, schema=START_CYCLE_SCHEMA
    )


async def async_setup_entry(hass: HomeAssistant, entry: SamsungWasherConfigEntry) -> bool:
    """Set up Samsung Washer Local from a config entry."""
    host: str = entry.data[CONF_HOST]
    client = SamsungWasherClient(
        host=host,
        token=entry.data[CONF_TOKEN],
        port=entry.data.get(CONF_PORT, DEFAULT_PORT),
    )
    try:
        await client.async_prepare(entry.data[CONF_CERT_PEM], entry.data[CONF_KEY_PEM])
    except (WasherError, OSError, ValueError) as err:
        raise ConfigEntryNotReady(f"could not set up the TLS client: {err}") from err

    coordinator = SamsungWasherCoordinator(
        hass,
        entry,
        client,
        host,
        entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
    )
    entry.runtime_data = coordinator
    entry.async_on_unload(client.async_close)

    _async_migrate_host_ids(hass, entry, coordinator.identity, host)
    await _async_register_services(hass)

    # Identity is static, so it is read once and only for the device registry. It is
    # allowed to fail: an idle appliance is usually off the network, and that must not
    # stop the integration from loading.
    try:
        coordinator.information = await client.async_read_information()
    except WasherError as err:
        _LOGGER.debug("Could not read the appliance identity: %s", err)

    # Deliberately not async_config_entry_first_refresh(): the appliance leaves the
    # Wi-Fi whenever it is idle, and a setup that fails then would make the
    # integration unusable most of the day. The entities stay unavailable until the
    # first successful poll instead.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_create_background_task(
        hass, coordinator.async_refresh(), f"{DOMAIN}_initial_refresh"
    )
    return True


def _async_migrate_host_ids(
    hass: HomeAssistant, entry: ConfigEntry, identity: str, host: str
) -> None:
    """Re-point entities that were identified by the host onto the stable identity.

    Up to and including 1.0.6 the identity came from the appliance's identity read, which
    is allowed to fail because the appliance is off the network most of the time. A restart
    during one of those absences produced ``<host>_<key>`` ids instead of
    ``<serial>_<key>``, and therefore a duplicate device with a duplicate set of entities.

    Renaming heals the installations that only ever got the host-based set, keeping their
    history, dashboards and automations pointed at the same entities. Where both sets exist
    the rename would collide, so the stale entity is left alone and reported; the leftover
    device can then be deleted from the UI, which
    ``async_remove_config_entry_device`` permits.
    """
    if identity == host:
        return
    registry = er.async_get(hass)
    stale: list[str] = []
    for entity in list(registry.entities.get_entries_for_config_entry_id(entry.entry_id)):
        prefix = f"{host}_"
        if not entity.unique_id.startswith(prefix):
            continue
        wanted = f"{identity}_{entity.unique_id[len(prefix):]}"
        if registry.async_get_entity_id(entity.domain, DOMAIN, wanted):
            stale.append(entity.entity_id)
            continue
        registry.async_update_entity(entity.entity_id, new_unique_id=wanted)
        _LOGGER.info("Migrated %s onto the appliance's serial number", entity.entity_id)
    if stale:
        _LOGGER.warning(
            "%d entities are left over from a duplicate device created by a restart while"
            " the appliance was unreachable (%s). They will stay unavailable; the duplicate"
            " device can be deleted from the device page, which removes them",
            len(stale),
            ", ".join(sorted(stale)[:4]) + ("…" if len(stale) > 4 else ""),
        )


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: SamsungWasherConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow a device to be deleted from the UI.

    There is only ever one real appliance per entry, so this exists for one purpose: to let
    the user clear out the duplicate device that versions up to 1.0.6 could create. The
    live device is re-created on the next reload, so nothing is lost by allowing it.
    """
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: SamsungWasherConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
