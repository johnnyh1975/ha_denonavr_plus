"""Options: activities (activities.py)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlowResult,
)
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
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

from .number import resolve_bounds
from . import activities, choices


from .options_base import DB_SELECTOR, KEY_ACTIVITY, KEY_DELETE, OptionsFlowBase, zones_of


class ActivitySteps(OptionsFlowBase):
    """Configure → Activities: add, change and delete activities.

    New or change → name and zone (or delete) → the input and what else the
    zone takes; each activity is saved when its last step is submitted.
    """

    _activity_first: dict[str, Any] | None = None

    def _activity_draft(self) -> list[dict[str, Any]]:
        if self._activities is None:
            self._activities = [
                item.as_dict() for item in activities.activities(dict(self.config_entry.options))
            ]
        return self._activities

    async def async_step_activities(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """New activity, or change one; without any, straight to a new one."""

        if self._discovery() is None:
            return self.async_abort(reason="not_connected")
        draft = self._activity_draft()
        if not draft:
            return await self.async_step_activity_new()
        return self.async_show_menu(
            step_id="activities",
            menu_options=["activity_new", "activity_change"],
            description_placeholders={"count": str(len(draft))},
        )

    async def async_step_activity_new(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._activity_index = None
        return await self.async_step_activity()

    async def async_step_activity_change(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Which activity to change or delete."""

        draft = self._activity_draft()
        names = [item[activities.KEY_NAME] for item in draft]
        if user_input is not None and user_input.get(KEY_ACTIVITY) in names:
            self._activity_index = names.index(user_input[KEY_ACTIVITY])
            return await self.async_step_activity()
        return self.async_show_form(
            step_id="activity_change",
            data_schema=vol.Schema(
                {
                    vol.Required(KEY_ACTIVITY): SelectSelector(
                        SelectSelectorConfig(options=names, mode=SelectSelectorMode.DROPDOWN)
                    )
                }
            ),
        )

    def _current_activity(self) -> dict[str, Any]:
        index = self._activity_index
        return self._activity_draft()[index] if index is not None else {}

    async def async_step_activity(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Name and zone, or delete the activity."""

        if self._discovery() is None:
            return self.async_abort(reason="not_connected")
        draft = self._activity_draft()
        index = self._activity_index
        current = self._current_activity()
        errors: dict[str, str] = {}
        if user_input is not None:
            if index is not None and user_input.get(KEY_DELETE):
                draft.pop(index)
                return self._save({activities.OPT_ACTIVITIES: list(draft)})
            name = " ".join(str(user_input.get(activities.KEY_NAME, "")).split())
            if not name:
                errors["base"] = "activity_incomplete"
            elif any(
                position != index
                and choices.normalized(other[activities.KEY_NAME]) == choices.normalized(name)
                for position, other in enumerate(draft)
            ):
                errors["base"] = "duplicate_name"
            else:
                self._activity_first = {
                    activities.KEY_NAME: name,
                    activities.KEY_ZONE: user_input[activities.KEY_ZONE],
                }
                return await self.async_step_activity_details()
            current = {**current, **user_input}
        options = dict(self.config_entry.options)
        zones = [
            zone
            for zone in zones_of(self.hass, self.config_entry)
            if choices.zone_enabled(options, zone)
        ]
        stored_zone = current.get(activities.KEY_ZONE)
        if stored_zone and stored_zone not in zones:
            # Its zone hidden since: kept selectable, so the activity can be
            # saved or deleted as it is.
            zones.append(stored_zone)
        fields: dict[Any, Any] = {
            vol.Required(
                activities.KEY_NAME, default=current.get(activities.KEY_NAME, "")
            ): TextSelector(),
            vol.Required(
                activities.KEY_ZONE, default=current.get(activities.KEY_ZONE, zones[0])
            ): SelectSelector(SelectSelectorConfig(options=zones, translation_key="activity_zone")),
        }
        if index is not None:
            fields[vol.Required(KEY_DELETE, default=False)] = BooleanSelector()
        return self.async_show_form(
            step_id="activity", data_schema=vol.Schema(fields), errors=errors
        )

    def _details_schema(self, zone: str, current: dict[str, Any]) -> vol.Schema:
        """The input and what else this zone takes; the main zone has more."""

        discovery = self._discovery()
        assert discovery is not None  # the steps abort without it
        options = dict(self.config_entry.options)
        refused = self.config_entry.runtime_data.device.refused_sources(zone)
        sources = [
            SelectOptionDict(value=code, label=choices.source_display(discovery, options, code))
            for code in choices.source_codes(discovery, options, zone)
            if code not in refused
        ]
        stored = current.get(activities.KEY_SOURCE)
        if stored and all(option["value"] != stored for option in sources):
            # Its input left the zone's list since (or was refused): kept
            # selectable, so the rest of the activity can be changed.
            sources.append(
                SelectOptionDict(value=stored, label=choices.source_display(discovery, options, stored))
            )
        fields: dict[Any, Any] = {
            vol.Required(
                activities.KEY_SOURCE,
                default=current.get(activities.KEY_SOURCE, sources[0]["value"] if sources else ""),
            ): SelectSelector(SelectSelectorConfig(options=sources, mode=SelectSelectorMode.DROPDOWN)),
        }
        main = zone == "main"
        if main:
            modes: list[str] = []
            player = activities.zone_entity_id(
                self.hass, self.config_entry.runtime_data, "media_player", "media_player_main"
            )
            # The main zone's list as it shows (renamed, readable); others typed.
            state = self.hass.states.get(player) if player else None
            if state is not None:
                modes = list(state.attributes.get("sound_mode_list") or [])
            fields[
                vol.Optional(
                    activities.KEY_SOUND_MODE,
                    description={"suggested_value": current.get(activities.KEY_SOUND_MODE)},
                )
            ] = SelectSelector(
                SelectSelectorConfig(options=modes, custom_value=True, mode=SelectSelectorMode.DROPDOWN)
            )
        fields[
            vol.Optional(
                activities.KEY_VOLUME,
                description={"suggested_value": current.get(activities.KEY_VOLUME)},
            )
        ] = DB_SELECTOR
        coordinator = self.config_entry.runtime_data
        spec = coordinator.device.profile.control(activities.HDMI_OUTPUT_CONTROL)
        # Offered where the receiver has the HDMI output select.
        if main and spec is not None and activities.zone_entity_id(
            self.hass, coordinator, "select", f"select_{activities.HDMI_OUTPUT_CONTROL}"
        ):
            values = spec.values
            labels = list(spec.get("labels") or values)
            fields[
                vol.Optional(
                    activities.KEY_HDMI_OUTPUT,
                    description={"suggested_value": current.get(activities.KEY_HDMI_OUTPUT)},
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=value, label=label)
                        for value, label in zip(values, labels, strict=False)
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        delay = coordinator.device.profile.control(activities.AUDIO_DELAY_CONTROL)
        # Offered where the receiver has the audio delay number.
        if main and delay is not None and activities.zone_entity_id(
            self.hass, coordinator, "number", f"number_{activities.AUDIO_DELAY_CONTROL}"
        ):
            low, high, step = resolve_bounds(coordinator.device, delay)
            fields[
                vol.Optional(
                    activities.KEY_AUDIO_DELAY,
                    description={"suggested_value": current.get(activities.KEY_AUDIO_DELAY)},
                )
            ] = NumberSelector(
                NumberSelectorConfig(
                    min=float(low if low is not None else 0),
                    max=float(high if high is not None else 200),
                    step=float(step or 1),
                    unit_of_measurement="ms",
                    mode=NumberSelectorMode.BOX,
                )
            )
        if main:
            fields[
                vol.Required(
                    activities.KEY_CLARITY,
                    default=current.get(activities.KEY_CLARITY, activities.CLARITY_KEEP),
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=[activities.CLARITY_KEEP, activities.CLARITY_ON, activities.CLARITY_OFF],
                    translation_key="activity_clarity",
                )
            )
        fields[
            vol.Required(activities.KEY_WAKE, default=bool(current.get(activities.KEY_WAKE, False)))
        ] = BooleanSelector()
        fields[
            vol.Optional(
                activities.KEY_WAKE_ENTITY,
                description={"suggested_value": current.get(activities.KEY_WAKE_ENTITY)},
            )
        ] = EntitySelector(
            EntitySelectorConfig(
                domain=list(activities.WAKE_DOMAINS), exclude_entities=self._own_players()
            )
        )
        return vol.Schema(fields)

    async def async_step_activity_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The input and what else the zone takes; submitting saves the activity."""

        if self._discovery() is None:
            return self.async_abort(reason="not_connected")
        first = self._activity_first
        assert first is not None  # set by the activity step
        draft = self._activity_draft()
        index = self._activity_index
        current = {**self._current_activity(), **first}
        errors: dict[str, str] = {}
        if user_input is not None:
            item = activities.parse({**user_input, **first})
            if item is None:
                errors["base"] = "activity_incomplete"
            else:
                if index is None:
                    draft.append(item.for_zone().as_dict())
                else:
                    draft[index] = item.for_zone().as_dict()
                return self._save({activities.OPT_ACTIVITIES: list(draft)})
            current = {**current, **user_input}
        return self.async_show_form(
            step_id="activity_details",
            data_schema=self._details_schema(str(first[activities.KEY_ZONE]), current),
            errors=errors,
            description_placeholders={"activity": str(first[activities.KEY_NAME])},
        )
