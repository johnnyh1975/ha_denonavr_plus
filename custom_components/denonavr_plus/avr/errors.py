"""Errors raised by the receiver client to its caller."""

from __future__ import annotations


class SetupWriteError(Exception):
    """The receiver did not accept a write through its setup API.

    Raised when a setting has no telnet command and is written over the setup
    interface (HTTPS 10443), and the receiver answers with an error or not at
    all. The caller decides how to report it; the receiver state is unchanged.
    """
