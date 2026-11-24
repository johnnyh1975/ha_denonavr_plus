# Receivers before 2013

Denon and Marantz network receivers of 2010 to 2012 (the manufacturer's
model years FY10 to FY12) do not describe themselves: recorded AVR-1912,
AVR-2312CI, AVR-3311CI, AVR-3312 and AVR-4810 answer `Deviceinfo.xml` with
an error page (`tests/fixtures/denonavr/`). This integration finds the HTTP
port and the receiver's functions in that file, so it cannot set these
receivers up today.

What is known about them comes from their protocol documents (AVR-1912 and
AVR-2112CI v7.4.0, see [References](../references.md); the recorded
AVR-1713 and AVR-3313 already report level 0210 and belong to the next
generation)
and from description files that Denon's older Remote App (1.1.9) ships for
13 of them. The app no longer uses those files (its support for these
models is switched off), but they are the only machine-readable source on
zones, inputs and sound modes of these receivers. They are app data, not
receiver answers (**App**); two errors are visible in them (below).

## How the app builds a description

One base file per brand, then one per model, then one per region (USA,
Europe, Japan, China), in the format of `Deviceinfo.xml`. Inputs are merged
by function name, and an input with an empty path is removed; sound modes
are appended; empty elements change nothing; the model's zone count cuts
the zone list.

Common to all 13 AV receivers: power, volume and mute per zone, the cursor
menu in the main zone, favourites, and AM/FM tuner. The files give 99 tuner
presets; the AVR-1912/2112CI document gives 56 (`A1`–`G8`), which is the
better source.

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
A100, AV7005, SR7005 and SR6006; several models add Sirius.

Base sound modes: Denon DIRECT, STEREO, PURE DIRECT, DOLBY DIGITAL, DTS
SURROUND, MULTI CH STEREO, ROCK ARENA, JAZZ CLUB, MONO MOVIE, VIDEO GAME,
MATRIX, VIRTUAL; Marantz DIRECT, STEREO, PURE DIRECT, DOLBY DIGITAL, DTS
SURROUND, MULTI CH STEREO, AUTO.

## Input tokens

The app sends inputs as `SI<token>` (main zone) and `Z2<token>`; most
tokens equal the input name. The exceptions:

| Input | Token | Note |
|---|---|---|
| GAME1, GAME2 | `GAME`, `GAME2` | Models with two game inputs; the AVR-1912/2112CI document confirms `SIGAME` and `SIGAME2` |
| HD Radio | `HDRADIO` | |
| NET/USB | `NET/USB` | |
| USB, iPod | `USB/IPOD` | |
| Internet Radio, Media Server, Favorites | `IRADIO`, `SERVER`, `FAVORITES` | |
| M-XPort | `M-XPORT` | Marantz |
| AUX on the AV7005 and SR7005 | `AUX` | The app names it differently inside |

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

- Denon: restorer Off, Mode 1, Mode 2, Mode 3.
- Marantz NR1602, SR5006, SR6006 and every Marantz receiver from level 200
  on: M-DAX with its own command, steps High, Mid, Low, Off.
- Marantz AV7005 and SR7005: M-DAX through the restorer; the app maps
  Mode 1 to Low, Mode 2 to Mid and Mode 3 to High.

## Identification without Deviceinfo.xml

The app identifies these receivers through
`/goform/formiPhoneAppDeviceSearch.xml` (element `Model`) and
`/goform/formiPhoneAppControlJudge.xml` (element `iPhoneControl`, which
tells whether and with which protocol year the receiver can be
controlled). The AV7005 and SR7005 put a `*` before the model name in
`description.xml`, like later receivers on port 80 do in `Deviceinfo.xml`.

Tuner frequencies in the app's preset data: below 200 FM in MHz, from 200
AM in kHz, `999999` DAB.

## Errors in the app data

- The European files list Sirius, which a comment in the same files limits
  to the USA.
- An older configuration file in the app gives the AVR-2112 three zones,
  the model file two. The AVR-2112CI document or a `Z3?` query would
  settle it.

## What support would need

A static description per model from these data (zones, inputs per zone,
sound modes, Quick Select count, restorer or M-DAX, tuner type per region),
setup without `Deviceinfo.xml` (by `description.xml` or telnet), and the
2011 encodings of their protocol documents (volume minimum `99`, Dynamic
Volume `NGT`/`EVE`/`DAY`, restorer `MODE1`–`3`). None of the recorded
receivers of this generation is in use by a tester of this integration.
