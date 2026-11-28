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
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo

from .avr import DenonAvrDevice
from .avr.profile import load_profile
from .avr.transport.telnet import async_answers
from .const import (
    CONF_HOST,
    CONF_PORT,
    CONFIG_MINOR_VERSION,
    CONFIG_VERSION,
    DOMAIN,
)
from .avr.command_test import QUIET
from .capabilities import OPT_CHECK_PENDING, described
from .command_test import OPT_TEST_PENDING
from .options_flow import DenonAvrOptionsFlow
from . import move

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
CONF_TEST = "test"


def _skip_reason(upnp: Any) -> str | None:
    """Why an SSDP announcement is no receiver to set up, or None if it is."""

    if upnp.get("manufacturer") not in SSDP_MANUFACTURERS:
        return "not_denon_marantz"
    if str(upnp.get("modelName", "")).startswith(_NOT_A_RECEIVER):
        return "not_a_receiver"
    return None



#: The text after setup: where to go next (config.create_entry.created).
CREATED = "created"

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
        self._mac: str | None = None
        self._serial: str | None = None
        #: The receiver is of a described generation: no test is offered.
        self._described = False
        #: The other integrations' entries for this receiver, and whether to take them over.
        self._old: list[ConfigEntry] = []
        self._move: list[str] = []
        #: The user chose to go on although the control connection is not answered.
        self._without_telnet = False

    async def _async_identify(self, host: str) -> tuple[str | None, str]:
        """Fetch the receiver's unique key and model name. Raises ConnectionError.

        The key is the MAC address; a receiver from 2010 to 2012 reports
        none (it has no Deviceinfo.xml), so its serial number stands in.

        Also learns the goform port (8080, or 80 on older receivers), which
        _entry_data stores with the address.
        """

        session = async_get_clientsession(self.hass)
        # Warm the profile cache off the loop before constructing the device.
        await self.hass.async_add_executor_job(load_profile)
        device = DenonAvrDevice(session, host)
        identity = await device.async_fetch_identity()
        self._port = device.http_port
        self._mac, self._serial = identity.mac_address, identity.serial_number
        self._described = described(identity)
        return identity.mac_address or identity.serial_number, identity.model_name or "Denon AVR"

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
        return await self._async_create(name, self._entry_data(host))

    async def _async_create(self, title: str, data: dict[str, Any]) -> ConfigFlowResult:
        """Create the entry; first offer to take over another integration's entry.

        Then a receiver of no described generation (a system, an interface
        level not seen yet) is offered the tests, chosen by default. A
        receiver of a described generation needs none; for all, the tests
        are in the options.
        """

        self._new_entry = (title, data)
        self._old = move.old_entries(self.hass, data.get(CONF_HOST), self._mac, self._serial)
        if self._old:
            return await self.async_step_move_offer()
        return await self._async_finish()

    async def async_step_move_offer(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The receiver is in another Denon integration: take that entry over?"""

        old = self._old[0]
        return self.async_show_menu(
            step_id="move_offer",
            menu_options=["move_yes", "move_no"],
            description_placeholders={
                "model": self._new_entry[0],
                "title": old.title,
                "integration": move.NAMES[old.domain],
            },
        )

    async def async_step_move_yes(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Take the other entry over: disable it now, hand its entities over at setup."""

        for old in self._old:
            await move.async_disable(self.hass, old)
        self._move = [old.entry_id for old in self._old]
        return await self._async_finish()

    async def async_step_move_no(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_finish()

    async def _async_finish(self) -> ConfigFlowResult:
        """Check the control connection, then create the entry (or offer the tests).

        Not when the other integration's entry is being taken over: it held
        the connection until a moment ago and lets go once it is unloaded.
        """

        title, data = self._new_entry
        if not self._move and not self._without_telnet and not await async_answers(
            data[CONF_HOST]
        ):
            return self.async_show_menu(
                step_id="telnet_unavailable",
                menu_options=["telnet_retry", "telnet_continue"],
                description_placeholders={"model": title},
            )
        if self._described:
            return self.async_create_entry(
                title=title, data=data, options=self._options(), description=CREATED
            )
        return self._unknown_model_form()

    async def async_step_telnet_unavailable(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """HTTP answers, the control connection does not: check again or go on."""

        return await self._async_finish()

    async def async_step_telnet_retry(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_finish()

    async def async_step_telnet_continue(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Go on over the web interface; the integration connects once the port is free."""

        self._without_telnet = True
        return await self._async_finish()

    def _options(self) -> dict[str, Any]:
        return {move.OPT_MOVE_PENDING: self._move} if self._move else {}

    def _unknown_model_form(self) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="unknown_model",
            data_schema=vol.Schema(
                {vol.Required(CONF_TEST, default=True): BooleanSelector()}
            ),
            description_placeholders={"model": self._new_entry[0]},
        )

    def _create_with_tests(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        title, data = self._new_entry
        options = self._options()
        if user_input.get(CONF_TEST):
            # The audible part needs someone at the TV: not in the background.
            options[OPT_CHECK_PENDING] = True
            options[OPT_TEST_PENDING] = [QUIET]
        return self.async_create_entry(
            title=title, data=data, options=options, description=CREATED
        )

    async def async_step_unknown_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """A receiver of no described generation: run the receiver test after setup?

        The check runs once the integration has connected, over its own
        connection, and the result goes into the diagnostics. Nothing is sent
        anywhere; a notification then points to a prefilled issue on GitHub.
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
            return await self._async_create(self._name, self._entry_data(self._host))
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
