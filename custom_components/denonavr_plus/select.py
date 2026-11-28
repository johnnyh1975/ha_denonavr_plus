"""Select platform for the Denon AVR Plus integration.

One select entity is created per enum control the receiver has (for example
Dynamic Compression, Dynamic Volume, MultEQ, Restorer). Fixed option sets come
from the profile: the state is a key (helpers.option_key) and the name on
screen its translation. Option sets the receiver supplies (sound modes,
sources, quick select, crossover) are shown as it names them.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from typing import Any, cast

from . import activities, choices, sound_modes
from .avr import graphic_eq
from .avr.speaker_layout import SpeakerLayout
from .avr.video_config import VideoConfigController
from .avr.profile import ControlSpec
from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .const import DOMAIN
from .avr import refusals
from .avr.errors import SettingNotTaken
from .entity import DenonAvrEntity, grayed_attributes, receiver_action, unique_id, zone_device
from .helpers import (
    control_enabled_by_default,
    control_sub_device,
    control_zone_shown,
    enum_options,
    option_key,
    group_name,
)


# Several selects write the setup interface, which takes one write at a time.
PARALLEL_UPDATES = 1

async def async_setup_entry(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a select entity for each supported enum control."""

    coordinator = entry.runtime_data
    discovery = coordinator.device.discovery
    entities: list[SelectEntity] = [
        DenonAvrSelect(coordinator, spec)
        for spec in coordinator.device.profile.controls.values()
        if spec.kind == "enum"
        and discovery.has_control(spec.scope, spec.feature, spec.id)
        and control_zone_shown(spec, dict(entry.options))
    ]
    # Two coupled sound mode selects: one picks the genre group (Movie/Music/
    # Game/Pure), the other lists the modes within the selected group. Both are
    # driven entirely by what the receiver reports. Only created when the
    # receiver advertises sound mode genres.
    if discovery.sound_mode_genres:
        entities.append(DenonAvrSoundModeGroupSelect(coordinator))
        entities.append(DenonAvrSoundModeSelect(coordinator))
    # Activities (activities.py), on each shown zone that has any.
    options = dict(entry.options)
    coordinator.activity_zones = activities.zones_with_activities(options)
    with_select = [
        zone
        for zone in discovery.zones
        if zone.id in coordinator.activity_zones and choices.zone_enabled(options, zone.id)
    ]
    entities.extend(DenonAvrActivitySelect(coordinator, zone) for zone in with_select)
    # A zone's last activity deleted: its select goes, not "no longer provided".
    registry = er.async_get(hass)
    for zone in discovery.zones:
        if zone in with_select:
            continue
        stale = registry.async_get_entity_id(
            "select", DOMAIN, unique_id(coordinator.device, f"select_activity_{zone.id}")
        )
        if stale is not None:
            registry.async_remove(stale)
    # Quick select presets, if the receiver reported any names.
    if discovery.quick_select_names:
        entities.append(DenonAvrQuickSelect(coordinator))
    if coordinator.device.has_tuner:
        entities.append(DenonAvrTunerPreset(coordinator))
    # One crossover select per speaker group the receiver reports a crossover
    # for (SSCFR). Like all speaker setup, these start disabled.
    profile = coordinator.device.profile
    for group in sorted(coordinator.data.crossovers):
        channels = profile.group_channels(group)
        entities.append(DenonAvrCrossover(coordinator, group, channels))
    # Crossover speaker-selection (All / Individual) and the single All crossover
    # it gates, when the receiver reports any crossovers.
    if coordinator.data.crossovers:
        entities.append(DenonAvrCrossoverSpeakerSelection(coordinator))
        entities.append(DenonAvrCrossoverAll(coordinator))
    # One size select (Large/Small) per regular speaker group the receiver
    # reports (the count groups - subwoofer, surround back - are not sizes).
    size_groups = profile.speakers.get("groups", {})
    for group in sorted(g for g in coordinator.data.speaker_sizes if g in size_groups):
        channels = profile.group_channels(group)
        entities.append(DenonAvrSpeakerSize(coordinator, group, channels))
    # Number of subwoofers, when the receiver reports a subwoofer config (SWF).
    if "SWF" in coordinator.data.speaker_sizes:
        entities.append(DenonAvrSubwooferCount(coordinator))
    # Manual graphic-EQ selects, on the EQ sub-device, when the receiver has a
    # graphic EQ this profile can drive: the speaker-selection mode and the
    # channel being adjusted (read per-channel via the config API's opt1 index).
    if coordinator.device.graphic_eq.supported:
        entities.append(DenonAvrEqSpeakerSelection(coordinator))
        entities.append(DenonAvrEqChannel(coordinator))
    # Multi-option video setup menu items (HDMI power-off, pass-through source,
    # OSD, 4K/TV format) read/written over the /ajax config API, on the Video
    # sub-device. Only those the receiver advertises are created.
    video = coordinator.device.video_config
    if video.supported:
        entities.extend(
            DenonAvrVideoSelect(coordinator, control)
            for control in video.controls()
            if control.get("kind") == "select" and video.present(control["id"])
        )
    # Per input settings of the Web Control generation (4K/8K format, HDCP),
    # one select per input, on the Inputs sub-device.
    if video.supported:
        entities.extend(
            DenonAvrInputSettingSelect(coordinator, spec["id"], slot)
            for spec in video.collection_specs()
            for slot in video.entity_slots(spec["id"])
        )
    if coordinator.device.speaker_layout.supported:
        entities.append(DenonAvrSpeakerLayoutSelect(coordinator))
    # Speaker-setup /ajax selects (Amp Assign), on the Speakers sub-device. The
    # amp-assign options are device-driven (keyed on the queried AmpType).
    speakers = coordinator.device.speaker_config
    if speakers.supported:
        entities.extend(
            DenonAvrVideoSelect(
                coordinator, control, controller_attr="speaker_config", sub_device="speakers"
            )
            for control in speakers.controls()
            if control.get("kind") == "select" and speakers.present(control["id"])
        )
    async_add_entities(entities)


class DenonAvrSelect(DenonAvrEntity, SelectEntity):
    """A select entity backed by a profile enum control."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DenonAvrCoordinator, spec: ControlSpec) -> None:
        # Route to a logical sub-device (Audio/Video/Speakers) by command group.
        super().__init__(
            coordinator, f"select_{spec.id}", sub_device=control_sub_device(spec)
        )
        self._spec = spec
        self._http_control = spec.id
        self._attr_translation_key = spec.id
        self._attr_entity_registry_enabled_default = control_enabled_by_default(spec)
        discovery = coordinator.device.discovery
        published = next(
            (
                discovery.option_labels[feature]
                for feature in spec.published_from or [spec.feature or ""]
                if feature in discovery.option_labels
            ),
            None,
        )
        if spec.count_from == "quick_select_names" and discovery.quick_select_names:
            # As many as the main zone has: the receiver's quick select buttons.
            published = spec.labels[: len(discovery.quick_select_names)]
        options, self._key_to_value, self._value_to_key = enum_options(spec, published)
        self._attr_options = options

    @property
    def current_option(self) -> str | None:
        value = self.coordinator.data.values.get(self._spec.id)
        if value is None:
            return None
        # Map the wire value back to its option key; return None (rather than
        # an unknown string) when the device reports a value outside our options,
        # so Home Assistant does not log an "invalid current option" warning.
        return self._value_to_key.get(str(value))

    @receiver_action
    async def async_select_option(self, option: str) -> None:
        value = self._key_to_value.get(option)
        if value is not None:
            await self.coordinator.device.async_set_control_confirmed(
                self._spec.id, value, turn_on=self._turn_on_for_settings
            )


class DenonAvrSoundModeGroupSelect(DenonAvrEntity, SelectEntity):
    """Selects which sound mode genre group to browse (Movie/Music/Game/Pure).

    The groups and their names come from the receiver. This select follows the
    receiver's active group by default; picking a different group lets the user
    browse that group's modes in the companion sound mode select, without
    changing anything on the receiver until a mode is actually chosen.
    """

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "select_sound_mode_group")
        self._attr_translation_key = "sound_mode_group"

    @property
    def _genres(self) -> dict[str, str]:
        return self.coordinator.device.discovery.sound_mode_genres

    @property
    def options(self) -> list[str]:
        return list(self._genres.values())

    @property
    def current_option(self) -> str | None:
        # The current group is reported directly by the receiver (SSSMG).
        code = self.coordinator.data.values.get("sound_mode_genre")
        return self._genres.get(str(code)) if code else None

    @receiver_action
    async def async_select_option(self, option: str) -> None:
        # Selecting a group switches the receiver to that genre; the receiver
        # then reports the resolved mode, which updates the sound mode select.
        for code, display in self._genres.items():
            if display == option:
                await self.coordinator.device.async_select_sound_mode_group(code)
                return


class DenonAvrSoundModeSelect(DenonAvrEntity, SelectEntity):
    """Lists and selects the sound modes within the active genre group.

    The modes come from the receiver (OPSMLALL) for the currently active group.
    Selecting a mode sends the wire token (a deterministic profile override when
    it differs, else the upper-cased name), which may differ from what the
    receiver reports back as the active mode (expected on Denon, for example
    'Dolby Audio - Dolby Surround').
    """

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "select_sound_mode")
        self._attr_translation_key = "sound_mode"
        self._words: dict[str, str] = dict(sound_modes.WORDS)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._words = await sound_modes.async_words(self.hass)

    def _modes(self) -> list[str]:
        # The receiver's current-context list (OPSML) is authoritative: it holds
        # exactly what the active group offers now, including modes OPSMLALL omits
        # such as Auto. Fall back to the static per genre group if unavailable.
        discovery = self.coordinator.device.discovery
        if discovery.current_sound_modes:
            return list(discovery.current_sound_modes)
        code = self.coordinator.data.values.get("sound_mode_genre")
        group = discovery.sound_mode_groups.get(str(code), []) if code else []
        # A receiver with sound mode groups but without these lists (the
        # AVR-X2100W and X4100W recordings): every mode it has, as the media
        # player offers them, rather than an empty selection.
        return (
            list(group)
            or list(discovery.all_sound_modes)
            or list(self.coordinator.device.fallback_sound_modes)
        )

    def _names(self) -> dict[str, str]:
        """Each mode of the group with the name shown, as the media player shows it."""

        options = self._entry_options
        readable = (
            sound_modes.readable_with(self._words)
            if options.get(sound_modes.OPT_READABLE_SOUND_MODES, True)
            else None
        )
        # Settled over every mode the receiver listed, as the media player does,
        # so a mode has the same name in both and whatever the genre.
        discovery = self.coordinator.device.discovery
        modes = self._modes()
        every = list(dict.fromkeys([*discovery.all_sound_modes, *discovery.current_sound_modes, *modes]))
        names = choices.sound_mode_names(options, every, readable)
        return {mode: names[mode] for mode in modes}

    @property
    def options(self) -> list[str]:
        return list(self._names().values())

    @property
    def current_option(self) -> str | None:
        values = self.coordinator.data.values
        current = values.get("sound_mode_display")
        if not current and (mode := values.get("sound_mode")):
            # A receiver without its own lists reports the token only, as
            # the media player reads it.
            device = self.coordinator.device
            current = device.fallback_sound_mode_name(str(mode)) or mode
        return self._names().get(str(current)) if current else None

    async def async_handle_select_option(self, option: str) -> None:  # type: ignore[misc]
        """Take the receiver's name for a mode too, as the name shown for it.

        The options are the readable names of the modes the current group
        lists, and Home Assistant accepts only an option from the list. A name
        shown or a receiver's name of one of these modes (any case; the name
        shown first, as media_player.select_sound_mode does) is turned into the
        name shown before that check. Modes outside the current group are not
        offered here; the media player takes them. Home Assistant marks this
        service wrapper final; it is overridden only to translate the name and
        then runs Home Assistant's own check unchanged. A test calls the
        service with a receiver's name, so a change in Home Assistant shows.
        """

        names = self._names()
        mode = choices.match_mode(option, names)
        await super().async_handle_select_option(names[mode] if mode is not None else option)

    @receiver_action
    async def async_select_option(self, option: str) -> None:
        mode = choices.match_mode(option, self._names()) or option
        await self.coordinator.device.async_select_sound_mode(mode)


class DenonAvrCrossover(DenonAvrEntity, SelectEntity):
    """A select entity for one speaker group's crossover frequency.

    The allowed frequencies are a discrete, non-uniform Denon protocol set (from
    the profile), so this is a select rather than a stepped number. Values are
    shown with their 'Hz' unit; the receiver reports the current value per group.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: DenonAvrCoordinator,
        group: str,
        channels: list[str],
    ) -> None:
        super().__init__(
            coordinator, f"select_crossover_{group}", sub_device="speakers"
        )
        self._group = group
        crossover = coordinator.device.profile.crossover
        self._unit = crossover.get("unit", "Hz")
        # The crossover grid is a fixed Denon protocol constant (same across the
        # lineup; verified live). It is not device-variable, so it lives in the
        # profile like every other enum's wire values - no runtime fetch needed.
        self._values = [int(v) for v in crossover.get("values", [])]
        self._attr_options = [self._format(v) for v in self._values]
        self._attr_translation_key = "speaker_crossover"
        self._attr_translation_placeholders = {
            "group": group_name(coordinator.device.discovery, channels)
        }

    def _format(self, hertz: int) -> str:
        return f"{hertz} {self._unit}"

    @property
    def available(self) -> bool:
        # In "All" crossover speaker-selection mode the single All crossover
        # applies, so the per-group ones are inactive.
        return (
            super().available
            and self.coordinator.data.values.get("crossover_speaker_selection") != "ALL"
        )

    @property
    def current_option(self) -> str | None:
        hertz = self.coordinator.data.crossovers.get(self._group)
        # Only report a value the receiver actually offers, so Home Assistant
        # never logs an "invalid current option" warning.
        return self._format(hertz) if hertz in self._values else None

    @receiver_action
    async def async_select_option(self, option: str) -> None:
        hertz = int(option.split()[0])
        await self.coordinator.device.async_set_crossover(self._group, hertz)


class DenonAvrCrossoverSpeakerSelection(DenonAvrEntity, SelectEntity):
    """Crossover speaker selection: All (one shared crossover) or Individual.

    This gates the crossover controls: in "All" the single All Crossover applies
    and the per-group crossovers go unavailable; in "Individual" the per-group
    ones apply and All Crossover goes unavailable.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(
            coordinator, "select_crossover_speaker_selection", sub_device="speakers"
        )
        self._attr_translation_key = "crossover_speaker_selection"
        # Wire tokens -> labels come from the profile grammar (kept out of this
        # HA layer like every other enum's tokens).
        self._labels: dict[str, str] = coordinator.device.profile.crossover.get(
            "speaker_selection", {"IDV": "Individual", "ALL": "All"}
        )
        self._attr_options = list(self._labels.values())

    @property
    def current_option(self) -> str | None:
        token = self.coordinator.data.values.get("crossover_speaker_selection")
        return self._labels.get(token) if isinstance(token, str) else None

    @receiver_action
    async def async_select_option(self, option: str) -> None:
        for token, label in self._labels.items():
            if label == option:
                await self.coordinator.device.async_set_crossover_mode(token)
                return


class DenonAvrCrossoverAll(DenonAvrEntity, SelectEntity):
    """The single 'All' crossover frequency, used in All speaker-selection mode."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "select_crossover_all", sub_device="speakers")
        self._attr_translation_key = "all_crossover"
        crossover = coordinator.device.profile.crossover
        self._unit = crossover.get("unit", "Hz")
        self._values = [int(v) for v in crossover.get("values", [])]
        self._attr_options = [f"{v} {self._unit}" for v in self._values]

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data.values.get("crossover_speaker_selection") == "ALL"
        )

    @property
    def current_option(self) -> str | None:
        hertz = self.coordinator.data.values.get("crossover_all")
        return f"{hertz} {self._unit}" if hertz in self._values else None

    @receiver_action
    async def async_select_option(self, option: str) -> None:
        await self.coordinator.device.async_set_crossover("ALL", int(option.split()[0]))


class DenonAvrSpeakerSize(DenonAvrEntity, SelectEntity):
    """A select entity for one speaker group's size (Large/Small).

    The size is a fixed two-value protocol enum (like the receiver's other enum
    controls); the wire tokens and labels come from the profile. The current
    value is read over telnet (SSSPC); groups the receiver reports as absent map
    to no option and are disabled by default.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: DenonAvrCoordinator,
        group: str,
        channels: list[str],
    ) -> None:
        super().__init__(
            coordinator, f"select_speaker_size_{group}", sub_device="speakers"
        )
        self._group = group
        sizes = coordinator.device.profile.speakers.get("sizes", {}).get("options", {})
        self._token_to_label = {token: option_key(label) for token, label in sizes.items()}
        self._label_to_token = {key: token for token, key in self._token_to_label.items()}
        self._attr_options = list(self._token_to_label.values())
        self._attr_translation_key = "speaker_size"
        self._attr_translation_placeholders = {
            "group": group_name(coordinator.device.discovery, channels)
        }

    @property
    def current_option(self) -> str | None:
        token = self.coordinator.data.speaker_sizes.get(self._group)
        # Absent/count tokens map to no option, so no "invalid option" warning.
        return self._token_to_label.get(token)

    @receiver_action
    async def async_select_option(self, option: str) -> None:
        token = self._label_to_token.get(option)
        if token:
            await self.coordinator.device.async_set_speaker_size(self._group, token)


class DenonAvrSubwooferCount(DenonAvrEntity, SelectEntity):
    """Number of subwoofers the receiver is configured for (None / 1 / 2).

    A telnet count group in the SSSPC speaker config (SSSPCSWF NON/1SP/2SP),
    settable like a speaker size. The range is the standard 0-2 subwoofers of
    this AVR class; a flagship supporting more would extend the token map.
    A receiver that answers YES/NO instead (AVR-X2000, AVR-X2600H) gets
    None / 1 with those tokens.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "select_subwoofer_count", sub_device="speakers")
        self._attr_translation_key = "subwoofer_count"
        # Wire tokens -> labels from the profile grammar, not hardcoded here.
        speakers = coordinator.device.profile.speakers
        labels = speakers.get("subwoofer_count", {"NON": "None", "1SP": "1", "2SP": "2"})
        presence = speakers.get("subwoofer_presence", {})
        if coordinator.data.speaker_sizes.get("SWF") in presence:
            labels = presence
        self._labels = {token: option_key(label) for token, label in labels.items()}
        self._attr_options = list(self._labels.values())
        self._label_to_token = {v: k for k, v in self._labels.items()}

    @property
    def current_option(self) -> str | None:
        return self._labels.get(self.coordinator.data.speaker_sizes.get("SWF"))

    @receiver_action
    async def async_select_option(self, option: str) -> None:
        token = self._label_to_token.get(option)
        if token:
            await self.coordinator.device.async_set_speaker_size("SWF", token)


class DenonAvrQuickSelect(DenonAvrEntity, SelectEntity):
    """Recall the receiver's main zone quick select presets.

    The preset names are discovered from the receiver (SSQSNZMA); the currently
    recalled preset is reported by MSQUICK (0 means none/modified). A playback
    control like the sound mode, so it carries no entity category.
    """

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "select_quick_select")
        self._attr_translation_key = "quick_select"

    @property
    def _names(self) -> dict[int, str]:
        return self.coordinator.device.discovery.quick_select_names

    @property
    def options(self) -> list[str]:
        return [name for _, name in sorted(self._names.items())]

    @property
    def current_option(self) -> str | None:
        number = self.coordinator.data.values.get("quick_select")
        if not number:
            return None
        return self._names.get(int(str(number)))

    @receiver_action
    async def async_select_option(self, option: str) -> None:
        for number, name in self._names.items():
            if name == option:
                await self.coordinator.device.async_select_quick_select(number)
                return


class DenonAvrActivitySelect(DenonAvrEntity, RestoreEntity, SelectEntity):
    """Start an activity of a zone; shows the one started while it lasts.

    The activity lasts while the zone is on and on the activity's input; the
    name survives a restart of Home Assistant.
    """

    def __init__(self, coordinator: DenonAvrCoordinator, zone: Any) -> None:
        super().__init__(coordinator, f"select_activity_{zone.id}", sub_device=zone_device(zone))
        self._zone = zone
        self._attr_translation_key = "start_activity"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        started = self.coordinator.started_activities
        last = await self.async_get_last_state()
        if self._zone.id not in started and last is not None and last.state in self.options:
            started[self._zone.id] = last.state

    @property
    def _activities(self) -> list[activities.Activity]:
        entry = self.coordinator.config_entry
        return activities.activities(dict(entry.options) if entry else {}, self._zone.id)

    @property
    def options(self) -> list[str]:
        return [item.name for item in self._activities]

    @property
    def current_option(self) -> str | None:
        name = self.coordinator.started_activities.get(self._zone.id)
        started = next((item for item in self._activities if item.name == name), None)
        zone = self.coordinator.data.zone(self._zone.id)
        if started is None or not zone.power:
            return None
        wire = activities.wire_source(self.coordinator.device, started.source)
        return started.name if zone.source == wire else None

    @receiver_action
    async def async_select_option(self, option: str) -> None:
        activity = next(item for item in self._activities if item.name == option)
        await activities.async_start(self.hass, self.coordinator, activity, self._context)


class DenonAvrTunerPreset(DenonAvrEntity, SelectEntity):
    """The tuner preset: 01-56, or A1-G8 on older receivers.

    Unknown while no zone plays the tuner; choosing a preset then switches
    the main zone to the tuner first. The receiver reports no preset names.
    Starts disabled, like the frequency: the media player covers the tuner.
    """

    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "select_tuner_preset")
        self._attr_translation_key = "tuner_preset"

    @property
    def options(self) -> list[str]:
        return self.coordinator.device.tuner_preset_codes

    @property
    def current_option(self) -> str | None:
        if self.coordinator.device.tuner_zone is None:
            return None
        preset = self.coordinator.data.values.get("tuner_preset")
        return str(preset) if preset in self.options else None

    @receiver_action
    async def async_select_option(self, option: str) -> None:
        device = self.coordinator.device
        await device.async_tuner_preset(device.tuner_zone or "main", option)


class DenonAvrEqSpeakerSelection(DenonAvrEntity, SelectEntity):
    """Graphic-EQ speaker selection: one curve for all, the L/R pair, or each."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "select_eq_speaker_selection", sub_device="eq")
        self._attr_translation_key = "eq_speaker_selection"
        # code -> label, from the fixed graphic-EQ grammar.
        self._by_code: dict[str, str] = dict(
            coordinator.device.graphic_eq.grammar.get("speaker_selection", {})
        )
        self._by_label = {label: code for code, label in self._by_code.items()}
        self._attr_options = list(self._by_code.values())

    @property
    def current_option(self) -> str | None:
        code = self.coordinator.device.graphic_eq.state.speaker_selection
        return self._by_code.get(code) if code else None

    @receiver_action
    async def async_select_option(self, option: str) -> None:
        code = self._by_label.get(option)
        if code:
            await self.coordinator.device.graphic_eq.set_speaker_selection(code)


class DenonAvrEqChannel(DenonAvrEntity, SelectEntity):
    """Graphic-EQ channel being read and adjusted.

    Each channel has its own curve, read via the config API's channel index
    (opt1); switching the channel loads that channel's own bands. The available
    channels and their labels depend on the speaker-selection mode (one shared
    curve in 'All', L/R pairs, or individual channels in 'Each').
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "select_eq_channel", sub_device="eq")
        self._attr_translation_key = "eq_channel"
        self._grammar = coordinator.device.graphic_eq.grammar

    def _options(self) -> list[tuple[int, str]]:
        eq = self.coordinator.device.graphic_eq.state
        return graphic_eq.channel_options(
            self._grammar, eq.speaker_selection, eq.selectable
        )

    @property
    def options(self) -> list[str]:
        return [label for _, label in self._options()]

    @property
    def current_option(self) -> str | None:
        options = self._options()
        index = self.coordinator.device.graphic_eq.channel
        for i, label in options:
            if i == index:
                return label
        # eq_channel not in the current mode's list (e.g. just after a mode
        # switch): show the first channel rather than an unknown state.
        return options[0][1] if options else None

    @receiver_action
    async def async_select_option(self, option: str) -> None:
        for index, label in self._options():
            if label == option:
                await self.coordinator.device.graphic_eq.set_channel(index)
                return


class DenonAvrVideoSelect(DenonAvrEntity, SelectEntity):
    """A multi-option video setup item, backed by the /ajax video controller.

    Its options and current value come from the receiver via the config API
    (device.video_config). A grayed-out item (e.g. Pass Through Source until
    HDMI Pass Through is on) shows the value its menu shows and refuses a
    change with the reason; its attributes say whether it can be changed now
    and, for the HDMI setup, what the receiver itself has stored.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: DenonAvrCoordinator,
        control: dict[str, Any],
        controller_attr: str = "video_config",
        sub_device: str = "video",
    ) -> None:
        super().__init__(coordinator, f"select_{control['id']}", sub_device=sub_device)
        self._id = control["id"]
        self._attr_translation_key = control["id"]
        self._controller_attr = controller_attr
        # A fixed option set (in the profile grammar) is shown through
        # translated keys; one the receiver supplies (its sources, its amp
        # assign modes) is shown as the receiver names it.
        self._fixed = bool(control.get("options"))

    def _shown(self, label: str) -> str:
        return option_key(label) if self._fixed else label

    @property
    def _ctrl(self) -> VideoConfigController:
        # The /ajax config controller backing this select (video or speakers).
        return cast(VideoConfigController, getattr(self.coordinator.device, self._controller_attr))

    @property
    def available(self) -> bool:
        return super().available and self._ctrl.present(self._id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return grayed_attributes(self._device, self._ctrl, self._id)

    @property
    def options(self) -> list[str]:
        return [self._shown(label) for label in self._ctrl.options(self._id).values()]

    @property
    def current_option(self) -> str | None:
        value = self._ctrl.value(self._id)
        label = self._ctrl.options(self._id).get(value) if value is not None else None
        return None if label is None else self._shown(label)

    @receiver_action
    async def async_select_option(self, option: str) -> None:
        if not self._ctrl.available(self._id):
            raise SettingNotTaken(self._id, refusals.MENU_GRAYED)
        for wire, label in self._ctrl.options(self._id).items():
            if self._shown(label) == option:
                await self._ctrl.async_set(self._id, wire)
                return


class DenonAvrInputSettingSelect(DenonAvrEntity, SelectEntity):
    """A setting per input of the Web Control generation: 4K/8K format or HDCP.

    One per input the receiver lists (AVC-X3800H/X3900H), named after the
    input as the receiver names it. An input the receiver grays out (TV Audio)
    shows its value and refuses a change with the reason.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DenonAvrCoordinator, collection_id: str, slot: str) -> None:
        super().__init__(coordinator, f"select_{collection_id}_{slot}", sub_device="inputs")
        self._collection = collection_id
        self._slot = slot
        self._attr_translation_key = collection_id
        item = self._video.collection(collection_id).get(slot) or {}
        self._attr_translation_placeholders = {"input": item.get("name") or slot}

    @property
    def _video(self) -> VideoConfigController:
        return self.coordinator.device.video_config

    @property
    def _keys(self) -> dict[str, str]:
        spec = self._video.collection_spec(self._collection) or {}
        return cast(dict[str, str], spec.get("option_keys", {}))

    def _item(self) -> dict[str, Any] | None:
        return self._video.collection(self._collection).get(self._slot)

    @property
    def available(self) -> bool:
        return super().available and self._item() is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"settable": self._video.item_settable(self._collection, self._slot)}

    @property
    def options(self) -> list[str]:
        item = self._item()
        return [self._keys.get(option, f"option_{option}") for option in (item or {}).get("options", [])]

    @property
    def current_option(self) -> str | None:
        item = self._item()
        if item is None or not item["value"]:
            return None
        return self._keys.get(item["value"], f"option_{item['value']}")

    @receiver_action
    async def async_select_option(self, option: str) -> None:
        if not self._video.item_settable(self._collection, self._slot):
            raise SettingNotTaken(self._collection, refusals.MENU_GRAYED)
        item = self._item() or {}
        wire = next(
            (value for value in item.get("options", []) if self._keys.get(value, f"option_{value}") == option),
            None,
        )
        if wire is not None:
            await self._video.async_set_item(self._collection, self._slot, wire)


class DenonAvrSpeakerLayoutSelect(DenonAvrEntity, SelectEntity):
    """The speaker layout (Web Control generation), prepared here, written by its button.

    A new layout makes the receiver reset the speaker positions and front
    speaker A/B, so choosing one only prepares it; the button "Apply speaker
    layout" writes it. The select shows the prepared layout until then.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "speaker_layout"

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "select_speaker_layout", sub_device="speakers")

    @property
    def _layout(self) -> SpeakerLayout:
        return self.coordinator.device.speaker_layout

    @property
    def available(self) -> bool:
        return super().available and self._layout.supported

    @property
    def options(self) -> list[str]:
        return [self._layout.key(mode) for mode in self._layout.options]

    @property
    def current_option(self) -> str | None:
        mode = self._layout.pending or self._layout.current
        return self._layout.key(mode) if mode else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"prepared": self._layout.pending is not None}

    async def async_select_option(self, option: str) -> None:
        mode = self._layout.mode_of(option)
        if mode is None or not self._layout.stage(mode):
            # While a layout is written or put back, nothing can be prepared.
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="speaker_layout_busy"
            )
        self.async_write_ha_state()
