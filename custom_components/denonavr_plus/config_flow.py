"""Config flow for the Denon AVR Plus integration.

The user only provides the receiver's IP address. The flow validates it by
fetching the Deviceinfo document, then uses the discovered MAC address as the
unique id and the discovered model name as the entry title. Nothing about the
device is entered by hand.
"""

from __future__ import annotations

import logging
from typing import Any, cast
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.components import ssdp
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
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo

from .avr import DenonAvrDevice
from .avr.profile import load_profile
from .const import (
    CONF_HOST,
    CONF_PORT,
    CONFIG_MINOR_VERSION,
    CONFIG_VERSION,
    DOMAIN,
    OPT_HEOS,
    OPT_POLL_INTERVAL,
    OPT_SETUP_REFRESH,
)
from .capabilities import OPT_CHECK_PENDING, CapabilityCheckSteps, known_model
from .command_test import OPT_TEST_PENDING, CommandTestSteps, chosen, groups_schema
from .coordinator import connection_options
from . import choices
from .choices import (
    OPT_MAIN_OFF_STANDBY,
    OPT_SOURCE_SHORTCUTS,
    OPT_SOUND_MODE_NAMES,
    OPT_SOUND_MODES,
    OPT_SOURCE_NAMES,
    OPT_SOURCES,
    OPT_ZONES,
)
from .volume import (
    OPT_ANNOUNCE_VOLUME,
    OPT_MAX_VOLUME,
    OPT_OFFICIAL_SCALE,
    OPT_TURN_ON_VOLUME,
    OPT_VOLUME_STEP,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema({vol.Optional(CONF_HOST): str})
# Reconfiguring needs an address; there is no search there.
STEP_RECONFIGURE_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})

#: Manufacturer strings of Denon and Marantz units in their UPnP description.
SSDP_MANUFACTURERS = frozenset({"Denon", "DENON", "DENON PROFESSIONAL", "Marantz"})
#: Model names of HEOS speakers and other units that are no receiver. They
#: announce the same device types under the same manufacturer.
_NOT_A_RECEIVER = ("HEOS", "Denon Home")
#: The device types Denon and Marantz units announce over SSDP.
SSDP_DEVICE_TYPES = (
    "urn:schemas-denon-com:device:AiosDevice:1",
    "urn:schemas-upnp-org:device:MediaRenderer:1",
    "urn:schemas-upnp-org:device:MediaServer:1",
)
CONF_RECEIVER = "receiver"
CONF_CHECK = "check"


def _skip_reason(upnp: Any) -> str | None:
    """Why an SSDP announcement is no receiver to set up, or None if it is."""

    if upnp.get("manufacturer") not in SSDP_MANUFACTURERS:
        return "not_denon_marantz"
    if str(upnp.get("modelName", "")).startswith(_NOT_A_RECEIVER):
        return "not_a_receiver"
    return None


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
        self._found: dict[str, str] = {}
        self._new_entry: tuple[str, dict[str, Any]] = ("", {})

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
        """Ask for the address; left empty, offer the receivers found on the network."""

        errors: dict[str, str] = {}
        if user_input is not None:
            host = (user_input.get(CONF_HOST) or "").strip()
            if not host:
                self._found = await self._async_discovered_receivers()
                if len(self._found) > 1:
                    return await self.async_step_pick()
                if not self._found:
                    errors["base"] = "no_receivers_found"
                else:
                    host = next(iter(self._found))
            if host:
                result = await self._async_add(host, errors)
                if result is not None:
                    return result

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose one of several receivers found on the network."""

        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_add(user_input[CONF_RECEIVER], errors)
            if result is not None:
                return result
        options = [
            SelectOptionDict(value=host, label=label) for host, label in self._found.items()
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_RECEIVER): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                )
            }
        )
        return self.async_show_form(step_id="pick", data_schema=schema, errors=errors)

    async def _async_add(self, host: str, errors: dict[str, str]) -> ConfigFlowResult | None:
        """Identify the receiver at host and create its entry; None after an error."""

        try:
            mac, name = await self._async_identify(host)
        except ConnectionError:
            errors["base"] = "cannot_connect"
            return None
        if mac:
            await self.async_set_unique_id(mac)
            self._abort_if_unique_id_configured(updates=self._entry_data(host))
        else:
            self._async_abort_entries_match({CONF_HOST: host})
        return self._async_create(name, self._entry_data(host))

    def _async_create(self, title: str, data: dict[str, Any]) -> ConfigFlowResult:
        """Offer the tests before the entry is created; all can be skipped.

        A model not checked before gets the capability check too (chosen by
        default); the command test is chosen by nobody by default.
        """

        self._new_entry = (title, data)
        if known_model(title):
            return self._command_test_form()
        return self._unknown_model_form()

    def _unknown_model_form(self) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="unknown_model",
            data_schema=vol.Schema(
                {vol.Required(CONF_CHECK, default=True): BooleanSelector(), **groups_schema()}
            ),
            description_placeholders={"model": self._new_entry[0]},
        )

    def _command_test_form(self) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="command_test",
            data_schema=vol.Schema(groups_schema()),
            description_placeholders={"model": self._new_entry[0]},
        )

    def _create_with_tests(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        title, data = self._new_entry
        options: dict[str, Any] = {}
        if user_input.get(CONF_CHECK):
            options[OPT_CHECK_PENDING] = True
        if groups := chosen(user_input):
            options[OPT_TEST_PENDING] = groups
        return self.async_create_entry(title=title, data=data, options=options)

    async def async_step_command_test(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Test commands after setup? Writes settings and puts them back."""

        if user_input is None:
            return self._command_test_form()
        return self._create_with_tests(user_input)

    async def async_step_unknown_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """A model not checked before: run the capability check after setup?

        The check runs once the integration has connected, over its own
        connection, and the result goes into the diagnostics. Nothing is sent
        anywhere; a repair then points to a prefilled issue on GitHub.
        """

        if user_input is None:
            return self._unknown_model_form()
        return self._create_with_tests(user_input)

    async def _async_discovered_receivers(self) -> dict[str, str]:
        """Receivers Home Assistant has seen over SSDP and that are not set up.

        Maps the address to a label for the list, the receiver's own name and
        its address. Comes from the SSDP cache, so it needs no new search.
        """

        configured = {
            entry.data.get(CONF_HOST)
            for entry in self._async_current_entries(include_ignore=False)
        }
        found: dict[str, str] = {}
        for device_type in SSDP_DEVICE_TYPES:
            for info in await ssdp.async_get_discovery_info_by_st(self.hass, device_type):
                host = urlparse(info.ssdp_location or "").hostname
                if not host or host in configured or _skip_reason(info.upnp):
                    continue
                name = info.upnp.get("friendlyName") or info.upnp.get("modelName") or host
                found.setdefault(host, f"{name} ({host})")
        return found

    async def async_step_ssdp(
        self, discovery_info: SsdpServiceInfo
    ) -> ConfigFlowResult:
        """Handle a receiver discovered over SSDP."""

        if reason := _skip_reason(discovery_info.upnp):
            return self.async_abort(reason=reason)
        host = urlparse(discovery_info.ssdp_location or "").hostname
        if not host:
            return self.async_abort(reason="cannot_connect")
        # A receiver announces up to three device types, so up to three
        # discoveries start at once; one is enough.
        self._async_abort_entries_match({CONF_HOST: host})
        if any(
            flow["context"].get(CONF_HOST) == host
            # Flows that have not shown a step yet count: announcements of one
            # receiver arrive together, and each would read Deviceinfo.
            for flow in self._async_in_progress(include_uninitialized=True)
        ):
            return self.async_abort(reason="already_in_progress")
        # Not a key Home Assistant defines; the flows of this domain read it above.
        cast(dict[str, Any], self.context)[CONF_HOST] = host
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

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult:
        """A receiver of this integration showed up under a new address.

        The manifest asks DHCP only for devices already registered here, so
        this follows a receiver whose address changed: the entry gets the new
        address and reloads. Anything else is checked like an SSDP find.
        """

        host = discovery_info.ip
        await self.async_set_unique_id(format_mac(discovery_info.macaddress))
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        try:
            mac, name = await self._async_identify(host)
        except ConnectionError:
            return self.async_abort(reason="cannot_connect")
        if mac:
            await self.async_set_unique_id(mac)
            self._abort_if_unique_id_configured(updates=self._entry_data(host))
        self._host = host
        self._name = name
        self.context["title_placeholders"] = {"name": name}
        return await self.async_step_ssdp_confirm()

    async def async_step_ssdp_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm adding an SSDP discovered receiver."""

        if user_input is not None:
            assert self._host is not None
            return self._async_create(self._name, self._entry_data(self._host))
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
                STEP_RECONFIGURE_SCHEMA, {CONF_HOST: entry.data[CONF_HOST]}
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
_STEP_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=0.5, max=5.0, step=0.5, unit_of_measurement="dB", mode=NumberSelectorMode.BOX
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


class DenonAvrOptionsFlow(CommandTestSteps, CapabilityCheckSteps, OptionsFlow):
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
            step_id="init",
            menu_options=[
                "volume",
                "zones",
                "sources",
                "sound_modes",
                "connection",
                "check",
                "command_test",
            ],
        )

    # Capability check (steps in capabilities.CapabilityCheckSteps) ---------

    _not_connected = "check_not_connected"

    def _check_entry(self) -> Any:
        return self.config_entry

    def _check_finished(self) -> ConfigFlowResult:
        return self.async_create_entry(data=dict(self.config_entry.options))

    # Connection ------------------------------------------------------------

    async def async_step_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Poll interval, HEOS and how often the setup settings are read.

        Opens without the receiver; a change reloads the entry.
        """

        if user_input is not None:
            return self._save(
                {
                    OPT_POLL_INTERVAL: int(user_input[OPT_POLL_INTERVAL]),
                    OPT_HEOS: user_input[OPT_HEOS],
                    OPT_SETUP_REFRESH: int(user_input[OPT_SETUP_REFRESH]),
                }
            )
        current = connection_options(self.config_entry)
        schema = vol.Schema(
            {
                vol.Required(
                    OPT_POLL_INTERVAL, default=current[OPT_POLL_INTERVAL]
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=30, max=600, step=10, unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(OPT_HEOS, default=current[OPT_HEOS]): BooleanSelector(),
                vol.Required(
                    OPT_SETUP_REFRESH, default=current[OPT_SETUP_REFRESH]
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=120, step=1, unit_of_measurement="min",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="connection", data_schema=schema)

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
                vol.Optional(
                    OPT_VOLUME_STEP,
                    description={"suggested_value": current.get(OPT_VOLUME_STEP)},
                ): _STEP_SELECTOR,
            }
            if zone == "main":
                # Announcements play on the main zone (over HEOS).
                zone_fields[
                    vol.Optional(
                        OPT_ANNOUNCE_VOLUME,
                        description={"suggested_value": current.get(OPT_ANNOUNCE_VOLUME)},
                    )
                ] = _DB_SELECTOR
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
            return self._save(
                {
                    OPT_ZONES: list(user_input[OPT_ZONES]),
                    OPT_MAIN_OFF_STANDBY: bool(user_input.get(OPT_MAIN_OFF_STANDBY, False)),
                }
            )
        current = stored if stored is not None else additional
        standby = bool(self.config_entry.options.get(OPT_MAIN_OFF_STANDBY, False))
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
                    ),
                    vol.Required(OPT_MAIN_OFF_STANDBY, default=standby): BooleanSelector(),
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
            self._pending = {
                OPT_SOURCES: selection_in,
                OPT_SOURCE_SHORTCUTS: bool(user_input.get(OPT_SOURCE_SHORTCUTS, True)),
            }
            return await self.async_step_source_names()
        stored = options.get(OPT_SOURCES)
        selection: dict[str, Any] = stored if isinstance(stored, dict) else {}
        fields: dict[Any, Any] = {
            vol.Required(
                OPT_SOURCE_SHORTCUTS, default=bool(options.get(OPT_SOURCE_SHORTCUTS, True))
            ): BooleanSelector()
        }
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
