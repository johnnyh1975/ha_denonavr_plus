"""Tests for avr/video_config.py."""

from __future__ import annotations

import pytest

from custom_components.denonavr_plus.avr.errors import SetupWriteError
from custom_components.denonavr_plus.avr.profile import load_profile
from custom_components.denonavr_plus.avr.video_config import VideoConfigController

from .test_avr_graphic_eq import FakeWebConfig


def _controller(webconfig: FakeWebConfig) -> VideoConfigController:
    grammar = load_profile().grammar["video_web"]
    return VideoConfigController(webconfig, grammar, lambda: None)


async def test_rejected_write_raises() -> None:
    webconfig = FakeWebConfig(accept=False)
    video = _controller(webconfig)
    control = video.controls()[0]

    with pytest.raises(SetupWriteError):
        await video.async_set(control["id"], control["on"])

    assert len(webconfig.writes) == 1


async def test_accepted_write_does_not_raise() -> None:
    webconfig = FakeWebConfig(accept=True)
    video = _controller(webconfig)
    control = video.controls()[0]

    await video.async_set(control["id"], control["on"])

    assert len(webconfig.writes) == 1
