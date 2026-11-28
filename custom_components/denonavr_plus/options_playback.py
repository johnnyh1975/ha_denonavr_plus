"""Options: playback, the players linked to the inputs (linked_players.py)."""

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
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from . import choices, linked_players
from .linked_players import (
    KEY_FOLLOW,
    KEY_FULL_VOLUME,
    KEY_POWER,
    KEY_PLAYER,
    OPT_LINKED_PLAYERS,
    OPT_PAUSE_ON_OFF,
    OPT_SIGNAL_STATE,
    Link,
)


from .options_base import OptionsFlowBase


class PlaybackSteps(OptionsFlowBase):
    """Configure → Playback: linked players and how playback is shown."""

    # Playback and linked players -------------------------------------------

    def _linkable(self) -> list[tuple[str, str]]:
        """The inputs a player can be linked to, with their shown names.

        Not the tuner: what it plays comes from the receiver itself. The
        network input can take Music Assistant's player, whose queue and
        library then go with it.
        """

        coordinator = getattr(self.config_entry, "runtime_data", None)
        device = getattr(coordinator, "device", None)
        if device is None:
            return []
        options = dict(self.config_entry.options)
        return [
            (source.code, choices.source_display(device.discovery, options, source.code))
            for source in device.discovery.visible_sources()
            if not device.is_tuner(source.code)
        ]

    def _is_network(self, code: str) -> bool:
        coordinator = getattr(self.config_entry, "runtime_data", None)
        device = getattr(coordinator, "device", None)
        if device is None:
            return False
        return linked_players.wire_code(device, code) in device.profile.netaudio.get("sources", [])

    def _player_name(self, entity_id: str) -> str:
        state = self.hass.states.get(entity_id)
        return str(state.name) if state is not None else entity_id

    def _suggested_player(self, code: str, name: str) -> str | None:
        """A player that likely is the device on the input (linked_players.suggest_player)."""

        coordinator = getattr(self.config_entry, "runtime_data", None)
        device = getattr(coordinator, "device", None)
        model = device.discovery.device.model_name if device is not None else None
        return linked_players.suggest_player(
            self.hass,
            name,
            exclude=self._own_players(),
            network=self._is_network(code),
            receiver_names=[self.config_entry.title, *([model] if model else [])],
        )

    def _playback_draft(self) -> dict[str, Any]:
        """The playback options as they will be saved: the entry's own now, with
        the change made in this dialog on top.

        Only the input edited here is taken from the dialog: a player renamed
        meanwhile (which rewrites the options) stays renamed on the others.
        """

        options = dict(self.config_entry.options)
        if self._playback is None:
            self._playback = {
                OPT_SIGNAL_STATE: bool(options.get(OPT_SIGNAL_STATE, False)),
                OPT_PAUSE_ON_OFF: bool(options.get(OPT_PAUSE_ON_OFF, True)),
            }
        links = options
        for code, link in self._link_edits.items():
            links = {OPT_LINKED_PLAYERS: linked_players.with_link(links, code, link)}
        return {
            OPT_SIGNAL_STATE: self._playback[OPT_SIGNAL_STATE],
            OPT_PAUSE_ON_OFF: self._playback[OPT_PAUSE_ON_OFF],
            OPT_LINKED_PLAYERS: linked_players.stored_links(links),
        }

    async def async_step_playback(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Link a player to an input, or how playback is shown."""

        if self._discovery() is None:
            return self.async_abort(reason="not_connected")
        options = ["playback_display"]
        if self._linkable():
            options.insert(0, "playback_link")
        return self.async_show_menu(step_id="playback", menu_options=options)

    async def async_step_playback_link(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Which input gets a linked player; an input with one shows it."""

        inputs = self._linkable()
        if user_input is not None and user_input.get("input") in dict(inputs):
            self._link_input = str(user_input["input"])
            return await self.async_step_linked_input()
        current = linked_players.links(self._playback_draft())
        return self.async_show_form(
            step_id="playback_link",
            data_schema=vol.Schema(
                {
                    vol.Required("input"): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=code,
                                    label=(
                                        f"{name} → {self._player_name(current[code].entity_id)}"
                                        if code in current
                                        else name
                                    ),
                                )
                                for code, name in inputs
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_playback_display(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pausing linked players with their zone, and playing by input signal."""

        draft = self._playback_draft()
        if user_input is not None:
            assert self._playback is not None
            self._playback[OPT_SIGNAL_STATE] = bool(user_input.get(OPT_SIGNAL_STATE, False))
            self._playback[OPT_PAUSE_ON_OFF] = bool(user_input.get(OPT_PAUSE_ON_OFF, True))
            return self._save(dict(self._playback_draft()))
        return self.async_show_form(
            step_id="playback_display",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        OPT_PAUSE_ON_OFF, default=draft[OPT_PAUSE_ON_OFF]
                    ): BooleanSelector(),
                    vol.Required(
                        OPT_SIGNAL_STATE, default=draft[OPT_SIGNAL_STATE]
                    ): BooleanSelector(),
                }
            ),
        )

    async def async_step_linked_input(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The player linked to one input, and whether the receiver follows it."""

        code = self._link_input
        names = dict(self._linkable())
        draft = self._playback_draft()
        current = linked_players.links(draft).get(code)
        if user_input is not None:
            # The selector refuses the receiver's own players; without a
            # player the input is unlinked and its switches have no meaning.
            player = str(user_input.get(KEY_PLAYER) or "")
            link = (
                Link(
                    player,
                    bool(user_input.get(KEY_FOLLOW, False)),
                    # Not on the network input: see linked_players._full_volume_now.
                    bool(user_input.get(KEY_FULL_VOLUME, False)) and not self._is_network(code),
                    bool(user_input.get(KEY_POWER, False)),
                )
                if player
                else None
            )
            self._link_edits[code] = link
            return self._save(dict(self._playback_draft()))
        schema = vol.Schema(
            {
                # A suggestion, not a default, so the player can be cleared.
                vol.Optional(
                    KEY_PLAYER,
                    description={
                        "suggested_value": current.entity_id
                        if current
                        else self._suggested_player(code, names.get(code, code))
                    },
                ): EntitySelector(
                    EntitySelectorConfig(
                        domain="media_player", exclude_entities=self._own_players()
                    )
                ),
                vol.Required(
                    KEY_FOLLOW, default=current.follow if current else False
                ): BooleanSelector(),
                vol.Required(
                    KEY_POWER, default=current.power if current else False
                ): BooleanSelector(),
            }
        )
        if not self._is_network(code):
            # Music Assistant's player may have the receiver as its volume:
            # full volume there would be the receiver's.
            schema = schema.extend(
                {
                    vol.Required(
                        KEY_FULL_VOLUME, default=current.full_volume if current else False
                    ): BooleanSelector()
                }
            )
        return self.async_show_form(
            step_id="linked_input",
            data_schema=schema,
            description_placeholders={"input": names.get(code, code)},
        )
