"""Errors raised by the receiver client to its caller."""

from __future__ import annotations


class SetupWriteError(Exception):
    """The receiver did not accept a write through its setup API.

    Raised when a setting has no telnet command and is written over the setup
    interface (HTTPS 10443), and the receiver answers with an error or not at
    all. The caller decides how to report it; the receiver state is unchanged.
    """


class UnsupportedReceiver(ConnectionError):
    """The receiver answers, but is of a generation this integration cannot set up.

    Receivers from 2010 to 2012 answer /goform/Deviceinfo.xml on port 80 with
    an error page ("Form Deviceinfo.xml is not defined"): without that file
    the integration knows neither their port nor their functions
    (docs/protocol/models/before-2013.md). A ConnectionError, so callers
    that only know that one keep retrying as before.
    """


class CommandNotSent(Exception):
    """A raw command (the send_command action) could not be sent.

    `reason` says why: "query_needs_telnet" when the telnet link is down and
    the command is a query, whose answer only telnet can bring back;
    "unreachable" when neither telnet nor HTTP reached the receiver.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SourceNotTaken(Exception):
    """A zone other than the main zone did not switch to the chosen source.

    Zone 2 on many receivers plays no HDMI input: an AVR-X2600H gave no reply
    to Z2AUX2 and kept its source. `zone_id` and `source` name the choice.
    """

    def __init__(self, zone_id: str, source: str) -> None:
        super().__init__(f"{zone_id}: {source}")
        self.zone_id = zone_id
        self.source = source
