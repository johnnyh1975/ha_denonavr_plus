# Working on Denon AVR Plus

For people and coding agents alike. Denon AVR Plus is a Home Assistant
custom integration for Denon and Marantz AV receivers. It talks to the
receiver locally: telnet (port 23) for state and control with push updates,
HTTP (port 8080) for discovery, the setup API (HTTPS 10443) for settings
without a telnet command, and HEOS (port 1255) for now playing.

## Layout

| What | Where |
|---|---|
| The integration | `custom_components/denonavr_plus/` |
| The receiver client, without Home Assistant | `custom_components/denonavr_plus/avr/` |
| The protocol grammar (command tokens, values) | `custom_components/denonavr_plus/avr/protocol_profile.json` |
| Its texts (English source) | `custom_components/denonavr_plus/strings.json` |
| Translations | `custom_components/denonavr_plus/translations/` |
| Tests | `tests/test_<module>.py`, `tests/test_avr_<module>.py` for `avr/` |
| Recorded receiver data for tests | `tests/fixtures/` |
| Repository checks | `scripts/check_*.py` |
| Read-only probe of a receiver's capabilities | `tools/probe_receiver.py` |
| Release notes, one file per version | `release-notes/` |
| Upgrade notes | `docs/UPGRADING.md` |

## Before you open a pull request

```bash
bash scripts/check_all.sh
```

It runs every check the CI runs: the repository checks, ruff, strict mypy
against its baseline, the test suite and the coverage per module. CI then
repeats the tests on two Home Assistant versions, the supported minimum
(2026.8) and a current one, both on Python 3.14; code has to work on both.

Coverage: 95 % per module is the rule. Modules that are not there yet are
held at a floor in `scripts/coverage_floors.json` that may only go up. New
modules start at 95 %.

## Rules

1. **Every fix comes with a test that fails without it.** Run the test
   with the fix reverted and say in the pull request that it failed.
   Keep tests to the core of the change.
2. **One test file per module**: tests for `config_flow.py` go in
   `tests/test_config_flow.py`, tests for `avr/parser.py` in
   `tests/test_avr_parser.py`.
3. **Build objects through their constructors in tests.** Setting private
   attributes by hand on an object made without `__init__` hides missing
   attributes.
4. **A background task catches what it can expect**, such as an
   unreachable receiver. Otherwise the error surfaces only as "Task
   exception was never retrieved".
5. **No private data**, not in code, tests, fixtures or docs. Use
   placeholders: `192.0.2.x` for addresses, `aa:bb:cc:dd:ee:ff` for MACs,
   `user@example.com` for accounts. Recorded receiver data is anonymised
   before it is committed. The CI checks every added line
   (`scripts/check_private_data.py`).
6. **Diagnostics contain no address, MAC or serial number.**
7. **Texts go in `strings.json` and every translation** (de, en, es, fr,
   it, nl, pl, pt). Keys are `[a-z0-9-_]`, and do not start or end with
   `-` or `_`; Home Assistant rejects anything else
   (`scripts/check_translations.py`). Entity ids are built from the English
   name in every language (`entity.py`, `suggested_object_id`), so name
   entities through `translation_key`, with placeholders for parts that
   come from the receiver, and keep device names English. The locale-slug
   guard enforces this (`scripts/check_locale_slugs.py`).
8. **Protocol facts say where they come from.** A command token or value
   that has not been confirmed on a receiver is marked in its test with
   `@pytest.mark.assumed("what is unconfirmed, and what would settle it")`.
   No texts or images copied from Denon or Marantz apps or manuals.
9. **Docs say how things are now.** History goes in the release notes,
   what a user must do in `docs/UPGRADING.md`.
10. **Leave versions to the maintainer.** No version bump in
    `manifest.json`, no new release-notes file; describe the change in the
    pull request and it is written up at release.

## Pull requests

Say which receiver you tested on (model, firmware) and whether a coding
assistant helped. Both are fine; the maintainer reviews and tests either
way.
