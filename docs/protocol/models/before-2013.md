# Receivers before 2013

Denon and Marantz network receivers of 2010 to 2012 (the manufacturer's
model years FY10 to FY12) do not describe themselves: recorded AVR-1912,
AVR-2312CI, AVR-3311CI, AVR-3312 and AVR-4810 answer `Deviceinfo.xml` with
an error page (`tests/fixtures/denonavr/`). This integration finds the HTTP
port and the receiver's functions in that file, so it cannot set these
receivers up today; setup recognises the error page and says so.

What is known about them comes from their protocol documents (AVR-1912 and
AVR-2112CI v7.4.0, see [References](../references.md); the recorded
AVR-1713 and AVR-3313 already report level 0210 and belong to the next
generation)
and from description files that the older Denon and Marantz Remote App
(1.1.9, one app under two names) ships for 13 of them. The app no longer
uses those files: a receiver reporting level 100 is hidden, and a list of
17 model names (these 13 receivers and the systems DNP-720, RCD-N7,
M-CR603 and NA7004) is excluded. Still, they are the only machine-readable
source on zones, inputs and sound modes of these receivers. They are app data, not
receiver answers (**App**); two errors are visible in them (below).

## How the app builds a description

One base file per brand, then one per model, then one per region (USA,
Europe, Japan, China), in the format of `Deviceinfo.xml`. Inputs are merged
by function name, and an input with an empty path is removed; sound modes
are appended; an empty element clears the value (it does not keep the
base file's value); the model's zone count cuts the zone list.

Common to all 13 AV receivers: power, volume and mute per zone, the cursor
menu in the main zone, favourites (up to 50), and AM/FM tuner. Zone 2 and
Zone 3 also offer `SOURCE` (follow the main zone, `Z2SOURCE`). The files
have no tone control, sleep timer, all-zone power or mute, and no volume
step (the app then uses whole steps; the protocol documents give the step).
Dock control and Quick Select are in the Denon files only. The files give
99 tuner presets; the AVR-1912/2112CI document gives `A1`–`G8` (56), which
is the better source.

## Models

Inputs as the app names them for Europe; Zone 3, where present, has the
inputs of Zone 2.

| Model | Brand | Zones | Main zone inputs | Not in Zone 2 | Quick Select | Restorer or M-DAX | Extra sound modes |
|---|---|---|---|---|---|---|---|
| AVR-991 | Denon | 2 | DVD, SAT/CBL, DOCK, V.AUX, TUNER, BD, TV, DVR, GAME, PHONO, CD, NET/USB | GAME | 3 | Restorer | – |
| AVR-1912 | Denon | 2 | DVD, SAT/CBL, DOCK, V.AUX, TUNER, BD, TV, GAME1, GAME2, CD, NET/USB | TV, GAME1, GAME2 | 4 | Restorer | – |
| AVR-2112 | Denon | 2 (see below) | as AVR-1912 | TV, GAME1, GAME2 | 4 | Restorer | – |
| AVR-2312 | Denon | 2 | as AVR-1912, plus DVR | TV, GAME1, GAME2 | 4 | Restorer | – |
| AVR-3311 | Denon | 3 | DVD, SAT/CBL, DOCK, V.AUX, TUNER, BD, TV, DVR, GAME, PHONO, CD, NET/USB | GAME | 3 | Restorer | – |
| AVR-3312 | Denon | 3 | as AVR-3311 with GAME1 and GAME2 | – | 4 | Restorer | – |
| AVR-4311 | Denon | 3 | as AVR-3311 | GAME | 3 | Restorer | WIDE SCREEN, CLASSIC CONCERT, SUPER STADIUM |
| AVR-A100 | Denon | 3 | as AVR-3311 | GAME | 3 | Restorer | WIDE SCREEN, CLASSIC CONCERT, SUPER STADIUM |
| NR1602 | Marantz | 2 | M-XPort, TUNER, BD, DVD, TV, CD, SAT, GAME, NET/USB | DVD, TV, GAME | – | M-DAX | VIRTUAL |
| SR5006 | Marantz | 2 | M-XPort, TUNER, BD, DVD, TV, CD, SAT, VCR, GAME, AUX, NET/USB | TV, GAME | – | M-DAX | VIRTUAL |
| SR6006 | Marantz | 3 | M-XPort, TUNER, BD, DVD, TV, CD, SAT, VCR, GAME, AUX1, AUX2, PHONO, NET/USB | – | – | M-DAX | VIRTUAL |
| SR7005 | Marantz | 3 | M-XPort, CDR, TUNER, BD, DVD, TV, CD, SAT, VCR, GAME, AUX, PHONO, NET/USB | GAME | – | M-DAX as restorer | NEURAL, DOLBY VS |
| AV7005 | Marantz | 3 | as SR7005 | GAME | – | M-DAX as restorer | NEURAL, DOLBY VS |

In the USA, HD Radio replaces the tuner input on the AVR-3311, 3312, 4311,
A100, AV7005, SR7005 and SR6006 (with multicast on all of them but the
3312 and SR6006); the AVR-991, 3311, 4311, A100, AV7005 and SR7005 add
Sirius.

Base sound modes: Denon DIRECT, STEREO, PURE DIRECT, DOLBY DIGITAL, DTS
SURROUND, MULTI CH STEREO, ROCK ARENA, JAZZ CLUB, MONO MOVIE, VIDEO GAME,
MATRIX, VIRTUAL; Marantz DIRECT, STEREO, PURE DIRECT, DOLBY DIGITAL, DTS
SURROUND, MULTI CH STEREO, AUTO.

## Input tokens

The app sends inputs as `SI<token>` (main zone), `Z2<token>` and
`Z3<token>`; most tokens equal the input name. Its translation table holds
for every AV receiver it knows, not just these; the exceptions:

| Input | Token | Note |
|---|---|---|
| GAME1, GAME2 | `GAME`, `GAME2` | Models with two game inputs; the AVR-1912/2112CI document confirms `SIGAME` and `SIGAME2` |
| AUX | `AUX1` | SR5006, whose files name the input `AUX` |
| AUX on the AV7005 and SR7005 | `AUX` | The app names it differently inside |
| TV AUDIO, CBL/SAT, Blu-ray, Media Player, NETWORK | `TV`, `SAT/CBL`, `BD`, `MPLAY`, `NET` | Later input names; the AVR-1713 document gives the same tokens |
| HD Radio | `HDRADIO` | |
| NET/USB | `NET/USB` | Reported as `USB/iPod` at level 100 |
| USB, iPod | `USB/IPOD` | |
| Internet Radio, Media Server, Favorites, Last.fm | `IRADIO`, `SERVER`, `FAVORITES`, `LASTFM` | |
| SiriusXM, Spotify, Bluetooth | `SIRIUSXM`, `SPOTIFY`, `BT` | |
| M-XPort | `M-XPORT` | Marantz |
| SAT, VCR, CDR | unchanged | Marantz |

## Sound modes: reported name and command

Over the legacy HTTP status these receivers report a sound mode in a form
of their own; the command is the telnet token.

| Reported | Command |
|---|---|
| `Direct`, `Stereo`, `Pure_Direct` | `MSDIRECT`, `MSSTEREO`, `MSPURE DIRECT` |
| `Standard(Dolby)`, `Standard(DTS)` | `MSDOLBY DIGITAL`, `MSDTS SURROUND` |
| `Multi_CH_Stereo` | `MSMCH STEREO` |
| `Wide_Screen`, `Rock_Arena`, `Jazz_Club`, `Classic_Concert`, `Mono_Movie`, `Video_Game`, `Super_Stadium` | `MSWIDE SCREEN`, `MSROCK ARENA` and so on |
| `Matrix`, `Virtual`, `Neural` | `MSMATRIX`, `MSVIRTUAL`, `MSNEURAL` |
| `Dolby_Vs` | `MSVIRTUAL` |
| `Auto`, `Movie`, `Music`, `Game` | `MSAUTO`, `MSMOVIE`, `MSMUSIC`, `MSGAME` |

## Restorer and M-DAX

The app sends both through `formiPhoneAppDirect.xml`:

- Denon: restorer `PSRSTR MODE1`, `MODE2`, `MODE3`, `OFF`.
- Marantz NR1602, SR5006, SR6006 and every Marantz receiver above level
  100: M-DAX as `PSMDAX HI`, `MID`, `LOW`, `OFF`, as in Marantz's 2019
  document. `PSMDA` is for the NA7004 network player only.
- Marantz AV7005 and SR7005: M-DAX through the restorer, `PSRSTR MODE1`–`3`
  or `OFF`.

The app pairs Mode 1 with Low and Mode 3 with High. Denon's 2020 command
list pairs them the other way (`LOW` = Mode 3, `HI` = Mode 1); which one the
receivers follow is open.

## Identification without Deviceinfo.xml

The app identifies these receivers through
`/goform/formiPhoneAppDeviceSearch.xml` (element `Model`) and
`/goform/formiPhoneAppControlJudge.xml` (element `iPhoneControl`; the app
has constants for not controllable, 2011 models and 2012 models, but that
the element carries exactly these values is not shown). The AV7005 and SR7005 put a `*` before the model name in
`description.xml`, like later receivers on port 80 do in `Deviceinfo.xml`.

Tuner presets: the app reads them from `formiPhoneAppTunerPreset.xml`, for
receivers of every generation. The field `param` holds the frequency in its
last six digits: when the first four are below 200 it is FM in MHz
(`008750` = 87.50 MHz), otherwise AM in kHz; `999999` marks a DAB preset.
The AVR-1912/2112CI document gives the same encoding for `TFAN` (limit
`050000`).

## Errors in the app data

- The European files list Sirius, which a comment in the same files limits
  to the USA.
- An older configuration file in the app, which no code reads any more,
  gives the AVR-2112 three zones, the model file two. The AVR-2112CI
  document or a `Z3?` query would settle it.

The same file marks network presets (`NSP1`–`NSP3`, `NSP<n> MEM`) for the
AVR-1912, 2112, 2312, 3312, SR6006, SR5006 and NR1602; the AVR-1912/2112CI
document lists the commands (page 40). The app holds the commands but never
enables them, so no app sends them.

## What support would need

A static description per model from these data (zones, inputs per zone,
sound modes, Quick Select count, restorer or M-DAX, tuner type per region),
setup without `Deviceinfo.xml` (by `description.xml` or telnet), and the
2011 encodings of their protocol documents (volume minimum `99`, Dynamic
Volume `NGT`/`EVE`/`DAY`, restorer `MODE1`–`3`). None of the recorded
receivers of this generation is in use by a tester of this integration.
