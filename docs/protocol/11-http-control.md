# HTTP control (goform)

Besides telnet, every networked Denon and Marantz receiver answers HTTP on
port 80 (until 2015) or 8080 (from 2016; [which port](02-network-and-protocols.md#which-port)).
Denon's and Marantz's own apps control the receiver almost entirely over
this interface, not over telnet ([manufacturer apps](12-manufacturer-apps.md)).
This page describes what the interface offers per generation, the
self-description in `Deviceinfo.xml`, and what this integration uses.

Sources: the recorded `Deviceinfo.xml` files in `tests/fixtures/denonavr/`
(**Documented**), the code of Denon AVR Remote 3.13.4 and Denon/Marantz
Remote App 1.1.9 (**App**, see [References](references.md#manufacturer-apps)),
and the `denonavr` library. Command and parameter names are wire facts;
none of this page is copied from the apps.

## Interfaces by generation

The interface level a receiver reports in `CommApiVers` decides which
pages and commands it has. The apps branch on the same values.

| `CommApiVers` | Self-description | Status | Commands |
|---|---|---|---|
| none (2010–2012) | No `Deviceinfo.xml`: the recorded AVR-1912, AVR-2312CI, AVR-3311CI, AVR-3312 and AVR-4810 answer with an error page. Denon's apps shipped their own description files for these models ([receivers before 2013](models/before-2013.md)) | `formMainZone_MainZoneXml.xml`, `formMainZone_MainZoneXmlStatus.xml`, `formZone<n>_Zone<n>XmlStatus.xml` | `formiPhoneAppDirect.xml?<telnet command>` and the `formiPhoneApp…` pages |
| 0210, 0220, 0250 (2013, 2014 entry models) | `Deviceinfo.xml` with few setup functions | `AppCommand.xml` | `AppCommand.xml`, `formiPhoneAppDirect.xml` |
| 0300 (2014–2015) | `Deviceinfo.xml` with the full setup | `AppCommand.xml`, `AppCommand0300.xml` | as above, plus `AppCommand0300.xml` for every setup function |
| 0301 (2016 on) | as 0300 | as 0300 | as 0300; the [setup config API](02-network-and-protocols.md#setup-config-api-https-10443-http-11080) and HEOS come on top |

The apps no longer support receivers without a level: Denon AVR Remote
3.13.4 and even the Remote App 1.1.9 hide them (**App**). The current apps
also turn away the receivers of 2012 to 2014 by model name and point to
the older app, so they do not use their paths for levels 0210, 0250 and
the 2014 units of 0300; those 0300 receivers still list the signal commands
under `StatusInfo` in their own `Deviceinfo.xml`
([model years](12-manufacturer-apps.md#model-years-the-current-apps-accept)).
From level 0200 the apps take the volume step from the receiver instead of
a fixed 0.5 dB.

## AppCommand.xml and AppCommand0300.xml

Both are `POST` requests with the content type `text/xml; charset="utf-8"`.
The body is a `tx` element with one `cmd` element per command; commands of
`AppCommand0300.xml` name the parameters they ask for. The receiver answers
with an XML document per command. The `denonavr` library builds the same
requests (`appcommand.py`).

### AppCommand.xml: zones, power, volume, names

Used by the apps for the zone status from level 0200 on (**App**):

| Command | Purpose |
|---|---|
| `GetAllZonePowerStatus`, `GetAllZoneVolume`, `GetAllZoneMuteStatus`, `GetAllZoneSource`, `GetAllZoneStereo` | State of every zone at once |
| `GetPowerStatus`, `GetVolumeLevel`, `GetMuteStatus`, `GetSourceStatus`, `GetSurroundModeStatus`, `GetRestorerModeStatus`, `GetNetAudioStatus` | State of one zone |
| `GetZoneName`, `GetRenameSource`, `GetDeletedSource` | Zone names (`zone1`–`zone3`), renamed and hidden inputs (`use`/`DEL`) |
| `SetAllZonePower`, `SetAllZoneMute`, `SetAllZoneVolume`, `SetAllZoneStereo` | All zones at once; All Zone Stereo takes `zones` and `selections` |
| `SetPower`, `SetInputFunction`, `SetSurroundMode`, `SetMaxVolume`, `SetQuickSelect`, `SetQuickSelectMemory`, `SetQuickSelectName`, `SetZoneName` | Single changes |
| `GetVolumeLimit`, `SetVolumeLimit`, `GetVariableOutLimit` | Volume limit |

### AppCommand0300.xml: setup functions

Every setup function of the apps goes through `AppCommand0300.xml`
(**App**). The parameter names are the ones the apps ask for and send. The
last column says how this integration reaches the same function today.

| Function | Commands | Parameters | This integration |
|---|---|---|---|
| Audyssey | `GetAudyssey`, `SetAudyssey`, `GetAudysseyEQCurveType` | `multeq`, `dynamiceq`, `dynamicvol`, `reflevoffset`, `audysseylfc`, `containamount`, `surroundlevelcompensation`; curve list `multeqlist` | Telnet; MultEQ, Dynamic EQ and Dynamic Volume read here while telnet is down; the Dynamic EQ surround level compensation is not offered |
| Surround parameters | `GetSurroundParameter`, `SetSurroundParameter`, `GetIMAXList`, `GetDynaCompList` | `cinemaeq`, `loudness`, `dialogcontrol`, `dtsneux`, `cspread`, `spvirtual`, `spsel`, `effectlev`, `delaytime`, `lfe`, `dyncomp`, `panorama`, `dimension`, `centerwidth`, `centerimage`, `roomsize`, `chexpander`, `auro3dpre`, `auro3dstr`, `auro3dmode`, `imax`, `imaxaud`, `imaxhpf`, `imaxlpf`, `imaxswm`, `imaxswo`, `sw` | Telnet; the channel expander is not offered |
| Signal information | `GetInputSignal`, `GetActiveSpeaker`, `GetAudioInfo`, `GetVideoInfo`, `GetAudyssyInfo`, `GetDiracLiveInfo` | `inputsigall` and `inputsig<a–e><1–4>`, `activespall` and `activesp<a–e><1–4>`, `inputmode`, `output`, `signal`, `sound`, `fs`, `videooutput`, `hdmisigin`, `hdmisigout`, `eqname`, `eqvalue`, `filter`, `basscontrol` | `/ajax/info` on receivers from 2016; nothing for 2014–2015 receivers |
| Dialog level | `GetDialogLevel`, `SetDialogLevel` | `status`, `adjust`, `level` | Telnet `PSCLV` (`PSDIL` until 2015) |
| Subwoofer level | `GetSubwooferLevel`, `SetSubwooferLevel` | `adjust`, `sw1value`–`sw4value` (up to four subwoofers) | Telnet `PSSWL`, `PSSWL2` |
| Subwoofer | `GetSubwoofer`, `SetSubwoofer` | `subwoofer`, `level`, `value` | Telnet |
| Tone | `GetToneControl`, `SetToneControlType2`, `GetBassTreble`, `SetBassTreble` | `bassvalue`, `treblevalue`, `z2bass`, `z2treble`, `z3bass`, `z3treble` | Telnet for the main zone; zone tone is not offered |
| Zone levels | `GetLRchLevel`, `SetLRchLevel` | `z2lchlevel`, `z2rchlevel`, `z3lchlevel`, `z3rchlevel` | Telnet for Zone 2 |
| Channel levels | `GetChLevel`, `SetChLevel` | `chlists`, `ch`, `sptype`, `level` | Telnet `CV` |
| Channel level monitoring | `GetChannelLevelMonitoring`, `SetChannelLevelMonitoring` | `mode` | Not offered |
| Graphic EQ | `GetEQSetting`, `SetEQSetting`, `GetEQParameter`, `SetEQParameter`, `GetEQAdjustChList`, `GetEQOtherFunc` | `eqenable`, `speakerselection`, `adjustch`, `curvecopy`, `default` | Setup config API (10443/11080) |
| Restorer | `GetRestorerMode`, `SetRestorerMode` | `mode` | Telnet |
| Audio delay | `GetAudioDelay`, `SetAudioDelay` | `audiodelay`, `autolipsync` | Telnet |
| Bass Sync | `GetBassSync`, `SetBassSync` | `basssync` | Not offered (telnet `PSBSC`, 0–16, in the 2015 and Marantz 2019 documents) |
| Dirac Live | `GetDiracLive`, `SetDiracLive` | `diraclive`, `name1`–`name3` (filter names) | Telnet; filter names not shown |
| Dialogue Enhancer | `GetDialogEnhancer`, `SetDialogEnhancer` | `mode` | Telnet |
| Speaker preset | `GetSpeakerPreset`, `SetSpeakerPreset` | `preset` | Telnet |
| HDMI setup | `GetHdmiSetup`, `SetHdmiSetup` | `audioout`, `videoout` | Telnet |
| Video output | `GetOutputSettings`, `SetOutputSettings`, `GetResolutionList`, `GetResolutionHDMIList`, `GetResolutionAnalogList`, `GetIpScalerList` | `videomode`, `resolution`, `reshdmi`, `resanalog`, `ipscaler`, `hdmiupscaler`, `progmode`, `aspect`, `sharpness` | Telnet |
| Input mode | `GetInputSelect`, `SetInputSelect` | `inputmode`, `mode` | Telnet `SD` |
| Input names | `GetHideSources`, `SetHideSources`, `GetSourceRename`, `SetSourceRename`, `SetSourceRenameDefault`, `GetAutoRename` | – | Telnet `SSSOD`, `SSFUN` (read only) |
| Bluetooth transmitter | `GetBTTX`, `SetBTTX`, `GetOptionBTTX` | `transmitter`, `output`, `connect` | Telnet `BTTX` |
| ECO and standby | `GetECO`, `GetECOMeter`, `SetECOMode`, `SetECOPwOnDefault`, `SetECODisplay`, `GetAutoStandby`, `SetAutoStandby` | `enable`, `time` | Telnet `ECO`, `STBY` |
| Network standby | `GetNetworkStandbyStatus`, `SetNetworkStandby` | `networkstandby` | Not offered |
| Locks | `GetSetupLock`, `SetSetupLock`, `GetMenuLock` | `lock` | Not offered (a lock can shut out the owner) |
| Network services | `GetNetworkAirplay`, `SetAirPlay`, `GetSpotify`, `SetSpotify`, `GetNetworkInfo` | `airplay`, `spotify`, `connection`, `dhcp`, `ssid` and the addresses | Not offered |
| Firmware | `GetFirmware`, `SetFirmware`, `GetUpdateInfo`, `SetCheckUpdate` | `chkupd`, `allowupd`, `autoupd`, `timezone` | Update state over HEOS |
| Sound modes | `GetSoundMode`, `GetSoundModeList`, `SetSoundMode` | `genrelist`, `selected` | Telnet `OPSML`, `MS` |

The apps also have commands for stereo receivers and systems (speaker A/B,
source direct, audio filter, CD and disc players); they are outside this
integration's scope.

### Values

The apps hold no value tables for these commands. A receiver publishes,
per function, the list of values it accepts in `Deviceinfo.xml`, and the
apps send the `CmdNo` of the chosen entry ([value lists](#value-lists)).
The numbers are the same across models for the same function (MultEQ 0–3,
Dynamic Volume 0–3, Restorer 0–3, Dynamic Compression 0–4, room size 1–5).

## Legacy pages

Pages the apps read from receivers before level 0200, and on some later
ones for single functions (**App**):

| Page | Content |
|---|---|
| `formMainZone_MainZoneXml.xml` | Full main zone: power, input list with renames and hidden inputs, surround list, volume display, model id, sales area |
| `formMainZone_MainZoneXmlStatus.xml`, `formMainZone_MainZoneXmlStatusLite.xml`, `formZone<n>_Zone<n>XmlStatus(Lite).xml` | Zone status; the Lite page is this integration's HTTP poll |
| `formiPhoneAppPower.xml`, `…Volume.xml`, `…Mute.xml`, `…Function.xml`, `…Tone.xml`, `…Tone_Status.xml`, `…SleepTimer.xml` | Single functions |
| `formiPhoneAppTuner.xml`, `…TunerPreset.xml`, `…Tuner_StatusXml.xml`, each also with `_DAB`, `_HDRadio`, `_SIRIUS`, `_XM` | Tuner status and presets |
| `formiPhoneAppNetAudio_StatusXml.xml`, `…NetAudioCommand.xml`, `…iPod_StatusXml.xml`, `…Favorite_Call.xml`, `…Favorite_StatusXml.xml`, `…CD.xml` | Network audio, dock, favourites, CD |
| `formiPhoneAppDeviceSearch.xml`, `formiPhoneAppControlJudge.xml` | Identification of receivers without `Deviceinfo.xml` (elements `Model` and `iPhoneControl`) |
| `/NetAudio/art.asp-jpg` | Cover art of the network source |

On HEOS-era firmware the now-playing pages answer 403
([HTTP](02-network-and-protocols.md#http-port-80-or-8080)).

## Deviceinfo.xml

The receiver's self-description. Elements this page relies on, as recorded:

| Element | Meaning | Recorded values |
|---|---|---|
| `CommApiVers` | Interface level ([above](#interfaces-by-generation)) | 0210, 0220, 0250, 0300, 0301 |
| `ModelName` | Model; receivers on port 80 put a `*` in front (`*AVR-X2000`) | — |
| `BrandCode` | 0 Denon, 1 Marantz; the apps also know 2 McIntosh and 3 D&M Professional | 0, 1 |
| `ProductCategory` | 01 AV receiver, 02 system; the apps also know 07 stereo receiver and treat it as an AV receiver | 01, 02 |
| `DeviceZones` | Number of zones | — |
| `FuncName` | Names of the functions the receiver has; the apps know 481 element names | — |
| `MinValue`, `MaxValue`, `StepValue`/`Step`, `DefaultValue` | Value range of a function | [below](#value-ranges) |
| `List`/`Value` with `CmdNo` and `DispName` | Values a function accepts | [below](#value-lists) |

The apps tell Denon from Marantz by `BrandCode` (or, without it, by the
manufacturer prefix of the MAC address); the package of the app plays no
part (**App**).

### Value ranges

Ranges the recorded receivers publish, where they differ from a fixed
assumption or between models:

| Function | Range, step | Receivers |
|---|---|---|
| Audio delay | 0–500 ms, 1 | All recorded 0301 units and the SR6011 |
| Audio delay | 0–200 ms, 1 | AVR-X2100W, AVR-X4100W |
| Effect level | 1–15, default 10 | AVC-X8500H, AVC-A10H, AVC-X3700H, AVR-X4500H. Over telnet an AVR-X2600H and an AVR-X2000 report `PSEFF 00` outside DSP modes |
| Auro-Matic 3D strength | 1–16, default 10 | AVC-X8500H, AVR-X4500H |
| Auro-Matic 3D strength | 0–15, default 12 | AVC-A10H |
| Bass Sync | 0–16, default 0 | AVC-X8500H, AVC-A10H, AVR-X4500H |
| Delay time | 0–300, default 30 | AVC-X8500H, AVC-A10H, AVC-X3700H, AVR-X4500H |
| Dialog control | 0–6 | all 0301 units and the SR6011 |
| Containment amount | 1–7, default 4 | all 0301 units except the NR1609, and the SR6011 |
| LFE | −10–0, 1; with the 7.1 channel input 0–15 in steps of 5 | all 0301 units and the SR6011 |
| Zone 2 and Zone 3 left/right level | −12–+12, 1 | all units with the function |
| Zone 2 and Zone 3 bass and treble | −10–+10, 1; on the AVR-X4100W −14–+14 in steps of 2 | all units with the function |
| Graphic EQ | −20–+6 dB, 0.5 | all units with the function |
| Master volume | up to 98, 0.5 | all units with the function |

### Value lists

Lists that differ between models. The numbers are the `CmdNo` the apps
send over HTTP; over telnet the same choices have tokens.

| Function | Difference | Telnet |
|---|---|---|
| Video mode | Bypass only on the AVC-X3700H and AVC-A10H | `VSVPMBYP`; Denon's 2020 table does not list it for the AVR-X6700H |
| Output resolution | The AVC-X3700H offers 8K but neither 1080p:24Hz nor 4K (60/50); the NR1609 has no 4K (60/50) | `VSSC…`, `VSSCH…`; the 8K token is in none of the documents here |
| Input mode | eARC missing on the AVC-X8500H and SR6011 | `SDEARC` |
| Auro-Matic 3D preset | Movie only on the AVC-X8500H and AVC-A10H | `PSAUROPR MOV` |
| Effect speakers | Floor and Floor & Height | `PSSP:FL`, `PSSP:HF` (Denon 2020, Marantz 2019) |
| HDMI video output | Named Monitor 1/2 or TV 1/2 (AVC-A10H); same values | `VSMONI` |

This integration uses fixed ranges and value lists from its profile for
these functions ([what it reads](#what-this-integration-uses)).

## What this integration uses

- `Deviceinfo.xml`: model, MAC, zones, `CommApiVers` (port and
  [value encodings](03-command-reference.md#differences-between-protocol-versions)),
  function names to decide which entities exist, the master volume scale,
  the bass and treble range, the ranges published as `MinRange`/`MaxRange`
  (channel, subwoofer and dialog levels) and the option labels of the
  functions it knows. `MinValue`/`MaxValue` ranges and `CmdNo` lists are
  not read yet.
- `formMainZone_MainZoneXmlStatusLite.xml` and the zone pages: the poll
  while telnet is down, every 10 seconds.
- `formiPhoneAppDirect.xml`: commands while telnet is down.
- `AppCommand.xml` (`GetSurroundModeStatus`, `GetToneControl`, from level
  0210) and `AppCommand0300.xml` (`GetAudyssey` for MultEQ, Dynamic EQ and
  Dynamic Volume, from level 0300): read with the poll while telnet is down,
  so these settings stay available. The request and answer formats are the
  `denonavr` library's; the tone and sound mode answers are recorded there,
  the Audyssey answer is not. The restorer is not read (no answer format
  known).
- The UPnP descriptions for firmware and serial number.

While telnet is up it sends no `AppCommand` requests; telnet carries state
and commands, with push updates the HTTP interface does not have.
`AppCommand0300.xml` is the way to the signal information of 2014–2015
receivers, which have no setup config API.
