# References

What each source is trusted for. Manufacturer documents settle model facts
and documented commands; recorded receiver data settles what a receiver
actually sent; community sources are leads, never the only evidence.

## Manufacturer: control protocols

| Document | Covers | Where |
|---|---|---|
| Denon AVR control protocol v03 (rev 10.1.0), AVR-X2000/E400, 2013 | AVR-X2000 grammar, events, timing, RS-232C | Project sources (`avrx2000_e400_protocol1010_v03.pdf`) |
| Denon AVR control protocol v02, AVR-X1200W/X2200W/X3200W/X4200W, 2015 | Closest official reference for HEOS-era models; per-model and per-region availability | Project sources (`steuerungsprotokoll_ip_rs232c_avr-x1200w_avr-x2200w_avr-x3200w_avr-x4200w.pdf`) |
| Denon AVR control protocol v04, AVR-2113CI/1913 | Tuner preset correction, older encodings | <https://downloads.denon.com/documentmaster/us/avr2113ci_avr1913_protocol_v04.pdf> |
| Denon AVR control protocol v8.6.0, AVR-1713/1613, 2012 | Older encodings | <https://assets.denon.com/DocumentMaster/UK/AVR1713_AVR1613_PROTOCOL_V860.pdf> |
| Denon AVR control protocol v7.4.0, AVR-2112CI/1912, 2011 | Volume minimum `99`, Dynamic Volume `NGT/EVE/DAY` | <https://assets.denon.com/documentmaster/master/avr2112ci_avr1912_protocol_v740.pdf> |
| HEOS CLI Protocol Specification 1.14, 2019 | HEOS commands, events, errors, limits | Project sources (`heos_cli_protocolspecification-version.pdf`) |
| Denon AVR control protocol V06, AVR-S700W to AVR-X7200W, 2014 | Dates the encoding changes to 2014 (volume minimum, `CVEND`, picture values, tuner presets, `MNZST?`, `STBY`/`ECO`/`DIM`/`TR`, `PSSWL`, `VSMONI`) | <https://www.mikrocontroller.net/attachment/343965/IP_Protocol_AVR-Xx100.pdf>; read through an automated text extract, not page by page |
| AVR-X6400H … S730H protocol V01, 2017 | HEOS-era command set | Formerly `assets.denon.com/…/AVR-X6400H_X4400H_X3400H_X2400H_X1400H_S930H_S730H_PROTOCOL_V01.xlsx`; link dead |
| AVC-X6500H … S640H protocol V01, 2018 | Includes the AVR-X2500H, the direct predecessor of the AVR-X2600H | Named in the IPSymconDenon project; no working link found |
| FY21 AVR control protocol, Ver.02, last update 12 May 2020 | AVR-X2700H, X3700H, X4700H, X6700H and S960H: commands, values and per-model availability; the newest Denon document known and the closest to the AVR-X2600H (2019) | <https://assets.denon.com/documentmaster/us/fy21avr_denon_protocol_v02.xlsx>; read from a downloaded copy, sheet "COMMAND and RESPONSE list" |

## Manufacturer: product information

| Document | Covers |
|---|---|
| AVR-X2600H product information sheet (US) | Channels, HDMI, formats, terminals — <https://assets.denon.com/documentmaster/us/avr-x2600h_product_information_sheet.pdf> |
| AVR-X2600H product information sheet (UK) | Same, European edition — <https://assets.denon.com/documentmaster/uk/avr-x2600h%20product%20information%20sheet.pdf> |
| AVR-X2600H owner's manual | Not yet in the sources; needed for menu names and Zone 2 limits |
| AVR-X2000 owner's manual | Not yet in the sources; needed for Wi-Fi, outputs, amp assignment |

Hardware facts for the AVR-X2000 currently rest on retailer and review
specifications (B&H Photo, Trusted Reviews, Crutchfield). Replace them with
the owner's manual when it is added.

## Recorded receiver data

| Source | Covers |
|---|---|
| `ol-iver/denonavr` test data (`tests/xml/`), 262 files | `Deviceinfo.xml` and status pages of 28 recorded units, including two AVR-X2000; the basis for the port and `CommApiVers` statements |
| This repository's `tests/fixtures/denonavr/` | Copy of the library's 28 `Deviceinfo.xml` recordings, both AVR-X2000 units included |
| This integration's findings on the AVR-X3600H | Everything marked "Tested on AVR-X3600H" |

## Home Assistant

| Source | Covers |
|---|---|
| <https://www.home-assistant.io/integrations/denonavr/> | Official integration: ports 23, 8080, 60006; single telnet connection; options |
| <https://www.home-assistant.io/integrations/heos/> | Official HEOS integration |
| <https://github.com/ol-iver/denonavr> | Library behind the official integration: port detection, `AppCommand` interfaces, telnet handling (commands over telnet while connected, HTTP otherwise; keepalive `PW?` after 30 s, reconnect after 60 s), resynchronisation query list, value tables (levels, dimmer, auto standby, ECO, dialog enhancer, Bluetooth output, Auro, IMAX, Dirac, channel tokens), SSDP; `doc/XML_data_dump.txt` holds AppCommand captures of the Denon 2016 AVR Remote app on an AVR-X1500H |

## Code of the official integrations and their clients

| Source | Used for |
|---|---|
| Home Assistant core, `homeassistant/components/denonavr` | Discovery matchers, unique ids, polling, telnet use |
| Home Assistant core, `homeassistant/components/heos` | Discovery, options, `play_media` paths |
| <https://github.com/andrewsayre/pyheos> 1.0.6 | HEOS client: heart beat, reconnect, player identity, preferred host, `ip` field |
| <https://github.com/music-assistant/server>, `providers/heos` | URL playback as the main path, AVR zones as separate HEOS players, grouping limits |

## Community

| Source | Used for | Caution |
|---|---|---|
| <https://github.com/alsak0de/marantz-denon-mqtt> | Telnet and HEOS notes from a Marantz Cinema 70s (HEOS firmware 3.88.614): setup config API on port 11080, undocumented status replies, sound mode set/report forms, Smart Select instability, HEOS practical behaviour, IR fallback; code, tests and probe file | One model. The probe ran in standby and used query forms without the documented space, so its "unsupported" results are inconclusive. The HEOS bridge sends six command names that are not in the specification. `PSSWL` scale misread; `SSINFAISFSV` described two ways. The repository's `AUDYSSEY.md` and telnet guide have the same sections as the project copies |
| <https://github.com/handijk/denon2mqtt> | Telnet-only MQTT bridge, tested on an AVR-X2500H: one session, 60 ms command gap, query set `PW? ZM? MV? MU? SI? SV? MS? Z2? Z2MU?` | Its claim that the official integration uses only HTTP holds only with telnet off |
| <https://github.com/namornoditz/denon_avr_parameter> | Scripts reading undocumented status tokens (`NSFRN`, `SYSDVIN`, `SYSDVOUT`, `SSINFAISFOR`, `SSOUTFAISFOR`) on an AVC-X4700H; one short telnet connection per query | Script quality is basic; the tokens are the useful part |
| <https://github.com/TheFab21/ha-denon-marantz-avr> | Community Home Assistant integration on `denonavr` + pyheos: `/ajax/info` and `/ajax/control` on 11080, HEOS player matched by IP, telnet-gated settings, port list | Port list cites receiver documentation without naming it; models for the 11080 findings not named |
| ioBroker Denon adapter, openHAB community | Single telnet connection on older models | Second-hand statements |

## Gaps in the sources

- No control protocol document for any HEOS-era model after 2015.
- No owner's manual for either reference receiver.
- One recording each from an AVR-X2600H (firmware 3.139.175) and an
  AVR-X2000 (capability checks); no telnet log over time yet.
- The 2017, 2018 and 2020 Denon spreadsheets are not in the sources; the
  2018 one (with the AVR-X2500H) or the FY21 one would be the closest
  official reference for the AVR-X2600H. Adding either to the project
  sources would settle many "To test" entries for that model.
- The 2014 document was read through a text extract; check the PDF before
  code depends on a detail from it.
