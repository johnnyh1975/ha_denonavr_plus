"""Tests for avr/generation.py: the receiver generations from Deviceinfo.xml."""

from __future__ import annotations

import pytest

from custom_components.denonavr_plus.avr.generation import GENERATIONS, generation_of
from custom_components.denonavr_plus.avr.models import DeviceInfo
from custom_components.denonavr_plus.avr.parser import parse_device_info

from .conftest import deviceinfo


@pytest.mark.parametrize(
    ("recording", "key"),
    [
        ("AVR-X2000-Deviceinfo.xml", "2013"),
        ("NR1604-Deviceinfo.xml", "2013"),
        ("AVR-X1100W-Deviceinfo.xml", "2014"),
        ("AVR-X4100W-Deviceinfo.xml", "2014_2016"),
        ("SR6011-Deviceinfo.xml", "2014_2016"),
        ("AVC-X3700H-Deviceinfo-8080.xml", "2016"),
        ("SR6012-Deviceinfo-8080.xml", "2016"),
    ],
)
def test_recorded_av_receivers_have_their_generation(recording: str, key: str) -> None:
    generation = generation_of(parse_device_info(deviceinfo(recording)).device)
    assert generation is not None and generation.key == key


@pytest.mark.parametrize(
    "recording",
    [
        # Systems (ProductCategory 2), at level 0220 and 0300.
        "M-CR510-Deviceinfo.xml",
        "DRA-N4-Deviceinfo.xml",
        # 2010-2012: an error page instead of Deviceinfo.xml.
        "AVR-1912-Deviceinfo.xml",
    ],
)
def test_other_devices_have_none(recording: str) -> None:
    assert generation_of(parse_device_info(deviceinfo(recording)).device) is None


def test_an_interface_level_not_seen_yet_is_not_described() -> None:
    assert generation_of(DeviceInfo(comm_api_version=302, product_category=1)) is None
    assert generation_of(DeviceInfo(comm_api_version=301, product_category=None)) is None


def test_the_goform_port_follows_the_level() -> None:
    """Port 8080 from level 0301 on, as avr/transport/goform.py detects it."""
    assert {g.key: g.port for g in GENERATIONS} == {
        "2013": 80, "2014": 80, "2014_2016": 80, "2016": 8080,
    }
