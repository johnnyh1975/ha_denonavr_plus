# Zones and multiroom

Three different things are called "multiroom" on these receivers. They do
not replace each other.

| Concept | What it is | Controlled over | Entity model |
|---|---|---|---|
| Physical zones | Main zone, Zone 2 (Zone 3 on larger models): separate outputs of one receiver | Telnet `ZM`, `Z2`, `Z3` | One `media_player` per zone |
| All Zone Stereo | A receiver mode: the main zone's signal goes to every zone in stereo | Telnet `MNZST`, `MS` | A switch on the receiver |
| HEOS groups | Several HEOS devices on the network play the same stream | HEOS CLI `group/…` | HEOS integration or Music Assistant |

## Power

| Command | Effect | Status |
|---|---|---|
| `PWON` / `PWSTANDBY` | Whole receiver | Documented (all documents) |
| `ZMON` / `ZMOFF` | Main zone only; network and other zones stay up | Documented |
| `Z2ON` / `Z2OFF` | Zone 2 | Documented |

Community observation (Cinema 70s): with Zone 2 on, `PWSTANDBY` leaves the
unit running; send `Z2OFF` first. Not in any official document. To test on
both receivers.

## Zone 2 commands

| Command | Values | AVR-X2000 | 2015 doc | Notes |
|---|---|---|---|---|
| `Z2<source>` | source names as for `SI` | Documented | Documented | `Z2SOURCE` = follow the main zone |
| `Z2<nn>`, `Z2UP`, `Z2DOWN` | 00–98, 80 = 0 dB | Documented | Documented | Two digits only for Zone 2 events |
| `Z2MUON/OFF` | mute | Documented | Documented | |
| `Z2CVFL`, `Z2CVFR` | 38–62, 50 = 0 dB | Documented | X4200W, X3200W, X2200W | Zone level trim |
| `Z2SLP` | 001–120, `OFF` | Documented | Documented | Sleep timer |
| `Z2QUICK1`–`5`, `… MEMORY` | recall / store | Documented | Documented | |
| `Z2STBY` | `2H`, `4H`, `8H`, `OFF` | — | all four models | Auto standby |
| `Z2PSBAS`, `Z2PSTRE` | tone | — | X4200W, X3200W | |
| `Z2HPF`, `Z2CS` (`ST`/`MONO`), `Z2HDA` (`THR`/`PCM`) | filter, channel mode, HDMI audio | — | X4200W, X3200W | |
| `Z2?` | returns power, source and volume as separate lines | Documented | Documented | Parser separates them by content |

`Z3…` mirrors `Z2…` on models with a third zone (X4200W in the 2015
document). Neither the AVR-X2000 nor the AVR-X2600H has Zone 3: the
AVR-X2000's description reports `DeviceZones` 2, and the AVR-X2600H product
sheet lists one Zone 2 output.

### Zone 2 on the two receivers

| | AVR-X2000 | AVR-X2600H |
|---|---|---|
| Zone 2 present | Documented (`DeviceZones` 2) | Sourced (pre-out and speaker-assignable) |
| Zone 2 on speakers | To test (amp assign) | Sourced (assignable) |
| Which sources Zone 2 can play | To test | To test (HDMI and digital sources are often limited to the main zone on this class) |
| Zone 2 playing a network source independently of the main zone | To test | To test (maintainer's plan, phase 5) |
| Switching the main zone to `NET` wakes Zone 2 | To test | To test (community reports this on several models) |

## All Zone Stereo

| Step | AVR-X2000 | AVR-X2600H |
|---|---|---|
| Switch on/off | `MNZST ON` / `MNZST OFF` (Documented) | same (Documented in 2015 doc; Tested on AVR-X3600H) |
| Read state | Event `MSALL ZONE STEREO` while active (Documented) | `MNZST ?` (Documented 2015; Tested on AVR-X3600H) |

All Zone Stereo needs a configured Zone 2. While it is on, the main zone's
sound mode reads `ALL ZONE STEREO`; the previous mode comes back when it is
switched off (To test). It is not a HEOS group: no other HEOS device takes
part, and HEOS does not report it.

Idea from the maintainer's plan (item 32): map "join Zone 2" in the
Home Assistant media player to All Zone Stereo. Only plannable after the
behaviour above is tested.

## HEOS groups

A HEOS-era receiver appears as one HEOS player in the documents and in
this integration's tests on the AVR-X3600H. Music Assistant's HEOS provider
notes that "the zones of an AVR" can appear as several HEOS players on one
address; whether the AVR-X2600H does that when Zone 2 plays a network
source is To test. Grouping it
with HEOS speakers is a HEOS command (`group/set_group`), see
[HEOS](05-heos.md#groups). The AVR-X2000 has no HEOS and cannot be in a HEOS
group.

## Home Assistant model

- One device per receiver, one `media_player` per physical zone.
- Zone 2 as its own device (room assignment, voice control) is roadmap
  item 15.
- All Zone Stereo as a switch on the receiver device.
- HEOS players and groups stay with the HEOS integration or Music Assistant;
  this integration reads now playing from HEOS but does not group.
