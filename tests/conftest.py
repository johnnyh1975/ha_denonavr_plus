"""Shared fixtures for the Denon AVR Plus tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant

FIXTURES = Path(__file__).parent / "fixtures"
DENONAVR_FIXTURES = FIXTURES / "denonavr"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None, hass: HomeAssistant
) -> Iterator[None]:
    """Let Home Assistant load custom_components/denonavr_plus in every test.

    SSDP, a dependency, counts as loaded: the real one opens sockets. Tests
    that read its cache patch ssdp.async_get_discovery_info_by_st.
    """
    hass.config.components.add("ssdp")
    yield


def deviceinfo(name: str) -> str:
    """The text of a recorded Deviceinfo document, e.g. 'AVC-X3700H-Deviceinfo-8080.xml'."""
    return (DENONAVR_FIXTURES / name).read_text(encoding="utf-8")


def all_deviceinfo_files() -> list[Path]:
    return sorted(DENONAVR_FIXTURES.glob("*Deviceinfo*.xml"))
