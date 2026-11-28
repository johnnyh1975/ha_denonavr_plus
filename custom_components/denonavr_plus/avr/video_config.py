"""Video setup config controller for the setup config API (see transport.webconfig).

The receiver's Video setup menus - HDMI setup (HDMI Control/CEC, ARC, pass
through, power off/saving, smart menu), on screen display, 4K signal format and
TV format - have no telnet command token, so they are read and written through
the same HTTPS config API the graphic EQ uses (``/ajax/video/{get,set}_config``).
This is the telnet-less fallback, used only because these items are genuinely not
reachable over telnet.

The controller is pure orchestration around :class:`WebConfigClient`: it reads
the availability map (``type=1``) plus each advertised menu, exposes the current
value / editability / options of every control the profile lists, and writes a
single field back. It holds no Home Assistant dependency and no protocol
constants of its own - every id, config type, XML tag and option map comes from
the profile's ``grammar.video_web`` block. All calls are best effort and no-ops
when the receiver has no video config API.

Menu ``display`` flags mirror the setup UI: 1 = not available (hidden), 2 = grayed
(present but not settable in the current state, e.g. ARC until HDMI Control is on),
3 = selectable.
"""

from __future__ import annotations

import asyncio
import copy
import xml.etree.ElementTree as ET
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast
from xml.sax.saxutils import escape

from .errors import SetupWriteError

if TYPE_CHECKING:
    from .transport.webconfig import WebConfigClient

# Per-field display flags used by the setup config API.
_NOT_AVAILABLE = 1
_GRAYOUT = 2
_SELECTABLE = 3


def _parse(xml_text: str | None) -> ET.Element | None:
    if not xml_text:
        return None
    try:
        return ET.fromstring(xml_text)
    except ET.ParseError:
        return None


def _int(text: str | None, default: int = 0) -> int:
    try:
        return int((text or "").strip())
    except (TypeError, ValueError):
        return default


def parse_collection(spec: dict[str, Any], root: ET.Element) -> dict[str, dict[str, Any]]:
    """One item per input or speaker position of a menu, keyed by its index.

    Items of the Web Control generation (AVC-X3900H): per input
    ``<Source index="1" display="3"><Name>..</Name><Format>2</Format>..``,
    per speaker position ``<Item index="2"><Config display="3"><Value>4</Value>
    <List>..</List></Config><Conn><Value>1</Value></Conn></Item>``. An item
    is as settable as the stricter of its own display and that of the element
    that carries the setting (``availability_from``).
    """

    if root.tag != spec.get("root"):
        return {}
    key_attr = spec.get("key_attr", "index")
    items: dict[str, dict[str, Any]] = {}
    for element in root.findall(spec.get("item", "Source")):
        slot = (element.get(key_attr) or "").strip()
        value = element.find(spec["value_field"])
        if not slot or value is None:
            continue
        display = _int(element.get("display"), _SELECTABLE)
        carrier = element.find(spec["availability_from"]) if "availability_from" in spec else None
        if carrier is not None:
            display = min(display, _int(carrier.get("display"), _SELECTABLE))
        if "options_from" in spec:
            options = [
                text for item in element.findall(spec["options_from"]) if (text := (item.text or "").strip())
            ]
        else:
            options = []
            for option in spec.get("options", []):
                rule = spec.get("conditional_options", {}).get(option)
                if rule and (element.findtext(rule["field"]) or "").strip() != rule["equals"]:
                    continue
                options.append(option)
        items[slot] = {
            "name": (element.findtext(spec.get("name_field", "Name")) or "").strip(),
            "value": (value.text or "").strip(),
            "display": display,
            "options": options,
        }
    return items


class VideoConfigController:
    """Owns the /ajax video setup subsystem: availability, read and write."""

    def __init__(
        self,
        webconfig: WebConfigClient,
        grammar: dict[str, Any],
        on_update: Callable[[], None],
    ) -> None:
        self._webconfig = webconfig
        self._grammar = grammar
        self._on_update = on_update
        self._section = grammar.get("config_section", "video")
        # Availability key -> flag (from the type=1 listSetupMenu document).
        self._avail: dict[str, int] = {}
        # Control id -> {"value": str, "display": int, "options": dict|None}.
        self._fields: dict[str, dict[str, Any]] = {}
        # Collection id -> slot -> {"name", "value", "display", "options"}.
        self._collections: dict[str, dict[str, dict[str, Any]]] = {}

    @property
    def supported(self) -> bool:
        return bool(self._grammar)

    def controls(self) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self._grammar.get("controls", []))

    def _control(self, control_id: str) -> dict[str, Any] | None:
        for control in self.controls():
            if control["id"] == control_id:
                return control
        return None

    # Discovery / availability -------------------------------------------

    def feature_flags(self) -> set[str]:
        """Feature names to inject into discovery for the menus that are present.

        Lets telnet controls gated on a menu (the picture controls, gated on
        ``PictureAdjust``) be created at platform-setup time, reusing the normal
        feature gating instead of a bespoke code path.
        """

        return {
            name
            for key, name in self._grammar.get("features", {}).items()
            if self._avail.get(key, _NOT_AVAILABLE) >= _GRAYOUT
        }

    def snapshot(self) -> dict[str, Any]:
        """What discover() read, as plain data for a start from the snapshot."""

        return {
            "avail": dict(self._avail),
            "fields": copy.deepcopy(self._fields),
            "collections": copy.deepcopy(self._collections),
        }

    def restore(self, data: dict[str, Any] | None) -> None:
        """Take what an earlier discover() read, until the next one runs."""

        if not isinstance(data, dict):
            return
        avail, fields = data.get("avail"), data.get("fields")
        if isinstance(avail, dict):
            self._avail = {str(key): int(value) for key, value in avail.items()}
        if isinstance(fields, dict):
            self._fields = copy.deepcopy(fields)
        collections = data.get("collections")
        if isinstance(collections, dict):
            self._collections = copy.deepcopy(collections)

    def layout(self) -> list[str]:
        """The controls entities are made for: present ones and the menu flags."""

        present = [control["id"] for control in self.controls() if self.present(control["id"])]
        items = [
            f"{spec['id']}:{slot}"
            for spec in self.collection_specs()
            if spec.get("entities", True)
            for slot in self.entity_slots(spec["id"])
        ]
        return sorted(present) + sorted(self.feature_flags()) + sorted(items)

    async def discover(self) -> None:
        """Read the availability map and the advertised menus (at connect)."""

        if not self.supported:
            return
        # Some sections (e.g. speakers amp-assign) have no listSetupMenu; only
        # read the availability map when the grammar declares one.
        if "availability_type" in self._grammar:
            await self._read_availability()
        await self._read_menus()

    async def refresh(self) -> None:
        """Re-read the advertised menus (non-disruptive), used by the poll."""

        if not self.supported:
            return
        previous = (self._fields, self._collections)
        await self._read_menus()
        # This runs on every reconcile poll; only notify when a value actually
        # changed so we do not write HA state each interval for no reason.
        if (self._fields, self._collections) != previous:
            self._on_update()

    async def _read_availability(self) -> None:
        type_id = self._grammar.get("availability_type", 1)
        root = _parse(await self._webconfig.async_get(self._section, type_id))
        if root is None:
            return
        self._avail = {child.tag: _int(child.text, _NOT_AVAILABLE) for child in root}

    async def _read_menus(self) -> None:
        fields: dict[str, dict[str, Any]] = {}
        asked = answered = 0
        for type_id, root_tag in self._grammar.get("menus", {}).items():
            # Only fetch a menu the receiver advertises, to avoid the HTTP 500 an
            # absent menu returns. When the section has no availability map
            # (self._avail empty), read every menu unconditionally.
            if self._avail and self._avail.get(root_tag, _NOT_AVAILABLE) < _GRAYOUT:
                continue
            asked += 1
            root = _parse(await self._webconfig.async_get(self._section, int(type_id)))
            if root is None:
                continue
            answered += 1
            for control in self.controls():
                if str(control["type"]) == str(type_id):
                    self._parse_control(control, root, fields)
        if asked and not answered:
            # The interface did not answer at all (gone for a moment): keep
            # what was read before, also what a start took from its snapshot.
            return
        self._fields = fields
        await self._read_collections()

    def collection_specs(self) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self._grammar.get("collections", []))

    def collection_spec(self, collection_id: str) -> dict[str, Any] | None:
        return next((spec for spec in self.collection_specs() if spec["id"] == collection_id), None)

    async def _read_collections(self, type_id: int | None = None) -> bool:
        """Read the per-item menus the receiver advertises; False when one did not answer."""

        documents: dict[int, ET.Element | None] = {}
        read: dict[str, dict[str, dict[str, Any]] | None] = {}
        complete = True
        for spec in self.collection_specs():
            menu_type = int(spec["type"])
            if type_id is not None and menu_type != type_id:
                continue
            if self._avail.get(spec["root"], _NOT_AVAILABLE) < _GRAYOUT:
                read[spec["id"]] = None
                continue
            if menu_type not in documents:
                documents[menu_type] = _parse(await self._webconfig.async_get(self._section, menu_type))
            root = documents[menu_type]
            if root is None:
                complete = False
                continue
            read[spec["id"]] = parse_collection(spec, root)
        # Only what was read now, so a read of another menu meanwhile stays.
        collections = dict(self._collections)
        for collection_id, items in read.items():
            if items is None:
                collections.pop(collection_id, None)
            else:
                collections[collection_id] = items
        self._collections = collections
        return complete

    def entity_slots(self, collection_id: str) -> list[str]:
        """The items that get an entity: shown ones, and only named positions where named."""

        spec = self.collection_spec(collection_id) or {}
        named = spec.get("position_keys")
        return [
            slot
            for slot, item in self.collection(collection_id).items()
            if item["display"] >= _GRAYOUT and (named is None or slot in named)
        ]

    def item_settable(self, collection_id: str, slot: str) -> bool:
        item = self.collection(collection_id).get(slot)
        return item is not None and item["display"] >= _SELECTABLE

    def collection(self, collection_id: str) -> dict[str, dict[str, Any]]:
        """The items of a collection, keyed by index, in the receiver's order."""

        return self._collections.get(collection_id, {})

    async def async_read_collection(self, collection_id: str) -> bool:
        """Read one collection's menu again now (to confirm a staged change)."""

        spec = self.collection_spec(collection_id)
        if spec is None:
            return False
        return await self._read_collections(int(spec["type"]))

    async def async_set_item(self, collection_id: str, slot: str, wire: str, *, read: bool = True) -> None:
        """Write one item of a collection: ``<Format index="1">2</Format>``."""

        spec = self.collection_spec(collection_id)
        item = self.collection(collection_id).get(slot)
        if spec is None or item is None:
            return
        tag = spec.get("write_field", spec["value_field"])
        key_attr = spec.get("key_attr", "index")
        payload = f'<{tag} {key_attr}="{escape(slot, {chr(34): "&quot;"})}">{escape(wire)}</{tag}>'
        if not await self._webconfig.async_set(self._section, int(spec["type"]), payload):
            raise SetupWriteError(collection_id)
        if read:
            # The receiver applies it a moment later, as its own page waits.
            await asyncio.sleep(float(self._grammar.get("write_settle_seconds", 0.8)))
            await self._read_collections(int(spec["type"]))
            self._on_update()

    def _parse_control(
        self, control: dict[str, Any], root: ET.Element, fields: dict[str, Any]
    ) -> None:
        tag = control["tag"]
        if tag == root.tag:
            # Single-value menu (e.g. TVFormat): the value is the root's text and
            # the menu-level availability is its only gate, so treat as settable.
            fields[control["id"]] = {
                "value": (root.text or "").strip(),
                "display": _int(root.get("display"), _SELECTABLE),
                "options": None,
            }
            return
        element = root.find(tag)
        if element is None:
            return
        display = _int(element.get("display"), _SELECTABLE)
        if control.get("dynamic"):
            # A device-supplied source list (Pass Through Source): the selected
            # value is in <field>, the offered items are the <list> children keyed
            # by a source tag whose wire value comes from the profile map.
            value = (element.findtext(control.get("field", "Source")) or "").strip()
            options: dict[str, str] = {}
            wire_map = self._grammar.get("pass_through_values", {})
            container = element.find(control.get("list", "List"))
            if container is not None:
                for item in container:
                    wire = wire_map.get(item.tag)
                    if wire:
                        options[wire] = (item.text or item.tag).strip()
            fields[control["id"]] = {
                "value": value,
                "display": display,
                "options": options,
            }
        elif control.get("value_list"):
            # A nested field with the current <Value> plus a device <List> of the
            # allowed <Value> ids (amp-assign Floor Layout / Height); labels for
            # those ids come from the control's static options map.
            value = (element.findtext("Value") or "").strip()
            container = element.find("List")
            allowed = (
                [(v.text or "").strip() for v in container.findall("Value")]
                if container is not None
                else []
            )
            fields[control["id"]] = {
                "value": value,
                "display": display,
                "options": None,
                "value_list": allowed,
            }
        else:
            field = {
                "value": (element.text or "").strip(),
                "display": display,
                "options": None,
            }
            # Amp assign: the valid option list depends on the receiver's amp
            # hardware, so capture the sibling AmpType value; options() keys the
            # per-AmpType table on it.
            amptype_tag = control.get("amptype_tag")
            if amptype_tag:
                field["amptype"] = (root.findtext(amptype_tag) or "").strip()
            fields[control["id"]] = field

    # Home Assistant facing accessors ------------------------------------

    def present(self, control_id: str) -> bool:
        """True when the control exists on this receiver (not hidden)."""

        field = self._fields.get(control_id)
        return field is not None and field["display"] >= _GRAYOUT

    def available(self, control_id: str) -> bool:
        """True when the control is settable now (not grayed out)."""

        field = self._fields.get(control_id)
        return field is not None and field["display"] >= _SELECTABLE

    def value(self, control_id: str) -> str | None:
        field = self._fields.get(control_id)
        return field["value"] if field else None

    def options(self, control_id: str) -> dict[str, str]:
        """Wire value -> label map (device-supplied list if any, else profile)."""

        field = self._fields.get(control_id)
        if field and field.get("options"):
            return cast(dict[str, str], field["options"])
        control = self._control(control_id) or {}
        if field is not None and field.get("value_list") is not None:
            # Options are the device-allowed ids, labelled from the static map.
            label_map = control.get("options", {})
            return {v: label_map.get(v, v) for v in field["value_list"]}
        amptype_options = control.get("amptype_options")
        if amptype_options is not None and field is not None:
            # Device-driven: pick the mode list for the receiver's queried
            # AmpType, deduping labels (a few amp types map two ids to one label).
            result: dict[str, str] = {}
            seen: set[str] = set()
            for value, label in amptype_options.get(field.get("amptype", ""), []):
                if label not in seen:
                    seen.add(label)
                    result[value] = label
            return result
        return cast(dict[str, str], control.get("options", {}))

    # Write --------------------------------------------------------------

    async def async_set(self, control_id: str, wire: str) -> None:
        control = self._control(control_id)
        if not self.supported or control is None:
            return
        # Most sections send ONLY the field element (the ``type`` selects the
        # menu); wrapping in the menu root then makes the receiver reject the
        # write (HTTP 501). The speakers amp-assign menu is the exception - it
        # requires the field wrapped in its root tag (grammar ``wrap_root``);
        # sending it bare returns HTTP 500. Verified live per section.
        tag = control["tag"]
        if control.get("dynamic"):
            field = control.get("field", "Source")
            payload = f"<{tag}><{field}>{wire}</{field}></{tag}>"
        else:
            payload = f"<{tag}>{wire}</{tag}>"
        root = self._grammar.get("menus", {}).get(str(control["type"]))
        if self._grammar.get("wrap_root") and root:
            payload = f"<{root}>{payload}</{root}>"
        if not await self._webconfig.async_set(
            self._section, int(control["type"]), payload
        ):
            raise SetupWriteError(control_id)
        # Re-read so state reflects exactly what the receiver applied.
        await self.refresh()
