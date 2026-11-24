# Network media and services

## Services

| Service | AVR-X2000 | AVR-X2600H |
|---|---|---|
| Internet radio, media server (DLNA), USB | Sourced; source tokens `IRADIO`, `SERVER`, `USB/IPOD` Documented | Sourced (HEOS) |
| AirPlay | AirPlay (Sourced) | AirPlay 2 (Sourced) |
| Bluetooth | Absent: no `BT` source in the protocol and none in the recorded device description | Sourced |
| Spotify | `SISPOTIFY`, North America and Europe (Documented) | Spotify Connect (Sourced); not controllable through the HEOS CLI |
| Pandora, SiriusXM | North America only (Documented) | through HEOS, region-dependent |
| Other streaming services | — | through HEOS (TuneIn, Deezer, Tidal, Amazon Music and others, per the HEOS CLI service table) |
| Voice assistants | — | Alexa, Google Assistant, Siri via AirPlay (Sourced) |

## Transport keys (`NS…`)

Work for the network, USB and (2015 onward) Bluetooth sources on both
generations. Each is a one-shot command without a reply.

| Command | Key | Command | Key |
|---|---|---|---|
| `NS90`–`NS93` | Cursor up, down, left, right | `NS9D`, `NS9E` | Skip forward, back |
| `NS94` | Enter (play/pause) | `NS9F`, `NS9G`, `NS9Z` | Search forward, back, stop |
| `NS9A` | Play | `NS9H`, `NS9I`, `NS9J` | Repeat one, all, off |
| `NS9B` | Pause | `NS9K`, `NS9M` | Random on, off |
| `NS9C` | Stop | `NSRPT`, `NSRND` | Repeat, random (toggle; includes AirPlay) |
| `NS9X`, `NS9Y` | Page next, previous | `NS9W` | iPod browse/remote mode |

HEOS-era receivers expose the same functions, with state, through the HEOS
CLI ([HEOS](05-heos.md)). This integration uses HEOS there.

## On-screen text (`NSA`, `NSE`)

`NSA` (ASCII) and `NSE` (UTF-8) return the network screen as nine lines,
`NSE0` to `NSE8`. Line 0 is the title ("Now Playing …"); list lines
start with one flag byte (bit 1 playable, bit 2 folder, bit 4 cursor, bit 7
picture), followed by up to 95 bytes of text. A NUL ends the text; bytes
after it are padding.

On the "now playing" screen the lines carry title, artist, album and time.
For the AVR-X2000 this, and `/goform/formNetAudio_StatusXml.xml` on port 80,
are the only sources of now-playing data (Documented; To test which lines
hold what). On HEOS-era firmware the goform now-playing pages answer 403,
which is why this integration uses HEOS.

## Presets and favorites

| Command | Purpose | AVR-X2000 | 2015 doc |
|---|---|---|---|
| `NSB<nn>` | Recall network preset | 00–55 | 00–35 |
| `NSC<nn>` | Store network preset | 00–55 | 00–35, reply `NSCOK` |
| `NSH` | Preset names, one `NSH<nn><name>` line each | Documented | Documented |
| `NSFV MEM` | Add to favorites | — | Documented |
| `ZMFAVORITE1`–`3`, `… MEMORY` | Favorite stations (also for Zone 2) | Documented | — |
| `SIIRP`, `SIFVP` | Switch to network and start the last internet radio / favorites | Documented | Documented |

On HEOS-era receivers, HEOS Favorites are the presets
(`browse/play_preset`).

## Tuner

Only accepted while the source is `TUNER`.

| Command | Purpose | Notes |
|---|---|---|
| `TFAN<6 digits>`, `TFANUP`, `TFANDOWN` | Frequency | Below `050000` FM in 10 kHz units (`008750` = 87.50 MHz); above, AM in kHz |
| `TPAN<nn>`, `TPANUP`, `TPANDOWN`, `TPANOFF` | Preset | `A1`–`G8` or `01`–`56`, see [command reference](03-command-reference.md#differences-between-protocol-versions) |
| `TPANMEM`, `TPANMEM<nn>` | Store preset | |
| `TMANAM`, `TMANFM`, `TMANAUTO`, `TMANMANUAL` | Band, tuning mode | AVR-X2000: band switching North America only |
| `TFANNAME?` | RDS station name | 2015, Europe and Asia-Pacific |
| `TFHD…`, `TPHD…`, `TMHD…`, `HD?` | HD Radio | North America; none of the four 2015 models |

DAB: an AVR-X2600H DAB variant exists for Europe. No DAB command is in any
document available here. Unverified.

The integration reads the frequency, station name and preset (`TFAN?`,
`TFANNAME?`, `TPAN?`) while the source is `TUNER`, and recalls and steps
presets with `TPAN`. Frequency entry, band and tuning mode are not used.
