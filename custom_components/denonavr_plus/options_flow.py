"""Options flow for the Denon AVR Plus integration.

The menu and the steps for volume, zones, connection, entities, sources and
sound modes are here; playback and activities have modules of their own,
the receiver test is in receiver_test.py.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlowResult,
)
from homeassistant.data_entry_flow import section
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

from .sound_modes import OPT_READABLE_SOUND_MODES
from .const import (
    OPT_HEOS,
    OPT_POLL_INTERVAL,
    OPT_SETUP_REFRESH,
)
from .receiver_test import ReceiverTestSteps
from .entity_groups import OPT_ENTITY_GROUPS
from .coordinator import connection_options
from . import choices, entity_groups, move, zone_sources
from .choices import (
    OPT_MAIN_OFF_STANDBY,
    OPT_SETTINGS_TURN_ON,
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

from .options_activities import ActivitySteps
from .options_playback import PlaybackSteps

from .options_base import (
    DB_SELECTOR,
    STEP_SELECTOR,
    ZONE_ORDER,
    OptionsFlowBase,
    not_offered,
    zones_of,
)


class DenonAvrOptionsFlow(ReceiverTestSteps, PlaybackSteps, ActivitySteps, OptionsFlowBase):
    """Options: volume, zones, and which sources and sound modes appear, and how."""

    # Menu ------------------------------------------------------------------

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        # Only what can be done now: steps that need the receiver appear while
        # it is connected, entities only with a group to show or hide.
        discovery = self._discovery()
        options = ["volume", "zones"]
        if discovery is not None:
            options.append("sources")
            if self._known_sound_modes(discovery):
                options.append("sound_modes")
            options += ["playback", "activities"]
        options.append("connection")
        if self._entity_groups_offered():
            options.append("entities")
        coordinator = getattr(self.config_entry, "runtime_data", None)
        if coordinator is not None and coordinator.device.telnet_connected:
            options.append("tests_start")
        if await move.async_moved(self.hass, self.config_entry.entry_id):
            options.append("move_back")
        return self.async_show_menu(step_id="init", menu_options=options)

    async def async_step_move_back(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Undo the move: this entry off, the entities back to the old one."""

        moves = await move.async_moved(self.hass, self.config_entry.entry_id)
        if user_input is None:
            return self.async_show_form(
                step_id="move_back",
                description_placeholders={
                    "titles": ", ".join(item["title"] for item in moves),
                    "count": str(sum(len(item["entities"]) for item in moves)),
                },
            )
        # After the flow: disabling this entry ends it.
        # Not a task of this entry: disabling the entry would cancel it.
        self.hass.async_create_background_task(
            move.async_move_back(self.hass, self.config_entry), "denonavr_plus move back"
        )
        return self.async_abort(reason="moved_back")

    # Receiver test (steps in receiver_test.ReceiverTestSteps) ---------------

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
        zones = zones_of(self.hass, self.config_entry)
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
                ): DB_SELECTOR,
                vol.Optional(
                    OPT_TURN_ON_VOLUME,
                    description={"suggested_value": current.get(OPT_TURN_ON_VOLUME)},
                ): DB_SELECTOR,
                vol.Optional(
                    OPT_VOLUME_STEP,
                    description={"suggested_value": current.get(OPT_VOLUME_STEP)},
                ): STEP_SELECTOR,
            }
            if zone == "main":
                # Announcements play on the main zone (over HEOS).
                zone_fields[
                    vol.Optional(
                        OPT_ANNOUNCE_VOLUME,
                        description={"suggested_value": current.get(OPT_ANNOUNCE_VOLUME)},
                    )
                ] = DB_SELECTOR
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
            else zones_of(self.hass, self.config_entry)
        )
        stored = self.config_entry.options.get(OPT_ZONES)
        known += [zone for zone in stored or [] if zone not in known]
        additional = [zone for zone in ZONE_ORDER if zone in known and zone != "main"]
        options = self.config_entry.options
        if user_input is not None:
            values: dict[str, Any] = {
                OPT_MAIN_OFF_STANDBY: bool(user_input.get(OPT_MAIN_OFF_STANDBY, False)),
                OPT_SETTINGS_TURN_ON: bool(user_input.get(OPT_SETTINGS_TURN_ON, False)),
            }
            if additional:
                values[OPT_ZONES] = list(user_input[OPT_ZONES])
            return self._save(values)
        fields: dict[Any, Any] = {}
        if additional:
            # A receiver with one zone gets the power settings only.
            current = stored if stored is not None else additional
            fields[vol.Required(OPT_ZONES, default=list(current))] = SelectSelector(
                SelectSelectorConfig(
                    options=additional,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                    translation_key="zone",
                )
            )
        fields[
            vol.Required(OPT_MAIN_OFF_STANDBY, default=bool(options.get(OPT_MAIN_OFF_STANDBY, False)))
        ] = BooleanSelector()
        fields[
            vol.Required(OPT_SETTINGS_TURN_ON, default=bool(options.get(OPT_SETTINGS_TURN_ON, False)))
        ] = BooleanSelector()
        return self.async_show_form(step_id="zones", data_schema=vol.Schema(fields))

    # Entities ----------------------------------------------------------------

    def _hidden_counts(self) -> dict[str, int]:
        discovery = self._discovery()
        configured = discovery.configured_channels if discovery is not None else set()
        return entity_groups.hidden_counts(self.hass, self.config_entry, configured)

    def _entity_groups_offered(self) -> bool:
        """A group to show (hidden entities) or to hide again (one shown)."""

        chosen = self.config_entry.options.get(OPT_ENTITY_GROUPS, [])
        return bool(chosen) or any(self._hidden_counts().values())

    async def async_step_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """A toggle per group of the entities that start hidden.

        Works from the registry, so also without the receiver. The change is
        applied by the update listener (entity_groups.async_apply).
        """

        chosen = list(self.config_entry.options.get(OPT_ENTITY_GROUPS, []))
        counts = self._hidden_counts()
        offered = [group for group in entity_groups.GROUPS if counts.get(group) or group in chosen]
        if user_input is not None:
            shown = [group for group in offered if user_input.get(group)]
            return self._save({OPT_ENTITY_GROUPS: shown})
        if not offered:
            return self.async_abort(reason="no_hidden_entities")
        return self.async_show_form(
            step_id="entities",
            data_schema=vol.Schema(
                {
                    vol.Required(group, default=group in chosen): BooleanSelector()
                    for group in offered
                }
            ),
            description_placeholders={
                f"n_{group}": str(counts.get(group, 0)) for group in entity_groups.GROUPS
            },
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
        every = {option["value"] for option in visible}
        stored = options.get(OPT_SOURCES)
        selection: dict[str, Any] = stored if isinstance(stored, dict) else {}
        if user_input is not None:
            selection_in: dict[str, list[str]] = {}
            for zone in zones:
                chosen = list(user_input.get(zone, {}).get(OPT_SOURCES, []))
                # All of them means all, also sources the receiver adds later.
                # A chosen source not offered now (hidden on the receiver) is
                # kept for when it comes back.
                selection_in[zone] = (
                    [] if set(chosen) >= every else chosen + not_offered(selection.get(zone), every)
                )
            self._pending = {
                OPT_SOURCES: selection_in,
                OPT_SOURCE_SHORTCUTS: bool(user_input.get(OPT_SOURCE_SHORTCUTS, True)),
            }
            if zone_sources.ZONE in zones:
                # Chosen here, every input included: no preselection later.
                self._pending[zone_sources.OPT_ZONE2_SOURCES_ASKED] = True
            return await self.async_step_source_names()
        fields: dict[Any, Any] = {
            vol.Required(
                OPT_SOURCE_SHORTCUTS, default=bool(options.get(OPT_SOURCE_SHORTCUTS, True))
            ): BooleanSelector()
        }
        for zone in zones:
            # Only what is offered now: a stored source the receiver no longer
            # offers would make the form's own default invalid.
            chosen = [code for code in selection.get(zone) or [] if code in every] or [
                option["value"] for option in visible
            ]
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
            # Only a name given here can clash: two the receiver itself shows
            # alike (an input named "Tuner" beside the tuner) must not block
            # saving.
            if any(shown.count(name) > 1 for name in names.values()):
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
        stored_modes = self.config_entry.options.get(OPT_SOUND_MODES)
        if user_input is not None:
            chosen_in = list(user_input[OPT_SOUND_MODES])
            # The names step lists the chosen modes; "all" is stored as empty.
            # A chosen mode not reported now is kept for when it comes back.
            self._pending = {
                OPT_SOUND_MODES: (
                    []
                    if set(chosen_in) >= set(modes)
                    else chosen_in + not_offered(stored_modes, set(modes))
                ),
                OPT_READABLE_SOUND_MODES: bool(user_input.get(OPT_READABLE_SOUND_MODES, True)),
            }
            self._mode_names_for = chosen_in
            return await self.async_step_sound_mode_names()
        chosen = [mode for mode in stored_modes or [] if mode in modes] or modes
        return self.async_show_form(
            step_id="sound_modes",
            data_schema=vol.Schema(
                {
                    vol.Required(OPT_SOUND_MODES, default=list(chosen)): SelectSelector(
                        SelectSelectorConfig(options=modes, multiple=True)
                    ),
                    vol.Required(
                        OPT_READABLE_SOUND_MODES,
                        default=bool(
                            self.config_entry.options.get(OPT_READABLE_SOUND_MODES, True)
                        ),
                    ): BooleanSelector(),
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

