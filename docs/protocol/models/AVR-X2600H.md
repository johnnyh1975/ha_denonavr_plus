# AVR-X2600H

7.2-channel receiver with HEOS Built-in, model year 2019. Same platform
generation as the AVR-X3600H, on which this integration has been tested.

## Sources for this model

- Denon product information sheet (US and UK editions): channels, HDMI
  (8 in, 2 out, eARC on one), Dolby Atmos, DTS:X, DTS Virtual:X, Dolby
  Vision, HDR10, HLG, AirPlay 2, Bluetooth, Wi-Fi, HEOS, Zone 2 pre-out and
  speaker assignment, phono input, two subwoofer pre-outs.
- No control protocol document is published for this model. The closest
  are the 2015 document for AVR-X1200W to X4200W and the HEOS CLI
  specification 1.14 (2019).
- This integration's own findings on the AVR-X3600H.
- A capability check on an AVR-X2600H with firmware 3.139.175, recorded in
  [`tests/fixtures/capability_check/AVR-X2600H-3.139.175.json`](../../../tests/fixtures/capability_check/AVR-X2600H-3.139.175.json):
  151 telnet queries and the receiver's answers, anonymised.

No owner's manual is in the project sources yet; it would settle the network
standby menu name, RS-232C and the Zone 2 source limits.

## Interfaces

| Interface | Status |
|---|---|
| Telnet 23 | Tested (3.139.175) |
| HTTP 8080: goform, `Deviceinfo.xml`, StatusLite | `Deviceinfo.xml` on 8080 Tested (3.139.175); goform, StatusLite Tested on AVR-X3600H |
| UPnP 60006 | Tested on AVR-X3600H |
| HEOS 1255 | Sourced; Tested on AVR-X3600H |
| Setup config API 10443 | Tested (3.139.175) |
| Calibration 1256 | To test |
| RS-232C | To test; not in the product sheet's terminal list |

## Expected differences to the AVR-X3600H

The AVR-X3600H is the larger 9.2 model. On the AVR-X2600H expect fewer
amplifier channels and therefore fewer amp-assign options and height
channels, no Zone 3, and fewer function names (a review lists IMAX
Enhanced as not supported on this model). Because entities are gated on the receiver's
own function list, these differences should appear automatically; the
[diagnostics file](../09-status-diagnostics-and-discovery.md#identifying-a-receiver)
shows the list.

## Capability check (firmware 3.139.175)

Queries the receiver answered, by group. A query without a reply is not proof
that the function is missing: some answer only in a given state (a sound mode,
a source, a speaker layout). The recording has the exact lines.

| Group | Answered | No reply |
|---|---|---|
| Power and zones | `PW?` `ZM?` `Z2?` `Z2MU?` `Z2CV?` (FL, FR) `Z2SLP?` `Z2STBY?` `Z2QUICK ?` | `Z2CS?` `Z2HPF?` `Z2PSBAS ?` `Z2PSTRE ?` `Z2HDA?`, all `Z3…`, `MNZST ?`, `SR?` |
| Volume and input | `MV?` `MU?` `CV?` `SI?` `SV?` `SD?` `DC?` `SLP?` `STBY?` `ECO?` `DIM ?` `MNMEN?` | `SLP ?` `TR?` |
| Surround | `MS?` `MSQUICK ?` `OPSML ?` `SSSMG ?` `SYSMI ?` `SYSDA ?` | `MSSMART ?` `OPSMLALL ?` `PSMODE: ?` |
| Audio | `PSTONE CTRL` `PSBAS` `PSTRE` `PSSWL` `PSCINEMA EQ.` `PSLOM` `PSMULTEQ:` `PSDYNEQ` `PSREFLEV` `PSDYNVOL` `PSGEQ` `PSHEQ` `PSDRC` `PSDIC` `PSLFE` `PSEFF` `PSDEL` `PSCES` `PSNEURAL` `PSSWR` `PSDELAY` `PSRSTR` `PSFRONT` `PSSPV` | `PSDIL` `PSSP:` `PSDYNSET` `PSLFC` `PSCNTAMT` `PSDSX` `PSSTW` `PSSTH` `PSDCO` `PSDEH` `PSBSC` `PSLFL` `PSPAN` `PSDIM` `PSCEN` `PSCEI` `PSCEG` `PSRSZ` `PSSB:` `PSATT` `PSPHG`, Auro, IMAX, Dirac, DAC filter, M-DAX, `SPPR` |
| Video | `VSASP` `VSMONI` `VSSCH` `VSAUDIO` `VSVPM` `PV?` `PVCN` `PVBR` `PVST` `PVDNR` `PVENH` `SYSDVIN` `SYSDVOUT` | `VSSC` `VSVST` `PVHUE` |
| Tuner | `TFAN?` `TPAN?` `TMAN?` | `TFANNAME?` and the HD Radio queries |
| Network | `NSFRN ?` | |
| Signal and setup | `SSINFAISSIG` `SSINFAISFSV` `SSINFAISFOR` `SSFUN` `SSSOD` `SSLEV` `SSSDE` `SSSPC` `SSCFR` `SSQSNZMA` `SSVCTZMA` `SSSWM` `SSLFL` `SSHOS` `SSPAA` `SSALS` `SSANA` `BTTX` | `SSINF` `SSOUTFAISFOR` `SSHOSALS` `SSAUDIO` `SSTTR` `ILB` `RM` |

What the answers show:

- **Zone 2** answers power, source, volume, mute, channel levels (front left
  and right), sleep timer, auto standby and Quick Select. Tone, high-pass
  filter and HDMI audio for Zone 2 got no reply. Amp assign is `ZO2`: Zone 2
  speakers are driven by the receiver's own amplifier channels. No Zone 3.
- **Zone 2 source replies.** The zone source arrives as `Z2NET`; `Z2QUICK0`
  answers the Quick Select query and is no source.
- **Channels.** `SSLEV` lists 18 speaker channels and one subwoofer (`SW`):
  front, center, surround, surround back, front height, top front, top
  middle, front Dolby and surround Dolby. No `SW2` in this layout.
- **Audyssey.** `PSMULTEQ:` answers (`BYP.LR`); the receiver lists four
  MultEQ modes: Reference, L/R Bypass, Flat, Off (no Manual). `PSLFC`,
  `PSCNTAMT` (LFC) got no reply.
- **`STBY` and `BTTX`** answer in the forms the integration expects
  (`STBY15M`; `BTTX OFF` and `BTTX SP`). `TR` (triggers) and `SSTTR`
  (tactile transducer) got no reply.
- **`MNZST ?`** (All Zone Stereo) got no reply, unlike on the AVR-X3600H.
- **Video mode.** The receiver lists Auto, Game, Movie and Bypass;
  `VSVPM` answered `AUTO`. The token for Bypass is not known yet.

## Compared with the protocol documents

The closest document is the 2015 one for the AVR-X1200W to X4200W. Most
queries without a reply are not meant for this class of receiver:

- **Larger models only.** Zone 2 tone, high-pass filter, channel setting and
  HDMI audio, triggers (`TR`): AVR-X3200W and X4200W. Zone 3, `PSLFC`,
  `PSCNTAMT`, `PSDSX`, `PSSTW`, `PSSTH`, `PSSP:` and Auro: AVR-X4200W.
- **Not in any of the four.** `PSMODE:` (Pro Logic modes), `PSPAN`,
  `PSDIM`, `PSCEN`, `PSCEI`, `PSCEG`, `PSPHG`, `PSBSC`, `PSDEH`, `PSLFL`,
  `VSVST`, `PVHUE`; `PSDCO`, `PSATT` and `PSSB:` are in older documents
  only.
- **Other models or regions.** HD Radio (North America), IMAX, Dirac, DAC
  filter, M-DAX, `MSSMART`, `SPPR`, `SSTTR`, `ILB` (newer models or
  Marantz). `VSSC` (analog resolution): the AVR-X2600H has no analog video
  output.
- **Documented for all four, but no reply:** `PSDIL`, `PSRSZ`,
  `TFANNAME?` and `MNZST ?`. The receiver itself calls dialog level "Center
  Level Adjust". Room size probably answers in some sound modes only, the
  station name only while the tuner plays. All Zone Stereo comes in `MS`.
- **Auto Lip Sync** is in neither document. The receiver answers `SSALS ?`
  with `SSALSSET ON`, `SSALSDSP OFF` and `SSALSVAL 000`. The AVR-X2000
  reports it inside `SSHOS ?` as `SSHOSALS`.

A command test (firmware 3.139.175, an Apple TV on HDMI sending PCM)
confirmed bass, treble, subwoofer level, video mode, picture mode with its
picture settings and aspect ratio. Several settings were taken without an
effect, as the documents lead one to expect:

- Dynamic compression, Loudness Management, LFE and Dialog Control act on
  Dolby and DTS bitstreams; the source sent PCM.
- The subwoofer switch acts in two-channel modes only; the command test now
  skips it in other modes.
- Headphone EQ needs headphones; Restorer acts on compressed two-channel
  sound, the effect level in DSP modes, Center Spread in the Dolby Surround
  upmix.
- Front speaker B cannot be chosen while amp assign is Zone 2.
- Digital input mode does not exist on an HDMI-only input; the command test
  now chooses Auto.

## Open points

| Point | Status |
|---|---|
| `CommApiVers` | To test |
| Concurrent telnet sessions | To test |
| `SW2` with two subwoofers configured | To test |
| `VSMONI` values other than `AUTO` | To test |
| Independent `NET` playback in Zone 2 | Confirmed: Zone 2 played Amazon Music through HEOS while the main zone played an HDMI source |
| Zone 2 playing an HDMI source | To test |
| `PSDIL`, `PSRSZ` (documented for all four 2015 models, no reply) | To test in a surround mode |
| `TFANNAME?` while the tuner plays | To test |
| Writing `SSALSSET ON` and `OFF` | To test |
| DRC, Loudness, LFE, Dialog Control with a Dolby or DTS bitstream | To test |
| Token for video mode Bypass | To test |
| `MNZST ?` in another state (All Zone Stereo on) | To test |
| HEOS: URL playback, Quick Select commands, `serial`, volume mapping | To test |
| Setup config API on 10443 or 11080 with current firmware | To test |
| Calibration port 1256 open | To test |

The capability check above is the first recording from this receiver and is
a test fixture. A later check in another state (Zone 2 on, a surround mode,
All Zone Stereo on) would settle several open points.
