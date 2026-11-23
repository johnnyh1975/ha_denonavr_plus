# Denon AVR Plus

[![Version](https://img.shields.io/badge/Version-0.2.0-brightgreen.svg)](https://github.com/johnnyh1975/ha_denonavr_plus/releases)
[![HA Version](https://img.shields.io/badge/HA-2026.8%2B-blue.svg)](https://www.home-assistant.io/)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Local Push](https://img.shields.io/badge/IoT%20Class-Local%20Push-green.svg)](https://www.home-assistant.io/blog/2016/02/12/classifying-the-internet-of-things/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Hassfest](https://github.com/johnnyh1975/ha_denonavr_plus/actions/workflows/hassfest.yml/badge.svg)](https://github.com/johnnyh1975/ha_denonavr_plus/actions/workflows/hassfest.yml)
[![HACS Validation](https://github.com/johnnyh1975/ha_denonavr_plus/actions/workflows/hacs.yml/badge.svg)](https://github.com/johnnyh1975/ha_denonavr_plus/actions/workflows/hacs.yml)
[![Typing](https://github.com/johnnyh1975/ha_denonavr_plus/actions/workflows/typing.yml/badge.svg)](https://github.com/johnnyh1975/ha_denonavr_plus/actions/workflows/typing.yml)

Local-push Home Assistant integration for Denon and Marantz AV receivers and
preamps, with full control of zones, sources, sound modes, Audyssey, graphic EQ
and speaker setup.

Domain: `denonavr_plus`. It can be installed alongside other Denon integrations,
such as the official one (`denonavr`), but only one of them should be connected
to a receiver at a time: many receivers accept only a single telnet session.

## Supported devices

Network-connected receivers of the HEOS generation (2016 and later).

| Brand   | Series                    | Status |
|---------|---------------------------|--------|
| Denon   | AVR-X                     | Confirmed: AVR-X3600H |
| Denon   | AVR-S, AVC-X              | Expected, not yet confirmed |
| Marantz | SR, NR, Cinema, AV        | Expected, not yet confirmed; add by IP address (automatic discovery is planned) |

Receivers from before 2016 are not supported yet; support is planned.

Own one of the unconfirmed models? Please open an issue with your model,
firmware version and the integration's diagnostics file (Settings → Devices &
Services → Denon AVR Plus → ⋮ → Download diagnostics), so it can be added to
the list.

## Features

- **Own, dependency free client.** Minimal external dependency.
- **Everything discovered from the device.** Model name, MAC, zones, input
  sources (and their renamed names + visibility), speaker configuration and
  channels, sound modes and their genre groups, quick select names, feature
  availability, control ranges and option labels are all read from the receiver
  at runtime. The only fixed data is the Denon wire grammar.
- **Telnet, HTTP** Telnet (port 23) provides the full state, real time push and
  the bulk of control; HTTP (port 8080, or 80 on receivers before 2016)
  provides discovery and a periodic reconciliation poll. When telnet is down,
  the media players stay usable over HTTP: power, volume, mute and source are
  polled every 10 seconds and commands go over HTTP; the other settings are
  unavailable until telnet is back. A few settings that have no telnet command (graphic‑EQ
  bands, HDMI/OSD/format setup, amp assign) are read and written over the setup
  config API (HTTPS 10443); everything else is telnet.
- **Stability** Auto reconnect with backoff, keepalive probe, teardown on
  unload. A command queue that sends your commands before pending queries,
  waits for each reply (paced by the receiver's measured response time) and
  is cleared when the link drops, so stale commands are never sent later.
- **Eight languages, English entity ids.** Texts and entity names in English,
  German, French, Italian, Spanish, Portuguese, Dutch and Polish. Entity ids
  are always built from the English names (for example
  `number.avr_x3600h_speakers_center_trim`), whatever language Home Assistant
  runs in, so automations and examples work in every installation. The names
  of the sub‑devices (Audio, Video, Picture, Speakers, Graphic EQ) stay
  English because they are part of every entity id. Sources, sound modes
  and option labels are shown as the receiver reports them.

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=johnnyh1975&repository=ha_denonavr_plus&category=integration)

1. Install [HACS](https://hacs.xyz/) if you haven't already
2. HACS → Integrations → three dots → Custom repositories.
3. Add this repository as a [custom integration repository](https://hacs.xyz/docs/faq/custom_repositories), category "Integration".
4. Install integration "Denon AVR Plus".
5. Restart Home Assistant.

### Manual
Copy the `denonavr_plus` folder into `<config>/custom_components/` and restart Home Assistant.

## Configuration

Settings → Devices & Services → Add Integration → **Denon AVR Plus**, then enter the
receiver's **IP address**. The name and all capabilities are read from the device
automatically; no other input is required.

To change the address later, open the integration's menu (three dots) and choose
**Reconfigure**. The new address must belong to the same receiver.

### Options

**Configure** on the integration entry opens a menu: volume, zones, sources
and sound modes. Nothing in it changes the receiver; it changes what Home
Assistant shows and does.

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

**Zones**: which additional zones (zone 2, zone 3) get entities. A zone that
is switched off loses its media player, volume and source sensor; switching it
on again brings them back.

**Sources** (needs the receiver connected, it reads its sources): per zone,
which sources the media player offers, then a name for each in Home
Assistant. The names and hidden inputs set on the receiver are already taken
over; this narrows down and renames on top. Selecting all keeps sources the
receiver gains later. Renamed sources can still be selected by their receiver
name, so older automations keep working.

**Sound modes** (needs the receiver connected): which sound modes the main
zone's media player offers, and their names. The receiver only offers the
modes that fit the current signal. The current source and sound mode are
always shown, also when they are not selected. The sound mode selects keep
the receiver's full list.

## Removal

1. Settings → Devices & Services → **Denon AVR Plus** → three dots on the receiver
   entry → **Delete**. This removes the device and all its entities. The settings
   stored on the receiver itself are not changed.
2. If you installed through HACS: HACS → **Denon AVR Plus** → three dots →
   **Remove**. For a manual installation, delete
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

Notes:

- The receiver accepts **multiple concurrent telnet (23) connections** and
  broadcasts events to all of them, so this integration coexists with the Denon
  app and other controllers.
- The HEOS (1255) and setup‑config (10443) channels are only opened when the
  receiver advertises the matching capability; both are non‑disruptive.
- Protocol details per receiver generation, a model comparison and test
  procedures are in [docs/protocol](docs/protocol/README.md).

## What you get

Settings are organized into **sub‑devices that mirror the receiver's own setup
menus**, so a large control set stays tidy instead of crowding one page. The main
receiver device keeps the day‑to‑day controls (player, power, source, sound mode,
quick select, input mode, front‑display dimmer); the **Audio**, **Video**,
**Picture**, **Speakers** and **Graphic EQ** sub‑devices each carry their own
part of the setup. **Picture** holds the image‑quality tuning (a coherent group,
split off from Video like the Graphic EQ is split off from Audio); **Video** holds
the output/connectivity settings (video mode, HDMI setup, on‑screen display, TV/4K
signal format). The lists below are grouped by entity type; each entity lands on
the sub‑device its setting belongs to.

- **Media player** per zone (main + zone 2 where present): power, volume, mute,
  source selection, and (main zone) sound mode. On the main zone, network sources
  show **album art, track/artist/album and play/pause/next transport** (via HEOS);
  otherwise the player carries a source‑aware **input icon** (TV, disc, tuner,
  Bluetooth, USB, cast, …).
- **Sound mode** as two coupled selects: a genre group (Movie/Music/Game/Pure)
  and the modes within the active group (context aware, includes Auto).
- **Selects**: Dynamic Compression, Dynamic Volume, Reference Level Offset,
  MultEQ, Restorer, ECO, Front Display dimmer, Video Mode, Picture Mode, HDMI
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
- **Numbers**: Bass, Treble, Subwoofer Level, LFE, Dialog Control, Center Level
  Adjust, Audio Delay, Effect Level, Containment Amount, Sleep Timer, per channel
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
- **Binary sensor**: telnet connectivity.

### Enabled and disabled entities

A receiver offers well over a hundred settings, so only what you change while
listening starts enabled:

- the media players, the sound mode selects and quick select
- tone (bass, treble, tone control), Dynamic EQ, Dynamic Volume, Dynamic
  Compression, MultEQ, Cinema EQ, dialogue level and Dialog Control
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
in the entity's settings. Home Assistant keeps your choice from then on.

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

## Notes and limitations

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

## Testing

Protocol behavior was verified live by the original author against the
following.

| Component                    | Version |
|------------------------------|---------|
| Receiver                     | Denon AVR-X3600H |
| Receiver hardware generation | `avr-x-2016` |
| Receiver firmware            | 3.88.614 |
| Home Assistant Core          | 2026.7.0 |
| Python                       | 3.13 |

Other Denon/Marantz HEOS generation receivers should work as the integration
adapts to whatever the device reports, but only this combination has been
exercised end to end.

## Contributing

Bug reports and pull requests are welcome. Read [AGENTS.md](AGENTS.md)
first: it lists the checks every change has to pass
(`bash scripts/check_all.sh`) and the rules for tests, private data and
protocol facts. For a receiver that is not fully supported, the
diagnostics file is what makes support possible; the issue form asks
for it. Where the integration cannot be set up at all, or to find out what a
model can do, run `python3 tools/probe_receiver.py <address>`: it asks the
receiver every documented query, changes nothing, and writes a summary of
its capabilities plus the raw answers with addresses and serial numbers
replaced.

## Origin

Denon AVR Plus is based on **[Denon AVR](https://github.com/LaserGuruGuy/denon_avr)
by [LaserGuruGuy](https://github.com/LaserGuruGuy)** (MIT licence), version 1.1.2.
The full commit history of that project is preserved in this repository. Its
copyright notice is kept in [LICENSE](LICENSE). This project is not affiliated
with or endorsed by LaserGuruGuy; please report problems with Denon AVR Plus
here, not upstream.

## Credits

With thanks to **Denon** for publicly documenting the AV Receiver control
protocol, which made this independent implementation possible. Denon's
official documentation is available at <https://manuals.denon.com/>.

For the official, first‑party Home Assistant integration for these receivers,
see **Denon AVR Network Receivers**:
<https://www.home-assistant.io/integrations/denonavr/>.

## Trademarks and acknowledgements

This is an independent, unofficial project. It is **not affiliated with, endorsed
by, sponsored by, or supported by** any of the companies below. All product and
company names, logos, and brands are the property of their respective owners and
are used here for identification purposes only (nominative use); their use does
not imply any affiliation or endorsement.

- **Denon**, **Marantz**, and **HEOS** are trademarks of D&M Holdings Inc. —
  <https://www.denon.com>
- **Audyssey**, **Audyssey MultEQ**, **MultEQ XT32**, **Dynamic EQ**,
  **Dynamic Volume**, and **LFC (Low Frequency Containment)** are trademarks of
  Audyssey Laboratories, Inc. — <https://audyssey.com>
- **Dolby**, **Dolby Audio**, **Dolby Surround**, and **Dolby Atmos** are
  trademarks of Dolby Laboratories, Inc. — <https://www.dolby.com>
- **DTS**, **DTS Neural:X**, and **DTS Virtual:X** are trademarks of DTS, Inc.,
  a Xperi company — <https://dts.com>
- **HDMI**, the HDMI logo, and **High‑Definition Multimedia Interface** are
  trademarks of HDMI Licensing Administrator, Inc. — <https://www.hdmi.org>
- **Home Assistant** and the Home Assistant logo are trademarks of the Open Home
  Foundation — <https://www.openhomefoundation.org>
- **HACS** (Home Assistant Community Store) — <https://hacs.xyz>

Any other product or company names mentioned are the trademarks of their
respective owners.

