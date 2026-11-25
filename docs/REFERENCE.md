# Denon AVR Plus – reference

Every option, entity, action and port, as it is today. To get started, read
the [user guide](USER_GUIDE.md) first; it explains the same things step by
step, with screenshots and recommendations. Back to the [README](../README.md).

**Contents**

- [Configuration](#configuration) and [options](#options)
- [Removal](#removal)
- [Network ports and protocols](#network-ports-and-protocols)
- [What you get](#what-you-get): every entity, which start
  [enabled or disabled](#enabled-and-disabled-entities), the
  [graphic equalizer card](#graphic-equalizer-card)
- [Actions](#actions) and the [device trigger](#device-trigger-turn-on-requested)
- [Blueprints and an example dashboard](#blueprints-and-an-example-dashboard)
- [With Music Assistant and the HEOS integration](#with-music-assistant-and-the-heos-integration)
- [Notes and limitations](#notes-and-limitations)

## Configuration

Step by step with screenshots: [user guide, section 2](USER_GUIDE.md#2-add-the-receiver).

Receivers on the network usually show up by themselves under Settings → Devices &
Services → Discovered.

Otherwise: Settings → Devices & Services → Add Integration → **Denon AVR Plus**,
then enter the receiver's **IP address**. Leave the field empty to choose from the
receivers Home Assistant has found on the network and that are not set up yet;
with only one, it is added directly. The name and all capabilities are read from
the device automatically; no other input is required.

When the receiver gets a new address from your router, Home Assistant notices it
(DHCP and SSDP) and the integration follows it by itself. To change the address by
hand, open the integration's menu (three dots) and choose **Reconfigure**. The new
address must belong to the same receiver.

### Options

Which settings are recommended, and why: [user guide, section 5](USER_GUIDE.md#5-tailor-it-in-the-options).

**Configure** on the integration entry opens a menu: volume, zones, sources,
sound modes, connection, entities, and **Test the receiver** (see
[user guide, section 7](USER_GUIDE.md#7-test-a-receiver-model)). Apart from that test, nothing in it changes the receiver; it
changes what Home Assistant shows and does.

**Volume** (works even when the receiver is off):

- **Volume scale of the official integration**: off, the media player's 0 to
  100 % spans the receiver's whole range up to its maximum. On, 100 % is
  +20 dB and 0 % is −80 dB, as the official Denon AVR integration computes it,
  so `volume_set` levels in automations sound the same after moving over.
- Per zone, in dB as the receiver shows it (0 dB is the reference):
  - **Maximum volume**: Home Assistant never sets the zone louder, whether
    from the media player, the volume number or volume up. The receiver's own
    buttons and remote are not limited; for that, use its Volume Limit
    setting.
  - **Turn-on volume**: set when Home Assistant switches the zone on, once the
    zone reports that it is on.
  - **Volume step**: dB per volume up or down from Home Assistant (the
    buttons of the media player card), 0.5 to 5 dB. Empty: the receiver's own
    step of 0.5 dB.
  - **Announcement volume** (main zone): the volume announcements play at.
    Empty: the volume as it is.

**Zones**: which additional zones (zone 2, zone 3) get entities. Each such zone
is a device of its own (`AVR-X2600H Zone 2`) with its media player, volume and
source sensor, and the zone's sleep timer, front left and right level, quick
select and auto standby where the receiver answers them, so you can put it in the room where it
plays. A zone that is
switched off loses its device and entities; switching it on again brings them
back. **Main zone off puts the receiver in standby**: turning the main zone off
from Home Assistant then puts the whole receiver in standby (`PWSTANDBY`), so
no other zone keeps playing; without it, only the main zone turns off.

**Sources** (needs the receiver connected, it reads its sources): per zone,
which sources the media player offers, then a name for each in Home
Assistant. The names and hidden inputs set on the receiver are already taken
over; this narrows down and renames on top. Selecting all keeps sources the
receiver gains later. Renamed sources can still be selected by their receiver
name, so older automations keep working. **Quick selects and favorites in the
source menu** (on by default) adds the main zone's quick selects and HEOS
favorites after the inputs.

**Sound modes** (needs the receiver connected): which sound modes the main
zone's media player offers, and their names. The receiver only offers the
modes that fit the current signal. The current source and sound mode are
always shown, also when they are not selected. The sound mode selects keep
the receiver's full list.

**Entities** (works even when the receiver is off): a toggle per group of the
entities that start hidden, with how many it holds: Audio, Speakers, Video and
picture, Graphic EQ, Zones, Receiver, Diagnostics. A group shows only entities
the integration hid; one you enabled or disabled yourself stays as it is, and
speaker entities of channels the receiver does not have stay hidden. Removing
a group off hides again only what it showed. Shown entities appear
within half a minute, when Home Assistant reloads the integration.

**Connection** (works even when the receiver is off; a change restarts the
integration):

- **Check interval**: how often the receiver is checked over HTTP while telnet
  is connected, 30 to 600 seconds (default 60). Changes arrive over telnet at
  once anyway; while telnet is down the check runs every 10 seconds.
- **Now playing over HEOS** (default on): title, artist, cover and play/pause for
  network sources. Turn it off if another program needs the HEOS connection.
- **Read setup settings every** … minutes (default 15): the graphic EQ, video
  setup and amp assign have no push. They are read when the connection comes up,
  after every change from Home Assistant, and at this interval to notice changes
  made on the receiver's menu; 0 reads them only then.

## Removal

1. Settings → Devices & Services → **Denon AVR Plus** → three dots on the receiver
   entry → **Delete**. This removes the device and all its entities. The settings
   stored on the receiver itself are not changed.
2. Remove it where you installed it: in the Marketplace (Settings →
   Marketplace → **Installed**) or, before Home Assistant 2026.11, in HACS.
   For a manual installation, delete
   `<config>/custom_components/denonavr_plus`.
3. Restart Home Assistant.

Automations, scripts and dashboards that use the removed entities are not deleted;
Home Assistant shows them as unavailable until you change or remove them.

## Network ports and protocols

All traffic is on the local network and is initiated by Home Assistant (client)
towards the receiver (server). To let the two communicate through a router or
VLAN firewall, allow the Home Assistant host to reach the receiver on these
ports. Nothing needs to be opened towards the internet.

| Port | Protocol | Purpose                                                                                                                                                                               | Ordinality |
|------|----------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------|
| **23/tcp** | Telnet | Primary control channel and real‑time push: power, volume, mute, source, sound modes and all state updates                                                                            | **Required** |
| **8080/tcp** | HTTP (goform) | Discovery (`Deviceinfo.xml`), the reconciliation poll (`…StatusLite.xml`) and, while telnet is down, the media players' commands (`formiPhoneAppDirect.xml`). Port 80 on receivers before 2016 | **Required** |
| **60006/tcp** | HTTP (UPnP/AIOS) | Device description read once at setup for firmware version and serial number                                                                                                          | Optional (degrades gracefully) |
| **1900/udp** | SSDP (multicast) | Automatic discovery of the receiver on the LAN                                                                                                                                        | Optional (auto‑discovery only) |
| **1256/tcp** | Length‑framed JSON | Not currently used (reserved for future use)                                                                                                                                          | Unused |
| **1255/tcp** | HEOS CLI | Now‑playing media, album art and transport (play/pause/next) for network sources                                                                                                      | Optional (degrades gracefully) |
| **10443/tcp** | HTTPS (setup config) | Graphic‑EQ per‑band values, the Video setup menu (HDMI setup/CEC, ARC, OSD, TV/4K format) and Amp Assign — settings with no telnet token, read/write (non‑disruptive; self‑signed cert) | Optional (graphic EQ + video + amp assign) |
| **11080/tcp** | HTTP (setup config) | The same, on newer firmware that closes 10443; used only when 10443 does not accept a connection | Optional (as 10443) |

Notes:

- Plan for **one telnet (23) connection** per receiver: whether a second
  session connects and gets the events is not yet tested on any model
  ([test](protocol/09-status-diagnostics-and-discovery.md#test-concurrent-telnet-sessions)).
  Connect one integration or program to it at a time. The Denon and
  Marantz AVR Remote app counts too while its tuner screen is open.
- The HEOS (1255) and setup‑config (10443) channels are only opened when the
  receiver advertises the matching capability; both are non‑disruptive.
- Protocol details per receiver generation, a model comparison and test
  procedures are in [docs/protocol](protocol/README.md).

## What you get

Settings are organized into **sub‑devices that mirror the receiver's own setup
menus**, so a large control set stays tidy instead of crowding one page. The main
receiver device keeps the day‑to‑day controls (player, power, source, sound mode,
quick select, input mode, front‑display dimmer); the **Audio**, **Video**,
**Picture**, **Speakers** and **Graphic EQ** sub‑devices each carry their own
part of the setup. **Picture** holds the image‑quality tuning (a coherent group,
split off from Video like the Graphic EQ is split off from Audio); **Video** holds
the output/connectivity settings (video mode, HDMI setup, on‑screen display, TV/4K
signal format). The sub‑devices are in the receiver's room: one without a room
takes it, and when the receiver moves, those that were in its room move along. A
sub‑device you put in another room stays there, and the zone devices (Zone 2,
Zone 3) keep a room of their own. The lists below are grouped by
entity type; each entity lands on the sub‑device its setting belongs to.
Select options follow what the receiver lists in its device description:
an AVR‑X2600H offers no Manual for MultEQ, Video Mode Bypass and the 8K
resolution appear only on receivers that list them (AVR‑X2600H, AVC‑X3700H,
AVC‑A10H), and Input Mode shows eARC only where the receiver has it.

- **Media player** per zone (main + zone 2 where present): power, volume, mute,
  source selection, and (main zone) sound mode. On the main zone, network sources
  show **album art, track/artist/album and play/pause/next transport**, with
  position, repeat and shuffle (via HEOS). Receivers without HEOS (AVR-X2000
  and older) show title, artist, album and cover from their on-screen list and
  take play, pause, stop and skip over telnet. The tuner shows its station or
  frequency and the recalled preset; skip steps through the presets. On any
  other input the player shows the input's name, with the sound mode and the
  signal (decoder and sample rate, such as `DOLBY TRUEHD · 48 kHz`) below it,
  and a source‑aware **input icon** (TV, disc, tuner, Bluetooth, USB, cast, …).
  The main zone's **quick selects** follow the inputs in the source menu, by
  their names; picking one recalls it.
  Play, pause and skip show only while a network source plays (on the tuner,
  skip only). Zone 2 on the main zone's source (or on a network source) shows
  what plays there and takes play, pause and skip too.
- **Zones as a group**: joining zone 2 to the main zone (Home Assistant's
  join, or the group dialog of the media player) sets it to the main zone's
  source, so it follows every change of input. Leaving the group keeps the
  current input without following. Only the main zone leads.
- **Media browser and `play_media`** on every zone: quick selects (zone 2
  its own, numbered), sources and tuner presets (by number: the receiver gives
  no preset names) to pick, each kind with its own icon. On receivers with
  HEOS the main zone also plays any URL and Home Assistant's own media sources
  (text to speech, radio, …), shown on the first level (HEOS `play_stream`,
  not yet tried on a receiver); another zone hears them by joining the main
  zone. Receivers without HEOS take quick selects, sources and presets only.
  A zone in standby is turned on first, also when a source or a sound mode is
  picked.
- **Voice**: "play Kino on the living room receiver" works with Assist. The
  player searches its quick selects, inputs and HEOS favorites by name
  (`media_player.search_media`) and Assist plays the first match.
- **Artwork for inputs**: an input without cover art (TV, Blu-ray, game,
  tuner, …) gets its own picture, a colour and a pictogram per kind of input,
  which the media control card paints as its background.
- **Announcements** on receivers with HEOS (main zone): text to speech and
  other media played with `announce` (as voice assistants and `tts.speak`
  do) plays over HEOS at the announcement volume (the call's
  `extra: {volume: 0.5}`, or the option). Power, input, volume and mute are
  kept before and put back once it has played; the sound mode follows the
  input. Music that played over HEOS before does not resume: HEOS replaces it.
- **HEOS music** on receivers with HEOS (main zone): the browser's HEOS folder
  holds the receiver's music sources (HEOS favorites, local media servers and
  USB, TuneIn, playlists, history, signed-in services), in pages of 50.
  Albums and playlists play as a whole, now, next, at the end or replacing the
  queue (`enqueue` of `play_media`); tracks and stations play alone. As with
  the HEOS integration, `play_media` also takes a favorite or a quick select by
  name or number (`media_content_type: favorite`, `media_content_id: Bayern 3`). The HEOS
  favorites also follow the quick selects in the source menu. Favorites need
  the HEOS system signed in to a HEOS account, which the HEOS app normally has
  done; this integration asks for no account.
  The HEOS folder also holds the **queue**: an item plays from it, the media
  player's clear playlist empties it, and actions read and change it (see
  Actions). The player names the HEOS service it plays from (`app_name`, such
  as TuneIn) and the station of a radio (`media_channel`). A playback error
  HEOS reports shows as a repair until something plays again.
- **Sound mode** as two coupled selects: a genre group (Movie/Music/Game/Pure)
  and the modes within the active group (context aware, includes Auto).
- **Selects**: Dynamic Compression, Dynamic Volume, Reference Level Offset,
  MultEQ, Restorer (Denon; on Marantz M-DAX takes its place), ECO, Front Display dimmer, Video Mode, Picture Mode, HDMI
  Monitor Out, HDMI Audio Out (Amp/TV), HDMI Resolution, Aspect Ratio, Input Mode
  (ARC/eARC/…), Quick Select, Subwoofer Mode (LFE / LFE+Main), Room Size, Volume
  Scale, Volume Limit, Muting Level, a per speaker group crossover frequency (Hz)
  plus a Crossover Speaker Selection (All / Individual) that gates them against a
  single All Crossover,
  a per speaker group size (Large/Small), Front Speaker (A/B/A+B), Amp Assign
  (the amp‑assignment mode — options are read from the receiver per its amp type,
  e.g. 7.1ch + Front B / 9.1ch / Bi‑Amp / Zone2), the amp‑assign Floor Layout and
  Height Speakers (options read from the receiver), and LPF for LFE (80–250 Hz).
  On **Video**: HDMI Power‑Off Control,
  Pass‑Through Source, RC Select, On‑Screen Volume position, Now‑Playing display,
  4K Signal Format, TV Format (NTSC/PAL). On **Picture**: Noise Reduction. Each
  only when the receiver advertises it.
- **Switches**: Main Power, Main Mute, Tone Control, Dynamic EQ, Loudness
  Management, Cinema EQ, Subwoofer, Speaker Virtualizer, Center Spread,
  DTS Neural:X, Low Frequency Containment (LFC), Graphic EQ, Auto Lip Sync,
  All Zone Stereo, and — on **Video** — HDMI Control (CEC), ARC/eARC, TV Audio
  Switching, HDMI Power Saving, Smart Menu, HDMI Pass‑Through, On‑Screen Info.
- **Volume** per zone in dB, settable (−80 dB up to the receiver's maximum or
  the zone's maximum from the options), next to the media player's 0–100 %.
- **Numbers**: Bass, Treble, Subwoofer Level, LFE, Dialog Control, Center Level,
  Audio Delay, Effect Level, Containment Amount, Sleep Timer, per channel
  volume trim and speaker distance (m) for each configured speaker, and — on
  **Picture** — Contrast, Brightness, Saturation, Enhancer.
- **Graphic EQ** (a sub‑device, when the receiver has one): the on/off, a
  speaker‑selection mode (L/R · Each · All), the channel being adjusted, and one
  slider per band (63 Hz … 16 kHz). Selecting a channel loads that channel's own
  curve; the sliders **stage** it and **Apply** writes the whole band block to
  the receiver at once. **Copy Curve** seeds the manual EQ from the reference
  curve, and **Default** resets the channel to flat. See the equalizer card
  recipe below.
- **Sensors** (diagnostic): sample rate, decoder, audio format, input signal,
  mode info and sound mode; and per zone the selected **source**, under the
  name Home Assistant shows.
- **Signal sensors**, on receivers with the setup interface (not the AVR-X2000):
  input and output resolution and HDR, input color space, color depth and FRL
  rate on the Video device, the input signal's channels and the active
  speakers on the Audio device. Input resolution and HDR start enabled; they
  are read at setup, with each poll and a few seconds after an input, power or
  signal change. Not yet confirmed on a receiver.
- **Binary sensors**: telnet connectivity, and on receivers with HEOS whether
  HEOS reports a **firmware update** (asked twice a day; HEOS gives no version,
  so the update is installed from the receiver's menu or the HEOS app).
- **Recover audio** (a button, starts disabled): standby and back on with the
  input it had, for a receiver that has lost its sound.
- **Model-dependent controls**, created only when the receiver answers their
  query at setup (they have no name in its device description): Auto Standby,
  12 V Triggers 1 and 2, Speaker Preset, Bluetooth Transmitter, Dialogue
  Enhancer, Effect Speakers, IMAX (mode, audio settings, high- and low-pass
  filter, subwoofer and its output), Auro-Matic 3D (preset, strength) and
  Auro-3D mode, Dirac Live filter slot, Tactile Transducer (on/off, level,
  low-pass filter), and on Marantz M-DAX, DAC Filter and Illumination. Their
  command formats come from the denonavr library (M-DAX from Marantz's 2019
  command list) and have not been tried on a receiver yet.
- **Buttons**: save a quick select preset (one per preset the receiver reports),
  reset all channel levels, restart the network module. They start disabled.
- **Remote**: the on-screen menu keys (see Actions).
- **Network Music** per zone, a switch for Music Assistant and automations: on turns
  the zone on and to the network input; off turns the zone off only while it
  still plays the network, so it never ends a film. It is on while the zone
  plays the network.
- **Tuner**: the preset (a select, 01–56 or A1–G8) and the FM frequency in
  MHz (a number, to tune directly). While no zone plays the tuner both are
  unknown; choosing a preset or a frequency then turns the main zone on and
  switches it to the tuner first, as the receiver takes tuner commands only
  then. Recalling and tuning have not been tried on a receiver yet.

### Enabled and disabled entities

A receiver offers well over a hundred settings, so only what you change while
listening starts enabled:

- the media players, the sound mode selects and quick select
- the tuner preset and frequency
- tone (bass, treble, tone control), Dynamic EQ, Dynamic Volume, Dynamic
  Compression, MultEQ, Cinema EQ, center level and Dialog Control
- subwoofer on/off and level, All Zone Stereo, sleep timer, front display dimmer
- the trims of the speakers the receiver has configured
- the volume of each zone in dB
- the decoder and connectivity sensors

Everything else starts disabled: speaker setup (distances, crossovers, sizes,
amp assign), video and HDMI setup, picture adjustment, the graphic EQ, volume
limits and scale, the sound mode parameters, and the power and mute switches,
which repeat the media player. The sample rate, input signal, audio format and
mode info sensors also start disabled, because they change with every track and
fill the history, and so do the sound mode and source sensors, which repeat the
media player. Enable any of them on the device page under its entities, or
in the entity's settings. Home Assistant keeps your choice from then on. To
show a whole group at once, use Configure → **Entities** (see Options).

Speaker crossover frequencies are a discrete, non-uniform set, so they are
exposed as a select rather than a stepped number. The current crossover per group
is read over telnet; the selectable frequencies are the fixed Denon
crossover grid (a protocol constant, verified live), the same as every other
enum's values.

### Graphic equalizer card

The graphic EQ is exposed as plain entities (one number per band, plus the
speaker‑selection and channel selects), so any card can drive it. For a proper
fader look, this integration does **not** ship a card (an integration must not);
instead install a mixer/fader card from HACS — for example **`wrodie/mixer-card`**
— and point it at the band numbers. A minimal Lovelace example:

```yaml
type: vertical-stack
cards:
  - type: entities
    entities:
      - switch.avr_x3600h_graphic_eq_enabled
      - select.avr_x3600h_graphic_eq_speaker_selection
      - select.avr_x3600h_graphic_eq_channel
      - button.avr_x3600h_graphic_eq_apply
      - button.avr_x3600h_graphic_eq_copy_curve
      - button.avr_x3600h_graphic_eq_default
  - type: custom:mixer-card
    faders:
      - entity_id: number.avr_x3600h_graphic_eq_63_hz
      - entity_id: number.avr_x3600h_graphic_eq_125_hz
      - entity_id: number.avr_x3600h_graphic_eq_250_hz
      - entity_id: number.avr_x3600h_graphic_eq_500_hz
      - entity_id: number.avr_x3600h_graphic_eq_1_khz
      - entity_id: number.avr_x3600h_graphic_eq_2_khz
      - entity_id: number.avr_x3600h_graphic_eq_4_khz
      - entity_id: number.avr_x3600h_graphic_eq_8_khz
      - entity_id: number.avr_x3600h_graphic_eq_16_khz
```

Pick a channel, adjust the nine faders, then press **Apply** to write that
channel's curve. Editing requires MultEQ **off** and the graphic EQ **enabled**
(both are entities above). The graphic EQ entities start disabled; enable them
on the **Graphic EQ** device first. Entity ids follow your device name — adjust
the prefix to match yours.

## Actions

### Send command (`denonavr_plus.send_command`)

Sends a telnet command to the receiver exactly as given, for settings that
have no entity. Target one media player of the receiver; any zone's player
reaches the same receiver.

| Field | Required | Description |
|---|---|---|
| `command` | yes | The command as in the protocol documents, for example `PSDYNEQ ON` or `MV?`. Printable ASCII, at most 134 characters. |

```yaml
action: denonavr_plus.send_command
target:
  entity_id: media_player.avr_x3600h
data:
  command: PSDYNEQ ON
```

Asked for a response (`response_variable` in a script), the action returns the
lines that start with the command's first two letters and arrive within two
seconds, for example `{"replies": ["MV50", "MVMAX 80"]}` for `MV?`. A change
pushed at the same moment, say from the remote, can be among them.

While telnet is down, a command goes over the web interface instead, as the
media players do; a query cannot, because its answer only comes back over
telnet, so the action reports an error. It also reports one when the receiver
cannot be reached. This replaces `denonavr.get_command` of the official
integration.

### HEOS queue (`denonavr_plus.get_queue`, `remove_from_queue`, `move_queue_item`)

As the HEOS integration's actions: `get_queue` returns the queue (queue id,
title, artist, album, image; the first 100), `remove_from_queue` removes items
by their `queue_ids`, `move_queue_item` moves them to `destination` (from 1).
They work on the main zone of a receiver with HEOS.

```yaml
action: denonavr_plus.get_queue
target:
  entity_id: media_player.avr_x2600h
response_variable: queue
```

### Volume ramp (`denonavr_plus.volume_ramp`)

Moves a zone's volume to a level over a number of seconds, one receiver step
(0.5 dB on most models) at a time, as for a wake-up or a fade-out. When the
time is too short for every step, it takes larger steps, at most four a
second. The action returns at once; the steps follow in the background. A
new ramp, any volume command (also `volume_set`, `volume_up`, `volume_down`)
or switching the zone off stops a running ramp; a duration of 0 sets the
volume at once. The zone has to be on; otherwise the action reports that.
The level is on the same scale as `media_player.volume_set` and respects the
zone's maximum volume from the options.

| Field | Required | Description |
|---|---|---|
| `volume_level` | yes | The level to end at, 0 to 1. |
| `duration` | no | Seconds, 0 to 3600; 10 if left out. |

```yaml
action: denonavr_plus.volume_ramp
target:
  entity_id: media_player.avr_x2600h
data:
  volume_level: 0.45
  duration: 120
```

### Snapshot and restore (`denonavr_plus.snapshot`, `denonavr_plus.restore`)

`snapshot` keeps what a zone does now (power, input, volume, mute);
`restore` puts it back, sending only what differs. Announcements do this on
their own; the actions are for your own sequences, such as a doorbell that
switches the receiver to a camera input for a while. `restore` without a
snapshot reports that instead of doing nothing. The snapshot lives until
Home Assistant restarts.

```yaml
action: denonavr_plus.snapshot
target:
  entity_id: media_player.avr_x2600h
```

### Store tuner preset (`denonavr_plus.store_tuner_preset`)

Stores the station the tuner plays as one of the receiver's 56 presets. The
tuner must be the source of a zone that is on; otherwise the action reports
that instead of sending a command the receiver would ignore. On older
receivers the presets are named A1 to G8; preset 9 is B1 there.

| Field | Required | Description |
|---|---|---|
| `preset` | yes | 1 to 56. |

```yaml
action: denonavr_plus.store_tuner_preset
target:
  entity_id: media_player.avr_x2600h
data:
  preset: 5
```

### Remote (`remote.send_command`)

The receiver's remote entity drives its on-screen menu. Commands: `up`,
`down`, `left`, `right`, `enter`, `back`, `option`, `info`, `channel_level`
(the channel level menu), `setup_menu_on`, `setup_menu_off`. `num_repeats` and
`delay_secs` work as usual; turning the remote on or off switches the main zone.

```yaml
action: remote.send_command
target:
  entity_id: remote.avr_x3600h_remote
data:
  command: [setup_menu_on, down, enter]
```

## Device trigger: turn on requested

With network standby off, a receiver that is off does not answer on the network
and cannot be switched on from Home Assistant. For that case the receiver device
offers the trigger **Turn on requested while the receiver does not answer**.
Build an automation with it that switches the receiver on another way, for
example with an infrared blaster or through HDMI-CEC from the TV.

As long as such an automation exists, the receiver's media players stay usable
while it does not answer and show it as off; turning one on runs the automation.
Once the receiver answers again, turning on goes over the network as usual.

```yaml
triggers:
  - trigger: device
    domain: denonavr_plus
    device_id: <the receiver's device>
    type: turn_on
actions:
  - action: remote.send_command
    target:
      entity_id: remote.living_room_ir
    data:
      command: denon_power_on
```

After a restart of Home Assistant while the receiver does not answer, its media
players are set up from what the integration read the last time it reached the
receiver, so turning on works then too. The other entities stay unavailable until
the receiver answers; then the integration sets itself up in full by itself. A
receiver that has never been reached since it was added has to be on for setup.

## Blueprints and an example dashboard

How to build on them: [user guide, sections 4 and 6](USER_GUIDE.md#4-everyday-use).

Two automation blueprints, to import in Home Assistant (Settings → Automations
& scenes → Blueprints → Import, with the file's GitHub address):

- [TV on, receiver on](../blueprints/automation/denonavr_plus/tv_on.yaml): the
  TV turns on, the receiver's zone follows with the TV's input and, if set, a
  sound mode and a volume; the TV turns off, the zone turns off, but only
  while it still plays the TV's input.
- [Night mode](../blueprints/automation/denonavr_plus/night_mode.yaml): between
  two times the volume stays below a limit and, if set, Dynamic Volume
  switches to a night setting and back.

[An example dashboard view](../examples/dashboard.yaml) shows what the standard
cards do with this integration: the media control card with the cover or the
input's artwork, tiles with source, sound mode and volume, the quick selects as
buttons, the tuner and zone 2. Paste it into a dashboard's raw configuration
editor and replace `avr_x2600h` with your receiver's entity ids.

## With Music Assistant and the HEOS integration

The receiver has one HEOS player. Three integrations can reach it; each has
its own job, so nothing is controlled twice:

| Job | Where |
|---|---|
| Power, input, volume, sound mode, zones, tuner, quick selects | this integration |
| Now playing, transport, URLs, text to speech and announcements on the receiver | this integration (over HEOS) |
| HEOS favorites, local servers, TuneIn, playlists in the browser | this integration (over HEOS) |
| Your music library, streaming services, queues, playing on many speakers | Music Assistant (its HEOS or AirPlay provider) |
| Grouping the receiver with other HEOS speakers in the home | the HEOS integration of Home Assistant |

With **Music Assistant**, use the Network Music switch of a zone as the power control
of the Music Assistant player: Music Assistant switches it on to play (zone on,
network input) and off when it stops, without ending a film on another input.
Volume can stay on this integration's media player. Zone 2 plays the network
by joining the main zone (Home Assistant's join); whether zone 2 can play a
stream of its own is not yet tried on a receiver.

The **HEOS integration** is only needed to group the receiver with other HEOS
speakers. It may run alongside: HEOS takes several connections. Its media
player then shows the same music a second time; hide one of them if that is
confusing. Without other HEOS speakers it can be removed.

## Notes and limitations

Symptoms and what to do about them: [user guide, troubleshooting](USER_GUIDE.md#11-troubleshooting).
Which receivers work, and what differs by generation and brand:
[Compatibility](COMPATIBILITY.md).

- Some parameters only apply to certain signals or sound modes (for example
  Dynamic Compression needs a Dolby/DTS bitstream, Video Mode needs an active
  video signal, Equalizer settings can not be altered when MultEQ is not Off).
  The receiver rejects changes that do not apply to the current
  context; this is expected Denon behavior, not an integration bug.
- Speaker distances and crossovers are supported over telnet (read/write). Other
  advanced one time setup settings that the receiver exposes only through its web
  UI's configuration API (full speaker layout, HDMI Control/CEC, per input
  assignment, some zone defaults) are not implemented.
- Room correction calibration remains a job for the receiver's own setup tooling.
