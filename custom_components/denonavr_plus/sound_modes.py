"""Readable sound mode names (package 55).

Receivers name their modes for a small display: "Dolby Audio - DD + DSur" is
Dolby Surround upmixing a Dolby Digital signal, "Multi Ch Stereo" plays
stereo on every speaker. Home Assistant shows a short name instead: the
upmixer where the mode has one, the Dolby format spelled out, the two
multichannel modes in the user's language. Every other name stays as the
receiver gives it. The receiver's name stays valid for choosing a mode, and
the options can switch the readable names off or rename a mode.

The names follow from the receivers' lists (an AVR-X2600H's diagnostics),
not from a table of the manufacturers' apps.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from homeassistant.core import HomeAssistant
from homeassistant.helpers.translation import async_get_translations

from .const import DOMAIN

#: Option: show readable names (default on).
OPT_READABLE_SOUND_MODES = "readable_sound_modes"

#: Words translated under selector.sound_mode_word.
MULTICHANNEL_STEREO = "multichannel_stereo"
MULTICHANNEL_INPUT = "multichannel_input"
WORDS = {MULTICHANNEL_STEREO: "Multichannel stereo", MULTICHANNEL_INPUT: "Multichannel input"}

#: An upmixer after " + " names what is heard.
_UPMIXERS = {
    "DSur": "Dolby Surround",
    "Dolby Surround": "Dolby Surround",
    "Neural:X": "DTS Neural:X",
    "Virtual:X": "DTS Virtual:X",
}
#: Short Dolby formats after "Dolby Audio - ".
_DOLBY = {
    "DD": "Dolby Digital",
    "DD+": "Dolby Digital Plus",
    "TrueHD": "Dolby TrueHD",
    "Atmos": "Dolby Atmos",
    "DSur": "Dolby Surround",
}
_DOLBY_AUDIO = "Dolby Audio - "


def readable(name: str, words: Mapping[str, str] = WORDS) -> str:
    """The short name for a receiver's sound mode name; unknown ones unchanged."""

    text = " ".join(name.split())
    if " + " in text:
        upmixer = _UPMIXERS.get(text.rsplit(" + ", 1)[1])
        if upmixer is not None:
            return upmixer
    if text.startswith(_DOLBY_AUDIO):
        rest = text[len(_DOLBY_AUDIO) :]
        if rest in _DOLBY:
            return _DOLBY[rest]
        if rest.startswith("Dolby "):
            return rest
    if text == "Multi Ch Stereo":
        return words.get(MULTICHANNEL_STEREO, WORDS[MULTICHANNEL_STEREO])
    if text == "Multi Ch In" or text.startswith("Multi Ch In "):
        word = words.get(MULTICHANNEL_INPUT, WORDS[MULTICHANNEL_INPUT])
        return word + text[len("Multi Ch In") :]
    return name


def readable_with(words: Mapping[str, str]) -> Callable[[str], str]:
    """readable() with the words of one language."""

    return lambda name: readable(name, words)


async def async_words(hass: HomeAssistant) -> dict[str, str]:
    """The translated words, in the language Home Assistant uses."""

    translations = await async_get_translations(
        hass, hass.config.language, "selector", [DOMAIN]
    )
    prefix = f"component.{DOMAIN}.selector.sound_mode_word.options."
    return {key: translations.get(prefix + key, word) for key, word in WORDS.items()}
