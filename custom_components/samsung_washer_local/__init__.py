"""The Samsung Washer Local integration - cloud-free control over port 8888."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .api import SamsungWasherClient, WasherError
from .const import (
    CONF_CERT_PEM,
    CONF_KEY_PEM,
    CONF_POLL_INTERVAL,
    CONF_TOKEN,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import SamsungWasherCoordinator

_LOGGER = logging.getLogger(__name__)

type SamsungWasherConfigEntry = ConfigEntry[SamsungWasherCoordinator]


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


async def async_unload_entry(
    hass: HomeAssistant, entry: SamsungWasherConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
