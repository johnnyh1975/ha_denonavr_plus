"""Own artwork for inputs without cover art."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from custom_components.denonavr_plus import artwork


def test_every_kind_is_a_valid_drawing() -> None:
    kinds = {artwork.kind_for_icon(icon) for icon in artwork._KINDS} | {"input"}
    for kind in kinds:
        root = ET.fromstring(artwork.svg(kind))
        assert root.tag.endswith("svg") and len(root) >= 3, kind
    # Every kind has its own colours, and an unknown input still gets a picture.
    assert len({artwork._COLOURS[kind] for kind in kinds}) == len(kinds)
    assert artwork.kind_for_icon(None) == "input"
    assert artwork.svg("unknown") == artwork.svg("input")
