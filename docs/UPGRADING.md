# Upgrading

What you have to do when you update Denon AVR Plus. Versions that need
nothing from you are not listed. What changed in each version is in the
[release notes](../release-notes/).

## From 0.5.x

If you imported the blueprint "TV on, receiver on", import it again (same
address, overwrite): the new version leaves the receiver alone when the TV
drops off the network.

## From 0.3.1

Zone 2 and zone 3 are devices of their own, for example `AVR-X2600H Zone 2`.
Their entity ids stay the same. Give the zone device its room under Settings →
Devices. An automation with the device trigger "turned on while unreachable"
on the receiver now fires for the main zone only; for zone 2, pick the trigger
on the zone device.

## From 0.2.0

Selects with a fixed set of options (dimmer, Dynamic Volume, MultEQ, speaker
size, video setup and the like) now show their options in your language. Their
state is a key, the English name in lower case with `_` for spaces and signs:
`Medium Small` becomes `medium_small`, `-20 dB` becomes `minus_20_db`. If an
automation or script sets or compares such an option, use the key, for example
`option: dark` instead of `option: Dark`. Developer tools → States shows the
keys under `options`. Selects whose options come from the receiver (sound modes,
sources, quick select, crossover) are unchanged.

## From 0.1.0

The read-only volume sensor (`sensor.<receiver>_volume`) is replaced by a
settable volume per zone in dB (`number.<receiver>_volume`,
`number.<receiver>_zone_2_volume`). The old sensor is removed at the first
start. If an automation, script or dashboard used it, point it to the
number; the value is the same.

## Coming from another Denon integration

Denon AVR Plus (`denonavr_plus`) is a separate integration. It does not take
over entities from the official Denon AVR Network Receivers integration
(`denonavr`) or from Denon AVR (`denon_avr`) by itself yet; a migration
assistant is planned.

Until then:

1. Remove the other integration's entry for the receiver first. Many
   receivers accept only one telnet session, so two integrations connected
   at the same time disturb each other.
2. Add the receiver in Denon AVR Plus.
3. If you want to keep entity IDs, history and dashboards, rename the new
   entities to the old entity IDs (Settings → Entities → the entity → ⚙ →
   Entity ID).

What looks different afterwards:

- **Many entities start disabled.** Only the everyday controls are enabled
  (see "Enabled and disabled entities" in the README). Speaker, video and
  picture setup, the graphic EQ, volume limits and the fast-changing signal
  sensors are there but off. If an automation or dashboard used one of
  them, enable it on the device page before renaming it.
- **Entity IDs are English** in every language, for example
  `number.avr_x3600h_speakers_center_trim` also on a German installation,
  where the other integrations may have created German ones.
- **Raw commands** go through `denonavr_plus.send_command` instead of
  `denonavr.get_command`. The field is `command` in both; the old action
  sent it over the web interface, the new one over telnet, so the command
  takes effect at once and, asked for a response, returns the receiver's
  replies.
