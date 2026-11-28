"""Tests for avr/video_config.py."""

from __future__ import annotations

import pytest

from custom_components.denonavr_plus.avr.errors import SetupWriteError
from custom_components.denonavr_plus.avr.profile import load_profile
from custom_components.denonavr_plus.avr.video_config import VideoConfigController

from .setup_api import FakeSetupApi
from .test_avr_graphic_eq import FakeWebConfig


def _controller(webconfig: FakeWebConfig) -> VideoConfigController:
    grammar = load_profile().grammar["video_web"]
    return VideoConfigController(webconfig, grammar, lambda: None)


async def test_rejected_write_raises() -> None:
    webconfig = FakeWebConfig(accept=False)
    video = _controller(webconfig)
    control = video.controls()[0]

    with pytest.raises(SetupWriteError):
        await video.async_set(control["id"], control["on"])

    assert len(webconfig.writes) == 1


async def test_accepted_write_does_not_raise() -> None:
    webconfig = FakeWebConfig(accept=True)
    video = _controller(webconfig)
    control = video.controls()[0]

    await video.async_set(control["id"], control["on"])

    assert len(webconfig.writes) == 1


# The whole read path against a fake setup API ---------------------------------


def _speakers(api: FakeSetupApi) -> VideoConfigController:
    grammar = load_profile().grammar["speakers_web"]
    return VideoConfigController(api, grammar, lambda: None)  # type: ignore[arg-type]


def _video(
    api: FakeSetupApi, updates: list[int] | None = None
) -> VideoConfigController:
    grammar = load_profile().grammar["video_web"]
    sink = updates if updates is not None else []
    return VideoConfigController(api, grammar, lambda: sink.append(1))  # type: ignore[arg-type]


async def test_discover_reads_only_the_advertised_menus() -> None:
    api = FakeSetupApi()
    video = _video(api)

    await video.discover()

    # TV format is hidden (1) in the availability map, so it is never fetched.
    assert [(section, type_id) for section, type_id, _ in api.reads] == [
        ("video", 1),
        ("video", 3),
        ("video", 7),
        ("video", 8),
    ]
    assert video.supported


async def test_plain_fields_report_value_and_display() -> None:
    video = _video(FakeSetupApi())
    await video.discover()

    assert video.value("hdmi_control") == "1"
    assert video.present("hdmi_control") and video.available("hdmi_control")
    # Grayed out: present but not settable.
    assert video.present("arc") and not video.available("arc")
    # Hidden: not present at all.
    assert not video.present("osd_info")
    # Absent from the document.
    assert video.value("smart_menu") is None
    assert not video.present("smart_menu")
    assert video.options("hdmi_power_off_control") == {
        "1": "All",
        "2": "Video",
        "3": "Off",
    }


async def test_pass_through_source_offers_the_receivers_list() -> None:
    video = _video(FakeSetupApi())
    await video.discover()

    assert video.value("pass_through_source") == "3"
    # Tags without a wire value in the profile are left out.
    assert video.options("pass_through_source") == {
        "1": "Last",
        "2": "Blu-ray",
        "3": "Apple TV",
    }


async def test_a_single_value_menu_takes_the_root_text() -> None:
    api = FakeSetupApi()
    api.documents[("video", 1)] = api.documents[("video", 1)].replace(
        "<TVFormat>1</TVFormat>", "<TVFormat>3</TVFormat>"
    )
    video = _video(api)
    await video.discover()

    assert video.value("tv_format") == "2"
    assert video.available("tv_format")


async def test_feature_flags_name_the_present_menus() -> None:
    api = FakeSetupApi()
    api.documents[("video", 1)] = (
        "<listSetupMenu><PictureAdjust>2</PictureAdjust></listSetupMenu>"
    )
    video = _video(api)
    await video.discover()

    assert video.feature_flags() == {"PictureAdjust"}


async def test_unreadable_documents_leave_nothing_behind() -> None:
    api = FakeSetupApi(documents={("video", 1): "<not xml", ("video", 3): ""})
    video = _video(api)

    await video.discover()

    assert video.value("hdmi_control") is None
    assert video.feature_flags() == set()


async def test_refresh_notifies_only_on_a_change() -> None:
    api = FakeSetupApi()
    updates: list[int] = []
    video = _video(api, updates)
    await video.discover()

    await video.refresh()
    assert updates == []

    api.documents[("video", 3)] = api.documents[("video", 3)].replace(
        '<HDMIControl display="3">1', '<HDMIControl display="3">2'
    )
    await video.refresh()
    assert updates == [1]
    assert video.value("hdmi_control") == "2"


async def test_without_a_grammar_nothing_is_read_or_written() -> None:
    api = FakeSetupApi()
    video = VideoConfigController(api, {}, lambda: None)  # type: ignore[arg-type]

    await video.discover()
    await video.refresh()
    await video.async_set("hdmi_control", "1")

    assert not video.supported
    assert api.reads == [] and api.writes == []


async def test_writes_send_the_field_alone_and_reread() -> None:
    api = FakeSetupApi()
    video = _video(api)
    await video.discover()
    api.reads.clear()

    await video.async_set("hdmi_control", "2")
    await video.async_set("pass_through_source", "2")
    await video.async_set("unknown", "1")

    assert api.writes == [
        ("video", 3, "<HDMIControl>2</HDMIControl>"),
        ("video", 3, "<PassThroughSource><Source>2</Source></PassThroughSource>"),
    ]
    assert ("video", 3, None) in api.reads


async def test_amp_assign_options_follow_the_amp_type() -> None:
    api = FakeSetupApi()
    speakers = _speakers(api)
    await speakers.discover()

    # The availability map first; without an answer the menu is read anyway.
    assert [(section, type_id) for section, type_id, _ in api.reads] == [
        ("speakers", 1),
        ("speakers", 2),
    ]
    assert speakers.value("amp_assign") == "2"
    options = speakers.options("amp_assign")
    assert options["2"] == "7.1ch + ZONE2"
    assert len(set(options.values())) == len(options)


async def test_value_list_fields_offer_the_allowed_ids() -> None:
    speakers = _speakers(FakeSetupApi())
    await speakers.discover()

    assert speakers.value("floor_layout") == "2"
    assert speakers.options("floor_layout") == {"1": "5ch", "2": "5ch & SB"}
    # A value without its <List>: nothing to offer.
    assert speakers.options("height_speaker") == {}


async def test_speaker_writes_are_wrapped_in_the_menu_root() -> None:
    api = FakeSetupApi()
    speakers = _speakers(api)
    await speakers.discover()

    await speakers.async_set("amp_assign", "1")

    assert api.writes == [
        ("speakers", 2, "<AmpAssign><AssignMode>1</AssignMode></AmpAssign>")
    ]


async def test_what_was_read_comes_back_from_the_snapshot_without_asking() -> None:
    import json

    read = _video(FakeSetupApi())
    await read.discover()
    snapshot = json.loads(json.dumps(read.snapshot()))  # as the store keeps it

    api = FakeSetupApi()
    restored = _video(api)
    restored.restore(snapshot)
    assert api.reads == []
    assert restored.layout() == read.layout()
    assert restored.feature_flags() == read.feature_flags()
    assert restored.layout()  # some menus are present
    restored.restore(None)  # an older snapshot without it changes nothing
    assert restored.layout() == read.layout()


async def test_a_setup_interface_that_does_not_answer_keeps_what_was_read() -> None:
    """Gone for a moment after a start from the snapshot: the menus stay."""
    api = FakeSetupApi()
    video = _video(api)
    await video.discover()
    before = video.layout()
    api.documents = {}
    await video.discover()
    await video.refresh()
    assert video.layout() == before


# Per-item menus of the Web Control generation (recordings of an AVC-X3900H) ---


async def test_the_recorded_avc_x3900h_lists_its_inputs_and_speaker_positions() -> None:
    from .setup_api import recorded

    api = FakeSetupApi(recorded("AVC-X3900H"))
    video, speakers = _video(api), _speakers(api)
    await video.discover()
    await speakers.discover()
    formats = video.collection("input_signal_format")
    assert formats["1"] == {"name": "AAP", "value": "2", "display": 3, "options": ["1", "2", "4"]}
    # TV Audio is grayed: listed, not settable.
    hdcp = video.collection("input_hdcp")
    assert hdcp["6"]["name"] == "TV Audio" and not video.item_settable("input_hdcp", "6")
    assert video.item_settable("input_hdcp", "1")
    # Speaker layout: item 1, with the modes this receiver allows.
    layout = speakers.collection("speaker_layout")
    assert layout == {
        "1": {"name": "", "value": "6", "display": 3, "options": ["18", "1", "2", "7", "14", "6", "17", "19"]}
    }
    # Connection only where the receiver reports one; a position is as settable as its <Conn>.
    assert {slot: item["value"] for slot, item in speakers.collection("speaker_connection").items()} == {
        "2": "1", "3": "1", "4": "1", "5": "0",
    }
    assert speakers.collection("speaker_layout_config")["2"]["display"] == 2
    # Entities: every listed input, the named positions, the layout; no positions' config.
    assert "input_hdcp:6" in video.layout()
    assert sorted(item for item in speakers.layout() if ":" in item) == [
        "speaker_connection:2", "speaker_connection:3", "speaker_connection:4",
        "speaker_connection:5", "speaker_layout:1",
    ]
    # Amp Assign is hidden on this receiver: not read, not offered.
    assert ("speakers", 2) not in [(section, type_id) for section, type_id, _ in api.reads]


async def test_a_receiver_without_the_menus_has_no_items() -> None:
    from .setup_api import recorded

    api = FakeSetupApi(recorded("AVR-X3600H"))
    video = _video(api)
    await video.discover()
    assert video.collection("input_signal_format") == {}
    assert video.entity_slots("input_signal_format") == []


@pytest.mark.assumed(
    "the write shape <Format index=\"1\">2</Format> is the receiver's own page's, as "
    "LaserGuruGuy read it; a write on an AVC-X3800H/X3900H would settle it"
)
async def test_an_input_setting_is_written_by_its_index_and_read_again() -> None:
    from .setup_api import recorded

    api = FakeSetupApi(recorded("AVC-X3900H"))
    updates: list[int] = []
    grammar = {**load_profile().grammar["video_web"], "write_settle_seconds": 0}
    video = VideoConfigController(api, grammar, lambda: updates.append(1))  # type: ignore[arg-type]
    await video.discover()
    await video.async_set_item("input_signal_format", "1", "4")
    assert api.writes == [("video", 14, '<Format index="1">4</Format>')]
    assert api.reads[-1][:2] == ("video", 14) and updates == [1]
    # Unknown items are not written; a refused write raises.
    await video.async_set_item("input_signal_format", "99", "4")
    assert len(api.writes) == 1
    api.accept = False
    with pytest.raises(SetupWriteError):
        await video.async_set_item("input_hdcp", "1", "2", read=False)


async def test_the_items_come_back_from_the_snapshot() -> None:
    from .setup_api import recorded

    api = FakeSetupApi(recorded("AVC-X3900H"))
    video = _video(api)
    await video.discover()
    again = _video(FakeSetupApi({}))
    again.restore(video.snapshot())
    assert again.collection("input_hdcp") == video.collection("input_hdcp")
    assert again.layout() == video.layout()
