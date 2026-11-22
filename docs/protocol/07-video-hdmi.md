# Video and HDMI

## Hardware

| Feature | AVR-X2000 | AVR-X2600H |
|---|---|---|
| HDMI inputs / outputs | 7 / 1 (Sourced, retailer specifications) | 8 / 2 (Sourced, product sheet) |
| 4K | upscaling and pass-through (Sourced) | 4K/60 pass-through, upscaling (Sourced) |
| HDR10, HLG, Dolby Vision | Unverified, expected absent | Sourced |
| ARC / eARC | ARC: Documented (`SDARC` event); eARC: Unverified, expected absent | eARC on one output (Sourced) |
| HDCP 2.3 | Unverified, expected absent | Sourced |

"Expected absent" rests on the release year (2013) and the absence of any
mention in the model's sources; no source excludes the feature outright.

## Input selection

| Command | Purpose | Values | Notes |
|---|---|---|---|
| `SI<source>` | Input source | see [command reference](03-command-reference.md) | Renamed and hidden sources: `SSFUN`/`SSSOD` on HEOS-era receivers; on the AVR-X2000 only through `AppCommand.xml` (`functionrename`, `functiondelete`, Documented in its recorded responses) |
| `SV<source>`, `SVON`, `SVOFF` | Video select: picture from another source | | `SVSOURCE` on older documents |
| `SD…` | Audio input mode | `AUTO`, `HDMI`, `DIGITAL`, `ANALOG`; reported also `ARC`, `NO` (2015: `7.1IN`) | |
| `DC…` | Digital input mode | `AUTO`, `PCM`, `DTS` | Not supported on Cinema 70s (community) |

Source names differ by generation: the AVR-X2000 has `AUX1` and
`USB/IPOD`, no `BT` and no `PHONO`; the 2015 document adds `BT`, `PHONO`
(X4200W only among the four), `AUX2` to `AUX7`. The AVR-X2600H has a phono
input and Bluetooth (Sourced), so `SIPHONO` and `SIBT` are expected (To test).

## Video output (`VS…`)

| Command | Values | AVR-X2000 | 2015 doc |
|---|---|---|---|
| `VSASP` | `NRM` (4:3), `FUL` (16:9) | Documented | X4200W–X2200W |
| `VSSC`, `VSSCH` | Resolution analog / HDMI: `48P`, `10I`, `72P`, `10P`, `10P24`, `4K`, `AUTO` (2015: `4KF` = 4K 60/50) | Documented | X4200W–X2200W; `4KF` X4200W, X3200W |
| `VSAUDIO` | HDMI audio to `AMP` or `TV` | Documented | all four |
| `VSVPM` | Video processing `AUTO`, `GAME`, `MOVI` | Documented | X4200W–X2200W |
| `VSMONI` | HDMI output `1`, `2`, `AUTO` | — (one output) | X4200W and X2200W in the table; the X3200W column is empty although it has two outputs, likely a document error |
| `VSVST` | Vertical stretch | — | none of the four |

AVR-X2600H: two outputs, so `VSMONI` is expected (To test; Tested on
AVR-X3600H by this integration).

## Picture (`PV…`)

| Command | AVR-X2000 | 2015 doc |
|---|---|---|
| `PV` mode | `OFF`, `STD`, `MOV`, `VVD`, `STM`, `CTM` | adds `DAY`, `NGT` (ISF) |
| `PVCN`, `PVST` | 44–56, 50 = 0 | `000`–`100`, `050` = 0 |
| `PVBR` | 00–12 | `000`–`100`, `050` = 0 |
| `PVHUE` | 44–56 | none of the four |
| `PVDNR` | `OFF`, `LOW`, `MID`, `HI` | same |
| `PVENH` | 00–12 | 00–12 |

The X1200W column is empty for every `PV` command: entry-level models have
no picture adjustment. This integration encodes the centered controls with
three digits; on the AVR-X2000 that is the wrong width (To test).

Picture writes only take effect with an active HDMI video signal
(maintainer's notes, AVR-X3600H).

## Signal status

The receiver reports the current video signal in three undocumented places:
telnet `SYSDVIN ?` / `SYSDVOUT ?` (format such as `1080p60`), and the setup
interface's `/ajax/info/get_config?type=3` (resolution, HDR format, color
space, pixel depth, FRL rate, ALLM, QFT for input and output). Both come
from community projects on newer models; AVR-X2600H To test. They are the
candidates for roadmap item 39 (video signal sensors). See the
[command reference](03-command-reference.md#undocumented-tokens-on-current-receivers)
and the [network page](02-network-and-protocols.md#setup-config-api-https-10443-http-11080).

## HDMI setup

HDMI-CEC, ARC/eARC, pass-through in standby, power-off control, TV audio
switching, OSD position and the 4K signal format have no telnet command. On
HEOS-era receivers this integration reads and writes them through the setup
config API (video section, Tested on AVR-X3600H). On the AVR-X2000 they are
menu-only.

Some libraries switch CEC with IR codes sent over telnet
(`RCKSK0410826` / `RCKSK0410827`); the codes are model-specific and not
documented for either receiver. Unverified.
