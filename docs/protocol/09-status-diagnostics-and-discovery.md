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
`SYSDVOUT ?`, `SSINFAISFOR ?`, `SSINFAISFSV ?`, `SSSMG ?`, `OPSMLALL ?`.
Note reply or silence.

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
