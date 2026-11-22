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
| Control protocol v02 | 2015 | AVR-X1200W, X2200W, X3200W, X4200W | Ethernet, RS-232C |
| Community guide (marantz-denon-mqtt) | current | Marantz Cinema 70s | Ethernet |

There is no published document for the AVR-X2600H. The closest official
reference is the 2015 X-W document; the integration's own findings on the
AVR-X3600H (2019, same platform generation) fill the rest. Every
X2600H-specific statement is therefore **To test** unless a page says
otherwise.

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
| Master volume minimum | 2011: `MV99` = minimum, `MV00` = −80 dB, `MV995` = −80.5 dB | 2012 onward: `MV00` = minimum, `MV005` = −79.5 dB | The codec reads `99` as +19 dB. Matters for pre-2012 models only; AVR-X2000 To test at volume minimum |
| Volume minimum, wording | The 2012 and 2013 documents state "99" in their notes and "00" in their tables | 2015: "00" throughout | Follow the tables and the 2015 document |
| `CV?` reply | Every channel of the layout, unused ones as `50` | Configured speakers only, then `CVEND` | Do not rely on `CVEND` for the AVR-X2000 |
| Picture adjust (`PVCN`, `PVST`, `PVHUE`) | AVR-X2000: two digits, 44–56, `50` = 0 | 2015: three digits, `000`–`100`, `050` = 0; `PVHUE` gone | The integration writes three digits; To test on the AVR-X2000 |
| Picture `PVBR` | 00–12 | `000`–`100`, `050` = 0 | as above |
| Dynamic Volume | 2011: `NGT`, `EVE`, `DAY` | 2012 onward: `OFF`, `LIT`, `MED`, `HEV` | Profile has the newer set |
| Audio Restorer | 2011–2013 (2112CI, 1713, 2113CI): `MODE1`–`MODE3`, `OFF` | AVR-X2000 and 2015: `LOW`, `MED`, `HI`, `OFF` (2015 maps LOW = MODE3, MED = MODE2, HI = MODE1) | Profile fits the AVR-X2000 |
| Tuner preset numbers | 2011 and AVR-X2000 v03: `A1`–`G8` | 2113CI v04 (May 2013, "corrected") and 2015: `01`–`56` | AVR-X2000 To test |
| All Zone Stereo state | AVR-X2000: set with `MNZST ON/OFF`, state only from the `MSALL ZONE STEREO` event | 2015: `MNZST?` query | The integration queries `MNZST ?`; AVR-X2000 To test |
| Sound modes | Pro Logic II/IIx/IIz, Neo:6 combinations, no `MSAUTO` command | Atmos, DTS:X, Neural:X, Dolby Surround, `MSAUTO`; PL II and Neo:6 gone | Mode list is read from the receiver (`OPSML`), which the AVR-X2000 probably lacks (To test) |
| Front speaker A/B | AVR-X2000 command table: `PSFRONT SPA+B`; its event table: `PSFRONT A+B` | 2015: `A+B` | Profile uses `A+B` |
| Quick Select slots | `MSQUICK1`–`5` documented for all | same | The AVR-X2000 description lists four (`Quick Select1`–`4`); To test |
| Zone 2 tone, HPF, channel mode | not documented | 2015: X3200W and X4200W only | X2600H To test |
| Auto standby, ECO, dimmer, triggers | not documented | 2015: all four models (triggers X3200W/X4200W) | AVR-X2000: expect absent |

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
`50` reference, and steps are 0.5 dB, not 0.1 dB.

## Small commands worth knowing

| Command | Purpose | Status |
|---|---|---|
| `SYREMOTE LOCK ON/OFF`, `SYPANEL LOCK ON/OFF`, `SYPANEL+V LOCK ON` | Lock remote or front panel | Documented (both documents) |
| `UGIDN` | Show and return the 12-digit upgrade ID | Documented |
| `RCKSK…`, `RCSHP…` | Send an IR code through the control port | Documented in the AVR-X2000 document; needs the IR code list; To test |
| `CVZRL` | Reset all channel levels | Documented (2015) |
| `MNMEN ON/OFF`, `MNCUP` … `MNRTN`, `MNOPT`, `MNINF` | Setup menu and cursor keys | Documented (both); fits a `remote` entity (roadmap item 12) |
