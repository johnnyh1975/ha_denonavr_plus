# Upgrading

What you have to do when you update Denon AVR Plus. Versions that need
nothing from you are not listed. What changed in each version is in the
[release notes](../release-notes/).

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
