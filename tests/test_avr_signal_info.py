"""Live video and channel information from the setup interface."""

from __future__ import annotations

import pytest

from custom_components.denonavr_plus.avr import signal_info

VIDEO = """<?xml version="1.0" encoding="utf-8"?>
<Video>
  <Resolution><Input>3840x2160p 60Hz</Input><Output>3840x2160p 60Hz</Output></Resolution>
  <HDR><Input>HDR10</Input><Output>HDR10</Output></HDR>
  <ColorSpace><Input>YCbCr 4:2:2</Input><Output>YCbCr 4:2:2</Output></ColorSpace>
  <PixelDepth><Input>12bit</Input><Output>12bit</Output></PixelDepth>
  <FRLRate><Input>---</Input><Output>---</Output></FRLRate>
</Video>"""

AUDIO = """<Audio>
  <InputSignal><List>
    <Speaker index="0">2</Speaker><Speaker index="1">2</Speaker><Speaker index="2">2</Speaker>
    <Speaker index="3">1</Speaker><Speaker index="99">2</Speaker><Speaker index="x">2</Speaker>
  </List></InputSignal>
  <ActiveSpeaker><List>
    <Speaker index="0">2</Speaker><Speaker index="1">2</Speaker><Speaker index="3">2</Speaker>
    <Speaker index="16">2</Speaker><Speaker index="17">0</Speaker>
  </List></ActiveSpeaker>
</Audio>"""


@pytest.mark.assumed(
    "the /ajax/info answers follow TheFab21/ha-denon-marantz-avr (webapi.py); "
    "an AVR-X2600H answer to get_config?type=3 and type=2 would settle them"
)
def test_video_and_channels() -> None:
    video = signal_info.parse_video(VIDEO)
    assert video == {
        "video_input_resolution": "3840x2160p 60Hz",
        "video_output_resolution": "3840x2160p 60Hz",
        "video_input_hdr": "HDR10",
        "video_output_hdr": "HDR10",
        "video_input_color_space": "YCbCr 4:2:2",
        "video_output_color_space": "YCbCr 4:2:2",
        "video_input_pixel_depth": "12bit",
        "video_output_pixel_depth": "12bit",
        # "---" means no value.
        "video_input_frl": None,
    }
    assert signal_info.parse_audio(AUDIO) == {
        "audio_input_channels": "FL FR C",
        "audio_active_speakers": "FL FR SW TFL",
    }


def test_other_answers_are_no_information() -> None:
    for text in (None, "", "<Video", "<Audio/>"):
        assert signal_info.parse_video(text) is None
    assert signal_info.parse_audio("<Video/>") is None
    assert signal_info.parse_video("<Video/>") == dict.fromkeys(signal_info.VIDEO_FIELDS)
    assert signal_info.parse_audio("<Audio/>") == dict.fromkeys(signal_info.AUDIO_FIELDS)
