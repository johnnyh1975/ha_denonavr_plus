"""Constants for the Denon AVR Plus integration.

Only Home Assistant level constants live here. All protocol grammar is in
avr/protocol_profile.json and all device specific configuration is discovered
from the receiver at runtime.
"""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import (
    CONF_HOST as CONF_HOST,
    CONF_PORT as CONF_PORT,
    Platform,
)

DOMAIN = "denonavr_plus"

# The config entry schema version (not the release version). Raise it, and
# add a step in migrations.py, whenever entry data, options, unique ids or
# device identifiers change.
CONFIG_VERSION = 1
CONFIG_MINOR_VERSION = 4

# Options markers for the channel trims (migrations.py and __init__.py): the
# one-time marker that the trims of speakers the receiver lacks were disabled,
# and the speakers' channels that was last done for.
OPT_CHANNELS_INITIALIZED = "channels_initialized"
OPT_CHANNELS_APPLIED = "channels_configured"
# Unique id fragment identifying a channel trim, followed by the channel code.
CHANNEL_TRIM_MARKER = "_channel_trim_"
CHANNEL_DISTANCE_MARKER = "_channel_distance_"

# CONF_HOST (the receiver address) and CONF_PORT (the goform port, 8080 or 80)
# are imported above and re-exported here so the rest of the integration keeps a single import
# site; everything else about the device is discovered at runtime.


# Platforms this integration provides.
PLATFORMS: list[Platform] = [
    Platform.MEDIA_PLAYER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.BUTTON,
    Platform.REMOTE,
    Platform.UPDATE,
]

# How often the HTTP reconciliation poll runs. Real time updates arrive over
# telnet push; this poll only confirms reachability and recovers state when the
# push channel is temporarily down.
RECONCILE_INTERVAL = timedelta(seconds=60)

# Connection options (Configure -> Connection). The poll interval replaces
# RECONCILE_INTERVAL; HEOS can be left off; the setup settings (graphic EQ,
# video and speaker setup, read over the setup interface) are read when the
# link comes up, after own writes, and every so many minutes (0: never).
OPT_POLL_INTERVAL = "poll_interval"
OPT_HEOS = "heos"
OPT_SETUP_REFRESH = "setup_refresh"
DEFAULT_SETUP_REFRESH = 15  # minutes

# While the telnet link is down the media players run on this HTTP poll alone,
# so it runs more often to catch changes made at the receiver.
HTTP_FALLBACK_INTERVAL = timedelta(seconds=10)

# How long the telnet link may stay down before the integration says so in the
# log and, if the receiver still answers over HTTP, as a repair. Short drops
# (a reboot, a firmware update, a brief network hiccup) stay quiet.
TELNET_OUTAGE_GRACE = timedelta(minutes=5)

# The telnet link going down this often within this window, while the receiver
# answers over HTTP, means something keeps breaking the session: another
# program taking the receiver's one telnet session over, or an unstable
# network. That is a repair, cleared once a whole window passes without a drop.
TELNET_UNSTABLE_DROPS = 5
TELNET_UNSTABLE_WINDOW = timedelta(minutes=10)
