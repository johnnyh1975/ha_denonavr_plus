# Overview

## Two generations

Denon receivers fall into two groups for network control. The dividing line
is the 2016 model year, when HEOS was built into the receivers and the web
interface moved.

| | AVR-X2000 generation (until 2015) | HEOS generation (2016 and later) |
|---|---|---|
| Example | AVR-X2000 (2013) | AVR-X2600H (2019) |
| Telnet control, port 23 | yes | yes |
| Web interface (goform, Deviceinfo) | port 80 | port 8080 |
| Device description for UPnP | port 8080, `/description.xml` | port 60006, `/upnp/desc/aios_device/aios_device.xml` |
| HEOS CLI, port 1255 | no | yes |
| Setup config API (`/ajax`) | no | HTTPS 10443 (newer firmware: HTTP 11080) |
| Calibration protocol, port 1256 | no | yes (used on AVR-X3600H) |
| RS-232C | depends on region (AVR-X2000: North America only) | depends on model |

Sources for each row are on the [network page](02-network-and-protocols.md).

## What stays the same

The telnet grammar is stable across both generations: a two-letter command,
a parameter, a carriage return. Power, volume, mute, source, sound mode,
channel levels, Audyssey switches, tone, zones and the network-audio
transport keys use the same tokens on the AVR-X2000 and on current models.
Newer models add tokens (Atmos and DTS:X modes, height channels, Neural:X,
Auro-3D, IMAX, Dirac, Bluetooth transmitter) and drop a few (Pro Logic II
variants, Audyssey DSX on most later models).

What does change are some value encodings; they are listed in the
[command reference](03-command-reference.md#differences-between-protocol-versions).

## What this means for the integration

Denon AVR Plus targets the HEOS generation. It reads the device description
on port 8080 and gates every entity on the function names in it. Both
choices exclude the AVR-X2000 today:

- the description is on port 80, so setup fails, and
- the AVR-X2000's description names far fewer functions than its telnet
  protocol supports.

Supporting it is roadmap item 16 (older receivers) in the maintainer's plan.
The [AVR-X2000 page](models/AVR-X2000.md) lists what that work needs.

## Architecture rules

These follow from the protocols and hold for both receivers.

- **One Home Assistant device per physical receiver**, one `media_player`
  for the main zone and one for Zone 2 where the receiver has it.
- **Zones are controlled over telnet** (`Z2…`, `Z3…`). HEOS knows nothing
  about zones.
- **All Zone Stereo is a receiver mode**, not a HEOS group: the main zone's
  signal is sent to the other zones (`MSALL ZONE STEREO`, `MNZST`).
- **HEOS is a separate network multiroom system.** On a receiver it is one
  HEOS player. Grouping it with other HEOS devices is a HEOS function, not a
  zone function ([HEOS](05-heos.md)).
- **No two clients on one telnet port** until the concurrency test has
  settled the question for the model ([test](09-status-diagnostics-and-discovery.md#test-concurrent-telnet-sessions)).
