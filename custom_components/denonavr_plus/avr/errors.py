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
