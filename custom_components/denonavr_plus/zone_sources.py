"""Which inputs Zone 2 plays: asked once, as a repair, after the receiver is set up.

Smaller receivers play no HDMI input in Zone 2 (an AVR-X2600H stays silent
and does not take Z2AUX2). A repair asks once which inputs Zone 2 offers,
with the HDMI inputs deselected when the receiver does not answer Z2HDA?
(Zone 2 HDMI audio), the sign that its Zone 2 has no HDMI. The choice is
the Zone 2 input list of the options, where it can be changed later; an input
Zone 2 refuses is still taken out of the list when it happens.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)

from . import choices
from .avr.models import Discovery
from .const import DOMAIN
from .coordinator import DenonAvrConfigEntry

ISSUE = "zone2_sources"
ZONE = "zone2"
#: Set once Zone 2's inputs were chosen through the repair.
OPT_ZONE2_SOURCES_ASKED = "zone2_sources_asked"
#: The control whose answer tells that Zone 2 has HDMI.
HDMI_CONTROL = "zone2_hdmi_audio"
#: Inputs with an HDMI connector on receivers of the X series as delivered;
#: the assignment can be changed on the receiver, so this is only the start.
HDMI_INPUTS = frozenset({"SAT/CBL", "DVD", "BD", "GAME", "MPLAY", "AUX1", "AUX2", "8K"})


def issue_id(entry_id: str) -> str:
    return f"{ISSUE}_{entry_id}"


def preselected(discovery: Discovery) -> list[str]:
    """The inputs offered for Zone 2 at first: all, or all but HDMI ones."""

    visible = [source.code for source in discovery.visible_sources()]
    if HDMI_CONTROL in discovery.answered:
        return visible
    return [code for code in visible if code not in HDMI_INPUTS]


@callback
def async_update_issue(hass: HomeAssistant, entry: DenonAvrConfigEntry) -> None:
    """Ask once, for a receiver with Zone 2 shown whose inputs were never chosen."""

    discovery = entry.runtime_data.device.discovery
    options = entry.options
    selection = options.get(choices.OPT_SOURCES)
    chosen = selection.get(ZONE) if isinstance(selection, dict) else None
    if (
        not any(zone.id == ZONE for zone in discovery.zones)
        or not choices.zone_enabled(dict(options), ZONE)
        or options.get(OPT_ZONE2_SOURCES_ASKED)
        or chosen
    ):
        ir.async_delete_issue(hass, DOMAIN, issue_id(entry.entry_id))
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id(entry.entry_id),
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE,
        translation_placeholders={"model": discovery.device.model_name or "Denon AVR"},
        data={"entry_id": entry.entry_id},
    )


class ZoneSourcesRepairFlow(RepairsFlow):
    """The repair "Which inputs can Zone 2 play?": stores the Zone 2 input list."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        # The repair starts with the issue's data, not with an answer.
        return await self.async_step_choose()

    async def async_step_choose(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        entry: DenonAvrConfigEntry | None = self.hass.config_entries.async_get_entry(
            self._entry_id
        )
        if entry is None or entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        discovery = entry.runtime_data.device.discovery
        visible = [
            SelectOptionDict(
                value=source.code,
                label=choices.source_display(discovery, dict(entry.options), source.code),
            )
            for source in discovery.visible_sources()
        ]
        if user_input is not None:
            chosen = list(user_input.get(choices.OPT_SOURCES) or [])
            selection = dict(entry.options.get(choices.OPT_SOURCES) or {})
            # All of them, or none, means all: also inputs the receiver adds later.
            every = {option["value"] for option in visible}
            selection[ZONE] = [] if set(chosen) >= every else chosen
            self.hass.config_entries.async_update_entry(
                entry,
                options={
                    **entry.options,
                    choices.OPT_SOURCES: selection,
                    OPT_ZONE2_SOURCES_ASKED: True,
                },
            )
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="choose",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        choices.OPT_SOURCES, default=preselected(discovery)
                    ): SelectSelector(SelectSelectorConfig(options=visible, multiple=True))
                }
            ),
            description_placeholders={"model": discovery.device.model_name or "Denon AVR"},
        )
