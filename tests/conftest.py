"""Shared fixtures for the Denon AVR Plus tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowManager

from .fakes import HOST, deviceinfo, receiver_network_patches  # noqa: F401 -- deviceinfo for the tests
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


@pytest.fixture
def receiver_network() -> Iterator[None]:
    """Patch the receiver's network edges (tests/fakes.py)."""
    with receiver_network_patches():
        yield


@pytest.fixture
async def loaded_entry(hass: HomeAssistant, receiver_network: None) -> AsyncIterator[Any]:
    """A receiver set up from the recorded AVC-X3700H, at the current entry version.

    tests/test_migrations.py covers older versions.
    """
    from homeassistant.const import CONF_HOST
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: HOST}, minor_version=CONFIG_MINOR_VERSION
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry


def all_deviceinfo_files() -> list[Path]:
    return sorted(DENONAVR_FIXTURES.glob("*Deviceinfo*.xml"))


@pytest.fixture
async def web_control(hass: HomeAssistant, receiver_network: Any) -> Any:
    """A receiver whose setup interface answers as the recorded AVC-X3900H.

    Its telnet is the usual fake (with the speaker answers of setup_api), its
    every entity enabled; ``.entry`` is the loaded entry.
    """
    from homeassistant.const import CONF_HOST
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN

    from .setup_api import FakeSetupApi, answering_receiver, recorded

    api = FakeSetupApi(recorded("AVC-X3900H"))
    with answering_receiver(api):
        entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: HOST}, minor_version=CONFIG_MINOR_VERSION)
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        api.entry = entry  # type: ignore[attr-defined]
        yield api


@pytest.fixture
async def flow_without_setup(
    request: pytest.FixtureRequest, hass: HomeAssistant
) -> AsyncIterator[None]:
    """A flow tested on its own: the entry is never set up for real.

    Except for the options tests that need a loaded receiver (its sources and
    sound modes come from it). A flow that creates or reconfigures an entry
    schedules its setup or reload; it is awaited here, while setup is still
    patched, or it would run during teardown against no receiver at all.
    """
    if "loaded_entry" in request.fixturenames:
        yield
        return
    with (
        patch("custom_components.denonavr_plus.async_setup_entry", return_value=True),
        # The receiver answers on its control connection, unless a test says not.
        patch("custom_components.denonavr_plus.config_flow.async_answers", AsyncMock(return_value=True)),
    ):
        yield
        await hass.async_block_till_done()
