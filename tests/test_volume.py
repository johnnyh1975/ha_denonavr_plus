"""Tests for volume.py: the 0..1 scales and the per-zone limit."""

from __future__ import annotations

from typing import Any

import aiohttp
import pytest

from custom_components.denonavr_plus import volume
from custom_components.denonavr_plus.avr import DenonAvrDevice


@pytest.fixture
async def device() -> Any:
    async with aiohttp.ClientSession() as session:
        yield DenonAvrDevice(session, "192.0.2.10")


def test_default_scale_spans_the_reachable_range(device: DenonAvrDevice) -> None:
    top = device.volume_effective_max("main")
    assert volume.level_from_raw(device, "main", top, {}) == 1.0
    assert volume.level_from_raw(device, "main", 0.0, {}) == 0.0
    assert volume.raw_from_level(device, "main", 0.5, {}) == pytest.approx(top / 2, abs=0.5)


def test_official_scale_is_db_plus_80_over_100(device: DenonAvrDevice) -> None:
    options = {volume.OPT_OFFICIAL_SCALE: True}
    reference = device.volume_reference
    # -40 dB is 0.4 in the official integration.
    assert volume.level_from_raw(device, "main", reference - 40, options) == pytest.approx(0.4)
    assert volume.raw_from_level(device, "main", 0.4, options) == reference - 40


def test_the_zone_limit_caps_every_set(device: DenonAvrDevice) -> None:
    options = {"main": {volume.OPT_MAX_VOLUME: -20.0}}
    limit = device.volume_reference - 20
    assert volume.ceiling_raw(device, "main", options) == limit
    assert volume.raw_from_level(device, "main", 1.0, options) == limit
    assert volume.clamp_raw(device, "main", limit + 5, options) == limit
    # Other zones are not limited by the main zone's setting.
    assert volume.ceiling_raw(device, "zone2", options) == device.volume_effective_max("zone2")


def test_values_land_on_half_steps(device: DenonAvrDevice) -> None:
    assert volume.clamp_raw(device, "main", 40.3, {}) == 40.5


def test_zone_options_read_missing_values_as_none() -> None:
    assert volume.zone_options({}, "main") == volume.ZoneVolumeOptions()
    assert volume.zone_options({"main": {volume.OPT_TURN_ON_VOLUME: -35}}, "main") == (
        volume.ZoneVolumeOptions(max_db=None, turn_on_db=-35.0)
    )
