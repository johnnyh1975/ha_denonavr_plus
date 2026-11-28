"""Tests for tests/fakes.py: the fakes keep the real clients' public face.

A fake without a method the code calls makes a test fail for the wrong
reason, or pass without testing anything.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from custom_components.denonavr_plus.avr.transport.heos import HeosClient
from custom_components.denonavr_plus.avr.transport.telnet import TelnetClient

from .fakes import FakeHeos, FakeTelnet


def _parameters(function: Any) -> list[str]:
    return [name for name in inspect.signature(function).parameters if name != "self"]


@pytest.mark.parametrize(("real", "fake"), [(TelnetClient, FakeTelnet), (HeosClient, FakeHeos)])
def test_a_fake_has_every_public_method_and_property_of_its_client(real: type, fake: type) -> None:
    assert _parameters(fake.__init__) == _parameters(real.__init__)
    instance = fake("192.0.2.10", lambda *args: None)
    for name, member in inspect.getmembers(real):
        if name.startswith("_"):
            continue
        if isinstance(member, property):
            assert hasattr(instance, name), f"{fake.__name__}.{name}"
        elif inspect.isfunction(member):
            assert hasattr(fake, name), f"{fake.__name__}.{name}"
            assert _parameters(getattr(fake, name)) == _parameters(member), f"{fake.__name__}.{name}"
