"""Tests for avr/graphic_eq.py."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.denonavr_plus.avr.errors import SetupWriteError
from custom_components.denonavr_plus.avr.graphic_eq import (
    GraphicEqController,
    channel_options,
)
from custom_components.denonavr_plus.avr.profile import load_profile


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
