"""Shared fixtures for the Denon AVR Plus tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowManager

from .flow_translations import problems

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


@pytest.fixture(autouse=True)
def flow_screens_have_their_texts() -> Iterator[None]:
    """Fail a test whose flow reaches a screen with a missing text or placeholder.

    In every language (tests/flow_translations.py).
    """
    original = FlowManager._async_handle_step
    found: list[str] = []

    async def checked(self: FlowManager, flow: Any, step_id: str, user_input: Any) -> Any:
        result = await original(self, flow, step_id, user_input)
        found.extend(
            f"{type(flow).__name__} {result.get('step_id') or result.get('reason')}: {problem}"
            for problem in problems(flow, result)
        )
        return result

    with patch.object(FlowManager, "_async_handle_step", checked):
        yield
    assert not found, "\n".join(dict.fromkeys(found))


def deviceinfo(name: str) -> str:
    """The text of a recorded Deviceinfo document, e.g. 'AVC-X3700H-Deviceinfo-8080.xml'."""
    return (DENONAVR_FIXTURES / name).read_text(encoding="utf-8")


def all_deviceinfo_files() -> list[Path]:
    return sorted(DENONAVR_FIXTURES.glob("*Deviceinfo*.xml"))
