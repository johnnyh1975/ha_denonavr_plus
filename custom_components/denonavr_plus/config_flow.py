"""Config flow for the Denon AVR Plus integration.

The user only provides the receiver's IP address. The flow validates it by
fetching the Deviceinfo document, then uses the discovered MAC address as the
unique id and the discovered model name as the entry title. Nothing about the
device is entered by hand.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo

from .avr import DenonAvrDevice
from .avr.profile import load_profile
from .const import (
    CONF_HOST,
    CONF_PORT,
    CONFIG_MINOR_VERSION,
    CONFIG_VERSION,
    DOMAIN,
)
from . import choices
from .choices import (
    OPT_SOUND_MODE_NAMES,
    OPT_SOUND_MODES,
    OPT_SOURCE_NAMES,
    OPT_SOURCES,
    OPT_ZONES,
)
from .volume import OPT_MAX_VOLUME, OPT_OFFICIAL_SCALE, OPT_TURN_ON_VOLUME

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})


class DenonAvrConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Denon AVR."""

    VERSION = CONFIG_VERSION
    MINOR_VERSION = CONFIG_MINOR_VERSION

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> DenonAvrOptionsFlow:
        return DenonAvrOptionsFlow()

    def __init__(self) -> None:
        self._host: str | None = None
        self._name: str = "Denon AVR"
        self._port: int | None = None

    async def _async_identify(self, host: str) -> tuple[str | None, str]:
        """Fetch the receiver's MAC and model name. Raises ConnectionError.

        Also learns the goform port (8080, or 80 on older receivers), which
        _entry_data stores with the address.
        """

        session = async_get_clientsession(self.hass)
        # Warm the profile cache off the loop before constructing the device.
        await self.hass.async_add_executor_job(load_profile)
        device = DenonAvrDevice(session, host)
        identity = await device.async_fetch_identity()
        self._port = device.http_port
        return identity.mac_address, identity.model_name or "Denon AVR"

    def _entry_data(self, host: str) -> dict[str, Any]:
        """The entry data: the address, and the goform port once it is known."""

        data: dict[str, Any] = {CONF_HOST: host}
        if self._port is not None:
            data[CONF_PORT] = self._port
        return data

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step where the user enters the IP address."""

        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                mac, name = await self._async_identify(host)
            except ConnectionError:
                errors["base"] = "cannot_connect"
            else:
                if mac:
                    await self.async_set_unique_id(mac)
                    self._abort_if_unique_id_configured(updates=self._entry_data(host))
                else:
                    self._async_abort_entries_match({CONF_HOST: host})
                return self.async_create_entry(title=name, data=self._entry_data(host))

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_ssdp(
        self, discovery_info: SsdpServiceInfo
    ) -> ConfigFlowResult:
        """Handle a receiver discovered over SSDP."""

        host = urlparse(discovery_info.ssdp_location or "").hostname
        if not host:
            return self.async_abort(reason="cannot_connect")
        try:
            mac, name = await self._async_identify(host)
        except ConnectionError:
            return self.async_abort(reason="cannot_connect")
        if mac:
            await self.async_set_unique_id(mac)
            self._abort_if_unique_id_configured(updates=self._entry_data(host))
        else:
            self._async_abort_entries_match({CONF_HOST: host})
        self._host = host
        self._name = name
        # Show the friendly name in the discovered-device card and confirm step.
        self.context["title_placeholders"] = {"name": name}
        return await self.async_step_ssdp_confirm()

    async def async_step_ssdp_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm adding an SSDP discovered receiver."""

        if user_input is not None:
            assert self._host is not None
            return self.async_create_entry(
                title=self._name, data=self._entry_data(self._host)
            )
        return self.async_show_form(
            step_id="ssdp_confirm",
            description_placeholders={"name": self._name},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the receiver's IP address without removing the integration."""

        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                mac, _name = await self._async_identify(host)
            except ConnectionError:
                errors["base"] = "cannot_connect"
            else:
                # Make sure the new address still points at the same receiver, so
                # a typo cannot silently rebind the entry to a different device.
                if mac:
                    await self.async_set_unique_id(mac)
                    self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry, data_updates=self._entry_data(host)
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, {CONF_HOST: entry.data[CONF_HOST]}
            ),
            errors=errors,
        )


# The volume range offered in the options, in dB relative to the reference
# (the raw scale 0..98 is -80..+18 dB).
_DB_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=-80.0, max=18.0, step=0.5, unit_of_measurement="dB", mode=NumberSelectorMode.BOX
    )
)
# Zones in the order the options show them; the receiver decides which exist.
_ZONE_ORDER = ("main", "zone2", "zone3")
_ZONE_MARKER = "_media_player_"


def _zones(hass: Any, entry: ConfigEntry) -> list[str]:
    """The zones of this receiver, from its media players in the registry.

    Read from the registry so the options open even when the receiver is
    unreachable.
    """

    registry = er.async_get(hass)
    found = {
        item.unique_id.split(_ZONE_MARKER)[-1]
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        if item.domain == "media_player" and _ZONE_MARKER in item.unique_id
    }
    return [zone for zone in _ZONE_ORDER if zone in found] or ["main"]


class DenonAvrOptionsFlow(OptionsFlow):
    """Options: volume, zones, and which sources and sound modes appear, and how."""

    def __init__(self) -> None:
        self._pending: dict[str, Any] = {}
        self._mode_names_for: list[str] = []

    # Helpers -------------------------------------------------------------

    def _discovery(self) -> Any | None:
        """The receiver's discovery, when the entry is loaded."""

        coordinator = getattr(self.config_entry, "runtime_data", None)
        device = getattr(coordinator, "device", None)
        return getattr(device, "discovery", None)

    def _save(self, values: dict[str, Any]) -> ConfigFlowResult:
        # Merge: the options also hold markers and settings of other steps.
        return self.async_create_entry(data={**self.config_entry.options, **values})

    # Menu ------------------------------------------------------------------

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init", menu_options=["volume", "zones", "sources", "sound_modes"]
        )

    # Volume ----------------------------------------------------------------

    async def async_step_volume(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        zones = _zones(self.hass, self.config_entry)
        options = dict(self.config_entry.options)
        errors: dict[str, str] = {}
        if user_input is not None:
            for zone in zones:
                values = user_input.get(zone, {})
                limit = values.get(OPT_MAX_VOLUME)
                turn_on = values.get(OPT_TURN_ON_VOLUME)
                if limit is not None and turn_on is not None and turn_on > limit:
                    errors["base"] = "turn_on_above_max"
            if not errors:
                return self._save(user_input)
            options = {**options, **user_input}

        fields: dict[Any, Any] = {
            vol.Required(
                OPT_OFFICIAL_SCALE, default=bool(options.get(OPT_OFFICIAL_SCALE, False))
            ): BooleanSelector(),
        }
        for zone in zones:
            stored = options.get(zone)
            current: dict[str, Any] = stored if isinstance(stored, dict) else {}
            zone_fields = {
                vol.Optional(
                    OPT_MAX_VOLUME,
                    description={"suggested_value": current.get(OPT_MAX_VOLUME)},
                ): _DB_SELECTOR,
                vol.Optional(
                    OPT_TURN_ON_VOLUME,
                    description={"suggested_value": current.get(OPT_TURN_ON_VOLUME)},
                ): _DB_SELECTOR,
            }
            fields[vol.Required(zone)] = section(
                vol.Schema(zone_fields), {"collapsed": zone != "main"}
            )
        return self.async_show_form(
            step_id="volume", data_schema=vol.Schema(fields), errors=errors
        )

    # Zones -----------------------------------------------------------------

    async def async_step_zones(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        discovery = self._discovery()
        known = (
            [zone.id for zone in discovery.zones]
            if discovery is not None
            else _zones(self.hass, self.config_entry)
        )
        stored = self.config_entry.options.get(OPT_ZONES)
        known += [zone for zone in stored or [] if zone not in known]
        additional = [zone for zone in _ZONE_ORDER if zone in known and zone != "main"]
        if not additional:
            return self.async_abort(reason="no_additional_zones")
        if user_input is not None:
            return self._save({OPT_ZONES: list(user_input[OPT_ZONES])})
        current = stored if stored is not None else additional
        return self.async_show_form(
            step_id="zones",
            data_schema=vol.Schema(
                {
                    vol.Required(OPT_ZONES, default=list(current)): SelectSelector(
                        SelectSelectorConfig(
                            options=additional,
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                            translation_key="zone",
                        )
                    )
                }
            ),
        )

    # Sources -----------------------------------------------------------------

    async def async_step_sources(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        discovery = self._discovery()
        if discovery is None:
            return self.async_abort(reason="not_connected")
        options = dict(self.config_entry.options)
        zones = [zone.id for zone in discovery.zones if choices.zone_enabled(options, zone.id)]
        visible = [
            SelectOptionDict(value=source.code, label=source.name)
            for source in discovery.visible_sources()
        ]
        if user_input is not None:
            every = {option["value"] for option in visible}
            selection_in: dict[str, list[str]] = {}
            for zone in zones:
                chosen = list(user_input.get(zone, {}).get(OPT_SOURCES, []))
                # All of them means all, also sources the receiver adds later.
                selection_in[zone] = [] if set(chosen) >= every else chosen
            self._pending = {OPT_SOURCES: selection_in}
            return await self.async_step_source_names()
        stored = options.get(OPT_SOURCES)
        selection: dict[str, Any] = stored if isinstance(stored, dict) else {}
        fields: dict[Any, Any] = {}
        for zone in zones:
            chosen = selection.get(zone) or [option["value"] for option in visible]
            fields[vol.Required(zone)] = section(
                vol.Schema(
                    {
                        vol.Optional(OPT_SOURCES, default=list(chosen)): SelectSelector(
                            SelectSelectorConfig(options=visible, multiple=True)
                        )
                    }
                ),
                {"collapsed": zone != "main"},
            )
        return self.async_show_form(step_id="sources", data_schema=vol.Schema(fields))

    async def async_step_source_names(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        discovery = self._discovery()
        if discovery is None:
            return self.async_abort(reason="not_connected")
        selection = self._pending.get(OPT_SOURCES, {})
        codes = [
            source.code
            for source in discovery.visible_sources()
            if any(source.code in (chosen or []) for chosen in selection.values())
            or not any(selection.values())
        ]
        errors: dict[str, str] = {}
        if user_input is not None:
            names = {
                code: str(user_input[code]).strip()
                for code in codes
                if str(user_input.get(code) or "").strip()
                and str(user_input[code]).strip() != discovery.source_name(code)
            }
            shown = [names.get(code) or discovery.source_name(code) or code for code in codes]
            if len(shown) != len(set(shown)):
                errors["base"] = "duplicate_name"
            else:
                return self._save({**self._pending, OPT_SOURCE_NAMES: names})
        current = dict(self.config_entry.options.get(OPT_SOURCE_NAMES) or {})
        fields = {
            vol.Optional(
                code,
                description={
                    "suggested_value": current.get(code) or discovery.source_name(code) or code
                },
            ): TextSelector()
            for code in codes
        }
        return self.async_show_form(
            step_id="source_names", data_schema=vol.Schema(fields), errors=errors
        )

    # Sound modes -------------------------------------------------------------

    def _known_sound_modes(self, discovery: Any) -> list[str]:
        modes = list(discovery.all_sound_modes)
        modes += [mode for mode in discovery.current_sound_modes if mode not in modes]
        return modes

    async def async_step_sound_modes(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        discovery = self._discovery()
        if discovery is None:
            return self.async_abort(reason="not_connected")
        modes = self._known_sound_modes(discovery)
        if not modes:
            return self.async_abort(reason="no_sound_modes")
        if user_input is not None:
            chosen_in = list(user_input[OPT_SOUND_MODES])
            # The names step lists the chosen modes; "all" is stored as empty.
            self._pending = {OPT_SOUND_MODES: [] if set(chosen_in) >= set(modes) else chosen_in}
            self._mode_names_for = chosen_in
            return await self.async_step_sound_mode_names()
        chosen = self.config_entry.options.get(OPT_SOUND_MODES) or modes
        return self.async_show_form(
            step_id="sound_modes",
            data_schema=vol.Schema(
                {
                    vol.Required(OPT_SOUND_MODES, default=list(chosen)): SelectSelector(
                        SelectSelectorConfig(options=modes, multiple=True)
                    )
                }
            ),
        )

    async def async_step_sound_mode_names(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        modes = list(self._mode_names_for)
        errors: dict[str, str] = {}
        if user_input is not None:
            names = {
                mode: str(user_input[mode]).strip()
                for mode in modes
                if str(user_input.get(mode) or "").strip()
                and str(user_input[mode]).strip() != mode
            }
            shown = [names.get(mode) or mode for mode in modes]
            if len(shown) != len(set(shown)):
                errors["base"] = "duplicate_name"
            else:
                return self._save({**self._pending, OPT_SOUND_MODE_NAMES: names})
        current = dict(self.config_entry.options.get(OPT_SOUND_MODE_NAMES) or {})
        fields = {
            vol.Optional(
                mode, description={"suggested_value": current.get(mode) or mode}
            ): TextSelector()
            for mode in modes
        }
        return self.async_show_form(
            step_id="sound_mode_names", data_schema=vol.Schema(fields), errors=errors
        )
