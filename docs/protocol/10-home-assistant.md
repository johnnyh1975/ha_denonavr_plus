# Home Assistant

## Integrations that can talk to these receivers

| Integration | Interfaces | AVR-X2000 | AVR-X2600H |
|---|---|---|---|
| Denon AVR Network Receivers (`denonavr`, core) | HTTP 80 or 8080, telnet 23 (optional), UPnP | Supported by its library (port 80, `CommApiVers` 0210 recorded in its tests) | Supported |
| HEOS (`heos`, core) | HEOS CLI 1255 | No HEOS | HEOS player, groups, favorites |
| Denon AVR Plus (`denonavr_plus`, this) | telnet 23, HTTP 8080, 60006, 1255, 10443 | Not yet (port 80, function list) | Supported generation; Tested on AVR-X3600H |
| Music Assistant | HEOS or AirPlay | AirPlay | HEOS provider or AirPlay 2 |

The official integration lists ports 23, 8080 and 60006 for VLAN setups
and warns that each receiver allows a single telnet connection (Sourced,
Home Assistant documentation).

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
| Choose the port from `CommApiVers` / model, not by fallback | Avoids storing a waking receiver on the wrong port | 16 |
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
