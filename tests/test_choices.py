"""Tests for choices.py: selections and names of sources and sound modes."""

from __future__ import annotations

from custom_components.denonavr_plus import choices
from custom_components.denonavr_plus.avr.models import Discovery, SourceDescriptor


def _discovery() -> Discovery:
    discovery = Discovery()
    discovery.sources = [
        SourceDescriptor("SAT/CBL", "Sky"),
        SourceDescriptor("BD", "Blu-ray"),
        SourceDescriptor("PHONO", "PHONO", visible=False),
        SourceDescriptor("NET", "HEOS Music"),
    ]
    return discovery


def test_without_options_every_visible_source_shows_under_the_receiver_name() -> None:
    assert choices.source_list(_discovery(), {}, "main") == ["Sky", "Blu-ray", "HEOS Music"]


def test_a_zone_shows_its_selection_in_the_receiver_order() -> None:
    options = {choices.OPT_SOURCES: {"zone2": ["NET", "SAT/CBL"], "main": []}}
    assert choices.source_list(_discovery(), options, "zone2") == ["Sky", "HEOS Music"]
    # An empty selection means all.
    assert len(choices.source_list(_discovery(), options, "main")) == 3


def test_a_renamed_source_maps_back_to_its_code() -> None:
    options = {choices.OPT_SOURCE_NAMES: {"BD": "Kino"}}
    discovery = _discovery()
    assert choices.source_display(discovery, options, "BD") == "Kino"
    assert choices.source_code_for(discovery, options, "Kino") == "BD"
    # The receiver's name still works, for older automations.
    assert choices.source_code_for(discovery, {}, "Blu-ray") == "BD"


def test_sound_modes_are_filtered_renamed_and_keep_the_current_one() -> None:
    options = {
        choices.OPT_SOUND_MODES: ["Stereo", "Dolby Surround"],
        choices.OPT_SOUND_MODE_NAMES: {"Dolby Surround": "Film"},
    }
    modes = ["Stereo", "Dolby Surround", "Multi Ch Stereo"]
    assert choices.sound_mode_list(options, modes, "Multi Ch Stereo") == [
        "Stereo", "Film", "Multi Ch Stereo",
    ]
    assert choices.sound_mode_for(options, "Film") == "Dolby Surround"
    assert choices.sound_mode_for(options, "Stereo") == "Stereo"


def test_the_main_zone_is_always_enabled() -> None:
    assert choices.zone_enabled({choices.OPT_ZONES: []}, "main")
    assert not choices.zone_enabled({choices.OPT_ZONES: []}, "zone2")
    assert choices.zone_enabled({}, "zone2")
