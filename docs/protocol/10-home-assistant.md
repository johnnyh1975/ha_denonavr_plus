# Home Assistant

## Integrations that can talk to these receivers

| Integration | Interfaces | AVR-X2000 | AVR-X2600H |
|---|---|---|---|
| Denon AVR Network Receivers (`denonavr`, core) | HTTP 80 or 8080, telnet 23 (optional), UPnP | Supported by its library (port 80, `CommApiVers` 0210 recorded in its tests) | Supported |
| HEOS (`heos`, core) | HEOS CLI 1255 | No HEOS | HEOS player, groups, favorites |
| Denon AVR Plus (`denonavr_plus`, this) | telnet 23, HTTP 8080, 60006, 1255, 10443 | Not yet (port 80, function list) | Supported generation; Tested on AVR-X3600H |
| Music Assistant | HEOS or AirPlay | AirPlay | HEOS provider or AirPlay 2 |
| ha-denon-marantz-avr (`denon_marantz_avr`, community) | `denonavr` library, HEOS via pyheos, setup interface on 11080 | as `denonavr` | yes, plus live sound modes and signal sensors |
| denon2mqtt, marantz-denon-mqtt (community, MQTT) | telnet 23; HEOS 1255 (marantz-denon-mqtt) | telnet basics | telnet basics, HEOS |

### Official `denonavr`

From the Home Assistant documentation:

- Ports 23, 8080 and 60006 for VLAN setups.
- `use_telnet` gives local push; each receiver allows only one telnet
  connection; on by default for new installations, off for older ones.
- Options: `show_all_sources`, `zone2`, `zone3`, `update_audyssey`
  (can take up to 10 s).
- Actions: `denonavr.get_command` (raw HTTP command),
  `denonavr.set_dynamic_eq`, `denonavr.update_audyssey`.
- Marantz receivers need Auto-Standby enabled to be powered on remotely.
- The list of known supported devices names both the AVR-X2000 and the
  AVR-X2600H.

### Official `heos`

One `media_player` per HEOS device; join and unjoin; `play_media` for
favorites, playlists, Quick Selects, queue items and URLs up to 255
characters; group volume and queue actions. No zone control, no receiver
power, no text-to-speech; the documentation points to a universal media
player for combining it with a receiver integration.

## Recommended setup per receiver

**AVR-X2000, today:** the official `denonavr` integration with telnet
enabled. Denon AVR Plus can replace it once the older-receiver work
(roadmap item 16) is done.

**AVR-X2600H:**

- Denon AVR Plus for the receiver: zones, sources, sound modes, Audyssey,
  tone, speakers, video, graphic EQ.
- The HEOS integration or Music Assistant for playback, browsing and HEOS
  groups. The HEOS CLI accepts many connections, so this does not collide
  with Denon AVR Plus reading now playing.
- Not the official `denonavr` integration at the same time, because of the
  telnet session (until the [concurrency test](09-status-diagnostics-and-discovery.md#test-concurrent-telnet-sessions)
  says otherwise).

## Entity model

| Entity | Source | AVR-X2000 | AVR-X2600H |
|---|---|---|---|
| `media_player` main zone | `PW`, `ZM`, `MV`, `MU`, `SI`, `MS`, HEOS | telnet only | telnet + HEOS |
| `media_player` Zone 2 | `Z2…` | yes | yes |
| Sound mode selects | `MS`, `OPSML` | fixed list needed | from the receiver |
| Audyssey, tone, audio | `PS…` | telnet commands documented, not in function list | telnet |
| Channel levels, distances, crossovers | `CV…`, `SS…`, setup config API | levels only | all |
| Graphic EQ | setup config API | none | yes |
| Video, picture | `VS…`, `PV…`, setup config API | `VS…`, `PV…` (two-digit encoding) | all |
| All Zone Stereo | `MNZST`, `MS` | set; read from `MS` | set and read |
| Now playing | HEOS / `NSE` | `NSE` lines or goform on port 80 | HEOS |
| Firmware `update` | HEOS `check_update` | none | To test |

## Gaps and suggested work

Findings from this analysis, with the roadmap item they belong to. New
items are marked "new".

| Finding | Effect | Roadmap |
|---|---|---|
| goform port fixed to 8080 | AVR-X2000 setup fails | 16 |
| Entities gated only on `FuncName`; AVR-X2000 lists 55 names without Audyssey, tone levels or video | Even with the port fixed, most settings would stay hidden | 16, new: probe telnet queries as a second gate for receivers with `CommApiVers` below 0300 |
| Choose the port from `CommApiVers` / model, not by fallback | Avoids storing a waking receiver on the wrong port; port 80 for 0210 to 0300, the old encodings only for 0210 | 16 |
| The 2018 and FY21 Denon protocol spreadsheets are missing | Many AVR-X2600H facts stay "To test" | add to the project sources |
| `MV99` = minimum on pre-2012 models | Volume read as +19 dB | 16 |
| Picture controls are two-digit on the AVR-X2000 | Wrong values written | 16 |
| `CV?` ends without `CVEND` and reports unused channels as `50` on the AVR-X2000 | The parser does not wait for `CVEND` today (keep it so); unused channels must not create enabled entities | 16 |
| No `MNZST ?` on the AVR-X2000 | All Zone Stereo state must come from `MS` | 16, 32 |
| Tuner presets `A1`–`G8` vs `01`–`56` | Wrong preset recalled | 37 |
| HEOS player chosen as the first `get_players` entry | Wrong now playing with HEOS speakers in the home | new (bug) |
| HEOS `player_state_changed` applied without `pid` check | Speakers change the receiver's play state | new (bug) |
| HEOS `play_stream?url=` documented since CLI 1.9 | Basis for `play_media` and announcements | 14, 21 (To test) |
| HEOS `check_update` documented since CLI 1.10 | Basis for the `update` entity | 31 (To test) |
| HEOS `player_now_playing_progress` ignored | Position and duration missing | 23 |
| Setup config API on HTTP 11080 on newer firmware | Graphic EQ and video setup missing there | new: try 11080 when 10443 is closed |
| README says both "one telnet session" and "several sessions" | Users get contradictory advice | 34, after the concurrency test |
| `webconfig.py` docstring mentions POST, code uses GET | Misleads contributors | small cleanup |
| Marantz manufacturer strings not in the SSDP matcher | No automatic discovery | 17 |
| No HEOS heart beat | Idle HEOS connection may drop unnoticed (community: drops after 60 s or more) | new |
| Now playing kept while HEOS reports `stop` | Stale title in the media player | 23 |
| Quick Select numbers beyond the advertised count | A Marantz became unstable after an out-of-range recall | 13, 30 |
| Live sound mode list and video signal via `/ajax/control` and `/ajax/info` | Better sound mode handling, video sensors | 30, 39 (To test on 10443) |
| Telnet `SYSDVIN`, `SYSDVOUT`, `SSINFAISFOR` | Signal sensors without HTTP | 39 (To test) |

### Lessons from ha-denon-marantz-avr

The closest comparable project merges three community integrations on top
of the `denonavr` library. Points worth adopting:

- One HEOS player chosen by the receiver's IP address.
- Live sound mode list and signal sensors from the setup interface, read
  only while the receiver is on, one request at a time, every 30 s, with a
  5-minute pause after a failure.
- Settings entities created only when the receiver answers the telnet
  query, which is the gating the AVR-X2000 would need.
- The main-zone media player turns into the HEOS player while a network
  source is selected, instead of a second entity.

## Rules for new functions

From the maintainer's plan and this repository's `AGENTS.md`:

1. A function is implementable only with a documented network command or a
   successful test on a receiver. A menu entry is not enough.
2. Every command token not confirmed on a receiver is marked
   `@pytest.mark.assumed(...)` in its test.
3. Build a custom function only where the official integrations leave a
   concrete gap.
4. The [capability matrix](models/capability-matrix.md) is updated with
   every test result.
