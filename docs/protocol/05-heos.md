# HEOS

HEOS is Denon's network multiroom system. HEOS-era receivers such as the
AVR-X2600H have it built in and expose its command line interface (CLI) on
TCP port 1255. The AVR-X2000 predates it and has no port 1255.

Reference: HEOS CLI Protocol Specification, version 1.14 (HEOS firmware
1.505.140, April 2019). Facts below come from it unless marked otherwise.

## Transport

| Property | Value |
|---|---|
| Port | 1255/tcp |
| Discovery | SSDP, search target `urn:schemas-denon-com:device:ACT-Denon:1` |
| Command | `heos://<group>/<command>?<attr>=<value>&…`, terminated by CRLF |
| Response | One JSON object per line, CRLF: `heos.command`, `heos.result` (`success`/`fail`), `heos.message`, optional `payload`, optional `options` |
| Escaping | `&`, `=`, `%` inside values are sent and received as `%26`, `%3D`, `%25` |
| Slow commands | First reply `"message": "command under process"`, the real reply follows |
| Correlation | Any extra `SEQUENCE=<n>` attribute is echoed in `message` |
| Connections | Up to 32 per device; one connection controls the whole HEOS system |

The message field is an `&`-separated attribute list that can gain
attributes in later firmware; parse it as key/value pairs, never by
position.

## Start-up sequence

The CLI is dormant until the first connection; right after it, players are
not yet known and a burst of events can follow. The specification
recommends:

1. Unregister change events (`system/register_for_change_events?enable=off`).
2. Sign in, if account credentials are used (`system/sign_in`).
3. Read the state: players, groups, sources, now playing, volume, play state.
4. Register for change events (`enable=on`).
5. Keep one connection open even when idle, so the CLI does not go dormant.

Typical controllers use one connection for events and one for commands.

## Commands relevant to a receiver

| Command | Purpose | Notes |
|---|---|---|
| `player/get_players` | All players in the HEOS system | Fields: `name`, `pid`, `gid`, `model`, `version`, `network`, `lineout`, `control`, `serial` (CLI 1.10+) |
| `player/get_player_info?pid=` | One player | |
| `player/get_now_playing_media?pid=` | Title, artist, album, `image_url`, `sid`, `qid`, `mid`, options | Type `song` or `station` |
| `player/get_play_state`, `set_play_state` | `play`, `pause`, `stop` | |
| `player/get_volume`, `set_volume` | 0–100 | Relation to the receiver's `MV` scale: To test |
| `player/volume_up`, `volume_down` | step 1–10, default 5 | |
| `player/get_mute`, `set_mute`, `toggle_mute` | | |
| `player/get_play_mode`, `set_play_mode` | repeat `on_all`/`on_one`/`off`, shuffle `on`/`off` | |
| `player/get_queue`, `play_queue`, `remove_from_queue`, `clear_queue`, `move_queue_item`, `save_queue` | Queue, max. 100 entries per reply | |
| `player/play_next`, `play_previous` | | |
| `player/set_quickselect`, `play_quickselect`, `get_quickselects` | Quick Select 1–6 | Marked "LS AVR only" in the specification; AVR-X2600H To test. Telnet `MSQUICK` works regardless |
| `player/check_update` | `update_exist` / `update_none` | CLI 1.10+; basis for a firmware `update` entity (roadmap item 31) |
| `browse/get_music_sources`, `browse`, `search` | Sources and their content | Ranges, `count` 0 = unknown size |
| `browse/play_stream?pid=&sid=&mid=&name=` | Play a station | |
| `browse/play_stream?pid=&url=` | Play a URL | Removed in CLI 1.2, back in 1.9 (2017); `url` must be the last attribute. AVR-X2600H To test (roadmap items 14 and 21) |
| `browse/play_preset?pid=&preset=` | Play HEOS Favorite n | |
| `browse/play_input?pid=&input=` | Play an input, optionally from another device (`spid`) | Input names such as `inputs/hdmi_in_1`, `inputs/tvaudio`, `inputs/phono` |
| `browse/add_to_queue?…&aid=` | 1 play now, 2 play next, 3 add to end, 4 replace and play | |
| `system/heart_beat` | Health probe | |
| `system/reboot` | Reboot the connected device | Avoid in an integration |

Not CLI commands, although a community document lists them:
`browse/play_url`, `browse/get_heos_playlists`, `browse/rename_heos_playlist`.
The specification plays a URL with `browse/play_stream` and handles
playlists with `browse/browse`, `browse/rename_playlist` and
`browse/delete_playlist`.

## Groups

`group/set_group?pid=<leader>,<member>,…` creates or changes a group (the
first pid leads), `pid=<leader>` alone ungroups. Group volume and mute have
their own commands. HEOS groups are network groups of devices; they have
nothing to do with the receiver's zones ([Zones](04-zones-and-multiroom.md)).

## Events

`sources_changed`, `players_changed`, `groups_changed`,
`player_state_changed`, `player_now_playing_changed`,
`player_now_playing_progress` (`cur_pos`, `duration` in ms),
`player_playback_error`, `player_queue_changed`, `player_volume_changed`,
`repeat_mode_changed`, `shuffle_mode_changed`, `group_volume_changed`,
`user_changed`.

Events arrive for **every player in the HEOS system**, each carrying its
`pid`.

## Errors

`result: fail` with `message: eid=<n>&text=…`. Codes 1–17, among them
2 invalid id, 8 user not logged in, 9 out of range, 12 system error (with
`syserrno`), 13 processing previous command, 16 too many commands in
queue, 17 skip limit reached.

## Limits

- Spotify cannot be browsed, searched or queued through the CLI; Spotify
  Connect is controlled through Spotify.
- An input can be sent to one other player or group at a time, and not one
  that is already playing elsewhere.
- Account changes made in the HEOS app show up in the CLI only after a new
  sign-in.

## How this integration uses HEOS

It opens one connection, registers for events, reads `get_players` and then
follows now playing and play state, with play, pause, stop, next and
previous as transport.

Two findings from comparing the code (`avr/transport/heos.py`) with the
specification:

1. **The player is chosen as the first entry of `get_players`.** That list
   holds every HEOS device in the home. With HEOS speakers next to the
   receiver, the first entry can be a speaker. The receiver's own player
   should be matched by IP address, as pyheos and ha-denon-marantz-avr do
   (the player list carries an `ip` field in practice, although the 2019
   specification does not list it), or by `serial` (To test that it equals
   the serial from port 60006). To fix and test with a second HEOS device.
2. **`player_state_changed` events are applied without checking `pid`.** A
   speaker starting or pausing would change the receiver's state in
   Home Assistant. Same fix: ignore events for other players.

Smaller points: events are registered before the state is read (the
specification suggests the reverse); `player_now_playing_progress` is
ignored, which roadmap item 23 (position and duration) will need; and there
is no `system/heart_beat`, so an idle connection relies on the TCP stack to
notice a drop (see the practical notes). Events are registered again on
every reconnect, as they must be.

## Practical notes from other implementations

Collected by the marantz-denon-mqtt project on a Marantz Cinema 70s (HEOS
firmware 3.88.614). Each is a behaviour, not a specification.

| Behaviour | Consequence |
|---|---|
| `get_now_playing_media` returns the last track even when paused or stopped | Show track data only while the play state is `play`, or clear it on `stop` |
| Event registration belongs to the connection | Register again after every reconnect |
| Idle connections dropped after 60 s or more; 30 s heart beat was safe | Send `system/heart_beat` about every 30 s |
| `heos.result` is `success` on this firmware, `ok` in some examples | Accept both |
| `sid` values differ between firmware and regions | Identify the service by the `mid` prefix (`spotify:track:` …), never by a fixed `sid` table |
| `type: station` also for Spotify radio with full track data | Do not treat `station` as radio |
| `set_volume` clears mute on this firmware | Do not assume volume and mute are independent |
| Long titles arrive truncated | Nothing to recover |

## The official HEOS integration

From the Home Assistant documentation: one `media_player` per HEOS device,
including receivers; `media_player.join`/`unjoin` for groups; `play_media`
with favorites, playlists, Quick Selects, queue positions and URLs of at
most 255 characters; no zone control, no receiver power, no text-to-speech.
Receiver power and zones therefore stay with a receiver integration such as
this one ([Home Assistant page](10-home-assistant.md)).

## Open questions for the AVR-X2600H

| Question | Status |
|---|---|
| Does `play_stream?url=` work on its firmware? | To test |
| Do HEOS volume (0–100) and `MV` move together, and how do they map? | To test |
| Which zone does the HEOS player play to when Zone 2 selects `NET`? | To test |
| Are the HEOS Quick Select commands available? | To test |
| Does the receiver report `serial` in `get_players`? | To test |
