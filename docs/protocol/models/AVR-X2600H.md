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

No owner's manual is in the project sources yet; it would settle the network
standby menu name, RS-232C and the Zone 2 source limits.

## Interfaces

| Interface | Status |
|---|---|
| Telnet 23 | Documented; Tested on AVR-X3600H |
| HTTP 8080: goform, `Deviceinfo.xml`, StatusLite | Tested on AVR-X3600H |
| UPnP 60006 | Tested on AVR-X3600H |
| HEOS 1255 | Sourced; Tested on AVR-X3600H |
| Setup config API 10443 | Tested on AVR-X3600H |
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

## Open points

| Point | Status |
|---|---|
| Full `FuncName` list and `CommApiVers` | To test (diagnostics file) |
| Concurrent telnet sessions | To test |
| Channel tokens for the Atmos layouts (`TFL`, `FDL`, `SDL`, `SW2` …) | To test |
| `VSMONI` for the two outputs | To test |
| Zone 2 sources and independent `NET` playback | To test |
| HEOS: URL playback, Quick Select commands, `serial`, volume mapping | To test |
| Setup config API on 10443 or 11080 with current firmware | To test |
| Calibration port 1256 open | To test |

The maintainer's plan uses this receiver for the first beta test. Save the
diagnostics file and an anonymised telnet log during that test; both become
test fixtures (roadmap item 29).
