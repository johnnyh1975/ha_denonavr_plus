"""Activities: one choice ("Film", "Music") that sets up a zone.

An activity is kept in the entry options (Configure → Activities) and belongs
to one zone. Starting it switches the zone on and, in this order, selects its
input, its sound mode, its HDMI output, its audio delay, its volume and the
speech clarity, and wakes the player linked to the input and one more device
(the TV). Each part is optional except the input.

Input and sound mode go through the zone's media player, so renamed inputs,
readable sound mode names and refused inputs behave as there. Other
integrations' activities (Logitech Harmony) were the model; the activity
sensor (activity.py) says what plays, this module what was chosen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from . import linked_players
from . import volume as _volume
from .const import DOMAIN
from .entity import unique_id

if TYPE_CHECKING:
    from .coordinator import DenonAvrCoordinator

#: Options key: the list of activities.
OPT_ACTIVITIES = "activities"
#: The activity fields as stored.
KEY_NAME = "name"
KEY_ZONE = "zone"
KEY_SOURCE = "source"
KEY_SOUND_MODE = "sound_mode"
KEY_VOLUME = "volume_db"
KEY_HDMI_OUTPUT = "hdmi_output"
KEY_CLARITY = "clarity"
KEY_WAKE = "wake_player"
KEY_AUDIO_DELAY = "audio_delay"
KEY_WAKE_ENTITY = "wake_entity"
#: The control that chooses the HDMI output (VSMONI).
HDMI_OUTPUT_CONTROL = "hdmi_video_output"
#: The audio delay in ms (PSDELAY).
AUDIO_DELAY_CONTROL = "audio_delay"
#: What can be woken besides the linked player: switched on with
#: homeassistant.turn_on.
WAKE_DOMAINS = ("media_player", "remote", "switch")
#: Speech clarity in an activity: leave it, switch it on or off.
CLARITY_KEEP = "keep"
CLARITY_ON = "on"
CLARITY_OFF = "off"


@dataclass(frozen=True)
class Activity:
    """One stored activity."""

    name: str
    zone: str
    source: str
    sound_mode: str | None = None
    volume_db: float | None = None
    hdmi_output: str | None = None
    clarity: str = CLARITY_KEEP
    wake_player: bool = False
    audio_delay: int | None = None
    wake_entity: str | None = None

    def for_zone(self) -> Activity:
        """The activity without what only the main zone has, outside it."""

        if self.zone == "main":
            return self
        return Activity(
            name=self.name,
            zone=self.zone,
            source=self.source,
            volume_db=self.volume_db,
            wake_player=self.wake_player,
            wake_entity=self.wake_entity,
        )

    def as_dict(self) -> dict[str, Any]:
        """The activity as stored in the options (unset fields left out)."""

        stored: dict[str, Any] = {KEY_NAME: self.name, KEY_ZONE: self.zone, KEY_SOURCE: self.source}
        if self.sound_mode:
            stored[KEY_SOUND_MODE] = self.sound_mode
        if self.volume_db is not None:
            stored[KEY_VOLUME] = self.volume_db
        if self.hdmi_output:
            stored[KEY_HDMI_OUTPUT] = self.hdmi_output
        if self.clarity != CLARITY_KEEP:
            stored[KEY_CLARITY] = self.clarity
        if self.wake_player:
            stored[KEY_WAKE] = True
        if self.audio_delay is not None:
            stored[KEY_AUDIO_DELAY] = self.audio_delay
        if self.wake_entity:
            stored[KEY_WAKE_ENTITY] = self.wake_entity
        return stored


def parse(raw: Any) -> Activity | None:
    """An activity from the options; None for anything incomplete."""

    if not isinstance(raw, dict):
        return None
    name, zone, source = raw.get(KEY_NAME), raw.get(KEY_ZONE), raw.get(KEY_SOURCE)
    if not (isinstance(name, str) and name and isinstance(zone, str) and isinstance(source, str)):
        return None
    volume = raw.get(KEY_VOLUME)
    clarity = raw.get(KEY_CLARITY, CLARITY_KEEP)
    delay = raw.get(KEY_AUDIO_DELAY)
    wake = raw.get(KEY_WAKE_ENTITY)
    return Activity(
        name=name,
        zone=zone,
        source=source,
        sound_mode=raw.get(KEY_SOUND_MODE) or None,
        volume_db=float(volume) if isinstance(volume, (int, float)) else None,
        hdmi_output=raw.get(KEY_HDMI_OUTPUT) or None,
        clarity=clarity if clarity in (CLARITY_ON, CLARITY_OFF) else CLARITY_KEEP,
        wake_player=bool(raw.get(KEY_WAKE, False)),
        audio_delay=int(delay) if isinstance(delay, (int, float)) and not isinstance(delay, bool) else None,
        wake_entity=wake if isinstance(wake, str) and wake.split(".")[0] in WAKE_DOMAINS else None,
    )


def activities(options: dict[str, Any], zone_id: str | None = None) -> list[Activity]:
    """The stored activities, of one zone or of all, in their order."""

    found = [item for raw in options.get(OPT_ACTIVITIES) or () if (item := parse(raw))]
    return [item for item in found if zone_id is None or item.zone == zone_id]


def zones_with_activities(options: dict[str, Any]) -> frozenset[str]:
    """The zones that have at least one activity (they get the activity select)."""

    return frozenset(item.zone for item in activities(options))


def find(options: dict[str, Any], name: str, zone_id: str | None = None) -> Activity | None:
    """An activity by its name, case and spaces ignored."""

    key = " ".join(name.split()).casefold()
    for item in activities(options, zone_id):
        if " ".join(item.name.split()).casefold() == key:
            return item
    return None


def wire_source(device: Any, code: str) -> str:
    """The code a zone reports for an input (NETWORK plays as NET)."""

    return linked_players.wire_code(device, code)


def zone_entity_id(hass: HomeAssistant, coordinator: DenonAvrCoordinator, domain: str, key: str) -> str | None:
    """The entity id of one of this receiver's entities by its key."""

    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id(coordinator.device, key))


async def async_start(
    hass: HomeAssistant,
    coordinator: DenonAvrCoordinator,
    activity: Activity,
    context: Context | None = None,
) -> None:
    """Set the zone up as the activity says; raises when its input is refused.

    However it was started (select, action, voice), the zone's select shows
    it from then on (coordinator.started_activities).
    """

    player = zone_entity_id(hass, coordinator, "media_player", f"media_player_{activity.zone}")
    if player is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="activity_zone_missing",
            translation_placeholders={"activity": activity.name},
        )
    # The media player switches the zone on, waits for it and applies its
    # turn-on volume; the activity's own volume comes after.
    await hass.services.async_call(
        "media_player",
        "select_source",
        {ATTR_ENTITY_ID: player, "source": activity.source},
        blocking=True,
        context=context,
    )
    # Started once the zone took the input; the rest only fine-tunes it.
    coordinator.started_activities[activity.zone] = activity.name
    coordinator.async_update_listeners()
    device = coordinator.device
    if activity.sound_mode and activity.zone == "main":
        await hass.services.async_call(
            "media_player",
            "select_sound_mode",
            {ATTR_ENTITY_ID: player, "sound_mode": activity.sound_mode},
            blocking=True,
            context=context,
        )
    if activity.hdmi_output and activity.zone == "main" and device.profile.control(HDMI_OUTPUT_CONTROL):
        await device.async_set_control(HDMI_OUTPUT_CONTROL, activity.hdmi_output)
    if (
        activity.audio_delay is not None
        and activity.zone == "main"
        and device.profile.control(AUDIO_DELAY_CONTROL)
    ):
        await device.async_set_control(AUDIO_DELAY_CONTROL, activity.audio_delay)
    entry = coordinator.config_entry
    options = dict(entry.options) if entry is not None else {}
    if activity.volume_db is not None:
        raw = _volume.clamp_raw(
            device, activity.zone, device.volume_reference + activity.volume_db, options
        )
        await device.async_set_volume_raw(activity.zone, raw)
    if activity.clarity != CLARITY_KEEP and activity.zone == "main":
        clarity = zone_entity_id(hass, coordinator, "switch", "switch_speech_clarity")
        if clarity is not None:
            await hass.services.async_call(
                "switch",
                "turn_on" if activity.clarity == CLARITY_ON else "turn_off",
                {ATTR_ENTITY_ID: clarity},
                blocking=True,
                context=context,
            )
    woken: list[str] = []
    if activity.wake_player:
        link = linked_players.links(options).get(activity.source)
        if link is not None:
            woken.append(link.entity_id)
    if activity.wake_entity and activity.wake_entity not in woken:
        woken.append(activity.wake_entity)
    for entity_id in woken:
        # Not blocking: a TV that takes its time does not hold up the activity.
        if hass.states.get(entity_id) is not None:
            await hass.services.async_call(
                "homeassistant",
                "turn_on",
                {ATTR_ENTITY_ID: entity_id},
                blocking=False,
                context=context,
            )
