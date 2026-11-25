# Compatibility

Which Denon and Marantz receivers work with Denon AVR Plus, and what differs
between them. The integration does not keep a list of models: every receiver
from 2013 on describes itself in `Deviceinfo.xml`, and the integration reads
its generation, brand and functions from there when it is added. Receivers
from 2010 to 2012 have no such file; they are read the way the official
integration reads them.

## How a receiver is recognised

`Deviceinfo.xml` tells three things:

- **Interface level** (`CommApiVers`): the generation. It sets the web port
  (80 or 8080) and how some values are written.
- **Brand** (`BrandCode`): Denon or Marantz. Marantz names a few functions
  differently.
- **Kind of device** (`ProductCategory`): AV receiver, or a system such as a
  network CD receiver.

The functions a receiver lists there (zones, inputs, Audyssey, video setup …)
decide which entities it gets. Receivers before 2014 list few of them; for
those the integration also asks the receiver over telnet when it is added and
offers what it answers.

A receiver from 2010 to 2012 answers `Deviceinfo.xml` with an error page.
Then, like the official integration (the `denonavr` library, receiver type
"avr"), the integration reads:

- model, manufacturer and serial number from the UPnP description on port
  8080 (the serial number is the receiver's id, as there is no MAC address);
- inputs, the names you gave them, which are in use, and the sound modes
  from the main zone page (`formMainZone_MainZoneXml.xml`);
- the zones from the answers to `Z2?` and `Z3?`, and every setting from its
  telnet answer, as for the 2013 receivers.

## Generations

| Generation | Interface level, web port | Denon | Marantz | Works |
|---|---|---|---|---|
| 2016 on, with HEOS | 0301, 8080 | AVR-X…00H, AVR-S…H, AVC-X, AVC-A | SR…12 and newer, NR…08 and newer, AV7703, AV8805 and newer, Cinema | Yes, everything |
| 2014–2016, without HEOS | 0300, 80 | AVR-X…100W, AVR-X…200W, AVR-X1300W to X3300W, AVR-S…W | SR…09 to SR…11, NR…05, NR1506, NR1606, NR1607, AV7702, AV8802 | Yes, without HEOS |
| 2014 entry models | 0250, 80 | AVR-X1100W | – | Yes, as 2014–2016 |
| 2013 | 0210, 80 | AVR-X…000, AVR-E…00, AVR-1713, AVR-1913, AVR-2113CI, AVR-3313 | SR…08, NR…04 | Yes, as 2014–2016, with the 2013 encodings |
| 2010–2012 | none, 80 | AVR-991, AVR-1912, AVR-2112CI, AVR-2312CI, AVR-3311CI, AVR-3312, AVR-4311CI, AVR-4810, AVR-A100 | NR1602, SR5006, SR6006, SR7005, AV7005 | Yes, as 2013, with the 2011 encodings; not tested on a receiver yet |

The model names are examples: by model year, from the manufacturer's apps and
the recorded receivers. What counts is the interface level the receiver
reports, not its name. Some 2012 models are in neither list (AVR-4520,
Marantz SR…07, NR…03, AV7701); setup shows whether they report a level.

Not covered: systems and network players (Marantz M-CR510, M-CR603 and
M-CR610, Denon DRA-N4, DNP-720, RCD-N7 and others with `ProductCategory`
2), and HEOS speakers.
Setup offers the receiver test for them (see below); some functions may
work.

### What differs by generation

The integration picks the right form on its own; nothing to set.

| | 2016 on | 2014–2016, 2014 entry | 2013 | 2010–2012 |
|---|---|---|---|---|
| Web port | 8080 | 80 | 80 | 80 |
| Now playing on network sources | HEOS: title, artist, album, cover, position, repeat, shuffle | from the on-screen list: title, artist, album, cover | as 2014–2016 | as 2014–2016 |
| HEOS music in the media browser, announcements over HEOS, firmware update notice | yes | – | – | – |
| Graphic EQ per band, video setup, amp assign, video signal sensors (setup web interface, port 10443 or 11080) | yes | not seen | not seen | – |
| Audio delay | 0–500 ms | 0–200 ms | 0–200 ms | 0–200 ms |
| Auto Lip Sync | `SSALS` | `SSHOSALS` | `SSHOSALS` | `SSHOSALS` |
| Center level | `PSCLV` | `PSDIL` | `PSDIL` | `PSDIL` |
| Restorer | `HI`, `MED`, `LOW` | `HI`, `MED`, `LOW` | AVR-X and AVR-E: `HI`, `MED`, `LOW`; other models: `MODE1`–`MODE3` | `MODE1`–`MODE3` |
| Dynamic Volume | Off, Light, Medium, Heavy | as 2016 | as 2016 | Midnight, Evening, Day (`NGT`, `EVE`, `DAY`) |
| Lowest volume | `MV00` | `MV00` | `MV00` | `MV99` |
| Picture adjustments | three digits | three digits | two digits | two digits |
| Tuner presets | 01–56 | 01–56 | A1–G8 (AVR-X2000 document) | A1–G8 |
| Functions named by the receiver | almost all | almost all (0300); few (0250) | few; the rest found by asking the receiver | none; all found by asking the receiver |

For the restorer, Mode 1 is High and Mode 3 is Low: so the receivers name
them in their own `Deviceinfo.xml` (AVR-1713, AVR-3313), and so Denon's
2015 document pairs them.

### Marantz

Marantz receivers use the same generations and the same commands, with three
differences the integration follows:

- **Smart Select** in place of Quick Select from interface level 0300 on
  (`MSSMART`, `Z2SMART`). Below 0300 (NR1604, SR5008) Marantz receivers keep
  Quick Select.
- **M-DAX** in place of the Restorer (`PSMDAX`: Off, Low, Mid, High). From
  level 0300 on these receivers list M-DAX under the name Restorer; the
  integration shows it as M-DAX only.
- Illumination, DAC filter and a few other Marantz settings appear when the
  receiver answers them.

## Tested, recorded, expected

| Status | Models |
|---|---|
| **Tested**: the receiver test ran on the receiver | AVR-X3600H, AVR-X2600H (2016 on), AVR-X2000 (2013) |
| **Recorded**: `Deviceinfo.xml` (from 2013) or the main zone page (2010–2012) of the model is in the tests | AVR-X4500H, AVC-X3700H, AVC-X8500H, AVC-A10H, NR1609, SR6012, AV7703 (2016 on); AVR-X2100W, AVR-X4100W, SR6011 (2014–2016); AVR-X1100W (2014); AVR-X4000, AVR-1713, AVR-3313, NR1604, SR5008 (2013); AVR-1912, AVR-2312CI, AVR-3311CI, AVR-3312, AVR-4810 (2010–2012) |
| **Expected**: same generation, not seen yet | every other Denon or Marantz AV receiver from 2010 on |

Protocol details per generation are in [docs/protocol](protocol/README.md):
[interface levels](protocol/01-overview.md#interface-levels-commapivers),
[value encodings](protocol/03-command-reference.md#differences-between-protocol-versions)
and the [receivers before 2013](protocol/models/before-2013.md).

## The receiver test is optional

For an AV receiver of a generation above, setup asks nothing about tests:
its commands are described. **Configure → Test the receiver** still runs the
test at any time. It is worth running:

- to confirm a model that is not tested yet, and share the result
  ([how](../CONTRIBUTING.md#test-the-receiver));
- after a firmware update;
- when a setting does not behave as expected, before you open an issue.

Setup offers the test, and a repair asks for it, only when the receiver is
not a described AV receiver: a system, or an interface level not seen yet
(a future generation).

The test covers every generation. Its first part asks every query of every
generation (about 155, the Marantz and 2011 forms included), so each
receiver answers the ones it knows. The command tests write the values in
the form that fits the receiver's generation and brand, and only for the
settings it answered. Recorded results exist for the AVR-X2600H (2016 on)
and the AVR-X2000 (2013) so far; a result from any other generation,
above all 2010–2012, 2014–2016 and Marantz, helps most.
