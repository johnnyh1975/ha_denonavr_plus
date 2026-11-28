"""Tests for avr/speaker_layout.py."""

from __future__ import annotations

import re
from typing import Any

import pytest

from custom_components.denonavr_plus.avr.errors import SetupWriteError
from custom_components.denonavr_plus.avr.profile import load_profile
from custom_components.denonavr_plus.avr.speaker_layout import (
    LAYOUT_CHANGED,
    LayoutNotSeen,
    PRESET_CHANGED,
    RestoreRefused,
    SpeakerLayout,
    changes,
)
from custom_components.denonavr_plus.avr.video_config import VideoConfigController

from .setup_api import recorded

pytestmark = pytest.mark.assumed(
    'the layout write <Config index="1">mode</Config> and the reset of the positions '
    "follow LaserGuruGuy's tests on an AVC-X3900H; this integration has not written one"
)


class LayoutReceiver:
    """The recorded AVC-X3900H's layout page; each layout brings its own defaults.

    The recording has layout 6 with positions 3 = 4 and 5 = 5 (the user's
    choices). A layout written resets positions 3 and 5 to that layout's
    defaults, as the receiver rebuilds its speaker setup.
    """

    DEFAULTS = {"6": {"3": "5", "5": "7"}, "17": {"3": "5", "5": "6"}}

    def __init__(self, *, takes: bool = True) -> None:
        self.documents = recorded("AVC-X3900H")
        self.takes = takes
        self.answers = True
        self.writes: list[tuple[str, int, str]] = []

    async def async_get(
        self, section: str, type_id: int, extra: Any = None
    ) -> str | None:
        if not self.answers and (section, type_id) == ("speakers", 15):
            return None
        return self.documents.get((section, type_id))

    async def async_set(self, section: str, type_id: int, xml: str) -> bool:
        self.writes.append((section, type_id, xml))
        match = re.fullmatch(r'<Config index="(\d+)">(\d+)</Config>', xml)
        if self.takes and match:
            self.set(match[1], match[2])
            if match[1] == "1":
                for slot, value in self.DEFAULTS[match[2]].items():
                    self.set(slot, value)
        return True

    def set(self, slot: str, value: str) -> None:
        page = self.documents[("speakers", 15)]
        if slot == "1":
            page = re.sub(
                r'(<Item index="1" display="3"><Value>)\d+', rf"\g<1>{value}", page
            )
        else:
            page = re.sub(
                rf'(<Item index="{slot}" display="\d"><Config display="\d"><Value>)\d+',
                rf"\g<1>{value}",
                page,
            )
        self.documents[("speakers", 15)] = page


class Telnet:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {"front_speaker": "A", "speaker_preset": "1"}
        self.sent: list[tuple[str, Any]] = []

    async def set(self, control_id: str, value: Any) -> None:
        self.sent.append((control_id, value))
        self.values[control_id] = value


async def _layout(receiver: LayoutReceiver, telnet: Telnet) -> SpeakerLayout:
    grammar = {**load_profile().grammar["speakers_web"], "layout_settle_seconds": 0}
    speakers = VideoConfigController(receiver, grammar, lambda: None)  # type: ignore[arg-type]
    await speakers.discover()
    return SpeakerLayout(speakers, telnet.values.get, telnet.set, grammar)


async def test_a_layout_is_prepared_then_applied_and_what_it_reset_is_returned() -> (
    None
):
    receiver, telnet = LayoutReceiver(), Telnet()
    layout = await _layout(receiver, telnet)
    assert layout.supported and layout.current == "6"
    assert layout.key("17") == "preamplifier" and layout.key("99") == "mode_99"
    assert layout.mode_of("preamplifier") == "17"
    assert not layout.stage("99")
    assert layout.stage("17") and layout.pending == "17"
    # Choosing the current one drops it; nothing prepared, nothing written.
    assert layout.stage("6") and layout.pending is None
    assert await layout.async_apply() is None
    layout.stage("17")
    point = await layout.async_apply()
    assert receiver.writes == [("speakers", 15, '<Config index="1">17</Config>')]
    assert layout.current == "17" and layout.pending is None
    assert point is not None and not point["unsettled"]
    assert (
        point["before"]["positions"]["3"] == "4"
        and point["after"]["positions"]["3"] == "5"
    )
    # Position 2 is grayed on this receiver: not counted, never written.
    assert "2" not in point["before"]["positions"]
    assert changes(point) == ["3", "5"]


async def test_an_earlier_state_before_stays_the_one_to_go_back_to() -> None:
    receiver, telnet = LayoutReceiver(), Telnet()
    layout = await _layout(receiver, telnet)
    layout.stage("17")
    first = await layout.async_apply()
    assert first is not None
    layout.stage("6")
    second = await layout.async_apply(first["before"])
    assert second is not None and second["before"] == first["before"]


async def test_without_a_read_first_nothing_is_written() -> None:
    receiver, telnet = LayoutReceiver(), Telnet()
    layout = await _layout(receiver, telnet)
    receiver.answers = False
    layout.stage("17")
    with pytest.raises(SetupWriteError):
        await layout.async_apply()
    assert receiver.writes == [] and layout.pending == "17"


async def test_a_layout_not_seen_in_time_keeps_its_way_back() -> None:
    receiver, telnet = LayoutReceiver(takes=False), Telnet()
    layout = await _layout(receiver, telnet)
    layout.stage("17")
    with pytest.raises(LayoutNotSeen) as err:
        await layout.async_apply()
    assert err.value.point["unsettled"] is True
    assert err.value.point["before"]["mode"] == "6"


async def test_restore_puts_back_the_layout_then_each_position_and_front_speaker() -> (
    None
):
    receiver, telnet = LayoutReceiver(), Telnet()
    layout = await _layout(receiver, telnet)
    layout.stage("17")
    point = await layout.async_apply()
    assert point is not None
    point["after"]["front_speaker"] = telnet.values["front_speaker"] = "B"
    receiver.writes.clear()
    left = await layout.async_restore(point)
    # The old layout brings its own defaults (3 = 5, 5 = 7): both are written back.
    assert receiver.writes == [
        ("speakers", 15, '<Config index="1">6</Config>'),
        ("speakers", 15, '<Config index="3">4</Config>'),
        ("speakers", 15, '<Config index="5">5</Config>'),
    ]
    assert telnet.sent == [("front_speaker", "A")]
    assert left == []


async def test_what_the_user_changed_since_is_left_alone() -> None:
    receiver, telnet = LayoutReceiver(), Telnet()
    layout = await _layout(receiver, telnet)
    layout.stage("17")
    point = await layout.async_apply()
    assert point is not None
    # By hand after the change: position 5 and front speaker B.
    receiver.set("5", "7")
    telnet.values["front_speaker"] = "B"
    receiver.writes.clear()
    left = await layout.async_restore(point)
    assert ("speakers", 15, '<Config index="3">4</Config>') in receiver.writes
    assert ("speakers", 15, '<Config index="5">5</Config>') not in receiver.writes
    assert telnet.sent == []
    assert left == []


async def test_a_restore_that_stopped_halfway_can_be_tried_again() -> None:
    receiver, telnet = LayoutReceiver(), Telnet()
    layout = await _layout(receiver, telnet)
    layout.stage("17")
    point = await layout.async_apply()
    assert point is not None
    # The old layout came back, its positions not yet.
    receiver.set("1", "6")
    receiver.writes.clear()
    assert await layout.async_restore(point) == []
    assert receiver.writes[0] == ("speakers", 15, '<Config index="3">4</Config>')


async def test_restore_is_refused_after_another_preset_or_layout() -> None:
    receiver, telnet = LayoutReceiver(), Telnet()
    layout = await _layout(receiver, telnet)
    layout.stage("17")
    point = await layout.async_apply()
    assert point is not None
    telnet.values["speaker_preset"] = "2"
    with pytest.raises(RestoreRefused) as err:
        await layout.async_restore(point)
    assert err.value.reason == PRESET_CHANGED
    telnet.values["speaker_preset"] = "1"
    receiver.set("1", "2")
    with pytest.raises(RestoreRefused) as err:
        await layout.async_restore(point)
    assert err.value.reason == LAYOUT_CHANGED
    receiver.answers = False
    with pytest.raises(SetupWriteError):
        await layout.async_restore(point)


async def test_after_a_change_not_seen_what_is_there_now_counts() -> None:
    receiver, telnet = LayoutReceiver(), Telnet()
    layout = await _layout(receiver, telnet)
    layout.stage("17")
    point = await layout.async_apply()
    assert point is not None
    point["unsettled"] = True
    point["after"] = {"mode": None, "positions": {}, "front_speaker": None}
    assert await layout.async_restore(point) == []
    assert layout.current == "6"


def test_front_speaker_counts_as_a_change() -> None:
    point = {
        "before": {"mode": "6", "positions": {"2": "4"}, "front_speaker": "A"},
        "after": {"mode": "17", "positions": {"2": "4"}, "front_speaker": "B"},
    }
    assert changes(point) == ["front_speaker"]
