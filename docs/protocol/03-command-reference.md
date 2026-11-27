# Command reference

The full token list, with what this integration implements, is the
[control protocol reference](../../custom_components/denonavr_plus/doc/DENON_CONTROL_PROTOCOL.md).
This page adds what that list does not show: which protocol document
describes what, and where the documents disagree.

## Protocol documents compared

| Document | Year | Models | Interfaces |
|---|---|---|---|
| Control protocol v7.4.0 | 2011 | AVR-2112CI, AVR-1912 | RS-232C, Ethernet |
| Control protocol v8.6.0 | 2012 | AVR-1713, AVR-1613 | Ethernet |
| Control protocol v04 | 2012–2013 | AVR-2113CI, AVR-1913 | Ethernet, RS-232C (2113CI) |
| Control protocol v03 (rev 10.1.0) | 2013 | **AVR-X2000**, AVR-E400 | Ethernet, RS-232C (X2000 North America) |
| Control protocol V06 | 2014 | AVR-S700W, S900W, X1100W, X2100W, X3100W, X4100W, X5200W, X7200W | Ethernet, RS-232C (read through a text extract; see note) |
| Control protocol v02 | 2015 | AVR-X1200W, X2200W, X3200W, X4200W | Ethernet, RS-232C |
| Community guide (marantz-denon-mqtt) | current | Marantz Cinema 70s | Ethernet |

No document for the AVR-X2600H is in the sources. Denon published
spreadsheets for later years (2017: AVR-X6400H to S730H; 2018: AVC-X6500H
to S640H, including the AVR-X2500H; 2020: "FY21" for that year's models),
but their links on denon.com no longer resolve from here; see
[References](references.md#manufacturer-control-protocols). Until one of
them is added, the closest official reference is the 2015 X-W document, and
the integration's own findings on the AVR-X3600H (2019, same platform
generation) fill the rest. Every X2600H-specific statement is therefore
**To test** unless a page says otherwise.

The 2014 V06 document was read through an automated text extract of its PDF,
not page by page; its statements below are good enough to date a change, and
should be checked against the PDF before code depends on them.

## Command families

| Prefix | Area | AVR-X2000 doc | 2015 doc | Page |
|---|---|---|---|---|
| `PW`, `ZM` | System and main-zone power | yes | yes | [Zones](04-zones-and-multiroom.md) |
| `MV`, `MU` | Master volume, mute | yes | yes | below |
| `CV` | Channel levels | 11 channel tokens | 31 channel tokens | [Audio](06-audio-audyssey-speakers.md) |
| `SI`, `SV`, `SD`, `DC` | Source, video source, input mode, digital mode | yes | yes | [Video](07-video-hdmi.md) |
| `MS` | Sound mode, Quick Select | yes | yes | [Audio](06-audio-audyssey-speakers.md#sound-modes) |
| `PS` | Surround, Audyssey, tone, audio parameters | yes | more | [Audio](06-audio-audyssey-speakers.md) |
| `VS`, `PV` | Video output, picture | yes | yes | [Video](07-video-hdmi.md) |
| `Z2`, `Z3` | Zones | Z2 | Z2, Z3 | [Zones](04-zones-and-multiroom.md) |
| `TF`, `TP`, `TM` | Tuner | yes | yes | [Media](08-network-media-and-services.md#tuner) |
| `NS` | Network audio transport, on-screen text, presets | yes | yes | [Media](08-network-media-and-services.md) |
| `MN` | Menu keys, All Zone Stereo | yes | yes | [Zones](04-zones-and-multiroom.md#all-zone-stereo) |
| `SY`, `UG` | Panel and remote lock, upgrade ID | yes | yes | below |
| `SLP`, `STBY`, `ECO`, `DIM`, `TR` | Sleep, auto standby, ECO, dimmer, triggers | `SLP` only | all | below |
| `RC` | Send an IR remote code | yes | not listed | below |
| `SS…`, `OP…`, `SY…` (newer) | Setup and status families | no | no | [Status](09-status-diagnostics-and-discovery.md) |

The `SS…` and `OP…` families (speaker setup, source rename, sound mode lists,
input signal info) are in no published document. This integration and the
community found them on current receivers; the AVR-X2000 is not expected
to have them (To test).

## Differences between protocol versions

These are the places where a reader written for one generation misreads
the other.

| Topic | Older documents | Newer documents | Impact here |
|---|---|---|---|
| Master volume minimum | 2011: `MV99` = minimum, `MV00` = −80 dB, `MV995` = −80.5 dB | 2012 onward (also 2014, 2015): `MV00` = minimum, `MV005` = −79.5 dB | `99` and `995` are read as the minimum on every receiver (the scale ends at 98 from 2012 on); AVR-X2000 To test at volume minimum |
| Volume minimum, wording | The 2012 and 2013 documents state "99" in their notes and "00" in their tables | 2014 and 2015: "00" throughout | Follow the tables and the 2015 document |
| `CV?` reply | Up to 2013: every channel of the layout, unused ones as `50` | 2014 onward: configured speakers only, then `CVEND` | Do not rely on `CVEND` for the AVR-X2000 |
| Picture adjust (`PVCN`, `PVST`, `PVHUE`) | AVR-X2000 (2013): two digits, 44–56, `50` = 0 | 2014 onward: three digits, `000`–`100`, `050` = 0; `PVHUE` listed but unavailable on every model | The integration writes two digits for receivers before 2014 (`CommApiVers` below 0250, or none) and three from 2014 on; To test on the AVR-X2000 |
| Picture `PVBR` | 00–12 | `000`–`100`, `050` = 0 | as above |
| Dynamic Volume | 2011: `NGT`, `EVE`, `DAY` | 2012 onward: `OFF`, `LIT`, `MED`, `HEV` | Profile: the 2011 set for receivers without `Deviceinfo.xml` (variant `2011`), the newer set otherwise |
| Audio Restorer | 2011–2013 (2112CI, 1713, 2113CI): `MODE1`–`MODE3`, `OFF` | AVR-X2000 and 2015: `LOW`, `MED`, `HI`, `OFF` (2015 maps LOW = MODE3, MED = MODE2, HI = MODE1) | Profile: `MODE1`–`MODE3` before 2014 outside the AVR-X and AVR-E series (variant `before_x`), `HI`, `MED`, `LOW` otherwise; the AVR-X2000 answers `PSRSTR LOW`. The AVR-1713 and AVR-3313 list MODE1 as High and MODE3 as Low in `Deviceinfo.xml` |
| Tuner preset numbers | 2011 and AVR-X2000 v03: `A1`–`G8` | 2113CI v04 (May 2013, "corrected"), 2014 and 2015: `01`–`56` | `A1`–`G8` before 2014 (`CommApiVers` below 0250, or none), `01`–`56` from 2014 on; AVR-X2000 To test |
| All Zone Stereo state | AVR-X2000: set with `MNZST ON/OFF`, state only from the `MSALL ZONE STEREO` event | 2014 onward: `MNZST?` query (2014 reply column inconsistent) | The integration queries `MNZST?` as the 2014 and 2020 documents write it; AVR-X2000 To test |
| Sound modes | Pro Logic II/IIx/IIz, Neo:6 combinations, no `MSAUTO` command | Atmos, DTS:X, Neural:X, Dolby Surround, `MSAUTO`; PL II and Neo:6 gone | Mode list is read from the receiver (`OPSML`), which the AVR-X2000 probably lacks (To test) |
| Front speaker A/B | AVR-X2000 command table: `PSFRONT SPA+B`; its event table: `PSFRONT A+B` | 2015: `A+B` | Profile uses `A+B` |
| Quick Select slots | `MSQUICK1`–`5` documented for all | same | The AVR-X2000 description lists four (`Quick Select1`–`4`); To test |
| Zone 2 tone, HPF, channel mode | not documented | 2014 and 2015: the larger models only (2014: X7200W to X3100W) | X2600H To test |
| Auto standby, ECO, dimmer, triggers | not documented | 2014 and 2015: all models (triggers on the larger ones) | AVR-X2000: expect absent |
| Subwoofer level `PSSWL`, dialog level `PSDIL`, `SIBT`, `PSGEQ`, `PSLFC`, `PSCNTAMT` | not documented | from 2014 (`PSLFC`, `PSCNTAMT` larger models only) | AVR-X2000: expect absent |
| Auro-3D strength `PSAUROST` | — | 2015: 1–16 | The `denonavr` library sends and tests 0–15; To test on an Auro model |

Most of the AVR-X2000's differences are therefore the 2013/2014 boundary,
not the 2016 HEOS boundary: the AVR-X2100W (2014) already uses the newer
encodings.

## Undocumented tokens on current receivers

Found by this integration and by community projects; no Denon document
describes them. All are To test on the AVR-X2600H and not expected on the
AVR-X2000.

| Query | Reply | Seen by |
|---|---|---|
| `NSFRN ?` | Friendly name, e.g. `NSFRN Denon AVC-X4700H` | denon_avr_parameter (AVC-X4700H) |
| `SYSMI ?` | Active sound mode as display text | marantz-denon-mqtt, denon_avr_parameter |
| `SYSDA ?` | Incoming audio format as text (`PCM`, `FLAC`, …) | same |
| `SYSDVIN ?`, `SYSDVOUT ?` | Video input and output format, e.g. `1080p60` | denon_avr_parameter |
| `SSINFAISFOR ?` | Input channel layout as `front/surround/.LFE`, e.g. `2/0/.0` | denon_avr_parameter |
| `SSOUTFAISFOR ?` | Answers with the `CV…` lines of the active output channels, ending in `CVEND` | denon_avr_parameter |
| `SSINFAISFSV ?` | Sampling frequency (`441` = 44.1 kHz) according to one marantz-denon-mqtt document, "speaker setup flag" according to another | marantz-denon-mqtt (contradictory) |
| `SSSMG ?` | Sound mode group `MOV`, `MUS`, `GAM`, `PUR` | marantz-denon-mqtt; this integration |
| `OPSML ?`, `OPSMLALL ?` | Available sound modes per group, ending in `OPSMLALL END` | this integration; marantz-denon-mqtt |
| `PSAUROMODE ?` | Auro-3D mode | denon_avr_parameter, `denonavr` |
| `SSINFAISSIG ?` | Input signal type as a raw code | this integration |
| `SSQSNZMA ?` | Quick Select names of the main zone, one per line (`SSQSNZMAQS<n> <name>`), ending in `SSQSNZMA END` | this integration |
| `SSHOSALS ?` | Auto lip sync, `ON`/`OFF`; silent unless a video source is active | this integration (candidate) |
| `SSQS1 ?`, `SSQS2 ?`, `SSQS3 ?`, `SSQS4 ?` | Quick Select as a block on newer receivers (AVC-X3900H): `SSQS<n>QSN <name>`, one line per stored item (`SRC`, `VOL`, `SDM` …), `SSQS<n> END`; no answer to `SSQSNZMA ?` there | LaserGuruGuy/denon_avr (recorded AVC-X3900H) |
| `OPALS ?` | Auto lip sync on newer receivers (AVC-X3900H): `OPALSSET ON`/`OFF` (the setting), `OPALSDSP`, `OPALSVAL`; also pushed with an input change; no `SSALS` there | LaserGuruGuy/denon_avr (recorded AVC-X3900H) |
| `SSLFL ?` | Low-pass filter for LFE, three-digit Hz: `080`, `090`, `100`, `110`, `120`, `150`, `180`, `200`, `250` | this integration; read and written on the AVR-X3600H |
| `SSSWM ?` | Subwoofer mode `LFE` or `L+M` (LFE + Main) | this integration; read and written on the AVR-X3600H |
| `SSVCTZMA ?` | Main zone volume settings, one line each: `SSVCTZMADIS` scale `REL`/`ABS`, `SSVCTZMALIM` limit `OFF`/`060`/`070`/`080`, `SSVCTZMAMLV` muting level `MUT`/`040` | this integration; read and written on the AVR-X3600H |

The `SYSDVIN`/`SYSDVOUT` and `SSINFAISFOR` replies are candidates for the
video and input-signal sensors (roadmap item 39).

Denon's own app sends further tokens that no document here lists, among
them `NSARA ?` and `NSHEA ?` (whether control by the remote app or the
HEOS app is switched off) and `MVLONGUP`, `MVLONGDOWN`, `MVLONGEND`
(volume held, sent by the app for a connected amplifier); the full list is on the
[manufacturer apps](12-manufacturer-apps.md#telnet-commands-in-the-apps)
page. All are To test.

## Sound mode tokens: set form and reported form

The token a receiver reports is not always the token it accepts.
Community tests on a Marantz Cinema 70s:

| Mode | Works as a set command | Reported as | Did not work |
|---|---|---|---|
| Multi Ch Stereo | `MSMCH STEREO` | `MSMCH STEREO` | `MSMULTI CH STEREO` |
| Dolby Surround | depends on source and group | `MSDOLBY AUDIO-DSUR` | `MSDOLBY SURROUND` from the tested state |
| DTS Neural:X | `MSNEURAL:X` | `MSNEURAL:X` | — |
| DTS Virtual:X | `MSVIRTUAL:X` | `MSVIRTUAL:X` | — |

This integration therefore takes the selectable list from the receiver and
keeps only a few overrides in its profile (`sound_mode_wire_overrides`).

## Quick Select and Smart Select

Denon models recall presets with `MSQUICK<n>`, Marantz models with
`MSSMART<n>`. Marantz's 2019 document (SR6014, SR5014, NR1710, NR1510,
NR1200) gives `MSSMART1`–`5`, `MSSMART<n> MEMORY` to store, `MSSMART0` to
cancel, `MSSMART ?` to ask, and the same with `Z2SMART` for Zone 2 and
`Z3SMART` for Zone 3. Denon's and Marantz's apps use one HTTP command for
both (`SetQuickSelect`) on receivers whose `Deviceinfo.xml` offers it,
from level 0250 on; to receivers without it (up to 2013, Marantz NR1604
and SR5008 included) they send `MSQUICK<n>`, `Z2QUICK<n>` and `Z3QUICK<n>`
over `formiPhoneAppDirect.xml`, for either brand, and they store presets
over HTTP only (**App**). Recorded Marantz units from level 0300 on (SR6011,
SR6012, NR1609, AV7703) name the presets Smart Select in `Deviceinfo.xml`.
This integration sends and reads `MSSMART`/`Z2SMART` on Marantz receivers
(`BrandCode` 1) from level 0300 on and `MSQUICK`/`Z2QUICK` on all others. `MSSMART` without a number is a
separate surround mode on some Marantz models. Never send a slot number beyond what the receiver
advertises: on the Cinema 70s, `MSSMART5` on a four-slot unit left the
receiver unstable until it was power-cycled (marantz-denon-mqtt test log).
The AVR-X2000 description lists four Quick Selects while its document
documents five; use four.

## Value encodings

| Encoding | Example | Used by |
|---|---|---|
| Half step: two digits whole, three digits with a trailing 5 | `55` = 55.0, `555` = 55.5 | `MV`, `CV`, `Z2` volume, `PSBAS`, `PSTRE`, `PSSWL` |
| Volume to dB | dB = value − 80 | `MV`, `Z2`, `Z3` |
| Level to dB | dB = value − 50 | `CV`, `PSBAS`, `PSTRE`, `PSSWL`, `PSDIL` |
| Centered, fixed width | `050` = 0 | `PVCN`, `PVBR`, `PVST` (2015 onward) |
| Direct number | `PSDEL 100` = 100 ms, `SLP030` = 30 min | delays, timers |
| Enumerations | `ON`, `OFF`, `LOW`, … | most switches and selects |

One community note to read with care: the marantz-denon-mqtt guide
describes `PSSWL 475` as "500 = 0 dB, 0.1 dB per unit". The half-step rule
explains the same observation: `475` is 47.5, which is −2.5 dB from the
`50` reference, and steps are 0.5 dB, not 0.1 dB. The `denonavr` library's
level table says the same (`385` = −11.5 dB, `505` = +0.5 dB).

## Small commands worth knowing

| Command | Purpose | Status |
|---|---|---|
| `SYREMOTE LOCK ON/OFF`, `SYPANEL LOCK ON/OFF`, `SYPANEL+V LOCK ON` | Lock remote or front panel | Documented (both documents) |
| `UGIDN` | Show and return the 12-digit upgrade ID | Documented |
| `RCKSK…`, `RCSHP…` | Send an IR code through the control port | Documented in the AVR-X2000 document; needs the IR code list; To test |
| `CVZRL` | Reset all channel levels | Documented (2015) |
| `MNMEN ON/OFF`, `MNCUP` … `MNRTN`, `MNOPT`, `MNINF`, `MNCHL` | Setup menu, cursor keys, channel level menu | Documented (both); sent by the `remote` entity; To test |
