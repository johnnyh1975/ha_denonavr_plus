"""What the steps of the options flow share: the flow's state and helpers.

The steps are split by topic (options_flow.py, options_playback.py,
options_activities.py); each part derives from OptionsFlowBase.
"""

from __future__ import annotations

from typing import Any


from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .linked_players import (
    Link,
)


#: The integration whose players may be made of this receiver's (see _own_players).
MUSIC_ASSISTANT = "music_assistant"

# The volume range offered in the options, in dB relative to the reference
# (the raw scale 0..98 is -80..+18 dB).
#: Fields of the activity steps that are not stored in an activity.
KEY_ACTIVITY = "activity"
KEY_DELETE = "delete"
DB_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=-80.0, max=18.0, step=0.5, unit_of_measurement="dB", mode=NumberSelectorMode.BOX
    )
)
STEP_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=0.5, max=5.0, step=0.5, unit_of_measurement="dB", mode=NumberSelectorMode.BOX
    )
)
# Zones in the order the options show them; the receiver decides which exist.
ZONE_ORDER = ("main", "zone2", "zone3")
ZONE_MARKER = "_media_player_"


def zones_of(hass: Any, entry: ConfigEntry) -> list[str]:
    """The zones of this receiver, from its media players in the registry.

    Read from the registry so the options open even when the receiver is
    unreachable.
    """

    registry = er.async_get(hass)
    found = {
        item.unique_id.split(ZONE_MARKER)[-1]
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        if item.domain == "media_player" and ZONE_MARKER in item.unique_id
    }
    return [zone for zone in ZONE_ORDER if zone in found] or ["main"]


def not_offered(stored: Any, offered: set[str]) -> list[str]:
    """The stored choices that are not offered now, kept as they were."""

    return [str(code) for code in stored or [] if code not in offered]


class OptionsFlowBase(OptionsFlow):
    """The options flow's state and the helpers every part uses."""

    def __init__(self) -> None:
        self._pending: dict[str, Any] = {}
        self._mode_names_for: list[str] = []
        self._link_input: str = ""
        self._playback: dict[str, Any] | None = None
        self._link_edits: dict[str, Link | None] = {}
        self._activities: list[dict[str, Any]] | None = None
        self._activity_index: int | None = None

    # Helpers -------------------------------------------------------------

    def _discovery(self) -> Any | None:
        """The receiver's discovery, when the entry is loaded."""

        coordinator = getattr(self.config_entry, "runtime_data", None)
        device = getattr(coordinator, "device", None)
        return getattr(device, "discovery", None)

    def _save(self, values: dict[str, Any]) -> ConfigFlowResult:
        # Merge: the options also hold markers and settings of other steps.
        return self.async_create_entry(data={**self.config_entry.options, **values})

    def _own_players(self) -> list[str]:
        """This receiver's players, and Music Assistant's players made of them.

        Music Assistant's "Home Assistant MediaPlayers" provider builds a player
        from a Home Assistant media player and names it by that entity id; its
        Music Assistant entity has it as unique id. Linked to the network input,
        play would go back and forth between the two, so they are left out too.
        """

        registry = er.async_get(self.hass)
        own = [
            item.entity_id
            for item in er.async_entries_for_config_entry(registry, self.config_entry.entry_id)
            if item.domain == "media_player"
        ]
        wrapped = [
            item.entity_id
            for item in registry.entities.values()
            if item.platform == MUSIC_ASSISTANT
            and item.domain == "media_player"
            and item.unique_id in own
        ]
        return own + wrapped
