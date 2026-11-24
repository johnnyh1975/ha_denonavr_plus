"""Own artwork for inputs without cover art, as the media player's picture.

The media control card paints the player's picture as its background and
takes its colours from it; the info dialog shows it too. A TV, disc or game
input has no cover, so the card stays plain. This module draws a simple
picture per kind of input instead: a colour gradient and a pictogram made of
plain shapes. All of it is drawn here (no logos, no images of Denon or
Marantz), served as SVG through Home Assistant's image proxy.
"""

from __future__ import annotations

#: Bump when the drawings change, so browsers fetch them again.
VERSION = 1

#: The input icon (as the media player shows it) -> kind of artwork.
_KINDS = {
    "mdi:television": "tv",
    "mdi:disc-player": "disc",
    "mdi:disc": "cd",
    "mdi:radio": "tuner",
    "mdi:record-player": "phono",
    "mdi:controller-classic": "game",
    "mdi:satellite-variant": "satellite",
    "mdi:cast-audio": "network",
    "mdi:bluetooth": "bluetooth",
    "mdi:audio-input-rca": "aux",
    "mdi:usb": "usb",
    "mdi:apple": "network",
    "mdi:spotify": "network",
}

#: Gradient colours per kind (top left, bottom right).
_COLOURS = {
    "tv": ("#1f3b73", "#0b1530"),
    "disc": ("#3b1f73", "#140b30"),
    "cd": ("#5a2d82", "#1d0f2e"),
    "tuner": ("#7a3e12", "#2a1406"),
    "phono": ("#6b4a1f", "#24180a"),
    "game": ("#1f6b3b", "#0a2414"),
    "satellite": ("#1f5f6b", "#0a2226"),
    "network": ("#145c73", "#06212b"),
    "bluetooth": ("#1f4f9e", "#0a1a38"),
    "aux": ("#5f5f6b", "#1e1e24"),
    "usb": ("#4a5a2a", "#181e0e"),
    "input": ("#3a3f4a", "#13151a"),
}

_STROKE = 'fill="none" stroke="#ffffff" stroke-opacity="0.85" stroke-width="14" stroke-linecap="round" stroke-linejoin="round"'
_FILL = 'fill="#ffffff" fill-opacity="0.85"'

#: The pictograms, drawn on a 600 x 600 canvas around its centre.
_SHAPES = {
    "tv": (
        f'<rect x="140" y="170" width="320" height="210" rx="22" {_STROKE}/>'
        f'<path d="M250 440 H350 M300 380 V440" {_STROKE}/>'
    ),
    "disc": (
        f'<rect x="130" y="330" width="340" height="90" rx="18" {_STROKE}/>'
        f'<circle cx="300" cy="230" r="90" {_STROKE}/>'
        f'<circle cx="300" cy="230" r="18" {_FILL}/>'
    ),
    "cd": (
        f'<circle cx="300" cy="300" r="150" {_STROKE}/>'
        f'<circle cx="300" cy="300" r="60" {_STROKE}/>'
        f'<circle cx="300" cy="300" r="16" {_FILL}/>'
    ),
    "tuner": (
        f'<rect x="140" y="250" width="320" height="190" rx="24" {_STROKE}/>'
        f'<path d="M190 250 L390 150" {_STROKE}/>'
        f'<circle cx="380" cy="345" r="50" {_STROKE}/>'
        f'<path d="M185 310 H290 M185 350 H290 M185 390 H290" {_STROKE}/>'
    ),
    "phono": (
        f'<circle cx="270" cy="320" r="140" {_STROKE}/>'
        f'<circle cx="270" cy="320" r="20" {_FILL}/>'
        f'<path d="M450 150 V330 L360 400" {_STROKE}/>'
    ),
    "game": (
        f'<rect x="120" y="220" width="360" height="190" rx="95" {_STROKE}/>'
        f'<path d="M210 285 V345 M180 315 H240" {_STROKE}/>'
        f'<circle cx="380" cy="295" r="14" {_FILL}/><circle cx="420" cy="335" r="14" {_FILL}/>'
    ),
    "satellite": (
        f'<path d="M170 210 A190 190 0 0 0 390 430 Z" {_STROKE}/>'
        f'<path d="M280 320 L400 200" {_STROKE}/><circle cx="410" cy="190" r="18" {_FILL}/>'
    ),
    "network": (
        f'<path d="M160 260 A200 200 0 0 1 440 260" {_STROKE}/>'
        f'<path d="M210 315 A130 130 0 0 1 390 315" {_STROKE}/>'
        f'<path d="M260 370 A60 60 0 0 1 340 370" {_STROKE}/>'
        f'<circle cx="300" cy="420" r="18" {_FILL}/>'
    ),
    "bluetooth": f'<path d="M230 220 L370 360 L300 430 V170 L370 240 L230 380" {_STROKE}/>',
    "aux": (
        f'<circle cx="230" cy="300" r="70" {_STROKE}/><circle cx="230" cy="300" r="18" {_FILL}/>'
        f'<circle cx="390" cy="300" r="70" {_STROKE}/><circle cx="390" cy="300" r="18" {_FILL}/>'
    ),
    "usb": (
        f'<path d="M300 440 V160 M300 160 L270 200 M300 160 L330 200" {_STROKE}/>'
        f'<path d="M300 380 L220 320 V270 M300 340 L380 290 V250" {_STROKE}/>'
        f'<circle cx="220" cy="255" r="16" {_FILL}/><rect x="366" y="222" width="28" height="28" {_FILL}/>'
    ),
    "input": (
        f'<rect x="170" y="170" width="260" height="260" rx="30" {_STROKE}/>'
        f'<path d="M110 300 H330 M270 240 L330 300 L270 360" {_STROKE}/>'
    ),
}


def kind_for_icon(icon: str | None) -> str:
    """The kind of artwork for the input icon the player shows."""

    return _KINDS.get(icon or "", "input")


def svg(kind: str) -> str:
    """The artwork of a kind of input, as an SVG document."""

    start, end = _COLOURS.get(kind, _COLOURS["input"])
    shape = _SHAPES.get(kind, _SHAPES["input"])
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="600" viewBox="0 0 600 600">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{start}"/><stop offset="1" stop-color="{end}"/>'
        "</linearGradient></defs>"
        '<rect width="600" height="600" fill="url(#g)"/>'
        f"{shape}</svg>"
    )
