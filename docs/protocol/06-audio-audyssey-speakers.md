# Audio, Audyssey and speakers

Telnet carries the runtime switches and levels. Speaker geometry (distances,
crossovers, sizes) and the graphic EQ bands need the setup config API on
HEOS-era receivers; on the AVR-X2000 they are only reachable through the
receiver's own menu.

## Sound modes

| | AVR-X2000 | AVR-X2600H |
|---|---|---|
| Set commands | `MSMOVIE`, `MSMUSIC`, `MSGAME`, `MSDIRECT`, `MSPURE DIRECT`, `MSSTEREO`, `MSSTANDARD`, `MSDOLBY DIGITAL`, `MSDTS SURROUND`, `MSMCH STEREO`, DSP modes (`ROCK ARENA`, `JAZZ CLUB`, `MONO MOVIE`, `MATRIX`, `VIDEO GAME`, `VIRTUAL`) — Documented | adds `MSAUTO`, `MSDOLBY ATMOS`, `MSDTS:X`, `MSNEURAL:X`, Dolby Surround and Neural:X combinations (2015 doc); Dolby Atmos and DTS:X Sourced |
| Reported decoder modes | Pro Logic II/IIx/IIz and Neo:6 combinations, Dolby TrueHD, DTS-HD | Atmos, DTS:X, `+DS`, `+NEURAL:X` combinations |
| Mode list from the receiver | Not expected (To test) | `OPSML`/`OPSMLALL` (Tested on AVR-X3600H) |
| Genre groups | Deviceinfo lists MOVIE/MUSIC/GAME/PURE | `MSSMG`/`SSSMG` (undocumented, Tested on AVR-X3600H) |
| Quick Select | `MSQUICK1`–`5`, `MSQUICK0` = none active | same |

A third source is the setup interface: `/ajax/control/get_config?type=9`
returns the modes offered for the current input with the active one marked,
and `set_config?type=4` selects one by index (ha-denon-marantz-avr, port
11080). The reported token can differ from the accepted one, see the
[command reference](03-command-reference.md#sound-mode-tokens-set-form-and-reported-form).

Sound modes are a frequent source of trouble in the official integration,
which works from a fixed list. This integration learns the list from the
receiver; for the AVR-X2000 a fixed fallback list from its document would
be needed.

## Audyssey

| Function | Command | Values | AVR-X2000 | AVR-X2600H |
|---|---|---|---|---|
| Room correction | — | — | MultEQ XT (Sourced) | MultEQ XT (Sourced) |
| MultEQ mode | `PSMULTEQ:` | `AUDYSSEY`, `BYP.LR`, `FLAT`, `MANUAL`, `OFF` | Documented | Documented without `MANUAL` (2015); To test |
| Dynamic EQ | `PSDYNEQ` | `ON`/`OFF` | Documented | Documented |
| Reference level offset | `PSREFLEV` | `0`, `5`, `10`, `15` | Documented | Documented |
| Dynamic Volume | `PSDYNVOL` | `OFF`, `LIT`, `MED`, `HEV` | Documented | Documented |
| LFC, containment amount | `PSLFC`, `PSCNTAMT` (1–7) | | Absent (not in the document) | 2015: X4200W only; To test |
| DSX, stage width/height | `PSDSX`, `PSSTW`, `PSSTH` | | Absent | 2015: X4200W only; expect absent |

The 2015 document labels `BYP.LR` as "L/R bypass" and `FLAT` as flat, but
its table pairs the labels one row off. Use the token names, not the labels.

## Tone and loudness

| Command | Values | Notes | AVR-X2000 | AVR-X2600H |
|---|---|---|---|---|
| `PSTONE CTRL` | `ON`/`OFF` | Enables bass and treble | Documented | Documented |
| `PSBAS`, `PSTRE` | 44–56 (±6 dB), `UP`/`DOWN` | | Documented | Documented |
| `PSLOM` | `ON`/`OFF` | Loudness management | Documented | Documented |

## Other audio parameters

| Command | Purpose | Values | AVR-X2000 | 2015 doc | 2020 table |
|---|---|---|---|---|---|
| `PSDRC` | Dynamic range compression | `AUTO`, `LOW`, `MID`, `HI`, `OFF` | yes | yes | yes |
| `PSLFE` | LFE level | `00` to `10` = 0 to −10 dB | yes | yes | yes |
| `PSDELAY` | Audio delay | 0–200 ms; 0–500 ms from 2016 (FY21 table) | yes | yes | yes |
| `PSDEL` | Effect delay | 0–300 ms | yes | yes | yes |
| `PSEFF` | Effect level | 1–15 | yes | yes | yes |
| `PSRSTR` | Audio restorer | `OFF`, `LOW`, `MED`, `HI` | yes | yes | yes |
| `PSCINEMA EQ.` | Cinema EQ | `ON`/`OFF` | yes | yes | yes |
| `PSSWR` | Subwoofer on/off in Direct/Stereo | `ON`/`OFF` | yes | yes | yes (`OFF` in Direct and Stereo only) |
| `PSRSZ` | Room size | `S`, `MS`, `M`, `ML`, `L` | yes | yes | yes |
| `PSPAN`, `PSDIM`, `PSCEN`, `PSCEI` | Pro Logic II / Neo:6 parameters | | yes | none of the four models | — |
| `PSMODE:`, `PSPHG` | Pro Logic mode, height gain | | yes | `PSMODE:` reported only | — |
| `PSFRONT` | Front speakers A/B | `SPA`, `SPB`, `A+B` | AVR-X2000 only (not E400) | yes | only with amp assign Front B; not S960H |
| `PSSWL`, `PSSWL2` | Subwoofer level | `ON`/`OFF`, 38–62 (50 = 0 dB) | — | yes (`SWL2` X4200W) | yes (`SWL2` X3700H and up) |
| `PSDIL` | Dialog level | `ON`/`OFF`, 38–62 | — | yes | — (replaced by `PSCLV`) |
| `PSCLV` | Center level adjust | 38–62 (50 = 0 dB) | — | — | X2700H, X3700H, X4700H, S960H |
| `PSDIC` | Dialog control | 0–6 | — | yes | yes |
| `PSGEQ`, `PSHEQ` | Graphic EQ on/off, headphone EQ | `ON`/`OFF` | — | yes | yes |
| `PSNEURAL`, `PSCES` | Neural:X, center spread | `ON`/`OFF` | — | yes | `PSNEURAL` yes; `PSCES` none of the five |
| `PSBSC`, `PSDEH` | Bass sync, dialogue enhancer | | — | none of the four models | `PSBSC` X4700H and up, `PSDEH` X6700H |

Mode gating: many `PS…` parameters only answer in a sound mode where they
apply (for example `PSEFF` in DSP modes, `PSCEN` in Neo:6). Silence to a
query in the wrong mode does not mean the command is missing.

## Channel levels

`CV<channel> <nn>`, 38–62, 50 = 0 dB, half steps allowed; `CVSW 00` means the
subwoofer is off in Direct mode.

| Receiver | Channel tokens |
|---|---|
| AVR-X2000 | `FL`, `FR`, `C`, `SW`, `SL`, `SR`, `SBL`, `SBR`, `SB`, `FHL`, `FHR` (Documented) |
| AVR-X2600H | The 7.2 layout with two subwoofers and Atmos heights: expect `SW2` and some of `TFL/TFR`, `TML/TMR`, `FDL/FDR`, `SDL/SDR` (To test) |
| Superset (`denonavr` library) | adds `SW3`, `SW4` and `CH` (center height) for larger models |

## Speaker setup

| Data | Over telnet | Over the setup config API | AVR-X2000 |
|---|---|---|---|
| Speaker configuration and size | `SSSPC…` (undocumented, read) | speakers section | menu only |
| Distances | `SSSDE…` (undocumented, read) | speakers `type=4` | menu only |
| Levels | `CV…`, `SSLEV…` | speakers `type=5` | `CV…` |
| Crossovers | `SSCFR…` (undocumented, read) | speakers `type=6` | menu only |
| Subwoofer and LFE low-pass | — | speakers `type=10`, `type=17` | menu only |
| Layout and amp assign | — | speakers `type=15`, amp assign | menu only |

The `SS…` tokens are in no published document; this integration reads them
on the AVR-X3600H.

## Setup config API

HTTPS 10443 on HEOS-era receivers (HTTP 11080 on some newer firmware), see
the [network page](02-network-and-protocols.md#setup-config-api-https-10443-http-11080).

Audio section `type` ids found by this integration on the AVR-X3600H:
2 center level, 3 subwoofer level, 4 surround parameters, 5 restorer,
6 audio delay and lip sync, 9 Audyssey, 10 graphic EQ, 11 bass sync,
12 dialog level, 13 DAC filter, 14 Dirac. An empty document means the model
does not have that group. The speakers section ids above come from the
community guide (Cinema 70s).

The HTTP API encodes some values differently from telnet:

| Setting | Telnet | HTTP |
|---|---|---|
| Dynamic Volume | `OFF`, `LIT`, `MED`, `HEV` | `4`, `1`, `2`, `3` |
| MultEQ | `OFF`, `AUDYSSEY`, `FLAT`, `BYP.LR`, `MANUAL` | `0`, `1`, `2`, `3`, `4` |
| Levels | 38–62, 50 = 0 dB | tenths of a dB, 0 = 0 dB |
| Graphic EQ band | — | dB × 10, −20.0 … +6.0 dB |

The HTTP column comes from the community guide; the graphic EQ row is
Tested on AVR-X3600H by this integration.

The same guide says `PSMULTEQ:` does not answer on the Cinema 70s and the
HTTP API is the only way to read MultEQ there. Its probe file does not
support that conclusion: the probe ran with the receiver in standby, and it
queried `PSMULTEQ:?`, `PSCINEMA EQ.?`, `PSRSZ?`, `PSRSTR?` and `SPPR?`
without the space the documents use (`PSMULTEQ: ?`, `PSRSZ ?`). Treat those
"unsupported" results as inconclusive.

## Audyssey over AppCommand0300 (port 8080)

The `denonavr` library reads and writes Dynamic EQ, Dynamic Volume,
reference level and MultEQ with `GetAudyssey`/`SetAudyssey` on
`/goform/AppCommand0300.xml`. The AVR-X2000 reports `CommApiVers` 0210 and is
not expected to have this endpoint (To test). This integration uses telnet
for these settings instead.

## Calibration

Measuring and writing room-correction filters runs over port 1256 and
interrupts playback. Not in scope for this integration's live state; see the
[network page](02-network-and-protocols.md#calibration-protocol-port-1256).
