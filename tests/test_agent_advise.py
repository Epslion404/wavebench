import shlex
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from wavebench.cli import _scope_focus_request
from wavebench.cli_parser import build_parser
from wavebench.errors import ConfigError
from wavebench.services.agent_advise import (
    _command_text,
    scope_advise_from_observation,
    scope_advise_payload,
)


def _write_config(root: Path) -> Path:
    path = root / "wavebench.toml"
    path.write_text(
        """
[connection]
resource = "TCPIP::scope::INSTR"

[scope]
driver = "ds1104"
default_channel = 1
""",
        encoding="utf-8",
    )
    return path


class _FakeScopeService:
    def __init__(self, *, config, logger):
        self.config = config

    def idn(self):
        return "RIGOL TECHNOLOGIES,DS1104Z Plus,123,1.0"

    def status(self, channel):
        return _snapshot(channel)

    def require_high_impedance(self, channel, *, allow_50ohm=False):
        return "DC"


def _snapshot(channel: int):
    from wavebench.instruments.models import (
        ScopeAnalogChannelSnapshot,
        ScopeEdgeTriggerSnapshot,
        ScopeHealthSnapshot,
        ScopeIdentitySnapshot,
        ScopeProbeSnapshot,
        ScopeSnapshot,
        ScopeTimebaseSnapshot,
        ScopeWaveformMetadataSnapshot,
    )

    return ScopeSnapshot(
        identity=ScopeIdentitySnapshot("RIGOL", "DS1104Z", "123", "1.0", ()),
        health=ScopeHealthSnapshot(0, 0, 0, 1, 1, 1_000_000.0, False, False),
        channel=ScopeAnalogChannelSnapshot(
            channel, True, "DC", 8.0, 1.0, 0.0, 0.0, None, "NORM", 0.0, "", False, False, "SAMPLE"
        ),
        timebase=ScopeTimebaseSnapshot(0.001, 12, 0.0, 0.0012, 50.0, 0.0001, False),
        probe=ScopeProbeSnapshot(channel, 10.0, None, None, 1_000_000.0, "P10", "PASSIVE"),
        waveform=ScopeWaveformMetadataSnapshot(
            channel, -0.0005, 0.0005, 1000, 1, 1e-6, -0.0005, 0.001, 0.0, 8
        ),
        trigger=ScopeEdgeTriggerSnapshot("EDGE", channel, "AUTO", "POS", "DC", 0.0, "AUTO", "OFF", 1e-6),
    )


def _observation(*, fetch_waveform: bool, measured_frequency: dict[int, float] | None = None) -> dict:
    def channel_section(channel: int, frequency: float | None, warnings: list[str]) -> dict:
        section = {
            "channel": channel,
            "scope_status": {
                "status": "ok",
                "data": {"channel": {"enabled": True, "scale_v_per_div": 1.0}},
            },
            "coupling": {"status": "ok", "data": {"channel": channel, "coupling": "DC", "accepted_for_capture": True}},
        }
        if frequency is not None:
            section["waveform"] = {
                "status": "ok",
                "data": {
                    "summary": {
                        "frequency_estimate_hz": frequency,
                        "estimated_cycles": 2.4 if warnings else 120.0,
                        "points_per_cycle": 500.0,
                        "voltage_vpp_v": 1.0,
                        "quality_warnings": warnings,
                    }
                },
            }
        return section

    measured = measured_frequency or {}
    channels = [
        channel_section(
            1,
            measured.get(1),
            ["low_cycle_count: 2.4"] if 1 in measured and len(measured) == 1 else [],
        ),
        channel_section(2, measured.get(2), []),
    ]
    return {
        "status": "ok",
        "read_only": not fetch_waveform,
        "query_only": not fetch_waveform,
        "mutates_instrument": fetch_waveform,
        "raw_scpi": False,
        "instrument_state_effects": ["a running acquisition may be stopped"] if fetch_waveform else [],
        "observation": {"channel": 1, "channels": [1, 2], "fetch_waveform": fetch_waveform},
        "channels": channels,
        "relationships": [],
        "warnings": [],
        "agent_hints": [],
    }


def test_scope_advise_payload_is_read_only_and_uses_expected_frequencies():
    with TemporaryDirectory() as tmp:
        config = _write_config(Path(tmp))
        with patch("wavebench.services.agent_observe.ScopeService", _FakeScopeService):
            payload = scope_advise_payload(
                config_path=config,
                channels=(1, 2),
                expected_frequencies_hz={1: 1000.0, 2: 50000.0},
            )

    assert payload["read_only"] is True
    assert payload["query_only"] is True
    assert payload["mutates_instrument"] is False
    assert payload["applies_recommendations"] is False
    assert payload["instrument_state_effects"] == []
    focus = [item for item in payload["recommendations"] if item["id"] == "focus_channel"]
    assert [item["channel"] for item in focus] == [1, 2]
    assert focus[0]["parameters"]["time_range_s"] == pytest.approx(0.01)
    assert focus[0]["parameters"]["frequency_confidence"] == "configured"
    assert focus[1]["parameters"]["time_range_s"] == pytest.approx(0.0002)
    span = payload["recommendations"][-1]
    assert span["id"] == "separate_timebase_profiles"
    assert span["frequency_span"]["ratio_high_over_low"] == pytest.approx(50.0)


def test_scope_advise_prefers_expected_frequency_over_low_confidence_measurement():
    observation = _observation(fetch_waveform=True, measured_frequency={1: 1000.0})

    payload = scope_advise_from_observation(
        observation,
        expected_frequencies_hz={1: 2000.0},
    )

    focus = [item for item in payload["recommendations"] if item["id"] == "focus_channel"]
    assert focus[0]["parameters"]["time_range_s"] == pytest.approx(10.0 / 2000.0)
    assert (
        focus[0]["parameters"]["frequency_confidence"] == "configured"
    )
    assert focus[0]["parameters"]["time_range_s"] is not None


def test_scope_advise_withholds_timebase_advice_when_measurement_is_low_confidence():
    observation = _observation(fetch_waveform=True, measured_frequency={1: 1000.0})

    payload = scope_advise_from_observation(observation)

    # 低置信度测量频率没有期望频率可回退时，不给基于该频率的时基建议
    withheld = [item for item in payload["recommendations"] if item["id"] == "timebase_advice_withheld"]
    assert len(withheld) == 1
    assert withheld[0]["channel"] == 1
    assert "low confidence" in withheld[0]["reason"]
    # 只允许保留与频率无关的垂直档位建议，时基建议必须为空
    focus = [
        item
        for item in payload["recommendations"]
        if item["id"] == "focus_channel" and item["channel"] == 1
    ]
    assert [item["parameters"]["time_range_s"] for item in focus] == [None]
    assert any("timebase advice withheld" in hint for hint in payload["agent_hints"])


def test_scope_advise_uses_trusted_measured_frequency_when_confidence_is_good():
    observation = _observation(fetch_waveform=True, measured_frequency={1: 1000.0, 2: 50000.0})

    payload = scope_advise_from_observation(observation)

    focus = [item for item in payload["recommendations"] if item["id"] == "focus_channel"]
    assert focus[0]["parameters"]["frequency_confidence"] == "measured"
    assert focus[0]["parameters"]["time_range_s"] == pytest.approx(0.01)


@pytest.mark.parametrize("value", [0.0, -1.0, float("nan"), float("inf")])
def test_scope_advise_rejects_invalid_targets(value):
    with pytest.raises(ConfigError, match="target_cycles"):
        scope_advise_payload(config_path="wavebench.toml", target_cycles=value)


@pytest.mark.parametrize("value", [0.0, -1.0, float("nan"), float("inf")])
def test_scope_advise_rejects_invalid_expected_frequencies(value):
    with pytest.raises(ConfigError, match="expected frequency"):
        scope_advise_from_observation(_observation(fetch_waveform=False), expected_frequencies_hz={1: value})


@pytest.mark.parametrize("fetch_waveform", [False, True])
def test_advice_commands_parse_with_real_cli(fetch_waveform):
    observation = _observation(
        fetch_waveform=fetch_waveform,
        measured_frequency={1: 1000.0, 2: 50000.0} if fetch_waveform else None,
    )
    observation["channels"][0]["scope_status"]["data"]["channel"]["enabled"] = False
    payload = scope_advise_from_observation(
        observation, expected_frequencies_hz={1: 1000.0, 2: 50000.0},
    )
    parser = build_parser()
    commands = [item for item in payload["recommendations"] if "command" in item]
    assert len(commands) == 3
    for recommendation in commands:
        args = parser.parse_args(shlex.split(recommendation["command"])[1:])
        parameters = recommendation["parameters"]
        if recommendation["action"] == "scope.display":
            assert args.channel == parameters["channel"]
            assert args.state == "on"
        else:
            request = _scope_focus_request(args)
            assert request.channels == (parameters["channel"],)
            assert request.time_range_s == pytest.approx(parameters["time_range_s"])
            assert len(request.vertical_scales) == 1
            assert request.vertical_scales[0].channel == parameters["channel"]
            assert request.vertical_scales[0].scale_v_per_div == pytest.approx(
                parameters["vertical_scale_v_per_div"]
            )
            assert request.hide_others is False


@pytest.mark.parametrize("hide_others", [False, True])
def test_focus_command_hide_others_uses_real_cli_flag(hide_others):
    command = _command_text("focus", {
        "channel": 2,
        "vertical_scale_v_per_div": 0.25,
        "hide_other_channels": hide_others,
    })
    args = build_parser().parse_args(shlex.split(command)[1:])
    request = _scope_focus_request(args)

    assert request.channels == (2,)
    assert request.vertical_scales[0].scale_v_per_div == 0.25
    assert request.hide_others is hide_others
