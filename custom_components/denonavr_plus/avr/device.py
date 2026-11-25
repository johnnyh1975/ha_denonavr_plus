"""High level device API for the Denon AVR client library.

DenonAvrDevice is the public entry point. It combines the telnet transport, the
HTTP transport, the protocol profile and the parser into a single object that:

* discovers the receiver's identity and capabilities,
* keeps a live AvrState updated from telnet push events,
* resynchronises the full state on every (re)connection,
* exposes high level, profile driven control methods,
* notifies a single callback whenever the state or availability changes.

All command encoding uses the profile, so no wire tokens live in this file.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import asdict, fields, replace
from typing import Any

import aiohttp

from .codec import encode_centered, encode_half_step
from .models import (
    AvrState,
    DeviceInfo,
    Discovery,
    KnownAnswers,
    NowPlaying,
    SourceDescriptor,
    ZoneDescriptor,
    ZoneSnapshot,
)
from .generation import generation_of, legacy_device_info
from .parser import TelnetParser, parse_device_info, parse_main_zone, parse_upnp_description
from .profile import OLDER, ControlSpec, ProtocolProfile, load_profile, profile_for_receiver
from . import (
    appcommand,
    capability_check,
    command_test,
    graphic_eq,
    refusals,
    signal_info,
    video_config,
)
from .video_config import VideoConfigController
from .const import (
    CHECK_REPLY_FIRST,
    QUEUE_WAIT_MAX,
    REPLY_COLLECT_FIRST,
    REPLY_COLLECT_QUIET,
    REPLY_WAIT_OLDER,
)
from .errors import CommandNotSent, SettingNotTaken, SourceNotTaken
from .transport.upnp import OLDER_DESCRIPTION_PATH, OLDER_PORT
from .transport.webconfig import HTTP_PORT as HTTP_SETUP_PORT
from .transport import (
    DEFAULT_PORT,
    GoformClient,
    HeosClient,
    TelnetClient,
    UpnpClient,
    WebConfigClient,
    async_detect_port,
    async_probe,
    is_query,
    is_without_deviceinfo,
)

_LOGGER = logging.getLogger(__name__)

#: Asked first by the setup probe: answers kept from a receiver in standby
#: may be incomplete, so only those of a receiver that is on are kept.
POWER_QUERY = "PW?"
CHANNEL_TRIMS_QUERY = "CV?"
#: Asked of a receiver without Deviceinfo.xml (2010-2012) for its zones: a
#: zone that exists answers its power, source and volume.
LEGACY_ZONE_QUERIES = ("Z2?", "Z3?")
#: The profile variant of receivers on port 80 (AVR-X2000 and its time).
#: Within this many seconds of our own OPSMLALL query its END is its answer:
#: an AVR-X2600H sends a line every 74 ms, 26 lines and more.
SOUND_MODES_ANSWER = 6.0
#: How often the now playing list and the tuner are asked while they play.
MEDIA_POLL_INTERVAL = 5.0
#: Telnet lines after which the live signal information may have changed:
#: input, power, the input signal and the decoder.
SIGNAL_TRIGGERS = ("SI", "PW", "ZM", "SSINFAI", "SYSDA", "SV")
#: How long after such a line the signal information is read (the receiver
#: settles on a new signal first).
SIGNAL_SETTLE = 3.0
#: How often HEOS is asked for a firmware update (seconds).
FIRMWARE_CHECK_INTERVAL = 12 * 3600
#: Recover audio: how long the receiver stays in standby, and how long it may
#: take to report on again.
RECOVER_STANDBY_TIME = 5.0
RECOVER_ON_WAIT = 20.0
#: A zone's source code meaning "the main zone's source" (Z2SOURCE).
FOLLOW_MAIN = "SOURCE"
#: How long a preset recall waits for the receiver to switch to the tuner.
TUNER_SWITCH_WAIT = 3.0
#: How long a zone other than the main zone has to confirm a new source.
ZONE_SOURCE_WAIT = 2.0
#: How long a setting the receiver may refuse has to come back, first as the
#: echo of the command, then as the answer to its query (avr/refusals.py).
CONFIRM_WAIT = 1.5

# After a command sent over HTTP (telnet down) nothing is pushed back, so the
# zone status is read again once the receiver has had a moment to apply it.
_HTTP_COMMAND_SETTLE = 0.5

# Debounce window for collapsing a burst of telnet lines into one notification.
_UPDATE_DEBOUNCE = 0.1


class DenonAvrDevice:
    """Own, dependency light client for a Denon AVR over telnet and HTTP."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        http_port: int | None = None,
        *,
        heos: bool = True,
    ) -> None:
        self._host = host
        # Whether to open the HEOS channel for now playing (connection option).
        self._heos_enabled = heos
        self._session = session
        # The goform port; None until it is known (detected at the first
        # Deviceinfo read, or given from the stored config entry).
        self._http_port = http_port
        # The setup answers of this receiver, kept to skip asking at the next start.
        self.known_answers: KnownAnswers | None = None
        self._media_task: asyncio.Future[None] | None = None
        self._profile: ProtocolProfile = load_profile()
        self._discovery = Discovery()
        self._state = AvrState()
        self._parser = TelnetParser(self._profile, self._discovery)

        # Each wire protocol is a separate, isolated transport (see transport/):
        # goform HTTP for discovery/poll, UPnP for firmware/serial, and telnet for
        # live control. The length-framed TCP status channel is used only for a
        # one-shot read at discovery (amp assignment).
        self._goform = GoformClient(session, host, http_port or DEFAULT_PORT)
        self._upnp = UpnpClient(session, host)
        # Setup-interface config API (HTTPS 10443 or HTTP 11080), used for the manual graphic
        # EQ per-band values, which are not on the telnet channel. The EQ
        # subsystem is encapsulated in its own controller (read/edit/apply),
        # keeping it off this device's transport-orchestration surface.
        self._webconfig = WebConfigClient(session, host)
        self._firmware_update: bool | None = None
        self._firmware_checked_at: float | None = None
        # None until the setup interface was asked for signal information.
        self._signal_supported: bool | None = None
        self._signal_handle: asyncio.TimerHandle | None = None
        self._graphic_eq = graphic_eq.GraphicEqController(
            self._webconfig,
            self._profile.grammar.get("graphic_eq", {}),
            lambda: self._discovery.supports("GraphicEQ"),
            self._schedule_update,
        )
        # Video setup menus that have no telnet token (HDMI setup/CEC, ARC, OSD,
        # 4K signal format, TV format), read/written via the same config API.
        self._video_config = video_config.VideoConfigController(
            self._webconfig,
            self._profile.grammar.get("video_web", {}),
            self._schedule_update,
        )
        # Speaker-setup settings with no telnet token (amp assign), same /ajax
        # config API, different section.
        self._speaker_config = video_config.VideoConfigController(
            self._webconfig,
            self._profile.grammar.get("speakers_web", {}),
            self._schedule_update,
        )
        self._telnet = TelnetClient(
            host,
            on_line=self._handle_line,
            on_connected=self._resynchronise,
            on_availability=self._handle_availability,
        )
        # Auxiliary HEOS channel for network now-playing media and album art.
        # Isolated from control: if it never connects, only now-playing is absent.
        self._heos = HeosClient(host, on_update=self._handle_now_playing)

        self._update_callback: Callable[[], None] | None = None
        # Listeners for raw command replies (async_send_command); see there.
        self._reply_listeners: list[Callable[[str], None]] = []
        # Sources a zone other than the main zone did not switch to, per zone,
        # until Home Assistant restarts (Zone 2 plays no HDMI on many models).
        self._refused_sources: dict[str, set[str]] = {}
        # Values read over AppCommand.xml while telnet is down; their entities
        # stay available then. Cleared when telnet is back.
        self._http_values: set[str] = set()
        self._update_handle: asyncio.TimerHandle | None = None
        # Strong references to fire and forget tasks (delayed queries, OPSML
        # refreshes). Without these, asyncio only keeps a weak reference and the
        # task can be garbage collected before it runs.
        self._pending_tasks: set[asyncio.Task[Any]] = set()
        # Data driven coalesced re-query groups (profile 'refresh' section). The
        # receiver does not push some derived state on every relevant change
        # (audio format on a mode change, channel/speaker layout on a mode or
        # source change, the signal dependent sound mode lists), so when a
        # group's trigger prefix arrives its queries are re-sent after a short
        # debounce. Coalescing avoids a query storm when several triggers arrive
        # at once. A group is scheduled at most once until its re-query fires.
        self._refresh_groups = self._profile.refresh_groups
        self._refresh_scheduled: set[str] = set()
        # The trigger prefixes reported since the link came up. The first
        # report of each after connecting is the answer to the resync, not a
        # change: it starts no re-query (an AVR-X2600H otherwise got the sound
        # mode lists five times in its first seconds).
        self._seen_triggers: set[str] = set()
        # When the full sound mode list was last asked for: its END is then
        # the end of our own answer, which needs no OPSML re-query.
        self._sound_modes_asked_at: float | None = None
        #: How long each part of the discovery took, for the diagnostics.
        self.setup_timings: dict[str, int] = {}
        self._available = False
        # Whether the last HTTP reconciliation poll got an answer. Tells a
        # blocked or occupied telnet port apart from a receiver that is off.
        self._http_reachable = False
        # Deviceinfo derived source list, used only if telnet introspection
        # yields no sources (see async_discover / async_await_ready).
        self._fallback_sources: list[SourceDescriptor] = []

    # Public properties ----------------------------------------------------

    @property
    def host(self) -> str:
        return self._host

    @property
    def identifier(self) -> str:
        """Stable device identifier: MAC, else serial number, else host."""

        device = self._discovery.device
        return device.mac_address or device.serial_number or self._host

    @property
    def discovery(self) -> Discovery:
        return self._discovery

    @property
    def state(self) -> AvrState:
        return self._state

    @property
    def available(self) -> bool:
        return self._available

    @property
    def telnet_connected(self) -> bool:
        return self._telnet.connected

    @property
    def http_reachable(self) -> bool:
        """Whether the receiver answered the last HTTP reconciliation poll."""

        return self._http_reachable

    @property
    def setup_url(self) -> str:
        """The receiver's setup web page: HTTP 11080 where 10443 is closed."""

        if self._webconfig.port == HTTP_SETUP_PORT:
            return f"http://{self._host}:{HTTP_SETUP_PORT}/"
        return f"https://{self._host}:10443/"

    @property
    def heos_enabled(self) -> bool:
        """Whether now playing over HEOS is on (a connection option)."""

        return self._heos_enabled

    @property
    def setup_port(self) -> int | None:
        """The port the setup interface answered on last (10443 or 11080)."""

        return self._webconfig.port

    @property
    def telnet_response_time(self) -> float | None:
        """The receiver's average telnet response time in seconds, once measured."""

        return self._telnet.response_time

    @property
    def profile(self) -> ProtocolProfile:
        return self._profile

    # Master volume scale, preferring the values the receiver published in its
    # Volume block and falling back to the protocol defaults from the profile.
    @property
    def volume_reference(self) -> float:
        return self._discovery.volume.get("reference", self._profile.volume_reference)

    @property
    def volume_step(self) -> float:
        return self._discovery.volume.get("step", 0.5)

    @property
    def volume_absolute_max(self) -> float:
        return self._discovery.volume.get(
            "absolute_max", self._profile.volume_max_fallback
        )

    def volume_effective_max(self, zone_id: str = "main") -> float:
        """The highest reachable raw volume: the receiver limit (MVMAX) or scale max."""

        raw_max = self._state.zone(zone_id).volume_max_raw
        return raw_max if raw_max else self.volume_absolute_max

    @property
    def volume_display(self) -> str:
        """The receiver's volume display mode; defaults to Relative (dB)."""

        return self._state.volume_display or "Relative"

    def register_update_callback(self, callback: Callable[[], None]) -> None:
        """Register the single callback invoked on state/availability changes."""

        self._update_callback = callback

    # Lifecycle ------------------------------------------------------------

    async def async_fetch_identity(self) -> DeviceInfo:
        """Fetch just the device identity (model name, MAC) over HTTP.

        Used by the config flow (including SSDP discovery) to validate the
        receiver and obtain the unique id without the heavier telnet probe that
        full discovery performs. Raises ConnectionError when unreachable.
        """

        text = await self._async_device_info()
        if is_without_deviceinfo(text):
            return await self._async_legacy_identity()
        return parse_device_info(text).device

    async def _async_legacy_identity(self) -> DeviceInfo:
        """The identity of a receiver without Deviceinfo.xml (2010-2012).

        Model, manufacturer and serial number come from the UPnP description
        on port 8080, as the denonavr library reads them for these receivers.
        Its main zone document must answer, or it is no receiver this
        integration can control. Raises ConnectionError otherwise.
        """

        if await self._goform.async_get_main_zone() is None:
            raise ConnectionError(f"No main zone document from {self._host}")
        xml_text = await UpnpClient(
            self._session, self._host, port=OLDER_PORT, path=OLDER_DESCRIPTION_PATH
        ).async_get_description()
        found = parse_upnp_description(xml_text) if xml_text else {}
        return legacy_device_info(
            found.get("model_name"), found.get("manufacturer"), found.get("serial_number")
        )

    async def _async_legacy_discovery(self) -> Discovery:
        """Discovery of a receiver without Deviceinfo.xml (2010-2012).

        Inputs, their names and sound modes from the main zone document; the
        zones from the answers to Z2? and Z3? over telnet. Every setting is
        then gated by its telnet answer, as on the AVR-X2000.
        """

        main_zone = await self._goform.async_get_main_zone()
        if main_zone is None:
            raise ConnectionError(f"No main zone document from {self._host}")
        discovered = parse_main_zone(main_zone, self._profile.listed_sound_modes)
        discovered.device = await self._async_legacy_identity()
        answered: set[str] = set()

        def feed(line: str) -> None:
            answered.update(zone for zone in ("Z2", "Z3") if line.startswith(zone))

        await async_probe(
            self._host,
            list(LEGACY_ZONE_QUERIES),
            feed,
            lambda: answered == {"Z2", "Z3"},
            deadline=1.5,
            reply_wait=REPLY_WAIT_OLDER,
        )
        for index in (2, 3):
            if f"Z{index}" not in answered:
                break
            discovered.zones.append(
                ZoneDescriptor(id=f"zone{index}", index=index, name=f"Zone {index}")
            )
        discovered.device.zone_count = len(discovered.zones)
        return discovered

    @property
    def http_port(self) -> int | None:
        """The goform port in use, once known (8080, or 80 on older receivers)."""

        return self._http_port

    async def _async_device_info(self) -> str:
        """Read Deviceinfo.xml, detecting the goform port the first time.

        Raises ConnectionError when the receiver cannot be reached.
        """

        if self._http_port is None:
            port, xml_text = await async_detect_port(self._session, self._host)
            self._http_port = port
            self._goform = GoformClient(self._session, self._host, port)
            return xml_text
        text = await self._goform.async_get_device_info()
        if text is None:
            raise ConnectionError(f"No Deviceinfo response from {self._host}")
        return text

    async def async_discover(self, known_answers: Sequence[KnownAnswers] = ()) -> Discovery:
        """Fetch the Deviceinfo document and populate the discovery model.

        Raises ConnectionError when the receiver cannot be reached, so the
        config flow can report a clear failure.
        """

        started = asyncio.get_running_loop().time()
        xml_text = await self._async_device_info()
        self.setup_timings["deviceinfo"] = self._ms_since(started)
        # Tell the parser which feature blocks to read titles/labels from, using
        # the feature names the profile associates with its controls.
        feature_names = {
            spec.feature for spec in self._profile.controls.values() if spec.feature
        }
        enum_features = {
            spec.feature
            for spec in self._profile.controls.values()
            if spec.kind == "enum" and spec.feature
        }
        # Option lists published under another name (Resolution on an AVC-X3700H).
        for spec in self._profile.controls.values():
            for feature in spec.get("published_from") or ():
                feature_names.add(feature)
                enum_features.add(feature)
        if is_without_deviceinfo(xml_text):
            discovered = await self._async_legacy_discovery()
        else:
            discovered = parse_device_info(
                xml_text,
                feature_names,
                enum_features,
                receiver_type=self._profile.receiver_type,
            )
        if not self._profile.variants:
            # Older receivers encode some values differently, and Marantz
            # names a few commands its own way; the profile's variants say
            # how, by generation (port 80 until 2015, the 2013 encodings
            # below CommApiVers 0250) and by brand.
            fitted = profile_for_receiver(
                self._profile,
                self._http_port,
                discovered.device.comm_api_version,
                discovered.device.brand_code,
                discovered.device.model_name,
            )
            if fitted is not self._profile:
                self._profile = fitted
                self._parser = TelnetParser(self._profile, self._discovery)
        # Copy discovered data into our shared discovery object so the parser and
        # entities keep referencing a single instance.
        self._discovery.device = discovered.device
        # Like the denonavr library: an AVR-X answering on port 80 is the older
        # "avr-x" type (AVR-X2000), on 8080 "avr-x-2016".
        receiver_type = self._profile.receiver_type
        if self._http_port == 80 and discovered.device.hardware_type == receiver_type.get(
            "avr_x_type"
        ):
            discovered.device.hardware_type = receiver_type.get("avr_x_port80_type")
        self._discovery.features = discovered.features
        self._discovery.channels = discovered.channels
        self._discovery.zones = discovered.zones
        self._discovery.feature_names = discovered.feature_names
        self._discovery.option_labels = discovered.option_labels
        self._discovery.numeric_meta = discovered.numeric_meta
        self._discovery.volume = discovered.volume
        self._discovery.sound_mode_genres = discovered.sound_mode_genres
        # The authoritative source list comes from the telnet SSFUN/SSSOD
        # introspection (correct codes, names and visibility). Keep the Deviceinfo
        # source list only as a fallback for models without that introspection.
        self._fallback_sources = discovered.sources
        self._discovery.sources = []
        # Three independent reads, asked together so the setup waits for the
        # slowest instead of their sum: the UPnP description (firmware, serial
        # number), the setup interface (video, speakers, signal) and the telnet
        # introspection over its own short connection (sources, channels,
        # speaker configuration, sound modes, quick selects), which builds the
        # entities regardless of the persistent connection's later timing.
        await asyncio.gather(
            self._timed("description", self._async_fetch_version_info()),
            self._timed("setup_interface", self._async_discover_setup_interface()),
            self._timed("introspection", self._async_probe_introspection()),
        )
        self._add_network_source()
        specs = self._answer_gated_controls()
        queries = {spec.query for spec in specs if spec.query}
        known = next(
            (item for item in known_answers if item.covers(self._discovery.device, queries)),
            None,
        )
        if known is not None:
            # Asked before (the capability check or an earlier start): no need
            # to ask again; an older receiver takes 10-15 s for it.
            self._discovery.answered = {spec.id for spec in specs if spec.query in known.answered}
            self.known_answers = known
        else:
            await self._timed("answers", self._async_probe_answered(specs))
        self.setup_timings["discovery"] = self._ms_since(started)
        return self._discovery

    async def _async_discover_setup_interface(self) -> None:
        """Read what the setup interface (HTTPS 10443 or HTTP 11080) offers.

        The video setup's availability and values, whose present menus become
        discovery features (the telnet picture controls are gated on
        "PictureAdjust"), the speaker setup (amp assign) and the live signal.
        """

        await self._video_config.discover()
        self._discovery.features |= self._video_config.feature_flags()
        await self._speaker_config.discover()
        await self.async_refresh_signal()

    async def _timed(self, name: str, step: Awaitable[None]) -> None:
        """Run a discovery step and keep how long it took."""

        started = asyncio.get_running_loop().time()
        await step
        self.setup_timings[name] = self._ms_since(started)

    @staticmethod
    def _ms_since(started: float) -> int:
        return round((asyncio.get_running_loop().time() - started) * 1000)

    def discovery_snapshot(self) -> dict[str, Any]:
        """The part of the discovery the media players need, as plain data.

        Kept by the caller after every successful discovery, so the media
        players can be set up from it while the receiver does not answer.
        """

        discovery = self._discovery
        return {
            "device": asdict(discovery.device),
            "zones": [asdict(zone) for zone in discovery.zones],
            "sources": [asdict(source) for source in discovery.sources],
            "answered": sorted(discovery.answered),
            "known_answers": self.known_answers.as_dict() if self.known_answers else None,
        }

    def restore_discovery(self, snapshot: dict[str, Any]) -> None:
        """Fill the discovery from a snapshot, for a start without the receiver.

        Unknown keys are ignored, so a snapshot from another version still
        loads.
        """

        def known(cls: type, data: dict[str, Any]) -> dict[str, Any]:
            names = {item.name for item in fields(cls)}
            return {key: value for key, value in data.items() if key in names}

        discovery = self._discovery
        discovery.device = DeviceInfo(**known(DeviceInfo, snapshot.get("device", {})))
        discovery.zones = [
            ZoneDescriptor(**known(ZoneDescriptor, zone)) for zone in snapshot.get("zones", [])
        ]
        discovery.sources = [
            SourceDescriptor(**known(SourceDescriptor, source))
            for source in snapshot.get("sources", [])
        ]

    async def _async_fetch_version_info(self) -> None:
        """Read firmware/serial and AIOS module versions from the UPnP document.

        Best effort: the goform Deviceinfo document omits these, but the UPnP
        (AIOS) description carries them. Any failure or missing field is left
        unset rather than guessed.
        """

        xml_text = await self._upnp.async_get_description()
        wanted: set[str] | None = None
        if not xml_text and self._http_port == 80:
            xml_text = await UpnpClient(
                self._session, self._host, port=OLDER_PORT, path=OLDER_DESCRIPTION_PATH
            ).async_get_description()
            # Only the identity: its model number is no network module version.
            wanted = {"manufacturer", "serial_number"}
        if not xml_text:
            return
        device = self._discovery.device
        for attr, value in parse_upnp_description(xml_text).items():
            if wanted is not None and attr not in wanted:
                continue
            if getattr(device, attr, None) is None:
                setattr(device, attr, value)

    async def _async_probe_introspection(self) -> None:
        """Populate the telnet-discovered model over a short lived connection.

        Separate from the persistent connection so discovery is deterministic at
        setup time. The raw socket handling lives in the telnet transport; this
        just supplies the queries and feeds the replies to the parser, stopping
        once the sources and speaker configuration have arrived.
        """

        queries = [
            spec["query"]
            for spec in self._profile.introspection.values()
            if spec.get("query")
        ]
        # The channel trims, for receivers without the SSLEV list (AVR-X2000):
        # their channels are known only from these.
        queries.append(CHANNEL_TRIMS_QUERY)
        def feed(line: str) -> None:
            self._parser.feed(line, self._state)

        await async_probe(
            self._host,
            queries,
            feed,
            lambda: bool(
                self._discovery.configured_channels and self._discovery.sources
            ),
        )

    def _add_network_source(self) -> None:
        """Offer the network source (SINET) where the source lists leave it out.

        An AVR-X2000 lists its network services (Favorites, Internet Radio …)
        in SSSOD but not NET itself, although it reports SINET when playing
        from the network and names NETWORK in Deviceinfo.
        """

        discovery = self._discovery
        # Only next to a list the receiver gave: without one, the Deviceinfo
        # list stands in later (async_await_ready).
        if (
            discovery.sources
            and discovery.supports("NETWORK")
            and not any(source.code == "NET" for source in discovery.sources)
        ):
            discovery.sources.append(SourceDescriptor(code="NET", name="NETWORK"))

    def _answer_gated_controls(self) -> list[ControlSpec]:
        """The controls that exist on this receiver only if it answers their query."""

        return self._profile.controls_by_scope("answered") + [
            spec
            for spec in self._profile.feature_controls()
            if spec.scope == "feature"
            and not self._discovery.supports(spec.feature or "")
            # Picture levels: an answer says the encoding fits only where the
            # profile knows the receiver's generation (the older variant has
            # the AVR-X2000 scale); a newer receiver names PictureAdjust.
            and (spec.kind != "centered" or self._profile.variant == OLDER)
        ]

    async def _async_probe_answered(self, specs: list[ControlSpec]) -> None:
        """Ask the queries of the "answered" controls; keep those that answer.

        These controls (auto standby, triggers, IMAX, Auro-3D, Dirac …) have
        no Deviceinfo name to gate them, so the receiver's answer decides.
        So does it for a feature control whose name the receiver does not
        list: older receivers (AVR-X2000) name few features in Deviceinfo but
        answer MultEQ, Dynamic EQ, tone and the like over telnet.
        """

        queries = list(dict.fromkeys(spec.query for spec in specs if spec.query))
        if not queries:
            return
        # The power state tells whether the answers can be kept: in standby
        # some receivers do not answer every query.
        queries.insert(0, POWER_QUERY)
        # All queries are sent before reading; the receiver answers within
        # 200 ms. So the first pause of 0.4 s without a line ends it: a model
        # without these controls stays silent, and waiting for answers that
        # never come would only delay every start.
        # Older receivers (port 80, AVR-X2000) drop queries sent while they
        # still answer one: wait for each answer (the protocol promises one
        # within 200 ms). They are also the ones that name few functions, so
        # their probe is long.
        await async_probe(
            self._host,
            queries,
            self._feed_quietly,
            _answers_done,
            deadline=2.0,
            reply_wait=REPLY_WAIT_OLDER if self._http_port == 80 else 0.0,
        )
        self._discovery.answered = {spec.id for spec in specs if spec.id in self._state.values}
        if self._state.system_power:
            asked = frozenset(query for query in queries if query != POWER_QUERY)
            device = self._discovery.device
            self.known_answers = KnownAnswers(
                device.model_name,
                device.firmware_version,
                asked,
                frozenset(spec.query for spec in specs if spec.id in self._discovery.answered and spec.query),
            )

    def _feed_quietly(self, line: str) -> None:
        """Feed a line to the state without notifying (setup is not done yet)."""

        self._parser.feed(line, self._state)

    @property
    def button_commands(self) -> list[str]:
        """The command buttons of the profile (channel level reset, network restart)."""

        return list(self._profile.button_commands)

    async def async_press(self, name: str) -> None:
        """Send a command button's command. Raises KeyError for an unknown name."""

        await self._send(self._profile.button_commands[name])

    @property
    def _quick_select_prefix(self) -> str:
        """MSQUICK, or MSSMART on a Marantz receiver."""

        return str(self._profile.introspection.get("quick_select", {}).get("prefix", "MSQUICK"))

    async def async_save_quick_select(self, number: int) -> None:
        """Store the current settings in a main zone quick select preset."""

        await self._send(f"{self._quick_select_prefix}{int(number)} MEMORY")

    @property
    def telnet_media(self) -> bool:
        """Now playing and transport over telnet: receivers without HEOS (port 80)."""

        return self._profile.variant == OLDER

    @property
    def transport_available(self) -> bool:
        """Whether play, pause and skip can reach the network player."""

        return self.telnet_media or self.heos is not None

    def media_art_url(self) -> str | None:
        """The cover of what plays, on receivers without HEOS (changes with the track)."""

        now = self._state.now_playing
        path = self._profile.netaudio.get("art_path")
        if not (self.telnet_media and path and now.active):
            return None
        stamp = abs(hash((now.title, now.artist, now.album)))
        return f"http://{self._host}:{self._http_port or 80}{path}?{stamp}"

    async def async_start(self) -> None:
        """Start the telnet transport (which triggers the first resync)."""

        await self._telnet.async_start()
        # Best effort; the HEOS channel is auxiliary and self-heals on its own.
        # Receivers on port 80 have no HEOS; they report over telnet instead.
        if self._heos_enabled and not self.telnet_media:
            await self._heos.async_start()
        self._media_task = asyncio.ensure_future(self._media_loop())

    async def _media_loop(self) -> None:
        """Ask for now playing (no HEOS) and the tuner while they are the source."""

        while True:
            await asyncio.sleep(MEDIA_POLL_INTERVAL)
            try:
                await self.async_poll_media()
            except Exception as err:  # noqa: BLE001 - a background task must not raise
                _LOGGER.debug("Asking %s for now playing failed: %s", self._host, err)

    async def async_poll_media(self) -> None:
        """One round: NSE for a network source, the tuner queries for the tuner.

        For every zone that is on: zone 2 can play the tuner or the network
        while the main zone plays something else.
        """

        if not self._telnet.connected:
            return
        grammar = self._profile.netaudio
        if not grammar:
            return
        playing = {
            self.zone_source(zone_id)
            for zone_id, zone in self._state.zones.items()
            if zone.power
        }
        network = bool(playing & set(grammar.get("sources", [])))
        if self.telnet_media and not network and self._state.now_playing.state is not None:
            # Off the network source: nothing plays there any more.
            self._state.now_playing = NowPlaying()
            self._schedule_update()
        if playing & set(grammar.get("tuner_sources", [])):
            for query in grammar.get("tuner_queries", []):
                await self._telnet.async_send(query)
        if self.telnet_media and network:
            await self._telnet.async_send(grammar["query"])

    @property
    def network_source(self) -> str | None:
        """The receiver's network input (HEOS Music, NET), if it lists one."""

        codes = {source.code for source in self._discovery.sources}
        listed = next(
            (code for code in self._profile.netaudio.get("sources", []) if code in codes), None
        )
        if listed is None and "NETWORK" in codes:
            # The Deviceinfo list names it NETWORK; on the wire it is NET.
            return "NET"
        return listed

    def plays_network(self, zone_id: str) -> bool:
        """Whether a zone is on and plays the network input."""

        zone = self._state.zone(zone_id)
        sources = self._profile.netaudio.get("sources", [])
        return bool(zone.power) and self.zone_source(zone_id) in sources

    def zone_source(self, zone_id: str) -> str | None:
        """The source a zone plays: the main zone's when it follows it (SOURCE)."""

        source = self._state.zone(zone_id).source
        if zone_id != "main" and source == FOLLOW_MAIN:
            return self._state.zone("main").source
        return source

    def is_tuner(self, source: str | None) -> bool:
        return source is not None and source in self._profile.netaudio.get("tuner_sources", [])

    @property
    def tuner_preset_codes(self) -> list[str]:
        """The tuner's preset codes: 01-56, or A1-G8 on older receivers."""

        presets = self._profile.netaudio.get("tuner_presets", {})
        count = int(presets.get("count", 0))
        if presets.get("format") == "letter":
            return [f"{chr(ord('A') + index // 8)}{index % 8 + 1}" for index in range(count)]
        return [f"{index + 1:02d}" for index in range(count)]

    @property
    def has_tuner(self) -> bool:
        """A tuner among the receiver's sources, with a known preset numbering."""

        return bool(self.tuner_preset_codes) and any(
            self.is_tuner(source.code) for source in self._discovery.sources
        )

    @property
    def tuner_zone(self) -> str | None:
        """A zone that is on and plays the tuner, or None.

        Tuner commands work only while the tuner is the source of a zone.
        """

        for zone_id, zone in self._state.zones.items():
            if zone.power and self.is_tuner(self.zone_source(zone_id)):
                return zone_id
        return None

    async def async_tune(self, frequency: int) -> None:
        """Tune to a frequency in the protocol's unit (FM: MHz x 100).

        With no zone on the tuner, the main zone is switched to it first: the
        receiver takes tuner commands only while the tuner plays.
        """

        if self.tuner_zone is None:
            await self._async_to_tuner("main")
        await self._send(f"TFAN{frequency:06d}")

    async def async_store_tuner_preset(self, code: str) -> None:
        """Store the station that plays as tuner preset `code`."""

        presets = self._profile.netaudio.get("tuner_presets", {})
        if code not in self.tuner_preset_codes:
            raise ValueError(f"no tuner preset {code!r}")
        await self._send(f"{presets['prefix']}MEM{code}")

    async def async_tuner_preset_step(self, up: bool) -> None:
        """The next or the previous tuner preset."""

        presets = self._profile.netaudio.get("tuner_presets", {})
        command = presets.get("up" if up else "down")
        if command:
            await self._send(command)

    async def async_tuner_preset(self, zone_id: str, code: str) -> None:
        """Recall a tuner preset, switching the zone to the tuner first.

        The receiver takes preset commands only while the tuner is the source.
        """

        presets = self._profile.netaudio.get("tuner_presets", {})
        if not presets or code not in self.tuner_preset_codes:
            raise ValueError(f"no tuner preset {code!r}")
        await self._async_to_tuner(zone_id)
        await self._send(f"{presets['prefix']}{code}")

    async def _async_to_tuner(self, zone_id: str) -> None:
        """Turn the zone on and switch it to the tuner, unless it plays it already."""

        tuners = self._profile.netaudio.get("tuner_sources", [])
        if not tuners:
            raise ValueError("no tuner")
        if self.is_tuner(self.zone_source(zone_id)) and self._state.zone(zone_id).power:
            return
        if not self._state.zone(zone_id).power:
            await self.async_set_power(zone_id, True)
        await self.async_select_source(zone_id, tuners[0])
        try:
            async with asyncio.timeout(TUNER_SWITCH_WAIT):
                while not self.is_tuner(self.zone_source(zone_id)):
                    await asyncio.sleep(0.1)
        except TimeoutError:
            _LOGGER.debug("%s did not switch to the tuner in time", self._host)

    async def async_await_ready(self, timeout: float = 8.0) -> None:
        """Wait, best effort, until the initial resync has populated state.

        This lets the platforms build the right entities at setup time. We wait
        for the core state, the channel calibration list (SSLEV) AND the speaker
        configuration (SSSPC), because the latter decides which channel entities
        are enabled by default. It never raises; on timeout it returns with
        whatever has arrived so far (falling back to "all channels enabled").
        """

        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + timeout
        while loop.time() < deadline:
            if (
                self._state.system_power is not None
                and self._state.channel_levels
                and self._discovery.configured_channels
            ):
                break
            await asyncio.sleep(0.1)
        self.setup_timings["ready"] = self._ms_since(started)
        # If the telnet introspection produced no sources, fall back to the
        # Deviceinfo source list so the media player still has a source list.
        if not self._discovery.sources and self._fallback_sources:
            self._discovery.sources = self._fallback_sources

    async def async_stop(self) -> None:
        """Stop the telnet transport and cancel any pending notification."""

        if self._update_handle is not None:
            self._update_handle.cancel()
            self._update_handle = None
        if self._signal_handle is not None:
            self._signal_handle.cancel()
            self._signal_handle = None
        for task in list(self._pending_tasks):
            task.cancel()
        self._pending_tasks.clear()
        if self._media_task is not None:
            self._media_task.cancel()
            try:
                await self._media_task
            except asyncio.CancelledError:
                pass
            self._media_task = None
        await self._telnet.async_stop()
        await self._heos.async_stop()

    async def async_poll(self) -> None:
        """HTTP reconciliation poll, used by the coordinator as a safety net.

        When telnet is connected this only confirms reachability. When telnet is
        down it recovers the zones' power, volume, mute and source, so the media
        players stay usable over HTTP.
        """

        status = await self._goform.async_get_status(self._zone_status_path(1))
        self._http_reachable = status is not None
        if status is None:
            # HTTP unreachable; rely on the telnet availability signal.
            return
        # The volume display mode (Absolute/Relative) is only exposed over HTTP,
        # so always capture it, even while telnet is the source for everything else.
        display = status.get("volume_display")
        if display is not None and display != self._state.volume_display:
            self._state.volume_display = str(display)
            self._schedule_update()
        if not self._telnet.connected:
            self._apply_http_status(self._main_zone_id(), status)
            await self._poll_additional_zones()
            await self._async_read_app_commands()
            self._schedule_update()

    @property
    def http_values(self) -> frozenset[str]:
        """The values read over HTTP while telnet is down (avr/appcommand.py)."""

        return frozenset(self._http_values)

    async def _async_read_app_commands(self) -> None:
        """Read the sound mode, tone and Audyssey over AppCommand(0300).xml.

        Only while telnet is down, and only on receivers whose level has the
        page; a receiver that does not answer is simply not read.
        """

        level = self._discovery.device.comm_api_version
        if level is None or level < appcommand.STATUS_FROM:
            return
        values = appcommand.parse_status(
            await self._goform.async_post(appcommand.STATUS_PATH, appcommand.STATUS_REQUEST)
        )
        if level >= appcommand.SETUP_FROM:
            values.update(
                appcommand.parse_audyssey(
                    await self._goform.async_post(
                        appcommand.SETUP_PATH, appcommand.AUDYSSEY_REQUEST
                    )
                )
            )
        self._state.values.update(values)
        self._http_values.update(values)

    @property
    def signal_supported(self) -> bool:
        """Whether the receiver reports live video and channel information."""

        return self._signal_supported is True

    async def async_refresh_signal(self) -> None:
        """Read the live video and channel information again. Never raises.

        Asked at setup, after a change of input, signal or power (a little
        later, once the receiver has settled) and with every poll. A receiver
        that does not answer at setup is not asked again.
        """

        if self._signal_supported is False:
            return
        try:
            video = signal_info.parse_video(
                await self._webconfig.async_get("info", signal_info.VIDEO_TYPE)
            )
            audio = signal_info.parse_audio(
                await self._webconfig.async_get("info", signal_info.AUDIO_TYPE)
            )
        except Exception as err:  # noqa: BLE001 - auxiliary, like the other setup reads
            _LOGGER.debug("Signal information of %s not read: %s", self._host, err)
            return
        if video is None:
            if self._signal_supported is None:
                self._signal_supported = False
            return
        self._signal_supported = True
        values = {**video, **(audio or dict.fromkeys(signal_info.AUDIO_FIELDS))}
        if values != self._state.signal:
            self._state.signal = values
            self._schedule_update()

    def _schedule_signal_refresh(self) -> None:
        """Read the signal information shortly after a change that may alter it."""

        if not self._signal_supported:
            return
        if self._signal_handle is not None:
            self._signal_handle.cancel()
        loop = asyncio.get_running_loop()
        self._signal_handle = loop.call_later(
            SIGNAL_SETTLE, lambda: self._create_task(self.async_refresh_signal())
        )

    @property
    def firmware_update(self) -> bool | None:
        """Whether HEOS reports a firmware update; None while it is not known."""

        return self._firmware_update

    async def async_check_firmware(self) -> None:
        """Ask HEOS for a firmware update, at most every FIRMWARE_CHECK_INTERVAL."""

        heos = self.heos
        loop = asyncio.get_running_loop()
        if heos is None or (
            self._firmware_checked_at is not None
            and loop.time() - self._firmware_checked_at < FIRMWARE_CHECK_INTERVAL
        ):
            return
        result = await heos.async_check_update()
        if result is None:
            return
        self._firmware_checked_at = loop.time()
        if result != self._firmware_update:
            self._firmware_update = result
            self._schedule_update()

    async def async_recover_audio(self) -> None:
        """Standby and back on, then the input it had: for a receiver stuck silent.

        As TheFab21/ha-denon-marantz-avr offers it: a soft power cycle, not a
        cut of the power. Waits for the receiver to report on before the input.
        """

        source = self._state.zone("main").source
        await self.async_set_system_power(False)
        await asyncio.sleep(RECOVER_STANDBY_TIME)
        await self.async_set_system_power(True)
        try:
            async with asyncio.timeout(RECOVER_ON_WAIT):
                while not self._state.zone("main").power:
                    await asyncio.sleep(0.25)
        except TimeoutError:
            _LOGGER.debug("%s did not report on after the recovery", self._host)
        if source:
            await self.async_select_source("main", source)

    async def async_refresh_setup(self) -> None:
        """Read the settings kept on the setup interface again.

        The graphic EQ bands, the video setup and the speaker setup (amp
        assign) have no telnet push. They are read when the telnet link comes
        up, after each own write, and when the caller asks (every so many
        minutes, a connection option). Each read is non-disruptive and a no-op
        when the receiver does not offer it; a failed read leaves the values.
        """

        await self._graphic_eq.refresh()
        await self._video_config.refresh()
        await self._speaker_config.refresh()

    # Telnet callbacks -----------------------------------------------------

    def _handle_line(self, line: str) -> None:
        """Feed a received telnet line to the parser and notify on change."""

        for listener in tuple(self._reply_listeners):
            listener(line)
        before = self._signal_context()
        # The first report of a trigger after connecting answers the resync,
        # which asked for everything already.
        first = self._first_report(line)
        # The receiver repeats signal lines (an AVR-X2600H after every OPSML
        # query); only a change is worth reading again, or the re-query that
        # brings the repeat starts the next one.
        changed = False
        if self._parser.feed(line, self._state):
            self._schedule_update()
            changed = self._signal_context() != before and not first
            if changed and line.startswith(SIGNAL_TRIGGERS):
                self._schedule_signal_refresh()
        # The receiver pushes OPSMLALL (all groups) when the sound mode context
        # changes, e.g. after a genre group switch, but never pushes OPSML (the
        # current group's selectable modes). When an OPSMLALL push completes,
        # re-query OPSML so the per group Sound Mode list follows the change.
        # The end of the answer to our own OPSMLALL query needs none: whoever
        # asked for it (the resync, a refresh group) asks for OPSML as well.
        if line.startswith("OPSMLALL"):
            remainder = line[len("OPSMLALL") :].strip()
            if remainder.startswith(self._profile.list_terminator) and not self._asked_recently():
                self._create_task(self._query_current_sound_modes())
        # Data driven refresh: any group whose trigger prefix this line matches
        # gets a coalesced re-query. This keeps state the receiver does not
        # re-push on every change in sync - the signal dependent sound mode
        # lists on an audio format change, and the audio information and
        # channel/speaker layout on a sound mode (MS) or source (SI) change.
        if not changed:
            return
        for group_id, group in self._refresh_groups.items():
            on = group.get("on")
            if on and line.startswith(tuple(on)):
                self._schedule_refresh(group_id)

    def _first_report(self, line: str) -> bool:
        """Whether the line is the first report of its trigger since connecting."""

        prefixes = {prefix for prefix in SIGNAL_TRIGGERS if line.startswith(prefix)}
        for group in self._refresh_groups.values():
            prefixes.update(prefix for prefix in group.get("on", ()) if line.startswith(prefix))
        if not prefixes or prefixes <= self._seen_triggers:
            return False
        self._seen_triggers |= prefixes
        return True

    def _note_sent(self, command: str) -> None:
        """Remember when the full sound mode list was asked for."""

        if command == self._profile.introspection_query("sound_mode_list"):
            self._sound_modes_asked_at = asyncio.get_running_loop().time()

    def _asked_recently(self) -> bool:
        """Whether an OPSMLALL list now ending is the answer to our own query."""

        asked = self._sound_modes_asked_at
        return asked is not None and asyncio.get_running_loop().time() - asked < SOUND_MODES_ANSWER

    def _signal_context(self) -> tuple[Any, ...]:
        """What the signal and the sound mode lists depend on, to spot a change."""

        state = self._state
        return (
            state.system_power,
            tuple((zone_id, zone.power, zone.source) for zone_id, zone in state.zones.items()),
            state.values.get("sound_mode"),
            state.values.get("video_select"),
            tuple(sorted(state.readonly.items())),
        )

    def _handle_now_playing(self, snapshot: dict[str, Any]) -> None:
        """Apply a HEOS now-playing snapshot and notify listeners."""

        self._state.now_playing = NowPlaying(**snapshot)
        self._schedule_update()

    def _handle_availability(self, available: bool) -> None:
        """React to the telnet link going up or down."""

        self._available = available
        if available:
            # Telnet reports everything again.
            self._http_values.clear()
        self._schedule_update()

    async def _resynchronise(self) -> None:
        """Query the full state after connecting so nothing is stale."""

        self._seen_triggers.clear()
        for command in self._resync_queries():
            self._note_sent(command)
            await self._telnet.async_send(command)
        # The setup settings may have changed while the link was down.
        self._create_task(self._refresh_setup_quietly())

    async def _refresh_setup_quietly(self) -> None:
        """async_refresh_setup as a background task: report, never raise."""

        try:
            await self.async_refresh_setup()
        except Exception as err:  # noqa: BLE001 - a background task must not raise
            _LOGGER.debug("Reading the setup settings of %s failed: %s", self._host, err)

    def _resync_queries(self) -> list[str]:
        """Build the list of query commands used to resynchronise state."""

        queries: list[str] = []

        def add(query: str | None) -> None:
            if query and query not in queries:
                queries.append(query)

        # Introspection first so the source and channel lists (which entities
        # are built from) arrive as early as possible after connecting.
        for introspection in self._profile.introspection.values():
            add(introspection.get("query"))
        # Core controls (main zone) are always present.
        for spec in self._profile.controls_by_scope("core"):
            add(spec.query)
        # Additional zones use the 'Z<index>' query, one per discovered zone.
        prefix = self._profile.zones.get("additional_prefix", "Z")
        for zone in self._discovery.zones:
            if not zone.is_main:
                add(f"{prefix}{zone.index}?")
                # Z2? reports power, source and volume, not the mute state.
                add(f"{prefix}{zone.index}MU?")
        # Read only audio information.
        for readonly in self._profile.readonly.values():
            add(readonly.get("query"))
        # Feature controls, only those the receiver advertises. Some controls
        # have several queries (a list query and a read query, e.g. channel
        # calibration via SSLEV plus live trims via CV); send all of them.
        for control in self._profile.feature_controls():
            if control.feature and (
                self._discovery.supports(control.feature)
                or control.id in self._discovery.answered
            ):
                add(control.query)
                add(control.get("list_query"))
                add(control.get("read_query"))
        # Controls without a Deviceinfo name that answered at setup.
        for answered in self._profile.controls_by_scope("answered"):
            if answered.id in self._discovery.answered:
                add(answered.query)
        return queries

    # Notification ---------------------------------------------------------

    def _schedule_update(self) -> None:
        """Debounce state notifications so a burst becomes a single update."""

        if self._update_callback is None:
            return
        loop = asyncio.get_running_loop()
        if self._update_handle is not None:
            self._update_handle.cancel()
        self._update_handle = loop.call_later(_UPDATE_DEBOUNCE, self._fire_update)

    def _fire_update(self) -> None:
        self._update_handle = None
        if self._update_callback is not None:
            self._update_callback()

    def _zone_status_path(self, index: int) -> str:
        """The StatusLite page of a zone (1 is the main zone)."""

        zones = self._profile.zones
        if index == 1:
            return str(
                zones.get(
                    "status_lite_main", "/goform/formMainZone_MainZoneXmlStatusLite.xml"
                )
            )
        template = zones.get(
            "status_lite_additional",
            "/goform/formZone{index}_Zone{index}XmlStatusLite.xml",
        )
        return str(template).format(index=index)

    def _main_zone_id(self) -> str:
        return str(self._profile.zones.get("main_zone_id", "main"))

    async def _poll_additional_zones(self) -> None:
        """Read the status of every zone but the main one over HTTP."""

        for zone in self._discovery.zones:
            if zone.is_main:
                continue
            status = await self._goform.async_get_status(
                self._zone_status_path(zone.index)
            )
            if status is not None:
                self._apply_http_status(zone.id, status)

    async def _refresh_after_http_command(self) -> None:
        """Read the zones again after a command sent over HTTP."""

        await asyncio.sleep(_HTTP_COMMAND_SETTLE)
        status = await self._goform.async_get_status(self._zone_status_path(1))
        if status is not None:
            self._apply_http_status(self._main_zone_id(), status)
        await self._poll_additional_zones()
        await self._async_read_app_commands()
        self._schedule_update()

    def _apply_http_status(self, zone_id: str, status: dict[str, object]) -> None:
        """Apply a parsed StatusLite snapshot to a zone (fallback path)."""

        zone = self._state.zone(zone_id)
        if "power" in status:
            zone.power = bool(status["power"])
        if "muted" in status:
            zone.muted = bool(status["muted"])
        if "source" in status:
            zone.source = str(status["source"])
        if "volume_db" in status:
            zone.volume_raw = self._profile.volume_reference + float(
                status["volume_db"]  # type: ignore[arg-type]
            )

    # Control methods ------------------------------------------------------

    async def async_send_command(
        self, command: str, *, collect_replies: bool = False
    ) -> list[str]:
        """Send a raw command exactly as given (the send_command action).

        Unlike the controls, which drop a command they cannot send, this
        raises CommandNotSent: the caller asked for exactly this command.
        While telnet is down a command goes over HTTP; a query cannot, as
        its answer only comes back over telnet.

        With collect_replies, the lines that start with the command's
        two-letter prefix and arrive after it are returned: up to
        REPLY_COLLECT_FIRST seconds for the first, then until none came for
        REPLY_COLLECT_QUIET. A line pushed for another reason with the same
        prefix (a change on the remote) can be among them. Over HTTP nothing
        comes back, so the list is empty.
        """

        if not self._telnet.connected:
            if is_query(command):
                raise CommandNotSent("query_needs_telnet")
            if not self._http_reachable or not await self._goform.async_send_command(command):
                raise CommandNotSent("unreachable")
            self._create_task(self._refresh_after_http_command())
            return []
        if not collect_replies:
            await self._telnet.async_send(command)
            return []

        prefix = command[:2].upper()
        return await self._send_and_collect(
            command, lambda line: line.startswith(prefix), REPLY_COLLECT_FIRST
        )

    async def _send_and_collect(
        self, command: str, wanted: Callable[[str], bool], first: float
    ) -> list[str]:
        """Send over telnet and return the wanted lines that arrive after it.

        Up to `first` seconds for the first line, then until none came for
        REPLY_COLLECT_QUIET; the replies of one command come in a burst.
        """

        return await self.async_collect(wanted, lambda: self._telnet.async_send(command), first)

    async def async_collect(
        self,
        wanted: Callable[[str], bool],
        action: Callable[[], Awaitable[Any]],
        first: float,
    ) -> list[str]:
        """Run `action` (which sends something) and return the wanted lines after it.

        Up to `first` seconds for the first line, then until none came for
        REPLY_COLLECT_QUIET; the replies of one command come in a burst. The
        state is already updated from each line when this returns.
        """

        replies: list[str] = []
        arrived = asyncio.Event()

        def listen(line: str) -> None:
            if wanted(line):
                replies.append(line)
                arrived.set()

        self._reply_listeners.append(listen)
        try:
            await action()
            # The client paces the queue; time the reply from when the command
            # has left it, not from when it was queued (behind a resync, say).
            loop = asyncio.get_running_loop()
            until = loop.time() + QUEUE_WAIT_MAX
            while self._telnet.queued and loop.time() < until and not replies:
                await asyncio.sleep(0.05)
            wait = first
            while True:
                try:
                    async with asyncio.timeout(wait):
                        await arrived.wait()
                except TimeoutError:
                    break
                arrived.clear()
                wait = REPLY_COLLECT_QUIET
        finally:
            self._reply_listeners.remove(listen)
        return replies

    async def async_check_capabilities(
        self,
        on_progress: Callable[[float], None] | None = None,
        on_current: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Ask every query of the capability check and return what came back.

        Over the existing telnet connection, as queries, so commands from Home
        Assistant still go first. Takes up to about a minute and a half. The
        reply lines are anonymised (MAC, IP and e-mail addresses). Raises
        CommandNotSent when telnet is down: the answers only come that way.
        """

        if not self._telnet.connected:
            raise CommandNotSent("query_needs_telnet")
        results: dict[str, dict[str, Any]] = {}
        current: str | None = None
        for index, (command, group) in enumerate(capability_check.QUERIES):
            if on_current is not None and group != current:
                current = group
                on_current(group)
            lines = await self._send_and_collect(
                command, _reply_or_error(capability_check.reply_prefix(command)), CHECK_REPLY_FIRST
            )
            results[command] = {
                "group": group,
                "result": capability_check.classify(command, lines),
                "lines": lines,
            }
            if on_progress is not None:
                on_progress((index + 1) / len(capability_check.QUERIES))
        device = self._discovery.device
        # Anonymised together, so a name the receiver reports in one reply
        # (NSFRN) is also replaced in every other.
        everything = [line for item in results.values() for line in item["lines"]]
        clean = iter(
            capability_check.anonymise(
                everything, macs=(device.mac_address,), serials=(device.serial_number,)
            )
        )
        for item in results.values():
            item["lines"] = [next(clean) for _ in item["lines"]]
        return {
            "check_version": capability_check.CHECK_VERSION,
            "model": device.model_name,
            "firmware": device.firmware_version,
            "hardware_type": device.hardware_type,
            # The generation decides which of the queries apply (docs/COMPATIBILITY.md).
            "generation": None if (generation := generation_of(device)) is None else generation.key,
            "queries": results,
        }

    async def async_command_test(
        self,
        groups: Sequence[str],
        on_progress: Callable[[float], None] | None = None,
        on_current: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Write settings, check the receiver confirms them, put them back.

        See avr/command_test.py. A main zone that is off is turned on for the
        test and off again afterwards. Raises CommandNotSent when telnet is
        down: the confirmations only come that way.
        """

        if not self._telnet.connected:
            raise CommandNotSent("query_needs_telnet")
        main_on = bool(self._state.zone("main").power)
        snapshot = self.zone_snapshot("main")
        found = command_test.context(self)
        turned_on = await command_test.async_power_on(self)
        try:
            results = await command_test.async_run(self, list(groups), on_progress, on_current)
        finally:
            if turned_on:
                # Off again as it was; also when the test is stopped.
                await self.async_restore("main", snapshot)
        device = self._discovery.device
        return {
            "test_version": command_test.TEST_VERSION,
            "model": device.model_name,
            "firmware": device.firmware_version,
            "groups": [group for group in command_test.GROUPS if group in groups],
            "main_zone_on": main_on,
            "turned_on_for_test": turned_on,
            "context": found,
            "tests": results,
        }

    async def _send(self, command: str) -> None:
        """Send over telnet, or over HTTP while the telnet link is down.

        Over HTTP only commands go out; a query's answer would arrive nowhere.
        A command that reaches the receiver neither way raises CommandNotSent.
        """

        if self._telnet.connected or is_query(command):
            self._note_sent(command)
            await self._telnet.async_send(command)
            return
        if not self._http_reachable or not await self._goform.async_send_command(command):
            # A query is only dropped (it is the integration's own); a command
            # was asked for, so the caller learns that it did not go out.
            raise CommandNotSent("unreachable")
        self._create_task(self._refresh_after_http_command())

    # Network media transport: over HEOS, or over telnet (NS9A-NS9E) on
    # receivers without HEOS. No-ops when nothing can take them.
    async def async_media_play(self) -> None:
        if await self._telnet_transport("play", state="play"):
            return
        await self._heos.async_play()

    async def async_media_pause(self) -> None:
        if await self._telnet_transport("pause", state="pause"):
            return
        await self._heos.async_pause()

    async def async_media_stop(self) -> None:
        if await self._telnet_transport("stop", state="stop"):
            return
        await self._heos.async_stop_playback()

    async def async_media_next(self) -> None:
        if await self._telnet_transport("next"):
            return
        await self._heos.async_next()

    async def async_media_previous(self) -> None:
        if await self._telnet_transport("previous"):
            return
        await self._heos.async_previous()

    async def _telnet_transport(self, action: str, state: str | None = None) -> bool:
        """Send a transport command over telnet where there is no HEOS.

        The on-screen list cannot tell play from pause, so the state follows
        the command.
        """

        command = self._profile.netaudio.get("transport", {}).get(action)
        if not (self.telnet_media and command):
            return False
        await self._send(command)
        if state is not None and self._state.now_playing.state is not None:
            self._state.now_playing = replace(self._state.now_playing, state=state)
            self._schedule_update()
        return True

    @property
    def heos(self) -> HeosClient | None:
        """The HEOS connection, where it browses and plays (HEOS receivers)."""

        if not self.heos_enabled or self.telnet_media or not self._heos.connected:
            return None
        return self._heos

    @property
    def heos_connected(self) -> bool:
        """Whether the HEOS connection is up and knows the receiver's player."""

        return self._heos.connected

    @property
    def heos_command_results(self) -> dict[str, dict[str, str]]:
        """The receiver's last answer to each HEOS command sent (for diagnostics)."""

        return dict(self._heos.command_results)

    @property
    def can_play_url(self) -> bool:
        """Whether a URL can be played: over HEOS, not on receivers without it."""

        return self._heos_enabled and not self.telnet_media

    async def async_play_url(self, url: str) -> None:
        """Play a stream URL over HEOS. Raises CommandNotSent if it cannot go out."""

        if not self.can_play_url or not await self._heos.async_play_url(url):
            raise CommandNotSent("unreachable")

    async def async_media_repeat(self, repeat: str) -> None:
        """Repeat off, all or one (HEOS off / on_all / on_one)."""

        await self._heos.async_set_repeat(repeat)

    async def async_media_shuffle(self, shuffle: bool) -> None:
        await self._heos.async_set_shuffle(shuffle)

    @property
    def graphic_eq(self) -> graphic_eq.GraphicEqController:
        """The manual graphic-EQ subsystem (read/edit/apply). See GraphicEqController."""

        return self._graphic_eq

    @property
    def video_config(self) -> VideoConfigController:
        """The /ajax video setup subsystem (HDMI setup, OSD, TV/4K format)."""

        return self._video_config

    @property
    def speaker_config(self) -> VideoConfigController:
        """The /ajax speaker setup subsystem (amp assign)."""

        return self._speaker_config

    def _zone(self, zone_id: str) -> ZoneDescriptor | None:
        """Return the discovered ZoneDescriptor for a zone id, if any."""

        for zone in self._discovery.zones:
            if zone.id == zone_id:
                return zone
        return None

    def _additional_zone_prefix(self, zone_id: str) -> str | None:
        """Return the 'Z<index>' prefix for a non main zone, or None."""

        zone = self._zone(zone_id)
        if zone is None or zone.is_main:
            return None
        base = self._profile.zones.get("additional_prefix", "Z")
        return f"{base}{zone.index}"

    @property
    def remote_commands(self) -> list[str]:
        """The names the remote entity accepts, in the profile's order."""

        return list(self._profile.remote_commands)

    async def async_send_remote(self, name: str) -> None:
        """Send one remote command by name (cursor, enter, setup menu …).

        Raises KeyError for a name the profile does not know.
        """

        await self._send(self._profile.remote_commands[name])

    def zone_snapshot(self, zone_id: str) -> ZoneSnapshot:
        """What the zone is doing now: power, input, volume, mute."""

        zone = self._state.zone(zone_id)
        return ZoneSnapshot(
            power=zone.power, source=zone.source, volume_raw=zone.volume_raw, muted=zone.muted
        )

    async def async_restore(self, zone_id: str, snapshot: ZoneSnapshot) -> None:
        """Put a zone back as the snapshot found it; only what differs is sent."""

        zone = self._state.zone(zone_id)
        if snapshot.power is False:
            if zone.power is not False:
                await self.async_set_power(zone_id, False)
            return
        if snapshot.power and not zone.power:
            await self.async_set_power(zone_id, True)
        if snapshot.source and snapshot.source != zone.source:
            await self.async_select_source(zone_id, snapshot.source)
        if snapshot.volume_raw is not None and snapshot.volume_raw != zone.volume_raw:
            await self.async_set_volume_raw(zone_id, snapshot.volume_raw)
        if snapshot.muted is not None and snapshot.muted != zone.muted:
            await self.async_set_mute(zone_id, snapshot.muted)

    async def async_set_power(self, zone_id: str, on: bool) -> None:
        """Turn a zone on or off using that zone's power control."""

        prefix = self._additional_zone_prefix(zone_id)
        if prefix is None:
            spec = self._profile.control("main_power")
            if spec is None:
                return
            token = spec.get("on", "ON") if on else spec.get("off", "OFF")
            await self._send(f"{spec.prefix}{token}")
            return
        zones = self._profile.zones
        token = zones.get("power_on", "ON") if on else zones.get("power_off", "OFF")
        await self._send(f"{prefix}{token}")

    async def async_set_system_power(self, on: bool) -> None:
        """Set the overall system standby power."""

        spec = self._profile.control("system_power")
        if spec is None:
            return
        token = spec.get("on", "ON") if on else spec.get("off", "STANDBY")
        await self._send(f"{spec.prefix}{token}")

    async def async_set_volume_raw(self, zone_id: str, raw: float) -> None:
        """Set a zone's volume to a raw scale value (clamped to the reachable max)."""

        raw = max(0.0, min(raw, self.volume_effective_max(zone_id)))
        encoded = encode_half_step(raw)
        prefix = self._additional_zone_prefix(zone_id)
        if prefix is None:
            spec = self._profile.control("main_volume")
            if spec is not None:
                await self._send(f"{spec.prefix}{encoded}")
        else:
            # Additional zones set the volume through their overloaded prefix.
            await self._send(f"{prefix}{encoded}")

    async def async_volume_step(self, zone_id: str, up: bool) -> None:
        """Nudge the main zone volume up or down by one step."""

        if zone_id != "main":
            return
        spec = self._profile.control("main_volume")
        if spec is None:
            return
        token = spec.get("up") if up else spec.get("down")
        if token:
            await self._send(token)

    async def async_set_mute(self, zone_id: str, muted: bool) -> None:
        """Mute or unmute a zone."""

        prefix = self._additional_zone_prefix(zone_id)
        if prefix is None:
            spec = self._profile.control("main_mute")
            if spec is None:
                return
            token = spec.get("on", "ON") if muted else spec.get("off", "OFF")
            await self._send(f"{spec.prefix}{token}")
        else:
            # Additional zones mute through their overloaded prefix (Z2MUON/OFF).
            zones = self._profile.zones
            mute_token = zones.get("mute_token", "MU")
            on_off = zones.get("mute_on", "ON") if muted else zones.get("mute_off", "OFF")
            await self._send(f"{prefix}{mute_token}{on_off}")

    async def async_select_source(self, zone_id: str, name: str) -> None:
        """Select an input source by its display name."""

        code = self._discovery.source_code(name) or name
        prefix = self._additional_zone_prefix(zone_id)
        if prefix is None:
            spec = self._profile.control("main_source")
            if spec is not None:
                await self._send(f"{spec.prefix}{code}")
        else:
            await self._send(f"{prefix}{code}")

    async def async_choose_source(self, zone_id: str, name: str) -> None:
        """Select a source someone chose, and report it when the zone refuses it.

        A zone other than the main zone confirms a new source by echoing it.
        Without the echo the zone kept its source (Zone 2 plays no HDMI input
        on many receivers): the source is remembered as refused and
        SourceNotTaken raised. Over HTTP nothing comes back, so nothing is
        checked; following the main zone is always taken.
        """

        code = self._discovery.source_code(name) or name
        prefix = self._additional_zone_prefix(zone_id)
        if prefix is None or code == FOLLOW_MAIN or not self._telnet.connected:
            await self.async_select_source(zone_id, name)
            return
        command = f"{prefix}{code}"
        replies = await self.async_collect(
            lambda line: line == command, lambda: self._send(command), ZONE_SOURCE_WAIT
        )
        refused = self._refused_sources.setdefault(zone_id, set())
        if replies or self._state.zone(zone_id).source == code:
            refused.discard(code)
            return
        refused.add(code)
        self._schedule_update()
        raise SourceNotTaken(zone_id, code)

    @property
    def communication_log(self) -> list[str]:
        """The last telnet lines sent and received, for the diagnostics."""

        return self._telnet.log

    @property
    def heos_log(self) -> list[str]:
        """The last HEOS lines sent and received, for the diagnostics."""

        return self._heos.log

    def refused_sources(self, zone_id: str) -> frozenset[str]:
        """The sources a zone did not switch to since Home Assistant started."""

        return frozenset(self._refused_sources.get(zone_id, ()))

    async def async_select_sound_mode(self, name: str) -> None:
        """Select a surround / sound mode by its display name.

        The wire token is often not the plain display name (for example
        'Dolby Audio - Dolby Surround' is sent as 'DOLBY AUDIO-DSUR'). Use the
        deterministic profile override when the token differs, otherwise the
        upper cased display name, which works for the simple modes.
        """

        spec = self._profile.control("sound_mode")
        if spec is None:
            return
        await self._send(f"{spec.prefix}{self._resolve_sound_mode_wire(name)}")
        await self._refresh_current_sound_modes()

    def _resolve_sound_mode_wire(self, name: str) -> str:
        """Resolve the MS wire token for a sound mode display name.

        Order: the deterministic profile override, then the upper cased display
        name (which works for the simple modes).
        """

        override = self._profile.sound_mode_wire_overrides.get(name)
        if override:
            return override
        listed = self._profile.listed_sound_modes.get(name)
        if listed:
            return listed
        return name.upper()

    @property
    def fallback_sound_modes(self) -> list[str]:
        """The sound modes of a receiver that does not list them over telnet.

        Older receivers (AVR-X2000) answer neither OPSML nor OPSMLALL. Their
        Deviceinfo lists the modes as function names; those with a documented
        MS command are offered, in the profile's order.
        """

        features = self._discovery.features
        return [name for name in self._profile.listed_sound_modes if name in features]

    def fallback_sound_mode_name(self, wire: str) -> str | None:
        """The listed name of a reported mode token ("MCH STEREO" -> "MULTI CH STEREO")."""

        for name in self.fallback_sound_modes:
            if self._profile.listed_sound_modes[name] == wire:
                return name
        return None

    def _current_sound_modes_query(self) -> str | None:
        """The OPSML query token from the profile, or None if unavailable."""

        return self._profile.introspection_query("current_sound_modes")

    async def _query_current_sound_modes(self) -> None:
        """Send a single OPSML query to refresh the current-context mode list."""

        query = self._current_sound_modes_query()
        if query:
            await self._send(query)

    def _schedule_refresh(self, group_id: str) -> None:
        """Coalesce a refresh group's re-query into one delayed send.

        The receiver emits a burst of events for a single change (several signal
        events for one format change; several MS/SI lines for one mode/source
        switch); a short debounce collapses them into one re-query and lets the
        receiver settle on the new state before querying. A group is scheduled
        at most once until its re-query fires.
        """

        if group_id in self._refresh_scheduled:
            return
        group = self._refresh_groups.get(group_id)
        if not group:
            return
        self._refresh_scheduled.add(group_id)
        queries = list(group.get("queries", []))
        debounce = float(group.get("debounce", 1.0))

        async def _refresh() -> None:
            try:
                await asyncio.sleep(debounce)
                for query in queries:
                    await self._send(query)
            except asyncio.CancelledError:
                pass
            finally:
                self._refresh_scheduled.discard(group_id)

        self._create_task(_refresh())

    async def _refresh_current_sound_modes(self) -> None:
        """Re-query the current-context mode list after a mode/group change.

        The receiver pushes OPSMLALL (not OPSML) on a change, so the OPSML list
        must be pulled. A query sent immediately can race the receiver still
        switching; the OPSMLALL push handler (see _handle_line) re-queries once
        the switch settles, and a delayed query here is a further safety net.
        Both are best effort.
        """

        query = self._current_sound_modes_query()
        if not query:
            return
        await self._send(query)
        self._schedule_delayed_query(query, 0.8)

    def _create_task(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Schedule a fire and forget coroutine, keeping a strong reference.

        asyncio holds only a weak reference to bare tasks, so a task started
        without keeping its reference can be garbage collected before it runs.
        Tracking it here (and discarding on completion) keeps it alive and lets
        async_stop cancel anything still pending.
        """

        task = asyncio.ensure_future(coro)
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    def _schedule_delayed_query(self, query: str, delay: float) -> None:
        """Fire and forget a telnet query after a short delay."""

        async def _later() -> None:
            try:
                await asyncio.sleep(delay)
                await self._send(query)
            except asyncio.CancelledError:
                pass

        self._create_task(_later())

    async def async_select_quick_select(self, number: int, zone_id: str = "main") -> None:
        """Recall a quick select preset of a zone by its number (MSQUICK, Z2QUICK)."""

        spec = None if zone_id == "main" else self._profile.control(f"{zone_id}_quick_select")
        prefix = spec.prefix if spec is not None else self._quick_select_prefix
        await self._send(f"{prefix}{int(number)}")

    async def async_select_sound_mode_group(self, code: str) -> None:
        """Switch the receiver to a sound mode genre group by its code.

        Uses the genre command from the profile (MOVIE/MUSIC/GAME/PURE DIRECT).
        The receiver then auto resolves the actual mode for the current signal.
        """

        wire = self._profile.sound_mode_genre_commands.get(code)
        spec = self._profile.control("sound_mode")
        if wire and spec is not None:
            await self._send(f"{spec.prefix}{wire}")
            await self._refresh_current_sound_modes()

    async def async_set_control(self, control_id: str, value: object) -> None:
        """Set any profile driven feature control to a new value.

        The `value` interpretation depends on the control kind: a bool for
        on/off, a wire token for enum, a dB offset for level, an int (0 or
        below) for attenuation, and minutes (0 = off) for minutes.
        """

        spec = self._profile.control(control_id)
        if spec is None:
            return
        argument = self._encode_control_value(spec, value)
        if argument is None:
            return
        await self._send(f"{spec.prefix}{argument}")

    async def async_set_control_confirmed(self, control_id: str, value: object) -> None:
        """Set a control; raise SettingNotTaken when the receiver keeps the old value.

        Confirmed only where the receiver may refuse (avr/refusals.py): a
        main-zone setting in standby and the settings it takes only in a
        context. The echo is awaited for CONFIRM_WAIT, then the value is asked
        for once. Over HTTP (telnet down) nothing comes back to confirm by,
        and a value the receiver already has is only sent.
        """

        spec = self._profile.control(control_id)
        reason = None
        if spec is not None:
            reason = refusals.refusal_reason(
                control_id, spec.group, self._state.zone("main").power
            )
        if (
            spec is None
            or reason is None
            or not self._telnet.connected
            or command_test.same(self._state.values.get(control_id), value)
        ):
            await self.async_set_control(control_id, value)
            return
        argument = self._encode_control_value(spec, value)
        if argument is None:
            return
        wanted = _reply_or_error(spec.prefix)
        command = f"{spec.prefix}{argument}"
        await self.async_collect(wanted, lambda: self._send(command), CONFIRM_WAIT)
        if command_test.same(self._state.values.get(control_id), value):
            return
        if spec.query:
            query = spec.query
            await self.async_collect(wanted, lambda: self._send(query), CONFIRM_WAIT)
            if command_test.same(self._state.values.get(control_id), value):
                return
        raise SettingNotTaken(control_id, reason)

    async def async_set_channel_trim(self, code: str, db: float) -> None:
        """Set a per channel volume trim in dB via the CV command."""

        spec = self._profile.control("channel_level")
        if spec is None:
            return
        raw = self._profile.level_reference + db
        set_prefix = spec.get("set_prefix", "CV")
        await self._send(f"{set_prefix}{code} {encode_half_step(raw)}")

    async def async_set_channel_distance(self, code: str, meters: float) -> None:
        """Set a per channel speaker distance via the SSSDE command."""

        spec = self._profile.introspection.get("channel_distances", {})
        set_prefix = spec.get("set_prefix") or spec.get("prefix") or "SSSDE"
        divisor = self._profile.distance.get("divisor", 100) or 100
        raw = max(0, int(round(meters * divisor)))
        await self._send(f"{set_prefix}{code} {raw:04d}")

    async def async_set_crossover(self, group: str, hertz: int) -> None:
        """Set a speaker group's crossover frequency via the SSCFR command.

        The frequency is zero padded to the protocol width (e.g. 80 -> '080').
        The receiver only accepts values from its discrete grid; sending one off
        the grid is silently ignored, so callers pass a value from the profile's
        crossover set.
        """

        spec = self._profile.introspection.get("crossover", {})
        set_prefix = spec.get("set_prefix") or spec.get("prefix") or "SSCFR"
        width = int(self._profile.crossover.get("width", 3) or 3)
        await self._send(f"{set_prefix}{group} {int(hertz):0{width}d}")

    async def async_set_crossover_mode(self, mode: str) -> None:
        """Set the crossover speaker-selection mode ('IDV' per-group / 'ALL').

        Sent as '<prefix> <mode>' (e.g. 'SSCFR IDV'); this gates whether the
        per-group crossovers or the single All crossover applies on the receiver.
        """

        spec = self._profile.introspection.get("crossover", {})
        set_prefix = spec.get("set_prefix") or spec.get("prefix") or "SSCFR"
        await self._send(f"{set_prefix} {mode}")

    async def async_set_speaker_size(self, group: str, token: str) -> None:
        """Set a speaker group's size (Large/Small) via the SSSPC command."""

        spec = self._profile.introspection.get("speaker_config", {})
        set_prefix = spec.get("set_prefix") or spec.get("prefix") or "SSSPC"
        await self._send(f"{set_prefix}{group} {token}")

    # Encoding helpers -----------------------------------------------------

    def _encode_control_value(self, spec: ControlSpec, value: Any) -> str | None:
        """Encode a Python value into the wire argument for a control."""

        kind = spec.kind
        if kind == "onoff":
            return str(spec.get("on", "ON") if value else spec.get("off", "OFF"))
        if kind == "enum":
            return str(value)
        if kind == "level":
            raw = self._profile.level_reference + float(value)
            return encode_half_step(raw)
        if kind == "centered":
            # Fixed-width integer offset around a centre (picture controls).
            width = int(spec.get("width", 3))
            return encode_centered(float(value), int(spec.get("center", 0)), width)
        if kind == "attenuation":
            return f"{abs(int(value)):0{int(spec.get('width', 2))}d}"
        if kind == "integer":
            # Zero pad to the wire width the receiver expects (e.g. 3 for PSDELAY).
            width = int(spec.get("width", 0) or 0)
            return str(int(value)).zfill(width)
        if kind == "minutes":
            minutes = int(value)
            if minutes <= 0:
                return str(spec.get("off", "OFF"))
            return f"{minutes:03d}"
        return str(value)


def _reply_or_error(prefix: str) -> Callable[[str], bool]:
    """Match a reply to a query with this prefix, or the receiver's error answer."""

    def wanted(line: str) -> bool:
        return line.startswith(prefix) or line in ("?", "ERR", "ERROR")

    return wanted


def _answers_done() -> bool:
    """For the setup probe of the model-dependent controls: done at the first pause."""

    return True
