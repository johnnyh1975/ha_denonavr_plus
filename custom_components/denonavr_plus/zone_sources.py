"""Which inputs Zone 2 plays: chosen once, after the receiver is set up.

Smaller receivers play no HDMI input in Zone 2 (an AVR-X2600H stays silent
and does not take Z2AUX2). Zone 2's input list starts without the HDMI inputs
when the receiver does not answer Z2HDA? (Zone 2 HDMI audio), the sign that
its Zone 2 has no HDMI; otherwise with all. It is the Zone 2 input list of the
options (Configure → Sources), where it can be changed; an input Zone 2
refuses is still taken out of the list when it happens.
"""

from __future__ import annotations


from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from . import choices, markers
from .avr.models import Discovery
from .const import DOMAIN
from .coordinator import DenonAvrConfigEntry

ISSUE = "zone2_sources"
ZONE = "zone2"
#: Set once Zone 2's inputs were chosen (before 1.6 through a repair).
OPT_ZONE2_SOURCES_ASKED = markers.ZONE2_SOURCES_ASKED
#: The control whose answer tells that Zone 2 has HDMI.
HDMI_CONTROL = "zone2_hdmi_audio"
#: Inputs with an HDMI connector on receivers of the X series as delivered;
#: the assignment can be changed on the receiver, so this is only the start.
#: Telnet codes, and the names of the Deviceinfo source list the inputs come
#: from when telnet listed none.
HDMI_INPUTS = frozenset(
    {"SAT/CBL", "DVD", "BD", "GAME", "MPLAY", "AUX1", "AUX2", "8K"}
    | {"CBLSAT", "CBL/SAT", "Blu-ray", "Media Player"}
)


def issue_id(entry_id: str) -> str:
    return f"{ISSUE}_{entry_id}"


def preselected(discovery: Discovery) -> list[str]:
    """The inputs offered for Zone 2 at first: all, or all but HDMI ones."""

    visible = [source.code for source in discovery.visible_sources()]
    if HDMI_CONTROL in discovery.answered:
        return visible
    return [code for code in visible if code not in HDMI_INPUTS]


@callback
def async_preselect(hass: HomeAssistant, entry: DenonAvrConfigEntry) -> None:
    """Choose Zone 2's inputs once, for a receiver whose Zone 2 inputs were never chosen.

    Before 1.6 a repair asked; an open one from then goes, the choice is made
    the same way without asking.
    """

    ir.async_delete_issue(hass, DOMAIN, issue_id(entry.entry_id))
    discovery = entry.runtime_data.device.discovery
    options = entry.options
    selection = options.get(choices.OPT_SOURCES)
    chosen = selection.get(ZONE) if isinstance(selection, dict) else None
    if (
        not any(zone.id == ZONE for zone in discovery.zones)
        or not choices.zone_enabled(dict(options), ZONE)
        or entry.runtime_data.markers.get(OPT_ZONE2_SOURCES_ASKED)
        or chosen
        or not discovery.visible_sources()
    ):
        return
    picked = preselected(discovery)
    every = [source.code for source in discovery.visible_sources()]
    new_selection = dict(selection) if isinstance(selection, dict) else {}
    # All of them means all: also inputs the receiver adds later.
    new_selection[ZONE] = [] if set(picked) >= set(every) else picked
    entry.runtime_data.markers.set(OPT_ZONE2_SOURCES_ASKED, True)
    markers.update_options(hass, entry, changes={choices.OPT_SOURCES: new_selection})
