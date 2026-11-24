# Status, diagnostics and discovery

## Identifying a receiver

`/goform/Deviceinfo.xml` (port 80 on the AVR-X2000 generation, 8080 later):

| Field | Meaning | AVR-X2000 (recorded) | HEOS era (recorded) |
|---|---|---|---|
| `ModelName` | Model; may carry a leading `*` | `*AVR-X2000` | e.g. `AVR-X4500H` |
| `ManualModelName`, `CategoryName`, `Gen` | Clean model name, category, generation | absent | present |
| `CommApiVers` | Interface version | `0210` | `0301` |
| `BrandCode` | Brand | `0` | `0` for Denon |
| `DeviceZones` | Number of zones | `2` | `2` or `3` |
| `MacAddress` | MAC | present | present |
| `DeviceCapabilities` | Setup and operation function names (`FuncName`) | 55 names | 117 names on an AVC-X3700H |
| `DeviceZoneCapabilities` | Per-zone power, volume, mute, sources, sound modes | per zone | per zone |

This integration takes the MAC as unique id and gates entities on the
function names. The AVR-X2000 list contains `ToneControl`, `Restorer`,
`SleepTimer`, the sources, the surround-mode names and the Quick Selects,
but no `Audyssey`, `MultEq`, `DynamicEq`, `Bass`, `Treble`, `ChannelLevel`,
`SubwooferLevel` or any video name. Its telnet document has commands for
all of them.

Recorded AVR-X2000 answers over HTTP (library test data): `AppCommand.xml`
returns only the renamed and deleted sources; the tone-control and
sound-mode requests return nothing. The goform zone pages report the volume
in dB (`-26`), not on the 0–98 scale. A Zone 3 page exists but is empty.

Firmware version and serial number come from the UPnP description on port
60006 (HEOS era) and from HEOS `get_players`. For the AVR-X2000 no network
source of the firmware version is known; `UGIDN` returns the upgrade ID,
not the version. Unverified.

## Discovery

SSDP on UDP 1900, see the [network page](02-network-and-protocols.md#ssdp-udp-1900).
Manual setup by IP address always works when the ports are reachable.

## Status channels

| Channel | Push | Content |
|---|---|---|
| Telnet events | yes | every state change, from any controller |
| Telnet queries | — | current value of one command family |
| goform `StatusLite`, `…XmlStatus.xml` | no, poll | power, source, volume, mute, sound mode per zone |
| `AppCommand.xml` | no, poll | names, renamed and hidden sources, zone status |
| HEOS events | yes | play state, now playing, volume, queue |

Undocumented status replies seen on current receivers (community, Cinema
70s; partly used by this integration on the AVR-X3600H): `OPSML`/`OPSMLALL`
(sound mode lists), `SSSMG` (mode group), `SYSMI` (mode in plain text),
`SYSDA` (stream format), `OPINFINS`, `OPINFASP` (bit masks), `SSINFAISFSV`,
`BTTX` and `OPTXM` (sent on power-on). Not expected on the AVR-X2000 (To
test).

For comparison, the `denonavr` library's full resynchronisation sends:
`SI? MV? MU? Z2? Z2MU? Z3? Z3MU? MS? MNMEN? TR? PSTONE CTRL ? PSDYNEQ ?
PSLFC ? PSNEURAL ? PSIMAXAUD ? PSIMAXSWM ? PSSWR ? SSTTR ? VSAUDIO ? PSCES ?
PSLOM ? PSCINEMA EQ. ? BTTX ? PSSPV ? PSGEQ ? PSHEQ ? PSBAS ? PSTRE ?
PSCNTAMT ? PSMULTEQ: ? PSREFLEV ? PSDYNVOL ? DIM ? PSDELAY ? ECO? VSMONI ?
PSDIRAC ? CV? PSIMAX ? PSIMAXHPF ? PSIMAXLPF ? PSIMAXSWO ? PSSWL ? STBY?
Z2STBY? Z3STBY? SLP? VSVPM ? PSLFE ? PSBSC ? PSDEH ? PSAUROPR ? PSAUROST ?
PSAUROMODE ? PSRSZ ? SPPR ? PSDIC ? PSSP: ? PSDRC ? PSDEL ? PSRSTR ?`,
plus `MSQUICK ?` on Denon and `MSSMART ?`, `PSMDAX ?`, `PSDACFIL ?`,
`ILB ?`, `SSHOS ?` on Marantz. `SPPR` (speaker preset) and `SSTTR` are in
no Denon document available here.

Unknown lines must never break anything: the official integration once
failed its setup on the unknown `ILB` reply. Roadmap item 29 adds a test for
this.

## Test procedures

Run each test on both receivers and note model, firmware and date. Use the
receiver's real address in place of `192.0.2.10`; do not commit it.
Disconnect other telnet clients first, including Home Assistant
integrations.

### Automated probe

`tools/probe_receiver.py` runs the read-only part of these tests in one go:

- the port test and the SSDP answers of the receiver (what discovery sees),
  with the device description they point to;
- the goform pages on port 80 and 8080: device description, the status
  pages of every zone (main, zone 2, zone 3, also the ones the HTTP
  fallback reads), network audio and tuner;
- `AppCommand.xml` and `AppCommand0300.xml` with the read-only `Get…`
  commands of the `denonavr` library (sources and their names, hidden
  sources, power, volume, mute, sound mode, tone, Audyssey);
- every documented telnet query (about 160, from the 2012 to 2015 protocol
  documents, the 2016+ queries the integration sends in their exact form,
  the full resynchronisation of the `denonavr` library and the undocumented
  ones listed above), with the reply time of each;
- the spacing test: six queries sent 0, 20, 50 and 80 ms apart without
  waiting, and how many get an answer;
- the session test: whether a second telnet session connects, gets
  answers, and leaves the first one working;
- HEOS: the player list, per player the info, now playing, play state,
  repeat and shuffle, volume, mute, Quick Selects and firmware update
  check; the groups, the music sources and a heartbeat;
- the setup interface pages, including the ones the integration reads
  (graphic EQ, HDMI setup, on-screen display, 4K and TV format, amp
  assign), and a scan of every section (globals, audio, video, speakers,
  control, info) for types 1 to 20, which shows what a model offers there
  beyond what the integration uses (not with `--quick`);
- which zones were on, from the `ZM?`, `Z2?` and `Z3?` answers, because a
  zone that is off answers fewer queries.

```bash
python3 tools/probe_receiver.py 192.0.2.10
python3 tools/probe_receiver.py 192.0.2.10 --quick   # about 20 queries
```

It sends only queries (commands ending in `?`, plus the screen-text
requests `NSE`, `NSA` and `NSH`), only `Get…` AppCommands and only HEOS
`get_`, `check_` and heartbeat requests, and refuses anything else, so it
cannot change a setting. When the receiver answers `PWSTANDBY`, it warns
that the results are incomplete. It writes `avr-probe-<model>.md`, a table
per capability group with "yes", "no reply", "rejected" or "other lines
only", plus the web pages, reply times, spacing and session results, and
`avr-probe-<model>.json` with every reply. Addresses, MACs (also the Wi-Fi
one), serial numbers, the names of the receiver and of other HEOS speakers
and groups, Wi-Fi names and account names are replaced before writing.
The JSON is the input for test fixtures and for the
[capability matrix](models/capability-matrix.md); "yes" there counts as
Tested for that model and firmware.

The tests below that change state (zones, All Zone Stereo, network
standby) are not part of the probe and stay manual. The concurrent-session
test is part of the probe as far as it only reads.

### Test: reachable ports

```bash
for p in 23 80 1255 1256 8080 10443 11080 60006; do
  nc -z -w 2 192.0.2.10 $p && echo "$p open" || echo "$p closed"
done
```

Expected: AVR-X2000 23 and 80 (and 8080 for the UPnP description);
AVR-X2600H 23, 1255, 8080, 10443, 60006, possibly 1256.

### Test: device description

```bash
curl -s http://192.0.2.10/goform/Deviceinfo.xml | head -20
curl -s http://192.0.2.10:8080/goform/Deviceinfo.xml | head -20
```

Note which port answers, `CommApiVers` and the full `FuncName` list.

### Test: telnet basics

```python
import socket, time

def query(host, *commands, wait=1.0):
    with socket.create_connection((host, 23), timeout=3) as s:
        for c in commands:
            s.sendall(c.encode() + b"\r")
            time.sleep(0.15)
        s.settimeout(wait)
        data = b""
        try:
            while chunk := s.recv(4096):
                data += chunk
        except socket.timeout:
            pass
    return [l for l in data.decode(errors="replace").split("\r") if l]

print(query("192.0.2.10", "PW?", "ZM?", "MV?", "MU?", "SI?", "MS?", "CV?", "Z2?"))
```

Record for each command: reply, no reply, or `?`. Repeat at the volume
minimum for `MV?` (does the AVR-X2000 report `00` or `99`?) and after
`CV?` (is there a `CVEND`?).

### Test: concurrent telnet sessions

1. Open session A (`nc 192.0.2.10 23`) and leave it open.
2. Open session B in a second terminal.
3. Send `MV?` in B. Does B connect, and does it get a reply?
4. Change the volume with the remote. Which sessions show the `MV…` event?
5. Does A stay open after B connects?

Result per receiver: one session / several sessions with events to all /
other. This settles the contradiction listed on the
[network page](02-network-and-protocols.md#concurrent-sessions).

### Test: network standby

Set the network standby option to its power-saving value, put the receiver
in standby, try ports 23 and 8080 (or 80). Repeat with the always-on value
and send `PWON`.

### Test: zones

- `Z2ON`, `Z2NET`, `Z250`, `Z2MUON`, `Z2?`: replies and audible result.
- Main zone to `SINET` with Zone 2 off: does Zone 2 switch on?
- `PWSTANDBY` with Zone 2 on: does the unit go to standby?
- `MNZST ON`, then `MS?` and `MNZST ?`; then `MNZST OFF` and `MS?`.

### Test: HEOS (AVR-X2600H)

```bash
printf 'heos://player/get_players\r\n' | nc -w 2 192.0.2.10 1255
```

Then `get_now_playing_media`, `get_volume` (compare with `MV?`),
`get_quickselects`, `check_update`, and `browse/play_stream?pid=<pid>&url=<test stream>`.
Note whether `get_players` returns `serial`.

### Test: live status over the setup interface (AVR-X2600H)

```bash
for port in 10443 11080; do
  for q in info/get_config?type=1 info/get_config?type=3 control/get_config?type=9; do
    curl -sk --max-time 3 "https://192.0.2.10:$port/ajax/$q" | head -5
    curl -s  --max-time 3 "http://192.0.2.10:$port/ajax/$q"  | head -5
  done
done
```

Note which port and scheme answer and whether the XML carries the video
format and sound mode list.

### Test: undocumented status queries

With the query helper above: `NSFRN ?`, `SYSMI ?`, `SYSDA ?`, `SYSDVIN ?`,
`SYSDVOUT ?`, `SSINFAISFOR ?`, `SSINFAISFSV ?`, `SSSMG ?`, `OPSMLALL ?`,
and the queries of Denon's app `NSARA ?` and `NSHEA ?`. Note reply or
silence.

### Test: commands seen in Denon's app

Send `MVLONGUP`, wait a second, then `MVLONGEND`, and note whether the
volume rises and stops; the same with `MVLONGDOWN`. Send `VSVPM ?` and note
whether the reply can be `VSVPMBYP`. Send `Z2CVFL 505` and note whether
the receiver ignores it (it takes whole dB only, as with `PSBAS 505`).

### Recording data for tests

A telnet log of the full resynchronisation (the integration's debug log, or
the `query` helper with every query the profile sends) is the most useful
test fixture. Before committing, replace addresses, MAC, serial, account
names and preset names that identify a person (`scripts/check_private_data.py`
checks addresses and MACs).

### Result template

| Test | AVR-X2000 (firmware) | AVR-X2600H (firmware) |
|---|---|---|
| Ports open | | |
| Deviceinfo port, `CommApiVers` | | |
| `MV?` at minimum | | |
| `CV?` ends with `CVEND` | | |
| Concurrent sessions | | |
| Network standby: telnet reachable, `PWON` works | | |
| Zone 2 commands | | |
| All Zone Stereo set and read | | |
| HEOS commands | n/a | |
