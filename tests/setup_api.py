"""A stand-in for the receiver's setup config API (avr/transport/webconfig.py).

Serves XML documents in the form the profile's grammar describes, keyed by
section and config type, and records what is written. The documents follow
the shapes the setup UI of an AVC-X3700H uses; the values are made up.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

#: The video availability map: HDMI setup, on-screen display and 4K format
#: present, TV format hidden.
AVAILABILITY = (
    "<listSetupMenu><HDMISetup>3</HDMISetup><OnScreenDisplay>3</OnScreenDisplay>"
    "<FourKSignalFormat>2</FourKSignalFormat><TVFormat>1</TVFormat></listSetupMenu>"
)
HDMI_SETUP = (
    "<HDMISetup>"
    '<HDMIControl display="3">1</HDMIControl>'
    '<ARC display="2">2</ARC>'
    '<PowerOffControl display="3">2</PowerOffControl>'
    '<HDMIPassThrough display="3">1</HDMIPassThrough>'
    '<PassThroughSource display="3"><Source>3</Source>'
    "<List><Last>Last</Last><HDMI1>Blu-ray</HDMI1><HDMI2>Apple TV</HDMI2><Odd>x</Odd></List>"
    "</PassThroughSource>"
    "</HDMISetup>"
)
ON_SCREEN = '<OnScreenDisplay><Volume display="3">2</Volume><Info display="1">1</Info></OnScreenDisplay>'
FOUR_K = '<FourKSignalFormat><Format display="3">1</Format></FourKSignalFormat>'
TV_FORMAT = '<TVFormat display="3">2</TVFormat>'
AMP_ASSIGN = (
    "<AmpAssign><AmpType>5</AmpType><AssignMode>2</AssignMode>"
    '<FloorLayout display="3"><Value>2</Value><List><Value>1</Value><Value>2</Value></List></FloorLayout>'
    '<HeightSpeaker display="3"><Value>1</Value></HeightSpeaker>'
    "</AmpAssign>"
)
GRAPHIC_EQ = (
    "<GraphicEQ><SpeakerSelection>2</SpeakerSelection>"
    "<SelectableSpeaker><Each>3303</Each></SelectableSpeaker>"
    "<AdjustEQ><Channel>{channel}</Channel><Eq63Hz>-15</Eq63Hz><Eq125Hz>20</Eq125Hz>"
    "<Eq250Hz>0</Eq250Hz><Eq500Hz>x</Eq500Hz></AdjustEQ></GraphicEQ>"
)


class FakeSetupApi:
    """Answers get_config from DOCUMENTS; set_config is accepted unless told."""

    def __init__(
        self, documents: dict[tuple[str, int], str] | None = None, accept: bool = True
    ) -> None:
        self.documents = dict(DOCUMENTS if documents is None else documents)
        self.accept = accept
        self.reads: list[tuple[str, int, dict[str, str] | None]] = []
        self.writes: list[tuple[str, int, str]] = []

    async def async_get(
        self, section: str, type_id: int, extra: dict[str, str] | None = None
    ) -> str | None:
        self.reads.append((section, type_id, extra))
        document = self.documents.get((section, type_id))
        if document is not None and "{channel}" in document:
            return document.format(channel=(extra or {}).get("opt1", "0"))
        return document

    async def async_set(self, section: str, type_id: int, xml: str) -> bool:
        self.writes.append((section, type_id, xml))
        return self.accept


DOCUMENTS: dict[tuple[str, int], str] = {
    ("video", 1): AVAILABILITY,
    ("video", 3): HDMI_SETUP,
    ("video", 7): ON_SCREEN,
    ("video", 8): FOUR_K,
    ("video", 9): TV_FORMAT,
    ("speakers", 2): AMP_ASSIGN,
    ("audio", 10): GRAPHIC_EQ,
}


def get(api: FakeSetupApi) -> Any:
    """A replacement for WebConfigClient.async_get that serves from ``api``."""

    async def async_get(
        _client: Any, section: str, type_id: int, extra: dict[str, str] | None = None
    ) -> str | None:
        return await api.async_get(section, type_id, extra)

    return async_get


def set_(api: FakeSetupApi) -> Any:
    """A replacement for WebConfigClient.async_set that records into ``api``."""

    async def async_set(_client: Any, section: str, type_id: int, xml: str) -> bool:
        return await api.async_set(section, type_id, xml)

    return async_set


# A receiver whose telnet answers the speaker queries, with the setup API and
# every entity enabled: for the entities that start disabled or need answers
# at setup (crossovers, speaker sizes, quick select, the /ajax selects).

#: Telnet answers, by query.
TELNET_ANSWERS: dict[str, list[str]] = {
    "SSCFR ?": [
        "SSCFR IDV",
        "SSCFRFRO 080",
        "SSCFRCEN 090",
        "SSCFRALL 080",
        "SSCFR END",
    ],
    "SSSPC ?": ["SSSPCFRO SMA", "SSSPCCEN LAR", "SSSPCSWF 1SP", "SSSPC END"],
    "SSQSNZMA ?": ["SSQSNZMAQS1 TV", "SSQSNZMAQS2 Music", "SSQSNZMA END"],
    "MSQUICK ?": ["MSQUICK1"],
}


@contextmanager
def answering_receiver(api: FakeSetupApi) -> Iterator[None]:
    """Make the fake telnet answer TELNET_ANSWERS and serve ``api`` over HTTPS."""
    from custom_components.denonavr_plus.avr.transport.webconfig import WebConfigClient
    from custom_components.denonavr_plus.entity import DenonAvrEntity

    from .test_init import FakeTelnet

    async def async_send(self: FakeTelnet, command: str) -> None:
        self.sent.append(command)
        self.push(*TELNET_ANSWERS.get(command, []))

    with (
        patch.object(FakeTelnet, "async_send", async_send),
        patch.object(WebConfigClient, "async_get", get(api)),
        patch.object(WebConfigClient, "async_set", set_(api)),
        patch.object(
            DenonAvrEntity,
            "entity_registry_enabled_default",
            property(lambda _self: True),
        ),
    ):
        yield
