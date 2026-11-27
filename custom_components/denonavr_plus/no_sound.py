"""Why is there no sound? Checks a zone and says it in plain words.

Uses only what the integration already knows; asks the receiver nothing.
Each finding has a message (the exceptions section of the translations,
`no_sound_<key>`), and some a safe fix: switch the zone on, unmute, send
HDMI audio to the receiver instead of the TV. Volume, speakers and inputs
are only named, never changed: that is the listener's call.

Offered as the action denonavr_plus.why_no_sound (with or without fixing)
and as a button per zone that fixes what it safely can and posts the
result as a notification.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.translation import async_get_translations

from . import choices, linked_players
from .avr.device import POWER_ON_SETTLE
from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import DenonAvrCoordinator

#: Below this the zone is all but silent (the receiver's 80 is 0 dB).
QUIET_DB = -60.0

#: Stands for the main zone's name until the message is told.
_MAIN_ZONE = "\x00main_zone"

FIX_POWER = "power_on"
FIX_UNMUTE = "unmute"
FIX_HDMI_AUDIO = "hdmi_audio_amp"


@dataclass(frozen=True)
class Finding:
    """One reason there may be no sound, and the fix that applies, if any."""

    key: str
    placeholders: dict[str, str] = field(default_factory=dict)
    fix: str | None = None


def findings(
    coordinator: DenonAvrCoordinator,
    zone_id: str,
    options: dict[str, Any],
    linked: State | None,
) -> list[Finding]:
    """The findings for a zone, most likely first; one 'nothing_found' if none."""

    if not coordinator.device.telnet_connected:
        # Over the web interface only power, volume and input are known.
        return [Finding("not_connected")]
    device = coordinator.device
    data = coordinator.data
    zone = data.zone(zone_id)
    # Zone 2 on SOURCE plays the main zone's input.
    effective = device.zone_source(zone_id)
    descriptor = next((item for item in device.discovery.zones if item.id == zone_id), None)
    # Named in the message's language when the finding is told (message()).
    zone_name = (descriptor.name if descriptor is not None else None) or _MAIN_ZONE
    source = zone.source
    source_name = (
        choices.source_display(device.discovery, options, effective) if effective is not None else ""
    ) or ""
    found: list[Finding] = []
    if zone.power is False:
        found.append(Finding("zone_off", {"zone": zone_name}, FIX_POWER))
    if zone.muted:
        found.append(Finding("muted", {"zone": zone_name}, FIX_UNMUTE))
    zero = device.volume_reference
    if zone.volume_raw is not None and zone.volume_raw - zero <= QUIET_DB:
        volume = f"{zone.volume_raw - zero:.0f} dB" if zone.volume_raw else "—"
        found.append(Finding("volume_low", {"volume": volume}))
    is_main = descriptor is None or descriptor.is_main
    main = data.zone("main")
    if not is_main and source == "SOURCE" and main.power is False:
        found.append(Finding("follows_main_off", {"zone": zone_name}))
    if effective is not None and effective in device.refused_sources(zone_id):
        found.append(Finding("refused_input", {"zone": zone_name, "input": source_name}))
    if is_main and data.values.get("hdmi_audio_out") == "TV":
        found.append(Finding("hdmi_audio_tv", fix=FIX_HDMI_AUDIO))
    if is_main and data.values.get("front_speaker") == "SPB":
        found.append(Finding("speakers_b"))
    player = linked.attributes.get("friendly_name", linked.entity_id) if linked else ""
    playing = linked_players.is_busy(linked)
    if linked is not None and not playing:
        found.append(Finding("player_not_playing", {"player": str(player), "input": source_name}))
    if zone.power and _input_signal(coordinator, zone_id, is_main, effective) is False:
        key = "no_signal_while_playing" if playing else "no_signal"
        found.append(Finding(key, {"input": source_name, "player": str(player)}))
    return found or [Finding("nothing_found")]


def _input_signal(
    coordinator: DenonAvrCoordinator, zone_id: str, is_main: bool, source: str | None
) -> bool | None:
    """Whether the zone's input carries a signal; None where nothing tells.

    The receiver reports the main zone's input only; network sources and the
    tuner have no signal to go by (as the media player's playing/idle option).
    """

    device = coordinator.device
    if source is None or device.is_tuner(source):
        return None
    if source in device.profile.netaudio.get("sources", []):
        return None
    main = coordinator.data.zone("main")
    if not is_main and not (main.power and main.source == source):
        return None
    readonly = coordinator.data.readonly
    if "sample_rate" not in readonly:
        return None
    return readonly["sample_rate"] is not None


async def async_fix(coordinator: DenonAvrCoordinator, zone_id: str, finding: Finding) -> bool:
    """Apply the finding's safe fix; whether there was one."""

    device = coordinator.device
    if finding.fix == FIX_POWER:
        await device.async_set_power(zone_id, True)
    elif finding.fix == FIX_UNMUTE:
        await device.async_set_mute(zone_id, False)
    elif finding.fix == FIX_HDMI_AUDIO:
        await device.async_set_control("hdmi_audio_out", "AMP")
    else:
        return False
    return True


async def async_texts(hass: HomeAssistant, language: str | None = None) -> dict[str, str]:
    """The messages in a language, by default Home Assistant's (English where missing)."""

    prefix = f"component.{DOMAIN}.exceptions."
    texts: dict[str, str] = {}
    for language in ("en", language or hass.config.language):
        found = await async_get_translations(hass, language, "exceptions", [DOMAIN])
        for key, text in found.items():
            if key.startswith(prefix) and key.endswith(".message"):
                texts[key[len(prefix) : -len(".message")]] = text
    return texts


def message(texts: dict[str, str], finding: Finding, fixed: bool) -> str:
    """The finding told with the given texts (async_texts)."""

    key = f"no_sound_{finding.key}{'_fixed' if fixed else ''}"
    placeholders = {
        name: texts.get("no_sound_main_zone", "The main zone") if value == _MAIN_ZONE else value
        for name, value in finding.placeholders.items()
    }
    text = texts.get(key, key)
    try:
        return text.format(**placeholders)
    except (KeyError, IndexError, ValueError):
        return text


async def async_check(
    hass: HomeAssistant,
    coordinator: DenonAvrCoordinator,
    zone_id: str,
    options: dict[str, Any],
    *,
    fix: bool,
    language: str | None = None,
) -> list[dict[str, Any]]:
    """Check a zone, fix what was asked, and return the findings as plain data."""

    code = coordinator.device.zone_source(zone_id)
    link = linked_players.links_on_wire(options, coordinator.device).get(code) if code else None
    linked = hass.states.get(link.entity_id) if link is not None else None
    texts = await async_texts(hass, language)
    result: list[dict[str, Any]] = []
    for finding in findings(coordinator, zone_id, options, linked):
        fixed = fix and await async_fix(coordinator, zone_id, finding)
        if fixed and finding.fix == FIX_POWER:
            # A receiver still starting up drops what comes next (AVR-X2000).
            await asyncio.sleep(POWER_ON_SETTLE)
        result.append(
            {"check": finding.key, "message": message(texts, finding, fixed), "fixed": fixed}
        )
    return result


async def async_notify(
    hass: HomeAssistant, entry_id: str, zone_id: str, result: list[dict[str, Any]]
) -> None:
    """Post the result as a notification (the button)."""

    texts = await async_texts(hass)
    persistent_notification.async_create(
        hass,
        "\n".join(f"- {item['message']}" for item in result),
        title=texts.get("no_sound_title", "Why no sound?"),
        notification_id=f"{DOMAIN}_no_sound_{entry_id}_{zone_id}",
    )
