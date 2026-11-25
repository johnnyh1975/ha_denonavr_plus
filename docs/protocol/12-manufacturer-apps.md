# Manufacturer apps

Denon's and Marantz's own apps show which interfaces the manufacturer
relies on and name commands that no protocol document lists. This page
sums up what their code shows. Everything here is a wire fact (an
endpoint, a command, a parameter name, a value) found in the code; no text
or image of the apps is used. Findings carry the label **App**
([verification status](README.md#verification-status)).

## The apps

| App | Package | Version | Read |
|---|---|---|---|
| Denon AVR Remote | `com.dmholdings.DenonAVRRemote` | 3.13.4 | Java code and the native control library (with symbols) |
| Marantz AVR Remote | `com.dmholdings.MarantzAVRRemote` | 3.13.4 | Same native library and assets as the Denon app, byte for byte; ten Java files differ (package name and build variant) |
| HEOS | `com.dnm.heos.phone` | 3.139.353 | Java code and the native libraries |
| Denon Remote App, Marantz Remote App | `com.dmholdings.denonremoteapp`, `com.dmholdings.marantzremoteapp` | 1.1.9 | The older apps; the same app under two names, with description files for receivers from 2010–2012 ([receivers before 2013](models/before-2013.md)) |

The Java code was decompiled; in the native libraries each string was
traced to the function that uses it, which shows which command a function
sends with which parameters.

## Which interface each app uses

| Interface | Denon/Marantz AVR Remote 3.13.4 | HEOS 3.139.353 | Remote App 1.1.9 |
|---|---|---|---|
| Telnet, port 23 | Three queries only: `ZM?`, `NSARA ?`, `NSHEA ?` | – | – |
| `formiPhoneAppDirect.xml?<telnet command>` | Keys, cursor, network transport, tuner, IR forwarding | – | yes |
| `AppCommand.xml` | Zone status, power, volume, names, favourites | – | yes |
| `AppCommand0300.xml` | Every setup function | – | – |
| Legacy `formMainZone_…`, `formiPhoneApp…` pages | Older receivers, tuner, CD, dock | – | yes |
| `Deviceinfo.xml`, `:8080/description.xml` | Identification and capabilities | – | yes |
| UPnP on port 60006, services `ACT`, `ZoneControl`, `GroupControl` | yes (built-in HEOS part) | yes | – |
| Protobuf channel | yes | yes | – |
| HEOS CLI, port 1255 | `heos://system/check_account` only | not used | – |
| Setup config API (`/ajax`) | `/ajax/upnp/get_device_info` only | the same | – |

So the apps control the receiver over HTTP and, for HEOS functions, over
UPnP and a Protobuf channel. They cannot settle the telnet protocol or the
HEOS CLI: for those the protocol documents and tests on a receiver remain
the only sources. The HTTP commands are on the
[HTTP control](11-http-control.md) page.

## How the apps recognise a receiver

- **Interface level** from `CommApiVers` in `Deviceinfo.xml`: none or 100
  is not supported any more, from 200 the volume step comes from the
  receiver, from 300 the setup functions go through `AppCommand0300.xml`,
  999 is a demo mode ([levels](11-http-control.md#interfaces-by-generation)).
- **Brand** from `BrandCode` (0 Denon, 1 Marantz, 2 McIntosh, 3 D&M
  Professional), otherwise from the manufacturer prefix of the MAC address
  (`00:05:CD` Denon, `00:06:78` Marantz). Each app shows the receivers of
  its own brand only: the Denon app those with `BrandCode` 0, the Marantz
  app those with 1.
- **Product category** from `ProductCategory`: 1 AV receiver, 2 system,
  7 stereo receiver (handled like an AV receiver).
- **HEOS generation**: a receiver found over Bonjour or the HEOS SDK counts
  as a HEOS receiver, except the 2015 and 2016 network receivers without
  HEOS, which the app lists by name: AVR-X1200W to X7200WA, X1300W to
  X3300W, S710W to S920W, NR1506, NR1606, NR1607, SR5010, SR5011, SR6010,
  SR6011, SR7010, AV7702 and AV8802.
- **Excluded by name** (2010–2012, level 100): AVR-991, 1912, 2112, 2312,
  3311, 3312, 4311, A100, AV7005, NR1602, SR5006, SR6006, SR7005, DNP-720,
  NA7004.

### Model years the current apps accept

Besides the interface level, the current apps sort receivers into model
years by name patterns (`*`, `Denon ` and `Marantz ` taken off first):

| In the app | Denon | Marantz |
|---|---|---|
| Not supported | 2010–2011 (DNP-720, RCD-N7), AVR-991, AVR-A100, Cocoon, HEOS speakers | 2010–2011: NA-7004, M-CR603, AV…005, SR…05, SR…06, NR1602 |
| Incompatible, pointed to the older app | 2012–2013: AVR-4520, AVR-X…000 (the AVR-X2000 among them), AVR-E…00; 2014: AVR-X5200, AVR-S…00W, AVR-X…100W | 2012–2013: AV…01, SR…07, SR…08, NR…03, NR…04; 2014: SR…09, NR…05 |
| Supported | Every other Denon AV receiver, so from 2015 on (AVR-X…200W, AVR-S…10W, the H series, AVC) | Every other Marantz AV receiver, from 2015 on (SR…10, NR…06 and newer, AV7702 and newer, Cinema) |

So the current apps are made for receivers from 2015 on. Their code still
holds the paths for levels 0210, 0250 and 0300, but no current app uses
them on the 2012–2014 receivers it turns away; for those models the
`AppCommand0300.xml` commands are a finding in the code, not a path an app
is seen to use.

This integration uses the interface level the same way
([value encodings by generation](03-command-reference.md#differences-between-protocol-versions)),
but not the HEOS-generation test: it uses HEOS when port 1255 answers,
unless HEOS is switched off in its options.

## Telnet commands in the apps

The Denon app opens port 23 for three queries: `ZM?` to align the power
state, and `NSARA ?` and `NSHEA ?`. The last two ask whether control by the
remote app or by the HEOS app is switched off on the receiver; the app
treats a reply `NSARA OFF` or `NSHEA OFF` as switched off. Everything else
goes over `formiPhoneAppDirect.xml`.

Tokens the apps send that no protocol document here lists; all are
**To test**:

| Token | Use in the app | Note |
|---|---|---|
| `NSARA ?`, `NSHEA ?` | Remote app or HEOS app allowed | Would explain a receiver that ignores network commands |
| `MVLONGUP`, `MVLONGDOWN`, `MVLONGEND` | Volume held and released | String only; the call site could not be traced |
| `SSDGO ON`, `SSDGO OFF` | Digital output on or off | Systems and stereo receivers |
| `SSPLV` | Network display: go to the playing view | |
| `NS92`, `NS94`, `NS9W`, `NSRND`, `NSRPT` | Pop-up OK, play/pause, iPod mode, shuffle, repeat | `NS9A`–`NS9E` are documented |
| `NSP1`–`NSP3`, `NSP<n> MEM` | Recall and store network presets | Strings only; 2012 models with network presets |
| `TFHDUP`/`DOWN`, `TFHDMCUP`/`DOWN`, `TFSTUP`/`DOWN`, `TPSTUP`/`DOWN` | HD Radio multicast, Sirius | North American models |
| `Z4SLP` | Sleep timer of a Zone 4 | String only; no recorded receiver has four zones |
| `PSBAL `, `PSSDB `, `PSSDI ` | Balance, Super Dynamic Bass, Source Direct | Stereo receivers and systems; strings only |
| `PSMDA ` | M-DAX | String only; Marantz's 2019 document gives `PSMDAX` ([below](#marantz)) |
| `CLK` | Show the clock | Systems |
| `IRTX…` | Forward an IR code to a connected Denon CD or Blu-ray player or amplifier | Model-dependent codes |
| `RCKSK0410859`, `RCRC51601520` | InstaPrevue | Models with InstaPrevue |
| `IP9…` | Dock | Obsolete |
| `BDPLAY`, `BDPAUSE`, `BDSTOP`, … | Built-in disc player of systems | Outside this integration |

Confirmed by the apps and already used here: `PWON`, `PWSTANDBY`, `ZM?`,
`MU`, the `MS…` sound modes, the menu keys `MNCUP`, `MNCDN`, `MNCLT`,
`MNCRT`, `MNENT`, `MNRTN`, `MNINF`, `MNOPT`, `MNMEN ON`/`OFF`, `NSFRN`,
`PSBAS`, `PSTRE`, `Z2SLP`, `Z3SLP`, `TFANUP`/`DOWN`, `TPANUP`/`DOWN`, and
`MSQUICK<n>`, `Z2QUICK<n>`, `Z3QUICK<n>` (put together at run time, sent to
receivers without the HTTP command for Quick Select).

## Marantz

- The current Denon and Marantz apps are one code base built twice. The
  build decides which brand the app shows and changes names in the app
  (Quick Select is called Smart Select on Marantz); commands, endpoints and
  values are the same.
- **Smart Select** goes over HTTP with the same command as Quick Select
  (`SetQuickSelect`) where the receiver offers it; receivers without it
  (up to 2013) get `MSQUICK<n>` over `formiPhoneAppDirect.xml`, whatever the
  brand. No app contains `MSSMART`. Over telnet Marantz's 2019 document gives `MSSMART1`–`5`,
  `MSSMART<n> MEMORY`, `MSSMART0` (cancel) and `MSSMART ?`, and `Z2SMART…`
  for Zone 2; this integration follows it on Marantz receivers from level
  0300 on ([Quick Select and Smart Select](03-command-reference.md#quick-select-and-smart-select)).
- **M-DAX** is Marantz's counterpart of the restorer, with the same steps.
  The app sends it on a path of its own, and the zone status reports
  `RestorerMode` and `MDAX` separately. Marantz's 2019 document gives
  `PSMDAX OFF`, `LOW`, `MID`, `HI` and `PSMDAX ?` for all its models. On
  the 2010 AV7005 and SR7005 the older app sends M-DAX as the restorer.

## HEOS generation: UPnP service ACT

The device description at `:60006/upnp/desc/aios_device/aios_device.xml`
lists the service `urn:schemas-denon-com:service:ACT:<version>`. Its
actions for a receiver (**App**, **To test**):

| Action | Content |
|---|---|
| `GetAudioConfig`, `SetAudioConfig` | Sound mode, available sound modes, high- and low-pass, subwoofer, output mode, tone, height/width/tilt with maximum and default, Dirac filters, active and available input and output channels |
| `GetAvrZoneStatus`, `SetAvrZoneStatus` | Per zone (1–3): name, active, power (ON, STANDBY, OFF), source, available inputs, signal format, sample rate |
| `GetSurroundSpeakerConfig`, `SetSurroundSpeakerConfig` | Per speaker pair: enabled, crossover, low-pass, phase, distance (m or ft), level, test tone |
| `GetCurrentState` | Whole state, for event subscription |
| Others | Quick Select recall, store and names, volume limit, power, tuner, TV and remote settings, Bluetooth, LED, low latency, firmware, network |

The channel lists use 19 input and 32 output positions, Dolby-enabled and
top positions included. This integration gets the same information from
`/ajax/info` on the setup config API and does not use ACT.

## HEOS generation: Protobuf channel

Newer firmware also speaks a Protobuf protocol over TCP. Devices find each
other by multicast on `239.255.255.250` and `ff02::ba4f:e34a`; the address
and port come in the discovery message. Messages with a receiver part:
zone configuration (count, name, input, power, active), audio
configuration (sound mode, supported modes, signals, filters, tone),
device power (ECO, network standby, deep standby, sleep per zone) and the
switches for the remote app, the HEOS app and the streaming services.
Field numbers were not read; nothing in this integration needs the
channel while telnet and `/ajax` cover the same state.

## Sound modes

The HEOS app's model knows 108 sound modes, among them separate entries
for an input format with an upmixer (Dolby Digital Plus with Dolby
Surround, DTS-HD with Neural:X, IMAX DTS:X with Virtual:X), MPEG-H,
Auro-3D and the 7.1 channel input, and separate 2022 variants of the Dolby
Surround upmixer. A fixed table would therefore age with every model year;
this integration reads the modes a receiver offers (`OPSML`).

## Limits

- The code shows what an app sends, not what a receiver accepts. Value
  ranges and value lists are not in the apps; receivers publish them
  ([HTTP control](11-http-control.md#deviceinfoxml)).
- Some tokens are strings without a traceable call site (`MVLONG…`,
  `PSBAL`, `PSSDB`, `PSSDI`, `PSMDA`, `NSP<n> MEM`, `Z4SLP`); they may be
  unused.
- The apps cover about 120 telnet tokens, 70 of them IR, dock and disc
  player codes. For the telnet command set of a model year, the protocol
  documents stay the reference ([References](references.md)).
