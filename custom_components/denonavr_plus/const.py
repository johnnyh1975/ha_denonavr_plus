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
]

# How often the HTTP reconciliation poll runs. Real time updates arrive over
# telnet push; this poll only confirms reachability and recovers state when the
# push channel is temporarily down.
RECONCILE_INTERVAL = timedelta(seconds=60)

# How long the telnet link may stay down before the integration says so in the
# log and, if the receiver still answers over HTTP, as a repair. Short drops
# (a reboot, a firmware update, a brief network hiccup) stay quiet.
TELNET_OUTAGE_GRACE = timedelta(minutes=5)
