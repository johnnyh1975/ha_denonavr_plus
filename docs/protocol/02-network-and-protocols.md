# Network and protocols

All interfaces are local; nothing needs the internet. Home Assistant is the
client in every case.

## Interfaces per receiver

| Port | Interface | AVR-X2000 | AVR-X2600H | Used by this integration |
|---|---|---|---|---|
| 23/tcp | Telnet control and events | Documented | Documented; Tested on AVR-X3600H | yes, primary |
| 80/tcp | goform web interface, `AppCommand.xml` | Documented (recorded responses) | To test (newer firmware may redirect to HTTPS) | no |
| 8080/tcp | goform web interface, `AppCommand.xml` | UPnP `/description.xml` only | Documented; Tested on AVR-X3600H | yes, discovery and reconciliation poll |
| 60006/tcp | UPnP/AIOS device description | Absent | Documented; Tested on AVR-X3600H | yes, firmware and serial at setup |
| 1900/udp | SSDP discovery | Documented | Documented | yes, discovery |
| 1255/tcp | HEOS CLI | Absent | Sourced (HEOS Built-in) | yes, now playing |
| 10443/tcp | Setup config API, HTTPS | Absent | To test; Tested on AVR-X3600H | yes, graphic EQ, video setup, amp assign |
| 11080/tcp | Setup config API, plain HTTP | Absent | Unverified (seen on 2022-era firmware) | no |
| 1256/tcp | Calibration protocol, length-framed JSON | Absent | To test | no (transport built, not wired) |
| RS-232C | Serial control, same grammar as telnet | North America model only | To test (not in the product sheet's terminal list) | no |

"Absent" for the AVR-X2000 rests on the model generation: these interfaces
arrived with HEOS-era firmware, the AVR-X2000 protocol document names only
telnet and RS-232C, and the library that handles both generations uses port
8080 on these receivers only for the description file. Probing the ports on
the receiver confirms it in a minute
([test](09-status-diagnostics-and-discovery.md#test-reachable-ports)).

For a firewall or VLAN: allow the Home Assistant host to reach the receiver
on 23, 8080 and 60006 (the official integration lists the same three), plus
1255 and 10443 for HEOS and the setup settings. For the AVR-X2000, port 80
replaces 8080 and 60006. SSDP needs multicast between the two networks, or
the receiver is added by IP address.

## Telnet (port 23)

### Framing

| Property | Value | Source |
|---|---|---|
| Characters | ASCII 0x20–0x7F | Protocol documents, all versions |
| Terminator | CR (0x0D), no LF, both directions | Protocol documents |
| Structure | 2-letter command + parameter (up to 25 characters) + CR | Protocol documents |
| Maximum message length | 135 bytes | Protocol documents |
| Query | command + `?` (some families with a space: `PSBAS ?`, `MSQUICK ?`) | Protocol documents |

Three message kinds:

- **Command**: controller to receiver.
- **Event**: receiver to controller, sent unprompted when the state changes,
  whatever caused it (remote, front panel, app, another client). Same form
  as the command.
- **Response**: the reply to a query, in the same form as the event.

A set command is answered by the event for the new state. A set to the
current value is usually answered with nothing, so silence after a command
is not an error. Silence after a query also does not prove the command is
unsupported; some receivers answer `?` for an unknown command, others say
nothing.

### Timing

| Rule | AVR-X2000 document (2013) | X-W document (2015) | This integration |
|---|---|---|---|
| Gap between commands | not stated | at least 50 ms | waits for the reply, at least 80 ms |
| Response to a query | within 200 ms | within 200 ms | waits 0.2–1 s, four times the measured response time |
| Event after a state change | not stated | within 5 s | push, plus poll on 8080 |
| After `PWON` | wait 1 s | wait 1 s | waits 1 s |
| Commands while events are sent | accepted | accepted | — |

Community measurements on a Marantz Cinema 70s suggest 80–100 ms between
commands and 3–5 s for a full boot after `PWON`. The receiver drops
commands sent faster; the drop is silent.

The integration sends a command that changes something before queries still
waiting in the queue, so a user's action does not wait for a resync. When the
link drops, whatever is still queued is discarded. The measured response time
is in the diagnostics (`telnet_response_ms`).

Keeping the session alive: the `denonavr` library sends `PW?` after 30 s
without any line from the receiver and reconnects after 60 s; this
integration probes after 30 s idle as well (`KEEPALIVE_INTERVAL`).

Side effects to expect after one command:

- A source change can change the sound mode; the receiver then sends the
  new `MS…` event and, on the older generation, the levels of every channel.
- On a sound-mode change the receiver first repeats the current mode, then
  sends the new one.
- `CV?` on the AVR-X2000 generation returns every channel of the current
  layout, unused ones with value `50`. The 2015 document says only
  configured speakers answer, followed by `CVEND`.

### Network standby

Telnet is only reachable in standby when the receiver keeps its network up.
The AVR-X2000 calls the setting **IP Control** (`Always On` /
`Off In Standby`); the 2014 document puts a **Network Standby Mode** option
under IP Control, and the 2015 document calls the menu **Network Control**. With the power-saving choice, a receiver in standby
cannot be switched on over the network; this integration then needs IR or
HDMI-CEC (roadmap item 38).

### Concurrent sessions

Sources disagree, and the answer probably depends on the model:

| Source | Statement |
|---|---|
| Official Home Assistant docs (`denonavr`) | Each receiver is limited to one telnet connection |
| ioBroker adapter, openHAB community | One connection; a second is refused, at least on older models |
| marantz-denon-mqtt (Cinema 70s) | One connection; the second fails silently |
| denon2mqtt (AVR-X2500H) | One connection at a time |
| ha-denon-marantz-avr | Receivers "only reliably support one Telnet client"; runs everything over one session |
| This repository's README, "Network ports and protocols" | Several connections, events sent to all |
| This repository's README, introduction | Many receivers accept only one session |

Until the [test](09-status-diagnostics-and-discovery.md#test-concurrent-telnet-sessions)
has been run on both receivers, treat port 23 as single-client: only one of
this integration, the official integration, an MQTT bridge or a script.
The Denon 2016 AVR Remote app controls the receiver over
`/goform/AppCommand.xml` (a 2019 network capture in the `denonavr`
repository); whether it or the HEOS app also opens telnet is unverified, and
the test shows it.

The `denonavr` library, and with it the official integration, sends commands
over telnet while its telnet connection is up and falls back to HTTP
(`formiPhoneAppDirect.xml`) otherwise. So with its telnet option on
(the default for new installations) it does hold the session. The denon2mqtt
README says the official integration "uses HTTP and is fine next to it";
that is only true with the telnet option off.

## HTTP (port 80 or 8080)

The goform interface serves XML status pages and accepts commands:

| Path | Purpose |
|---|---|
| `/goform/Deviceinfo.xml` | Static description: model, `CommApiVers`, zone count, MAC, function names |
| `/goform/formMainZone_MainZoneXmlStatusLite.xml` | Compact main-zone status (this integration's poll) |
| `/goform/formMainZone_MainZoneXmlStatus.xml`, `…Zone2…`, `…Zone3…` | Fuller zone status |
| `/goform/AppCommand.xml`, `/goform/AppCommand0300.xml` | POST an XML list of commands, get an XML reply |
| `/goform/formiPhoneAppDirect.xml?<telnet command>` | Send a telnet command over HTTP |
| `/goform/formNetAudio_StatusXml.xml`, `formTuner_TunerXml.xml` | Network audio and tuner status (older models; HEOS-era firmware answers 403 for the now-playing pages) |

### Which port

The `denonavr` library tries port 80 first and treats the receiver as a
2016-generation model when the description is on 8080. Recorded responses
from two AVR-X2000 units come from port 80 and report `CommApiVers` 0210;
the 2014 models AVR-X2100W and AVR-X4100W report 0300, also on port 80;
all seven 2016-or-later units in the library's test data report 0301 on
port 8080.

This integration tries 8080 first and accepts port 80 only when the
description there reports `CommApiVers` below 0301 (or none), then stores
the port in the config entry (`avr/transport/goform.py`). This follows a
lesson from the official integration (issue #163759): choose the port by
device type, not by falling back after a failed request on 8080, because a
receiver that is still waking up would be stored on the wrong port.

While its telnet link is down, this integration sends the media players'
commands over `formiPhoneAppDirect.xml` and reads every zone's StatusLite
page every 10 seconds, like the `denonavr` library's HTTP fallback.

## UPnP description (port 60006)

`/upnp/desc/aios_device/aios_device.xml` on HEOS-era receivers gives the
firmware version and serial number, which `Deviceinfo.xml` omits. The
AVR-X2000 generation has a plain UPnP description at `/description.xml` on
port 8080 instead.

## SSDP (UDP 1900)

Receivers answer M-SEARCH for `upnp:rootdevice` and
`urn:schemas-upnp-org:device:MediaRenderer:1`. HEOS-era receivers also
announce the device type `urn:schemas-denon-com:device:AiosDevice:1` and
answer the HEOS search target `urn:schemas-denon-com:device:ACT-Denon:1`;
they are also found over mDNS as `_heos-audio._tcp.local.` (the official
HEOS integration and Music Assistant use it).

The manufacturer string varies (`Denon`, `DENON`, `DENON PROFESSIONAL`,
`Marantz`). How the integrations match:

| Integration | SSDP match |
|---|---|
| Official `denonavr` | Device types MediaRenderer, MediaServer and AiosDevice, each with all four manufacturer strings (12 entries); aborts when model name or serial is missing |
| Official `heos` | Search target `ACT-Denon:1`, plus mDNS |
| This integration | AiosDevice with manufacturer `Denon` only |

So this integration discovers neither Marantz nor `DENON`-labelled units,
and no pre-2016 receiver, because those have no AiosDevice description
(roadmap items 16 and 17).

## HEOS CLI (port 1255)

JSON over TCP, CRLF-terminated. Covered on its own [page](05-heos.md).

## Setup config API (HTTPS 10443, HTTP 11080)

The receiver's setup web page reads and writes settings that have no telnet
command: graphic EQ bands, speaker distances and crossovers, HDMI and OSD
setup, amp assignment.

- Read: `GET /ajax/<section>/get_config?type=<id>` returns XML.
- Write: `GET /ajax/<section>/set_config?type=<id>&data=<url-encoded XML>`.
  This integration found that a POST is rejected with HTTP 400 on the
  AVR-X3600H.
- Self-signed certificate on 10443.
- Community reports from a 2022-era Marantz describe the same API on plain
  HTTP port 11080 with 10443 closed. This integration does not try 11080.

The same `/ajax` interface has two more sections that matter for live
state (ha-denon-marantz-avr, on port 11080, models not named):

| Request | Content |
|---|---|
| `/ajax/info/get_config?type=1` | Source, input signal, decoding |
| `/ajax/info/get_config?type=2` | Volume, input signal channels, active speakers, Dirac state |
| `/ajax/info/get_config?type=3` | HDMI in and out: resolution, HDR, color space, pixel depth, FRL, ALLM, QFT |
| `/ajax/control/get_config?type=9` | Sound modes offered for the current input, with the active one |
| `/ajax/control/set_config?type=4&data=<SoundMode>n</SoundMode>` | Select a sound mode by its list index |

Whether these sections also answer on 10443 is To test; they are the most
direct source for the video signal sensors (roadmap item 39).

Details of the audio and speaker groups are on the
[audio page](06-audio-audyssey-speakers.md#setup-config-api).

## Calibration protocol (port 1256)

A length-framed JSON protocol used by the Audyssey MultEQ apps. It reads and
writes speaker setup and room-correction filters. Entering a calibration
session interrupts playback, and the port has no push. This integration has
a transport for it (`avr/transport/tcp_client.py`) but does not use it.

## Other ports seen on HEOS-era receivers

ha-denon-marantz-avr lists further services, citing the receiver's own
documentation without naming it. Unverified; listed for firewall planning,
none is needed by this integration.

| Port | Service as described there |
|---|---|
| 443/tcp, 1256/tcp | "Secure variants" of HTTP and the HEOS CLI (conflicts with this integration's finding that 1256 carries the length-framed calibration protocol) |
| 3100/tcp, 3101/tcp | Tidal Connect, Qobuz Connect |
| 5353/udp | mDNS discovery |
| 7000/tcp | AirPlay |
| 8015/tcp, 60080/tcp | Internal UPnP server and controller |
| 8090/tcp | Dirac Live control |
| 10101, 10234, 10555, 10800/tcp; 1678–1687/udp | Internal HEOS services |

## RS-232C

9600 baud, 8 data bits, no parity, 1 stop bit, DB-9 female (DCE),
straight cable. Same command grammar as telnet. The AVR-X2000 document
limits the port to the North American model. Not relevant for this
integration.
