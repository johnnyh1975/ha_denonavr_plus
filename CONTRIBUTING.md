# Contributing to Denon AVR Plus

Bug reports, receiver tests and pull requests are welcome. Back to the
[README](README.md).

- **Something does not work?** See [troubleshooting](docs/USER_GUIDE.md#11-troubleshooting)
  first, then [open an issue](https://github.com/johnnyh1975/ha_denonavr_plus/issues)
  with the diagnostics file (Settings → Devices & services → Denon AVR Plus →
  ⋮ → Download diagnostics; addresses are replaced).
- **A model that is not confirmed yet?** Run the [receiver test](#test-the-receiver)
  and attach its diagnostics to the device-support issue.
- **Code?** Read [AGENTS.md](AGENTS.md): the checks every change has to pass
  (`bash scripts/check_all.sh`) and the rules for tests, private data and
  protocol facts.

## Working models

| Receiver | Firmware | Status | How we know |
|---|---|---|---|
| Denon AVR-X3600H | 3.88.614 | Works | Protocol verified live by the original author (Home Assistant 2026.7) |
| Denon AVR-X2600H | 3.139.175 | Works | Capability check and receiver tests with PCM and Dolby Digital ([recording](tests/fixtures/command_test/AVR-X2600H.json)) |
| Denon AVR-X2000 | | Works, with limits | Capability check ([recording](tests/fixtures/capability_check/AVR-X2000.json)). Most settings stay hidden, as on receivers before 2016 in general |

Siblings of these models count as checked too, so setup asks no check of
them: the AVR-E400 (same protocol document as the AVR-X2000) and Denon's
other 2019 receivers, the AVR-X1600H, AVR-S950H, AVR-S750H and AVR-S650H (by
Denon's app compatibility list), and Marantz's 2019 AV receivers SR6014,
SR5014, NR1710 and NR1510 (by Marantz's protocol document for that year).
Nobody has reported on them yet; a report is welcome.

Receivers of the HEOS generation (2016 on) should work, as the integration
adapts to what the device reports. Receivers from before 2016 work with fewer
settings. A receiver without a network port cannot be reached at all.

**Report your receiver** in the Discussions category
[Receiver reports](https://github.com/johnnyh1975/ha_denonavr_plus/discussions/categories/receiver-reports):
model, firmware and how well it works, with the diagnostics of a receiver
test if you can. Each report goes into this list.

## Test the receiver

Configure → **Test the receiver** opens a page that
says what happens: three steps, about six minutes, and everything that changes
is put back. **Start** begins:

1. **Read capabilities**: about 150 read-only questions over the
   integration's own connection (nothing is changed, commands from Home
   Assistant still go first), about a minute and a half.
2. **Test quietly**: settings change for a moment and are put back (see
   below), about four minutes.
3. **Test what you hear and see**: asked for after the quiet part, with
   **Test now** and **Skip**. It needs someone in front of the TV.

Each step has its own page (step x of 3) with a checklist, the setting being
tested right now, and one progress for the whole test. Closing the window
stops the test and puts back what it changed. The result page is titled
**Everything put back**, or **Check the receiver** with the settings that did
not come back. It counts the questions answered and the commands confirmed,
with a missing precondition, without effect, or skipped, and links to a
receiver report in Discussions with model, firmware and version filled in;
attach the diagnostics file there (addresses are replaced). GitHub documents
filling in forms from the link for issues only, so for discussions the fields
may come up empty. Nothing is sent anywhere by itself.

The test is optional. Only for a device that is not a described AV receiver
(a system, or an interface level not seen yet;
[Compatibility](docs/COMPATIBILITY.md)) does setup offer it once the
receiver is connected (on by default; the first two parts, in the
background), and a repair runs the same three parts later. A message then
points to the issue.

### What a command test does

Each command test reads a setting, writes a different valid
value, waits up to 2 seconds for the receiver to confirm it, asks again, then
writes the old value back and checks that it is back. Some settings need
another one: bass and treble are tested with tone control on for the moment,
the picture adjustments in the custom picture mode. What cannot be set quietly
counts as a missing precondition instead of a failure: Audyssey settings with
MultEQ off (it needs a calibration), Zone 2 settings with Zone 2 off.

- **Quiet tests**: settings you hardly notice for a few seconds: tone,
  Audyssey, surround and picture settings, timers, the display, ECO, Zone 2
  levels, and the main zone's volume 0.5 dB down and back.
- **Audible and visible tests**: mute, MultEQ and Dirac, front speakers,
  speaker preset, HDMI output and audio output, output resolution, input mode,
  volume limit, All Zone Stereo, Zone 2 following the main zone, a Zone 2 quick
  select, the tuner's frequency and preset (only while a zone plays the tuner),
  and the setup menu opened and closed on the TV.

Never tested: power, the 12 V triggers, the Bluetooth transmitter, the input,
the sound mode, and anything that stores or resets (tuner preset and quick
select memory, channel level reset, network restart). A receiver in standby
ignores settings, so a main zone that is off is turned on for the test and off
again afterwards; if it does not come on, its settings are skipped. A test that
is stopped (the
connection drops, Home Assistant stops) still puts its setting back. The result
per command is *confirmed* (echo and answer agree), *query_only* (no echo, but
the receiver reports the new value), *echo_only*, *no_effect*, *rejected*,
*no_value* (the receiver did not report the setting), *skipped* or
*interrupted*, with whether the old value came back. It goes into the
diagnostics file; attach it to the receiver report the result links to. Leave the receiver alone while it runs (a few minutes): a change made
on it at the same time can read as a failure.

The diagnostics file also shows how now playing and playback reach the
receiver (HEOS or telnet), which now-playing fields are filled, and the
receiver's last answer to each HEOS command it was sent, such as playing a URL.
Titles, artists and the cover address are left out.

At setup the integration asks the receiver which of its model-dependent
settings it has. The answers are kept: later starts do not ask again until the
model or firmware changes, or a new release adds a setting. A capability check
stands in for these questions too; after one, the next start uses its answers
(reload the integration to get settings it found).

### When setup fails

Where the integration cannot be set up at all, run
`python3 tools/probe_receiver.py <address>`: it asks the same questions and
more (web pages, HEOS, the setup interface), changes nothing, and writes a
summary plus the raw answers with addresses and serial numbers replaced. The
two lists of questions are kept equal by a test.
