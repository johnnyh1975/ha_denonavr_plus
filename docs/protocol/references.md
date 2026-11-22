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
| <https://github.com/ol-iver/denonavr> | Library behind the official integration: port detection, `AppCommand` interfaces, telnet handling, SSDP |

## Community

| Source | Used for | Caution |
|---|---|---|
| <https://github.com/alsak0de/marantz-denon-mqtt> | Telnet and HEOS notes from a Marantz Cinema 70s; the setup config API on port 11080; undocumented status replies | One model; some HEOS command names are not in the specification; `PSSWL` scale misread (see [command reference](03-command-reference.md#value-encodings)) |
| <https://github.com/handijk/denon2mqtt> | Further telnet bridge implementation | Not analysed here yet |
| <https://github.com/namornoditz/denon_avr_parameter> | Parameter handling | Not analysed here yet |
| <https://github.com/TheFab21/ha-denon-marantz-avr> | Alternative Home Assistant integration | Not analysed here yet |
| ioBroker Denon adapter, openHAB community | Single telnet connection on older models | Second-hand statements |

## Gaps in the sources

- No control protocol document for any HEOS-era model after 2015.
- No owner's manual for either reference receiver.
- No recorded data from an AVR-X2600H.
- The three community repositories marked "not analysed" were listed in
  the planning paper but are not in the project sources.
