# Recordings from LaserGuruGuy/denon_avr

Real, read-only captures of an AVR-X3600H and an AVC-X3900H (firmware
3.139.174), made and redacted by LaserGuruGuy for his integration
<https://github.com/LaserGuruGuy/denon_avr> (version 1.3.1, November 2026)
and copied here unchanged under its MIT licence (`LICENSE`).

Each folder holds the receiver's `Deviceinfo.xml`, its UPnP description, the
setup-interface documents (`ajax_<section>_<type>.xml`) and `telnet_corpus.json`:
every telnet line with the query it followed. `fixture.json` and
`capture_meta.json` describe how the capture was made.

Used by `tests/test_avr_parser.py` and `tests/test_avr_device.py` to check this
integration against receivers we do not have.
