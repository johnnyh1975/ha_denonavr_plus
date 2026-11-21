## What and why

<!-- The problem, what you saw, and how this fixes it. Link the issue if there is one. -->

## Tested on

- Receiver (model):
- Firmware:
- Home Assistant version:
- Live on the receiver, or tests only:

Coding assistant used (fine either way): yes / no

## Checklist

- [ ] `bash scripts/check_all.sh` passes. If a test also fails on an unchanged `main`, name it here instead of ticking:
- [ ] The new test fails without the change (`AGENTS.md`, rule 1)
- [ ] No private data; placeholders as in `AGENTS.md`, rule 5
- [ ] Unconfirmed protocol facts are marked `assumed` (rule 8)
- [ ] If texts changed: in `strings.json` and every translation (rule 7)
- [ ] No version bump in `manifest.json` and no new file in `release-notes/` (rule 10)
