from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pytest

from wavebench.errors import ConfigError
from wavebench.instruments.models import (
    ScopeAnalogChannelSnapshot,
    ScopeEdgeTriggerSnapshot,
    ScopeHealthSnapshot,
    ScopeIdentitySnapshot,
    ScopeProbeSnapshot,
    ScopeSnapshot,
    ScopeTimebaseSnapshot,
    ScopeWaveformMetadataSnapshot,
    WaveformData,
    WaveformHeader,
)
from wavebench.services.agent_observe import (
    scope_observe_payload,
    scope_waveform_report_payload,
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

[waveform]
points = "def"
""",
        encoding="utf-8",
    )
    return path


def _snapshot(channel: int) -> ScopeSnapshot:
    return ScopeSnapshot(
        identity=ScopeIdentitySnapshot("RIGOL", "DS1104Z", "123", "1.0", ()),
        health=ScopeHealthSnapshot(0, 0, 0, 1, 1, 1_000_000.0, False, False),
        channel=ScopeAnalogChannelSnapshot(
            channel,
            True,
            "DC",
            8.0,
            1.0,
            0.0,
            0.0,
            None,
            "NORM",
            0.0,
            "",
            False,
            False,
            "SAMPLE",
        ),
        timebase=ScopeTimebaseSnapshot(0.001, 12, 0.0, 0.0012, 50.0, 0.0001, False),
        probe=ScopeProbeSnapshot(channel, 10.0, None, None, 1_000_000.0, "P10", "PASSIVE"),
        waveform=ScopeWaveformMetadataSnapshot(
            channel,
            -0.0005,
            0.0005,
            1000,
            1,
            1e-6,
            -0.0005,
            0.001,
            0.0,
            8,
        ),
        trigger=ScopeEdgeTriggerSnapshot("EDGE", channel, "AUTO", "POS", "DC", 0.0, "AUTO", "OFF", 1e-6),
    )


class _FakeScopeService:
    """记录是否真的读取过波形，用于断言只读路径不碰仪器写路径。"""

    instances: list["_FakeScopeService"] = []

    def __init__(self, *, config, logger):
        self.config = config
        self.fetched_channels: list[int] = []
        self.allow_50ohm_seen: list[bool] = []
        _FakeScopeService.instances.append(self)

    def idn(self):
        return "RIGOL TECHNOLOGIES,DS1104Z Plus,123,1.0"

    def status(self, channel):
        return _snapshot(channel)

    def require_high_impedance(self, channel, *, allow_50ohm=False):
        self.allow_50ohm_seen.append(allow_50ohm)
        return "DC"

    def fetch_waveform(self, channel):
        self.fetched_channels.append(channel)
        times = np.linspace(0.0, 0.005, 2000)
        return WaveformData(
            channel=channel,
            header=WaveformHeader(x_start=0.0, x_stop=0.005, points=2000),
            voltages_v=np.sin(2 * np.pi * 1000 * times),
        )


@pytest.fixture(autouse=True)
def _reset_instances():
    _FakeScopeService.instances = []
    yield
    _FakeScopeService.instances = []


def test_scope_observe_payload_is_strictly_read_only():
    with TemporaryDirectory() as tmp:
        config = _write_config(Path(tmp))
        with patch("wavebench.services.agent_observe.ScopeService", _FakeScopeService):
            payload = scope_observe_payload(config_path=config, channel=2)

    assert payload["status"] == "ok"
    assert payload["read_only"] is True
    assert payload["query_only"] is True
    assert payload["mutates_instrument"] is False
    assert payload["raw_scpi"] is False
    assert payload["instrument_state_effects"] == []
    assert payload["observation"]["channel"] == 2
    assert payload["observation"]["channels"] == [2]
    assert payload["identity"]["data"]["idn"].startswith("RIGOL")
    assert payload["scope_status"]["data"]["channel"]["channel"] == 2
    assert payload["coupling"]["data"]["accepted_for_capture"] is True
    # 只读路径不得读取波形，也不得暴露波形/关系字段
    assert "waveform" not in payload["channels"][0]
    assert "relationships" not in payload
    assert "expectations" not in payload
    assert _FakeScopeService.instances[0].fetched_channels == []
    assert any("read-only observation" in hint for hint in payload["agent_hints"])


def test_scope_observe_payload_supports_multiple_channels():
    with TemporaryDirectory() as tmp:
        config = _write_config(Path(tmp))
        with patch("wavebench.services.agent_observe.ScopeService", _FakeScopeService):
            payload = scope_observe_payload(config_path=config, channels=(1, 2))

    assert payload["observation"]["channel"] == 1
    assert payload["observation"]["channels"] == [1, 2]
    assert [item["channel"] for item in payload["channels"]] == [1, 2]


def test_scope_observe_payload_passes_allow_50ohm_to_the_safety_check():
    with TemporaryDirectory() as tmp:
        config = _write_config(Path(tmp))
        with patch("wavebench.services.agent_observe.ScopeService", _FakeScopeService):
            scope_observe_payload(config_path=config, channel=1, allow_50ohm=True)

    assert _FakeScopeService.instances[0].allow_50ohm_seen == [True]


def test_scope_waveform_report_is_an_explicit_write_path():
    with TemporaryDirectory() as tmp:
        config = _write_config(Path(tmp))
        with patch("wavebench.services.agent_observe.ScopeService", _FakeScopeService):
            payload = scope_waveform_report_payload(config_path=config, channel=1)

    assert payload["read_only"] is False
    assert payload["query_only"] is False
    assert payload["mutates_instrument"] is True
    assert payload["instrument_state_effects"]
    assert any("acquisition may be stopped" in item for item in payload["instrument_state_effects"])
    assert payload["waveform_source"]["same_acquisition"] is False
    assert payload["channels"][0]["waveform"]["data"]["summary"]["samples"] == 2000
    assert _FakeScopeService.instances[0].fetched_channels == [1]


def test_scope_waveform_report_marks_multi_channel_timing_analysis_as_skipped():
    with TemporaryDirectory() as tmp:
        config = _write_config(Path(tmp))
        with patch("wavebench.services.agent_observe.ScopeService", _FakeScopeService):
            payload = scope_waveform_report_payload(config_path=config, channels=(1, 2))

    assert payload["waveform_source"]["same_acquisition"] is False
    relationship = payload["relationships"][0]
    assert relationship["channels"] == [1, 2]
    assert relationship["correlation"] == {"status": "skipped", "reason": "not_same_acquisition"}
    assert relationship["phase_degrees_at_left_frequency"] is None
    assert any("not from one acquisition" in hint for hint in payload["agent_hints"])


def test_scope_waveform_report_evaluates_channel_expectations():
    with TemporaryDirectory() as tmp:
        config = _write_config(Path(tmp))
        with patch("wavebench.services.agent_observe.ScopeService", _FakeScopeService):
            payload = scope_waveform_report_payload(
                config_path=config,
                channel=1,
                expectations={1: {"frequency_hz": 1000.0, "frequency_tolerance_ratio": 0.05}},
            )

    assert payload["expectations"]["status"] == "pass"
    assert payload["channels"][0]["expectation"]["data"]["checks"][0]["metric"] == "frequency_hz"


def test_scope_waveform_report_rejects_invalid_expectation_before_any_instrument_io():
    with TemporaryDirectory() as tmp:
        config = _write_config(Path(tmp))
        with patch("wavebench.services.agent_observe.ScopeService", _FakeScopeService):
            with pytest.raises(ConfigError, match="unknown expectation field"):
                scope_waveform_report_payload(
                    config_path=config,
                    channel=1,
                    expectations={1: {"frequncy_hz": 1000}},
                )

    # 校验失败时连 service 都不应创建，更不会产生仪器写入
    assert _FakeScopeService.instances == []


def test_scope_waveform_report_rejects_expectation_for_unobserved_channel():
    with TemporaryDirectory() as tmp:
        config = _write_config(Path(tmp))
        with patch("wavebench.services.agent_observe.ScopeService", _FakeScopeService):
            with pytest.raises(ConfigError, match="expectation channels must be observed channels"):
                scope_waveform_report_payload(
                    config_path=config,
                    channel=1,
                    expectations={2: {"frequency_hz": 1000.0}},
                )


def test_scope_observe_rejects_invalid_channel():
    with TemporaryDirectory() as tmp:
        config = _write_config(Path(tmp))
        with pytest.raises(ConfigError, match="positive integer"):
            scope_observe_payload(config_path=config, channel=0)


def test_scope_observe_rejects_ambiguous_channel_arguments():
    with TemporaryDirectory() as tmp:
        config = _write_config(Path(tmp))
        with pytest.raises(ConfigError, match="either channel or channels"):
            scope_observe_payload(config_path=config, channel=1, channels=(2,))
