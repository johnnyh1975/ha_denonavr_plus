# Capability matrix

The central comparison of the two reference receivers. Labels as defined in
the [overview](../README.md#verification-status). "Tested on AVR-X3600H"
means the integration confirmed it on that model of the same generation;
it remains To test on the AVR-X2600H. "Tested (3.139.175)" is the capability
check on an AVR-X2600H with that firmware
([model page](AVR-X2600H.md#capability-check-firmware-3139175)).

## Hardware and formats

| Capability | AVR-X2000 (2013) | AVR-X2600H (2019) |
|---|---|---|
| Amplifier channels | 7 (Sourced) | 7 (Sourced) |
| Subwoofer pre-outs | Unverified | 2 (Sourced) |
| Zones | Main + Zone 2 (Documented: `DeviceZones` 2) | Main + Zone 2, pre-out and speaker-assignable (Sourced) |
| Zone 3 | Absent (`DeviceZones` 2) | Absent (one Zone 2 output in the product sheet) |
| Dolby TrueHD, DTS-HD | Documented (decoder modes in the protocol) | Sourced |
| Dolby Atmos, DTS:X | Absent (no such mode in its protocol document) | Sourced |
| DTS Virtual:X, Dolby height virtualization | Absent (as above) | Sourced |
| Room correction | Audyssey MultEQ XT (Sourced) | Audyssey MultEQ XT (Sourced) |
| HDMI in / out | 7 / 1 (Sourced) | 8 / 2 (Sourced) |
| 4K | Upscaling, pass-through (Sourced) | 4K/60 pass-through, upscaling (Sourced) |
| HDR10, HLG, Dolby Vision | Unverified, expected absent | Sourced |
| ARC / eARC | ARC Documented (`SDARC`); eARC Unverified, expected absent | eARC (Sourced) |
| Phono input | Absent (no `PHONO` source in its document) | Sourced |
| USB | Sourced (`USB/IPOD` source Documented) | Sourced (front) |

## Network functions

| Capability | AVR-X2000 | AVR-X2600H |
|---|---|---|
| Ethernet | Sourced | Sourced |
| Wi-Fi | Unverified | Sourced |
| Bluetooth | Absent (no `BT` source, not in the device description) | Sourced |
| AirPlay | AirPlay 1 (Sourced) | AirPlay 2 (Sourced) |
| HEOS Built-in | Absent (generation; no port 1255) — confirm with the port test | Sourced |
| Spotify | `SISPOTIFY` (Documented, North America and Europe) | Spotify Connect (Sourced) |
| Internet radio, DLNA | Sourced | Sourced |
| Voice assistants | Not in its sources | Alexa, Google Assistant, Siri via AirPlay (Sourced) |

## Control interfaces

| Interface | AVR-X2000 | AVR-X2600H |
|---|---|---|
| Telnet, port 23 | Documented | Tested (3.139.175) |
| Concurrent telnet sessions | To test | To test |
| goform HTTP | Port 80 (Documented, recorded responses) | Port 8080 (Tested on AVR-X3600H) |
| `AppCommand.xml` | Documented (recorded responses) | Documented (library) |
| `AppCommand0300.xml` (Audyssey) | To test, expected absent | Documented (library) |
| UPnP/AIOS description, port 60006 | Absent | Tested on AVR-X3600H |
| HEOS CLI, port 1255 | Absent | Sourced; Tested on AVR-X3600H |
| Setup config API, 10443 | Absent | Tested (3.139.175) |
| Calibration, port 1256 | Absent | To test |
| RS-232C | North America model only (Documented) | To test; not in the product sheet's terminal list |
| SSDP discovery | Documented (library) | Documented |
| Network standby | "IP Control" (Documented) | "Network Control" (To test) |

## Control functions

| Function | AVR-X2000 | AVR-X2600H |
|---|---|---|
| Power, main zone, volume, mute, source | Documented | Tested (3.139.175) |
| Zone 2 power, volume, mute, source | Documented | Documented |
| Zone 2 level trim, sleep, Quick Select | Documented | Tested (3.139.175) |
| Zone 2 auto standby | Absent (not in its document) | Tested (3.139.175) |
| Zone 2 tone, HPF | Absent (not in its document) | No reply (3.139.175) |
| Independent Zone 2 source | To test | To test |
| All Zone Stereo set | Documented (`MNZST ON/OFF`) | Documented; Tested on AVR-X3600H |
| All Zone Stereo read | From `MS` event only (Documented) | `MNZST ?` Tested on AVR-X3600H; no reply on AVR-X2600H (3.139.175) |
| Sound mode set | Documented, fixed list | Documented; list from receiver (Tested on AVR-X3600H) |
| Quick Select | 4 in the device description, 5 in the protocol (To test) | Documented |
| Audyssey MultEQ mode, Dynamic EQ, ref. level, Dynamic Volume | Documented | Documented |
| Audyssey LFC | Absent | No reply (3.139.175; 2015 doc: top model only) |
| Tone, loudness | Documented | Documented |
| Channel levels | Documented, 11 channels | Tested (3.139.175): 18 channels and `SW` |
| Subwoofer level `PSSWL` | Absent (not in its document) | Documented |
| Speaker distances, crossovers, sizes | Menu only | `SS…` and setup config API (Tested on AVR-X3600H) |
| Graphic EQ | Absent | Setup config API (Tested on AVR-X3600H) |
| Dialog level, dialog control | Absent | Dialog control Tested (3.139.175); `PSDIL ?` no reply |
| Video resolution, processing, aspect | Documented | Documented |
| HDMI monitor out select | Absent (one output) | `VSMONI ?` answers (Tested, 3.139.175) |
| Picture mode and adjustments | Documented, two-digit values | Documented, three-digit values |
| HDMI-CEC, ARC, OSD setup | Menu only | Setup config API (Tested on AVR-X3600H) |
| ECO, auto standby, dimmer | Absent (not in its document) | Documented (2015 doc); To test |
| 12 V trigger | Unverified | To test |
| Network transport keys | Documented | Documented; HEOS preferred |
| Now playing | `NSE` lines, goform port 80 (Documented) | HEOS (Tested on AVR-X3600H) |
| Play a URL | Absent | HEOS CLI 1.9+ (To test) |
| Firmware update check | Unverified | HEOS CLI 1.10+ (To test) |
| Tuner | Documented (presets `A1`–`G8` per its document; To test) | Documented (2015 doc); To test |
| Remote and panel lock | Documented | Documented |

## Home Assistant support

| Integration | AVR-X2000 | AVR-X2600H |
|---|---|---|
| `denonavr` (core) | Listed as supported (Sourced) | Listed as supported (Sourced) |
| `heos` (core) | n/a | Supported |
| `denonavr_plus` (this) | Not yet: port 80 and function-list gating | Supported generation |

Update this page with every test result: replace "To test" by "Tested
(firmware, date)" or by "Absent".
