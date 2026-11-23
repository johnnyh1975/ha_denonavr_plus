"""Shared transport timing constants for the Denon AVR client library.

Only timing and backoff facts live here, shared across the transports. Each
transport owns its own endpoint (TCP port and paths); see the modules in the
`transport` subpackage. All protocol grammar (command tokens, enum values,
introspection queries) lives in the external data file protocol_profile.json,
and all device specific configuration is discovered at runtime from the receiver.
"""

from __future__ import annotations

from typing import Final

# Telnet pacing (docs/protocol/02-network-and-protocols.md, "Timing"). After
# each command the client waits for the receiver's reply, so the gap follows
# the receiver's own response time. The 2015 protocol document asks for at
# least 50 ms between commands; owners of a Marantz Cinema 70s report drops
# below 80 ms, so the floor is 80 ms.
COMMAND_SPACING: Final = 0.08
# Both protocol documents promise a reply to a query within 200 ms. The wait
# for a reply adapts to the measured response time within these bounds; a
# command that changes nothing gets no reply at all, so the wait must stay short.
REPLY_TIMEOUT_MIN: Final = 0.2
REPLY_TIMEOUT_MAX: Final = 1.0
# A raw command (the send_command action) that asks for its replies waits this
# long for the first one; it may queue behind a resync. The replies of one
# command come in a burst, so collecting ends once none arrived for QUIET.
REPLY_COLLECT_FIRST: Final = 2.0
REPLY_COLLECT_QUIET: Final = 0.3
# A command whose replies are collected waits at most this long to leave the
# client's queue before its reply window starts.
QUEUE_WAIT_MAX: Final = 10.0
# The capability check waits this long for the first reply to each query, as
# the probe script does; many queries get none on a given model.
CHECK_REPLY_FIRST: Final = 0.6
# The protocol documents allow at most 135 bytes per message, the closing
# carriage return included.
MAX_MESSAGE_LENGTH: Final = 135
# Both protocol documents ask to wait 1 s after PWON before the next command.
POWER_ON_SETTLE: Final = 1.0
CONNECT_TIMEOUT: Final = 5.0
HTTP_TIMEOUT: Final = 8.0

# If nothing arrives for this long we send a light probe to keep the socket
# alive and to detect a silently dropped link.
KEEPALIVE_INTERVAL: Final = 30.0

# Reconnect backoff grows from MIN to MAX after each failed attempt.
RECONNECT_BACKOFF_MIN: Final = 1.0
RECONNECT_BACKOFF_MAX: Final = 60.0
