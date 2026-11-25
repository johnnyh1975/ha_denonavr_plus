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

## Interface levels (`CommApiVers`)

Within the two generations, `Deviceinfo.xml` reports an interface level in
`CommApiVers`. Denon's AVR Remote app (3.13.4, read from its code) decides by
it which functions it offers ([manufacturer apps](12-manufacturer-apps.md));
the recorded `Deviceinfo.xml` files in `tests/fixtures/denonavr/` show the
same steps. What each level offers over HTTP is on the
[HTTP control](11-http-control.md#interfaces-by-generation) page.

| `CommApiVers` | Receivers recorded | Setup functions in `Deviceinfo.xml` | Web interface |
|---|---|---|---|
| none (2010–2012) | AVR-1912, AVR-2312CI, AVR-3311CI, AVR-3312, AVR-4810 | no `Deviceinfo.xml` at all: these units answer with an error page ([before 2013](models/before-2013.md)) | port 80 |
| 0210 (2013) | AVR-X2000, AVR-X4000, AVR-1713, AVR-3313, NR1604, SR5008 | a few (restorer, sleep, tone) | port 80 |
| 0220 | Marantz M-CR510, M-CR610 (systems) | few | port 80 |
| 0250 (2014 entry models) | AVR-X1100W | as 0210 | port 80 |
| 0300 (2014 to 2016, receivers without HEOS) | AVR-X2100W, AVR-X4100W, SR6011; the stereo receiver DRA-N4 | full setup through `AppCommand0300.xml` on the AV receivers; 19 functions on the DRA-N4 | port 80 |
| 0301 (2016 on) | AVR-X4500H, AVC-X3700H, AVC-X8500H, AVC-A10H, NR1609, SR6012, AV7703 | full setup | port 8080 |

The app no longer supports receivers without a level, and this
integration cannot set them up, because it reads port and functions from
`Deviceinfo.xml`. Below 0300 a receiver
names few of its functions in `Deviceinfo.xml`, which is why the AVR-X2000
lists far fewer than its telnet protocol has.

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

Denon AVR Plus reads the device description on the port that fits the
receiver (8080, or 80 for receivers before 2016) and keeps the port in the
entry. It gates entities on the function names in the description, and on
receivers that name few functions (the AVR-X2000) it also creates a control
when the receiver answers its telnet query at setup. Value encodings
follow the generation: receivers on port 80 (until 2015) read dialog level
as `PSDIL`, and those below 0250 also take picture levels in two digits
and tuner presets as `A1`–`G8`. The
[AVR-X2000 page](models/AVR-X2000.md) lists what works there.

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
