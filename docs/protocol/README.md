# Receiver protocols and capabilities

Source-based reference for the network interfaces of Denon AV receivers and
how this integration uses them. It is written for two receivers in
particular, the **AVR-X2000** (2013) and the **AVR-X2600H** (2019), because
they sit on either side of the 2016 generation change that decides which
interfaces a receiver has.

The rule behind every page: a function counts as controllable only when a
documented network command exists for it, or when it was tested on a
receiver. A setting that appears in the receiver's menu proves nothing.

## Pages

| Page | Content |
|---|---|
| [Overview](01-overview.md) | The two receiver generations, what changed in 2016, what this means for the integration |
| [Network and protocols](02-network-and-protocols.md) | Every port and interface, per generation, with framing and timing rules |
| [Command reference](03-command-reference.md) | Telnet grammar and the differences between protocol document versions |
| [Zones and multiroom](04-zones-and-multiroom.md) | Main zone, Zone 2, All Zone Stereo, and how they differ from HEOS groups |
| [HEOS](05-heos.md) | The HEOS CLI on port 1255: players, groups, events, limits |
| [Audio, Audyssey, speakers](06-audio-audyssey-speakers.md) | `PS…`/`CV…` commands, the setup config API, value encodings |
| [Video and HDMI](07-video-hdmi.md) | `VS…`/`PV…` commands and their encoding change |
| [Network media and services](08-network-media-and-services.md) | `NS…` transport, on-screen text, presets, tuner |
| [Status, diagnostics, discovery](09-status-diagnostics-and-discovery.md) | Identification, discovery, and the test procedure for both receivers |
| [Home Assistant](10-home-assistant.md) | Official integrations, this integration, gaps and next steps |
| [Capability matrix](models/capability-matrix.md) | The central model comparison |
| [AVR-X2000](models/AVR-X2000.md) · [AVR-X2600H](models/AVR-X2600H.md) | Model pages |
| [References](references.md) | All sources, with what each one is trusted for |

For the full token list with implementation coverage, see the
[control protocol reference](../../custom_components/denonavr_plus/doc/DENON_CONTROL_PROTOCOL.md)
that ships with the integration. These pages do not repeat it; they add the
per-generation and per-model view.

## Verification status

Every capability carries one of these labels.

| Label | Meaning |
|---|---|
| **Tested** | Confirmed on a named receiver, with model and firmware |
| **Sourced** | Stated in a manufacturer document or the official Home Assistant documentation |
| **Documented** | Described in a control protocol document or visible in a recorded receiver response |
| **To test** | Expected, but not yet checked on the target receiver |
| **Unverified** | No reliable statement possible |
| **Absent** | Excluded by a reliable source |

**Tested** sits above the other labels: a test on the receiver settles what a
document only describes. A test on one model does not carry over to another,
but a test on a model of the same generation is worth noting; the pages say
so where it applies (for example "Tested on AVR-X3600H, to test on
AVR-X2600H").

## Main findings

1. **The AVR-X2000 cannot be added today.** Its web interface answers on
   port 80; this integration reads the device description on port 8080 only
   ([details](02-network-and-protocols.md#http-port-80-or-8080)).
2. **Even with the port fixed, the AVR-X2000 would show few settings.** Its
   recorded device description lists 55 function names, against 117 on an
   AVC-X3700H (2020). Audyssey, tone, subwoofer and video functions are missing from
   the list, although the telnet protocol for the model documents commands
   for them. Entities are gated on that list
   ([details](models/AVR-X2000.md#what-blocks-it-in-this-integration)).
3. **The two receivers share the telnet grammar, not the interface set.**
   HEOS (1255), the UPnP description (60006), the setup config API (10443)
   and the calibration port (1256) exist only on the 2016 generation.
4. **"Only one telnet session" is not settled.** The official Home Assistant
   documentation and community sources say one session; this repository's
   README says both one session and several. The test in
   [Status and diagnostics](09-status-diagnostics-and-discovery.md#test-concurrent-telnet-sessions)
   settles it per model.
5. **Value encodings changed between protocol versions** (picture controls,
   the volume minimum, tuner preset numbers, Dynamic Volume values,
   `CV?` replies). Code that is correct for one generation can misread the
   other ([details](03-command-reference.md#differences-between-protocol-versions)).

## Not here

- History of this documentation: it lives in git, and changes to the
  integration in the [release notes](../../release-notes/).
- Copied manufacturer text: command tokens are wire facts and are listed;
  descriptions are written for this project.
