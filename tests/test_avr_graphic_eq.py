"""Tests for avr/graphic_eq.py."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.denonavr_plus.avr.errors import SetupWriteError
from custom_components.denonavr_plus.avr.graphic_eq import (
    GraphicEqController,
    channel_options,
    parse,
)
from custom_components.denonavr_plus.avr.profile import load_profile

from .setup_api import FakeSetupApi


class FakeWebConfig:
    """Stands in for the setup API client; records writes, answers with ``accept``."""

    def __init__(self, accept: bool) -> None:
        self.accept = accept
        self.writes: list[tuple[str, int, str]] = []

    async def async_get(
        self, section: str, type_id: int, extra: dict[str, str] | None = None
    ) -> str | None:
        return None

    async def async_set(self, section: str, type_id: int, xml: str) -> bool:
        self.writes.append((section, type_id, xml))
        return self.accept


def _controller(webconfig: FakeWebConfig) -> GraphicEqController:
    grammar: dict[str, Any] = load_profile().grammar["graphic_eq"]
    return GraphicEqController(webconfig, grammar, lambda: True, lambda: None)


async def test_rejected_apply_raises_and_keeps_the_staged_edits() -> None:
    webconfig = FakeWebConfig(accept=False)
    eq = _controller(webconfig)
    await eq.set_band("63 Hz", 2.5)

    with pytest.raises(SetupWriteError):
        await eq.apply()

    assert len(webconfig.writes) == 1
    assert eq.has_pending
    assert eq.band_value("63 Hz") == 2.5


async def test_accepted_apply_clears_the_staged_edits() -> None:
    webconfig = FakeWebConfig(accept=True)
    eq = _controller(webconfig)
    await eq.set_band("63 Hz", 2.5)

    await eq.apply()

    assert len(webconfig.writes) == 1
    assert not eq.has_pending


@pytest.mark.parametrize("action", ["curve_copy", "set_defaults"])
async def test_rejected_curve_actions_raise(action: str) -> None:
    eq = _controller(FakeWebConfig(accept=False))
    with pytest.raises(SetupWriteError):
        await getattr(eq, action)()


async def test_rejected_speaker_selection_raises() -> None:
    eq = _controller(FakeWebConfig(accept=False))
    code = next(iter(eq.grammar["speaker_selection"]))
    with pytest.raises(SetupWriteError):
        await eq.set_speaker_selection(code)


def test_all_mode_offers_one_shared_curve() -> None:
    grammar: dict[str, Any] = load_profile().grammar["graphic_eq"]
    assert channel_options(grammar, "1", "") == [(0, "All")]


def test_a_mode_without_enabled_channels_still_offers_the_first() -> None:
    grammar: dict[str, Any] = load_profile().grammar["graphic_eq"]
    labels = grammar["channels_each"]
    # No channel flagged as selectable: the select keeps one valid option.
    assert channel_options(grammar, "2", "") == [(0, labels[0])]


async def test_switching_the_channel_drops_the_staged_edits() -> None:
    eq = _controller(FakeWebConfig(accept=True))
    await eq.set_band("63 Hz", 2.5)

    await eq.set_channel(1)

    assert eq.channel == 1
    assert not eq.has_pending


# Reading and the other writes against a fake setup API ------------------------


def _eq(
    api: FakeSetupApi, updates: list[int] | None = None, supported: bool = True
) -> GraphicEqController:
    grammar: dict[str, Any] = load_profile().grammar["graphic_eq"]
    sink = updates if updates is not None else []
    return GraphicEqController(api, grammar, lambda: supported, lambda: sink.append(1))  # type: ignore[arg-type]


def test_parse_reads_selection_flags_channel_and_bands() -> None:
    grammar = load_profile().grammar["graphic_eq"]
    state = parse(
        "<GraphicEQ><SpeakerSelection>2</SpeakerSelection>"
        "<SelectableSpeaker><Each>33</Each></SelectableSpeaker>"
        "<AdjustEQ><Channel>1</Channel><Eq63Hz>-15</Eq63Hz><Eq125Hz>x</Eq125Hz></AdjustEQ>"
        "</GraphicEQ>",
        grammar,
    )

    assert state.speaker_selection == "2"
    assert state.selectable == "33"
    assert state.channel_index == 1
    assert state.bands == {"Eq63Hz": -1.5}


def test_parse_takes_the_pair_flags_and_tolerates_junk() -> None:
    grammar = load_profile().grammar["graphic_eq"]

    pairs = parse(
        "<GraphicEQ><SelectableSpeaker><LR>3</LR></SelectableSpeaker></GraphicEQ>",
        grammar,
    )
    assert pairs.selectable == "3"
    assert pairs.bands == {}

    assert parse("<GraphicEQ", grammar).bands == {}


async def test_refresh_reads_the_selected_channel_and_notifies_on_change() -> None:
    api = FakeSetupApi()
    updates: list[int] = []
    eq = _eq(api, updates)

    await eq.refresh()
    await eq.refresh()

    assert api.reads[0] == ("audio", 10, {"opt1": "0", "opt2": "0"})
    assert updates == [1]
    assert eq.band_value("Eq63Hz") == -1.5
    assert eq.band_value("Eq125Hz") == 2.0


async def test_refresh_without_an_answer_keeps_the_state() -> None:
    api = FakeSetupApi(documents={})
    updates: list[int] = []
    eq = _eq(api, updates)

    await eq.refresh()

    assert updates == [] and eq.band_value("Eq63Hz") is None


async def test_an_unsupported_eq_reads_and_writes_nothing() -> None:
    api = FakeSetupApi()
    eq = _eq(api, supported=False)

    await eq.refresh()
    await eq.curve_copy()

    assert not eq.supported
    assert api.reads == [] and api.writes == []


async def test_apply_without_a_read_writes_nothing() -> None:
    api = FakeSetupApi()
    eq = _eq(api)

    await eq.apply()

    assert api.writes == []


async def test_apply_sends_the_whole_block_with_the_edit() -> None:
    api = FakeSetupApi()
    eq = _eq(api)
    await eq.refresh()
    await eq.set_band("Eq250Hz", -3.0)

    await eq.apply()

    xml = api.writes[0][2]
    assert "<Eq63Hz>-15</Eq63Hz>" in xml and "<Eq250Hz>-30</Eq250Hz>" in xml
    assert "<Eq16kHz>0</Eq16kHz>" in xml


async def test_channel_and_mode_changes_drop_staged_edits() -> None:
    api = FakeSetupApi()
    eq = _eq(api)
    await eq.set_band("Eq63Hz", 1.0)

    await eq.set_channel(3)
    assert eq.channel == 3 and not eq.has_pending
    assert api.reads[-1][2] == {"opt1": "3", "opt2": "0"}

    await eq.set_band("Eq63Hz", 1.0)
    await eq.set_speaker_selection("1")
    assert eq.channel == 0 and not eq.has_pending
    assert (
        api.writes[-1][2]
        == "<GraphicEQ><SpeakerSelection>1</SpeakerSelection></GraphicEQ>"
    )


async def test_curve_copy_and_defaults_send_their_commands() -> None:
    api = FakeSetupApi()
    eq = _eq(api)

    await eq.curve_copy()
    await eq.set_defaults()

    assert [xml for _, _, xml in api.writes] == [
        "<GraphicEQ><CurveCopy>1</CurveCopy></GraphicEQ>",
        "<GraphicEQ><SetDefaults>1</SetDefaults></GraphicEQ>",
    ]
    assert eq.grammar["root_tag"] == "GraphicEQ"


def test_channel_options_per_mode() -> None:
    grammar = load_profile().grammar["graphic_eq"]

    assert channel_options(grammar, "1", "") == [(0, "All")]
    assert channel_options(grammar, "2", "303") == [(0, "Front L"), (2, "Center")]
    assert channel_options(grammar, "3", "0") == [(0, "Front")]
