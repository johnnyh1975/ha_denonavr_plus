"""Number platform for the Denon AVR Plus integration.

Creates adjustable number entities for the level style controls the receiver
advertises (bass, treble, subwoofer level, LFE), the sleep timer, and one per
configured channel volume trim. Ranges come from the receiver where it publishes
them; only where it does not (LFE, sleep timer) does the profile supply a
protocol defined range.
"""

from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import UnitOfFrequency, UnitOfLength, UnitOfSoundPressure, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import choices
from . import volume as _volume
from typing import cast

from .avr import DenonAvrDevice, graphic_eq
from .avr.models import ZoneDescriptor
from .avr.profile import ControlSpec
from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .entity import DenonAvrEntity, receiver_action, zone_device
from .helpers import control_enabled_by_default, control_sub_device, control_zone_shown

# The control kinds that map to a number entity.
_NUMBER_KINDS = {"level", "attenuation", "minutes", "integer", "centered"}

# Map the profile's plain wire unit tokens to the canonical Home Assistant unit
# constants (the profile stays HA-independent; this HA layer does the mapping).
_UNIT_TOKENS = {"ms": UnitOfTime.MILLISECONDS, "m": UnitOfLength.METERS}


# The graphic EQ bands and distances write the setup interface one at a time.
PARALLEL_UPDATES = 1

async def async_setup_entry(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the adjustable number entities discovered for this receiver."""

    coordinator = entry.runtime_data
    device = coordinator.device
    discovery = device.discovery
    entities: list[NumberEntity] = []

    # The volume of each zone in dB, settable (the media player shows 0..1).
    for zone in discovery.zones:
        if choices.zone_enabled(dict(entry.options), zone.id):
            entities.append(DenonAvrVolume(coordinator, zone))

    if device.has_tuner:
        entities.append(DenonAvrTunerFrequency(coordinator))

    for spec in device.profile.controls.values():
        if spec.kind not in _NUMBER_KINDS:
            continue
        if not discovery.has_control(spec.scope, spec.feature, spec.id):
            continue
        if not control_zone_shown(spec, dict(entry.options)):
            continue
        entities.append(DenonAvrNumber(coordinator, spec))

    # One trim per known channel. Channels that the receiver reports as
    # configured (from SSSPC) are enabled; the rest are registered but disabled
    # by default so they stay out of the way yet can be enabled from the UI.
    configured = discovery.configured_channels
    calibrated = [c.code for c in discovery.channels] or sorted(
        coordinator.data.channel_levels
    )
    # Without the SSLEV list (AVR-X2000) the channels come from CV?, and
    # there are no distances in a form this integration reads.
    codes = calibrated or list(coordinator.data.channel_trims)
    for code in codes:
        enabled = (not configured) or code in configured
        entities.append(DenonAvrChannelTrim(coordinator, code, enabled))
        if calibrated:
            entities.append(DenonAvrChannelDistance(coordinator, code))

    # One number per manual graphic-EQ band, on the EQ sub-device, when the
    # receiver has a graphic EQ this profile can drive. The bands reflect the
    # channel picked by the EQ Channel select.
    if device.graphic_eq.supported:
        for label in device.graphic_eq.grammar.get("bands", []):
            entities.append(DenonAvrEqBand(coordinator, label))

    async_add_entities(entities)


class DenonAvrNumber(DenonAvrEntity, NumberEntity):
    """A number entity backed by a profile level, attenuation or minutes control."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: DenonAvrCoordinator, spec: ControlSpec) -> None:
        # Route to a logical sub-device (Audio/Video/Speakers) by command group.
        super().__init__(
            coordinator, f"number_{spec.id}", sub_device=control_sub_device(spec)
        )
        self._spec = spec
        self._attr_translation_key = spec.id
        self._attr_entity_registry_enabled_default = control_enabled_by_default(spec)

        if spec.kind == "minutes":
            self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
            self._attr_device_class = NumberDeviceClass.DURATION
        elif spec.kind == "integer":
            # A plain integer control uses whatever unit the profile gives (or
            # none), for example milliseconds for the audio delay, surfaced as the
            # canonical HA unit constant where known.
            unit = spec.get("unit")
            self._attr_native_unit_of_measurement = _UNIT_TOKENS.get(unit, unit)
            if unit == "ms":
                self._attr_device_class = NumberDeviceClass.DURATION
        elif spec.kind == "centered":
            # Picture controls are a unitless offset (e.g. contrast -50..+50).
            self._attr_native_unit_of_measurement = None
        else:
            self._attr_native_unit_of_measurement = UnitOfSoundPressure.DECIBEL

        low, high, step = _resolve_bounds(coordinator.device, spec)
        if low is not None:
            self._attr_native_min_value = low
        if high is not None:
            self._attr_native_max_value = high
        if step is not None:
            self._attr_native_step = step

    @property
    def native_value(self) -> float | None:
        value = self.coordinator.data.values.get(self._spec.id)
        return float(cast(float, value)) if value is not None else None

    @receiver_action
    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.device.async_set_control(self._spec.id, value)


class DenonAvrChannelTrim(DenonAvrEntity, NumberEntity):
    """A number entity for one channel's volume trim in dB."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = UnitOfSoundPressure.DECIBEL

    def __init__(
        self, coordinator: DenonAvrCoordinator, code: str, enabled: bool
    ) -> None:
        super().__init__(
            coordinator, f"number_channel_trim_{code}", sub_device="speakers"
        )
        self._code = code
        # Channels the receiver has not configured are registered but disabled by
        # default; the user can still enable them from the UI.
        self._attr_entity_registry_enabled_default = enabled
        discovery = coordinator.device.discovery
        self._attr_translation_key = "channel_trim"
        self._attr_translation_placeholders = {"channel": discovery.channel_name(code)}
        meta = discovery.numeric_meta.get(code)
        if meta:
            self._attr_native_min_value = meta["min"]
            self._attr_native_max_value = meta["max"]
            self._attr_native_step = meta["step"]

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.channel_trims.get(self._code)

    @receiver_action
    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.device.async_set_channel_trim(self._code, value)


class DenonAvrChannelDistance(DenonAvrEntity, NumberEntity):
    """A number entity for one channel's speaker distance."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_device_class = NumberDeviceClass.DISTANCE
    # Speaker distances are measured once at installation.
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DenonAvrCoordinator, code: str) -> None:
        super().__init__(
            coordinator, f"number_channel_distance_{code}", sub_device="speakers"
        )
        self._code = code
        discovery = coordinator.device.discovery
        self._attr_translation_key = "channel_distance"
        self._attr_translation_placeholders = {"channel": discovery.channel_name(code)}
        dist = coordinator.device.profile.distance
        self._attr_native_min_value = dist.get("min", 0.0)
        self._attr_native_max_value = dist.get("max", 18.0)
        self._attr_native_step = dist.get("step", 0.1)
        unit = dist.get("unit")
        if unit:
            self._attr_native_unit_of_measurement = _UNIT_TOKENS.get(unit, unit)

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.channel_distances.get(self._code)

    @receiver_action
    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.device.async_set_channel_distance(self._code, value)


def _resolve_bounds(
    device: DenonAvrDevice, spec: ControlSpec
) -> tuple[float | None, float | None, float | None]:
    """Return (min, max, step), preferring the receiver published range."""

    meta = device.discovery.numeric_meta.get(spec.feature or "")
    if meta:
        return meta["min"], meta["max"], meta["step"]
    # Fall back to the profile's protocol defined range (LFE, sleep timer).
    return spec.get("min"), spec.get("max"), spec.get("step")


class DenonAvrEqBand(DenonAvrEntity, NumberEntity):
    """One manual graphic-EQ band gain in dB, shown as a slider (fader).

    The value applies to the channel currently picked by the EQ Channel select
    (or to all/LR depending on the speaker-selection mode). Read and written via
    the setup config API; the range comes from the fixed graphic-EQ grammar.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfSoundPressure.DECIBEL
    _attr_mode = NumberMode.SLIDER
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DenonAvrCoordinator, label: str) -> None:
        grammar = coordinator.device.graphic_eq.grammar
        self._tag = graphic_eq.band_tag(grammar, label)
        super().__init__(coordinator, f"number_eq_{self._tag}", sub_device="eq")
        # The device is already named "... Graphic EQ", so the band name is just
        # the frequency (avoids a doubled "Graphic EQ EQ 63 Hz").
        self._attr_name = label
        self._attr_native_min_value = float(grammar.get("min_db", -20.0))
        self._attr_native_max_value = float(grammar.get("max_db", 6.0))
        self._attr_native_step = float(grammar.get("step_db", 0.5))

    @property
    def native_value(self) -> float | None:
        # Show the staged edit if any, else the value read from the receiver.
        return self.coordinator.device.graphic_eq.band_value(self._tag)

    @receiver_action
    async def async_set_native_value(self, value: float) -> None:
        # Stage the edit; press Apply (button) to write the whole curve.
        await self.coordinator.device.graphic_eq.set_band(self._tag, value)


class DenonAvrVolume(DenonAvrEntity, NumberEntity):
    """A zone's master volume in dB, as the receiver shows it (for example -38.0).

    0 dB is the receiver's reference. The upper end follows the receiver's
    maximum and the zone's maximum from the options; values above are never
    sent. Like the media player it stays usable over HTTP while telnet is down.
    """

    _attr_device_class = NumberDeviceClass.SOUND_PRESSURE
    _attr_native_unit_of_measurement = UnitOfSoundPressure.DECIBEL
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: DenonAvrCoordinator, zone: ZoneDescriptor) -> None:
        super().__init__(coordinator, f"number_volume_{zone.id}", sub_device=zone_device(zone))
        self._zone = zone
        self._attr_translation_key = "volume"
        self._attr_native_step = coordinator.device.volume_step or 0.5
        self._attr_native_min_value = -coordinator.device.volume_reference

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and (
            self.coordinator.available or self._device.http_reachable
        )

    @property
    def native_max_value(self) -> float:
        ceiling = _volume.ceiling_raw(self._device, self._zone.id, self._entry_options)
        return ceiling - self._device.volume_reference

    @property
    def native_value(self) -> float | None:
        raw = self.coordinator.data.zone(self._zone.id).volume_raw
        if raw is None:
            return None
        return round(raw - self._device.volume_reference, 1)

    @receiver_action
    async def async_set_native_value(self, value: float) -> None:
        raw = _volume.clamp_raw(
            self._device, self._zone.id, value + self._device.volume_reference, self._entry_options
        )
        await self._device.async_set_volume_raw(self._zone.id, raw)


#: FM band, in MHz: the widest of the regions (Europe 87.5-108, North America
#: 87.5-107.9); the receiver keeps to its own.
_FM_MIN, _FM_MAX, _FM_STEP = 87.5, 108.0, 0.05
#: TFAN values below this are FM (MHz x 100), above it AM (kHz x 100).
_AM_FROM = 50000


class DenonAvrTunerFrequency(DenonAvrEntity, NumberEntity):
    """The FM frequency in MHz, to read and to tune directly.

    Available while a zone plays the tuner on FM.
    """

    _attr_device_class = NumberDeviceClass.FREQUENCY
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfFrequency.MEGAHERTZ
    _attr_native_min_value = _FM_MIN
    _attr_native_max_value = _FM_MAX
    _attr_native_step = _FM_STEP

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "number_tuner_frequency")
        self._attr_translation_key = "tuner_frequency"

    @property
    def _frequency(self) -> int | None:
        value = self.coordinator.data.values.get("tuner_frequency")
        return value if isinstance(value, int) and 0 < value < _AM_FROM else None

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.device.tuner_zone is not None
            and self._frequency is not None
        )

    @property
    def native_value(self) -> float | None:
        frequency = self._frequency
        return None if frequency is None else frequency / 100

    @receiver_action
    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.device.async_tune(round(value * 100))
