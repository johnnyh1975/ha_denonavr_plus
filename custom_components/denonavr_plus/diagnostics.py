"""Diagnostics support for the Denon AVR Plus integration.

Dumps the discovery model and the current state so issues can be debugged
without direct access to the receiver. No credentials are involved. The MAC
address and the serial number are redacted, and the host address is never
included (AGENTS.md rule 6).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .avr.device import DenonAvrDevice
from .avr.generation import generation_of
from .coordinator import DenonAvrConfigEntry

TO_REDACT = {"mac_address", "serial_number"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: DenonAvrConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""

    coordinator = entry.runtime_data
    device = coordinator.device
    discovery = device.discovery
    state = device.state

    return {
        "available": device.available,
        "started_offline": coordinator.started_offline,
        "http_port": device.http_port,
        "setup_port": device.setup_port,
        # The receiver generation (docs/COMPATIBILITY.md); None: not described.
        "generation": (
            None if (generation := generation_of(discovery.device)) is None else generation.key
        ),
        # The capability check, with addresses already replaced (None: not run).
        "capability_check": coordinator.capabilities.result,
        # The command test (None: not run).
        "command_test": coordinator.command_test.result,
        # How long each part of the discovery at the last start took.
        "setup_timings_ms": device.setup_timings,
        "telnet_response_ms": (
            None
            if device.telnet_response_time is None
            else round(device.telnet_response_time * 1000)
        ),
        "device": async_redact_data(asdict(discovery.device), TO_REDACT),
        "features": sorted(discovery.features),
        "zones": [asdict(zone) for zone in discovery.zones],
        "sources": [asdict(source) for source in discovery.sources],
        "channels": [asdict(channel) for channel in discovery.channels],
        "feature_names": discovery.feature_names,
        "option_labels": discovery.option_labels,
        "numeric_meta": discovery.numeric_meta,
        "all_sound_modes": discovery.all_sound_modes,
        "current_sound_modes": discovery.current_sound_modes,
        "media": _media(device),
        # Sources a zone did not switch to since Home Assistant started.
        "refused_sources": {
            zone.id: sorted(device.refused_sources(zone.id)) for zone in discovery.zones
        },
        # The last telnet lines, "<" received, ">" sent: what the receiver said.
        "communication_log": device.communication_log,
        # The same for HEOS, without payloads, account, password or URLs.
        "heos_log": device.heos_log,
        "state": {
            "system_power": state.system_power,
            "zones": {zone_id: asdict(zone) for zone_id, zone in state.zones.items()},
            "values": state.values,
            "readonly": state.readonly,
            "signal": state.signal,
            "channel_levels": state.channel_levels,
            "channel_trims": state.channel_trims,
        },
    }


def _media(device: DenonAvrDevice) -> dict[str, Any]:
    """How now playing and playback reach the receiver, and how it answered.

    Which now-playing fields are filled, not what they say: the diagnostics
    are attached to public issues, and the cover URL holds an address.
    """

    now = device.state.now_playing
    return {
        "heos_enabled": device.heos_enabled,
        "heos_connected": device.heos_connected,
        "telnet_media": device.telnet_media,
        "transport_available": device.transport_available,
        "can_play_url": device.can_play_url,
        "heos_command_results": device.heos_command_results,
        "now_playing": {
            "state": now.state,
            "media_type": now.media_type,
            "repeat": now.repeat,
            "shuffle": now.shuffle,
            "duration": now.duration,
            "has_position": now.position is not None,
            "has": [
                name
                for name in ("title", "artist", "album", "image_url")
                if getattr(now, name)
            ],
        },
    }
