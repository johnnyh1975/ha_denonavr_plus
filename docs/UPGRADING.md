# Upgrading

What you have to do when you update Denon AVR Plus. Versions that need
nothing from you are not listed. What changed in each version is in the
[release notes](../release-notes/).

## From 1.1.x

- **Sound mode names in capitals** (AVR-X2000 and other receivers before
  2016) show as words: *Pure Direct* where it said *PURE DIRECT*,
  *Multichannel stereo* where it said *MULTI CH STEREO*. Choosing by the
  receiver's name still works; a condition or template that compares
  `sound_mode` with the capitals needs the new name. **Readable names** off
  under Configure → Sound modes keeps the capitals.
- **New setups** start with the firmware binary sensor and the tuner preset
  and frequency disabled. An existing setup keeps its entities as they are.

## From 1.0.x

Sound modes show readable names: the media player's `sound_mode` and
`sound_mode_list` and the sound mode select say *Dolby Surround* where they
said *Dolby Audio - DD + DSur*, *Multichannel stereo* (in your language)
where they said *Multi Ch Stereo*. Two modes that would get the same short
name keep the receiver's names. The sound mode select now also shows the
names set under Configure → Sound modes.

- Choosing a mode by the receiver's name still works, with
  `media_player.select_sound_mode` and with `select.select_option` on the
  sound mode select.
- A condition or template that compares `sound_mode` or the select's state
  with the receiver's name compares with the short name now.
- A scene that holds the sound mode select's state stored the receiver's
  name, which the select no longer lists: Home Assistant skips it when the
  scene runs. Save the scene again, or keep the sound mode in the media
  player's state.

To keep the receiver's names everywhere, switch **Readable names** off under
Configure → Sound modes.

## From 0.9.4

The tuner preset and frequency are now *unknown* instead of *unavailable*
while no zone plays the tuner; choosing a preset or a frequency switches the
main zone to the tuner. A condition or card that waited for them to be
`unavailable` checks for `unknown` now. If you imported the example
dashboard, take its tuner section again.

A setting the receiver keeps at its old value now reports an error with the
reason (for example Dynamic Compression without a Dolby or DTS signal, or any
setting while the receiver is in standby); before, the old value came back
silently. An automation or script stops at such an error. Where a setting is
meant to be tried whatever the signal, add `continue_on_error: true` to that
action.

If Zone 2's input list was never chosen in the options, a repair asks once
which inputs Zone 2 can play. Answer it, or ignore it to keep every input.

## From 0.9.3

Marantz receivers from 2015 on: the select *Restorer* is no longer created.
These receivers list M-DAX under the name Restorer, and Marantz's command
list has no restorer command; M-DAX is the select *M-DAX*. The old select
shows as no longer provided: delete it under Settings → Entities, and point
automations that used it to the M-DAX select (options `off`, `low`,
`medium`, `high`).

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

Denon AVR Plus (`denonavr_plus`) takes a receiver over from the official
Denon AVR Network Receivers integration (`denonavr`) and from Denon AVR
(`denon_avr`):

- **When you add the receiver** and it is already set up in one of them
  (found by its address, MAC or serial number), Denon AVR Plus asks whether
  to take it over.
- **Later**, while the other integration still runs the receiver, a repair
  offers the same.

Taking over does this:

1. The other entry is **disabled**, not removed: many receivers accept only
   one telnet session, so two integrations connected at the same time
   disturb each other.
2. Its entities **move to Denon AVR Plus** and keep their entity IDs, names,
   areas and history, so automations and dashboards keep working. From the
   official integration these are the media players of the zones; from
   Denon AVR, which used the same unique IDs, every entity that Denon AVR
   Plus still has. The rest stays with the disabled entry.
3. From the official integration, the option **"Volume scale of the
   official integration"** is switched on, so `volume_set` levels sound the
   same.
4. Automations and scripts that use something of the official integration
   that works differently here are **listed in a repair**, each place with
   what replaces it. Nothing in them is changed.

**Undo** it in the options of Denon AVR Plus ("Undo the move"): the
entities go back with their entity IDs, the other entry is enabled again,
and Denon AVR Plus is disabled. Enable or delete it afterwards.

What looks different afterwards:

- **Many entities start disabled.** Only the everyday controls are enabled
  (see [Enabled and disabled entities](REFERENCE.md#enabled-and-disabled-entities)). Speaker, video and
  picture setup, the graphic EQ, volume limits and the fast-changing signal
  sensors are there but off. An entity taken over keeps its state (an
  enabled one stays enabled).
- **Entity IDs of new entities are English** in every language, for example
  `number.avr_x3600h_speakers_center_trim` also on a German installation.
  Entities taken over keep the IDs they had.
- **Raw commands** go through `denonavr_plus.send_command` instead of
  `denonavr.get_command`. The field is `command` in both; the old action
  sent it over the web interface, the new one over telnet, so the command
  takes effect at once and, asked for a response, returns the receiver's
  replies.
