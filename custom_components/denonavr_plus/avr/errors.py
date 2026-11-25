"""Errors raised by the receiver client to its caller."""

from __future__ import annotations


class SetupWriteError(Exception):
    """The receiver did not accept a write through its setup API.

    Raised when a setting has no telnet command and is written over the setup
    interface (HTTPS 10443), and the receiver answers with an error or not at
    all. The caller decides how to report it; the receiver state is unchanged.
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


class SettingNotTaken(Exception):
    """The receiver kept a setting at its old value.

    `control_id` names the setting, `reason` why the receiver takes it only
    in a context (avr/refusals.py), such as "standby" or "dolby_dts".
    """

    def __init__(self, control_id: str, reason: str) -> None:
        super().__init__(f"{control_id}: {reason}")
        self.control_id = control_id
        self.reason = reason
