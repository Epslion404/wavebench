from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np

from wavebench.errors import DataError


ANALYSIS_TIME_METRICS = frozenset({
    "voltage_min_v",
    "voltage_max_v",
    "voltage_mean_v",
    "voltage_rms_v",
    "voltage_vpp_v",
})
ANALYSIS_FREQUENCY_METRICS = frozenset({
    "peak_frequency_hz",
    "peak_amplitude_v",
    "noise_floor_v",
    "thd_ratio",
    *(
        f"harmonic_{order}_{field}"
        for order in range(2, 6)
        for field in ("frequency_hz", "amplitude_v")
    ),
})
ANALYSIS_FIR_RESPONSES = frozenset({"lowpass", "highpass", "bandpass", "bandstop"})
ANALYSIS_FIR_MODES = frozenset({"causal", "zero_phase"})
SIGNIFICANT_PEAK_V = 1e-12


@dataclass(frozen=True)
class TimeSignal:
    time_s: np.ndarray
    voltage_v: np.ndarray
    coherent_gain: float = 1.0
    window_name: str | None = None

    def as_array(self) -> np.ndarray:
        return np.column_stack((self.time_s, self.voltage_v))


@dataclass(frozen=True)
class FrequencySignal:
    frequency_hz: np.ndarray
    spectrum_v: np.ndarray
    samples: int
    sample_interval_s: float
    coherent_gain: float
    window_name: str | None

    @property
    def amplitude_v(self) -> np.ndarray:
        return np.abs(self.spectrum_v)

    @property
    def sample_rate_hz(self) -> float:
        return 1.0 / self.sample_interval_s

    @property
    def resolution_hz(self) -> float:
        if self.frequency_hz.size < 2:
            return 0.0
        return float(self.frequency_hz[1] - self.frequency_hz[0])

    def as_array(self) -> np.ndarray:
        return np.column_stack(
            (
                self.frequency_hz,
                self.spectrum_v.real,
                self.spectrum_v.imag,
                self.amplitude_v,
            )
        )


@dataclass(frozen=True)
class FirFilterResult:
    signal: TimeSignal
    taps: np.ndarray
    sample_interval_s: float
    scipy_version: str

    @property
    def sample_rate_hz(self) -> float:
        return 1.0 / self.sample_interval_s


def validate_waveform(data: Any) -> TimeSignal:
    array = np.asarray(data)
    if array.ndim != 2 or array.shape[1:] != (2,) or array.shape[0] < 1:
        raise DataError("analysis pipeline input must be a non-empty Nx2 waveform array")
    if not np.issubdtype(array.dtype, np.number) or np.issubdtype(
        array.dtype, np.complexfloating
    ):
        raise DataError("analysis pipeline input must contain real numeric values")
    array = np.array(array, dtype=np.float64, copy=True)
    if not np.all(np.isfinite(array)):
        raise DataError("analysis pipeline input must contain only finite values")
    if array.shape[0] > 1 and not np.all(np.diff(array[:, 0]) > 0):
        raise DataError("analysis pipeline time axis must be strictly increasing")
    return TimeSignal(time_s=array[:, 0], voltage_v=array[:, 1])


def remove_dc(signal: TimeSignal) -> TimeSignal:
    voltage = signal.voltage_v - float(np.mean(signal.voltage_v))
    return _replace_voltage(signal, voltage)


def detrend_linear(signal: TimeSignal) -> TimeSignal:
    if signal.time_s.size < 2:
        raise DataError("linear detrend requires at least two samples")
    centered_time = signal.time_s - float(np.mean(signal.time_s))
    centered_voltage = signal.voltage_v - float(np.mean(signal.voltage_v))
    denominator = float(np.dot(centered_time, centered_time))
    if not np.isfinite(denominator) or denominator <= 0:
        raise DataError("linear detrend requires a usable time axis")
    slope = float(np.dot(centered_time, centered_voltage) / denominator)
    trend = float(np.mean(signal.voltage_v)) + slope * centered_time
    voltage = signal.voltage_v - trend
    return _replace_voltage(signal, voltage)


def window_signal(signal: TimeSignal, name: str) -> TimeSignal:
    windows = {
        "hann": np.hanning,
        "hamming": np.hamming,
        "blackman": np.blackman,
    }
    factory = windows.get(name)
    if factory is None:
        raise DataError("analysis window must be one of hann, hamming, blackman")
    window = factory(signal.voltage_v.size)
    gain = float(np.mean(window))
    coherent_gain = signal.coherent_gain * gain
    if not np.isfinite(coherent_gain) or coherent_gain <= 0:
        raise DataError("analysis window has an invalid coherent gain")
    voltage = signal.voltage_v * window
    result = _replace_voltage(signal, voltage)
    return TimeSignal(
        time_s=result.time_s,
        voltage_v=result.voltage_v,
        coherent_gain=coherent_gain,
        window_name=name,
    )


def filter_fir(
    signal: TimeSignal,
    *,
    response: str,
    cutoff_hz: float | Sequence[float],
    numtaps: int,
    mode: str,
) -> FirFilterResult:
    if response not in ANALYSIS_FIR_RESPONSES:
        raise DataError("analysis FIR response must be lowpass, highpass, bandpass, or bandstop")
    if mode not in ANALYSIS_FIR_MODES:
        raise DataError("analysis FIR mode must be causal or zero_phase")
    if isinstance(numtaps, bool) or not isinstance(numtaps, int) or numtaps < 3 or numtaps % 2 == 0:
        raise DataError("analysis FIR numtaps must be an odd integer >= 3")

    cutoff = _fir_cutoff(response, cutoff_hz)
    sample_interval = _uniform_sample_interval(signal, "analysis FIR filter")
    sample_rate = 1.0 / sample_interval
    nyquist = sample_rate / 2.0
    cutoff_values = [cutoff] if isinstance(cutoff, float) else cutoff
    if any(value >= nyquist for value in cutoff_values):
        raise DataError(
            f"analysis FIR cutoff_hz must be below Nyquist frequency {nyquist:.17g} Hz"
        )

    if mode == "zero_phase":
        minimum_samples = 3 * numtaps + 1
        if signal.voltage_v.size < minimum_samples:
            raise DataError(
                "analysis zero-phase FIR requires at least "
                f"{minimum_samples} samples for numtaps={numtaps}"
            )

    try:
        from scipy import __version__ as scipy_version
        from scipy.signal import filtfilt, firwin, lfilter
    except ImportError as exc:  # pragma: no cover - RunService checks this before execution
        raise DataError(
            "analysis FIR filter requires SciPy; install WaveBench with `.[analysis]`"
        ) from exc

    try:
        taps = np.asarray(
            firwin(
                numtaps,
                cutoff,
                window="hamming",
                pass_zero=response,
                scale=True,
                fs=sample_rate,
            ),
            dtype=np.float64,
        )
        if mode == "causal":
            voltage = lfilter(taps, [1.0], signal.voltage_v, axis=-1)
        else:
            voltage = filtfilt(
                taps,
                [1.0],
                signal.voltage_v,
                axis=-1,
                padtype="odd",
                padlen=3 * numtaps,
                method="pad",
            )
    except ValueError as exc:
        raise DataError(f"analysis FIR filter failed: {exc}") from exc

    return FirFilterResult(
        signal=_replace_voltage(signal, np.asarray(voltage, dtype=np.float64)),
        taps=taps,
        sample_interval_s=sample_interval,
        scipy_version=scipy_version,
    )


def fft_signal(signal: TimeSignal) -> FrequencySignal:
    samples = int(signal.voltage_v.size)
    if samples < 4:
        raise DataError("analysis FFT requires at least four samples")
    sample_interval = _uniform_sample_interval(signal, "analysis FFT")
    if not np.isfinite(signal.coherent_gain) or signal.coherent_gain <= 0:
        raise DataError("analysis FFT requires a positive coherent gain")

    spectrum = np.fft.rfft(signal.voltage_v) / (samples * signal.coherent_gain)
    if samples % 2 == 0:
        spectrum[1:-1] *= 2.0
    else:
        spectrum[1:] *= 2.0
    frequencies = np.fft.rfftfreq(samples, d=sample_interval)
    if not np.all(np.isfinite(spectrum)):
        raise DataError("analysis FFT produced non-finite values")
    return FrequencySignal(
        frequency_hz=frequencies,
        spectrum_v=spectrum,
        samples=samples,
        sample_interval_s=sample_interval,
        coherent_gain=signal.coherent_gain,
        window_name=signal.window_name,
    )


def measure_time(signal: TimeSignal, metrics: Iterable[str]) -> dict[str, float]:
    selected = _selected_metrics(metrics, ANALYSIS_TIME_METRICS, "time")
    voltage = signal.voltage_v
    minimum = float(np.min(voltage))
    maximum = float(np.max(voltage))
    scale = float(np.max(np.abs(voltage)))
    rms = 0.0 if scale == 0 else float(scale * np.sqrt(np.mean((voltage / scale) ** 2)))
    values = {
        "voltage_min_v": minimum,
        "voltage_max_v": maximum,
        "voltage_mean_v": float(np.mean(voltage)),
        "voltage_rms_v": rms,
        "voltage_vpp_v": maximum - minimum,
    }
    return {metric: _finite_metric(values[metric], metric) for metric in selected}


def measure_frequency(
    signal: FrequencySignal, metrics: Iterable[str]
) -> tuple[dict[str, float | None], list[str]]:
    selected = _selected_metrics(metrics, ANALYSIS_FREQUENCY_METRICS, "frequency")
    amplitudes = signal.amplitude_v
    non_dc = amplitudes[1:]
    peak_index = int(np.argmax(non_dc) + 1)
    peak_amplitude = float(amplitudes[peak_index])
    significant = peak_amplitude > SIGNIFICANT_PEAK_V
    warnings: list[str] = []

    values: dict[str, float | None] = {}
    if significant:
        peak_frequency = float(signal.frequency_hz[peak_index])
        values["peak_frequency_hz"] = peak_frequency
        values["peak_amplitude_v"] = peak_amplitude
    else:
        peak_frequency = None
        values["peak_frequency_hz"] = None
        values["peak_amplitude_v"] = None
        warnings.append("no_significant_non_dc_peak")

    noise_bins = np.delete(non_dc, peak_index - 1) if significant else non_dc
    if noise_bins.size:
        values["noise_floor_v"] = float(np.median(noise_bins))
    else:
        values["noise_floor_v"] = None
        warnings.append("noise_floor_unavailable")

    requested_orders = {
        order
        for order in range(2, 6)
        if any(metric.startswith(f"harmonic_{order}_") for metric in selected)
    }
    if "thd_ratio" in selected:
        requested_orders.update(range(2, 6))

    harmonic_amplitudes: list[float] = []
    for order in sorted(requested_orders):
        frequency_key = f"harmonic_{order}_frequency_hz"
        amplitude_key = f"harmonic_{order}_amplitude_v"
        if peak_frequency is None:
            values[frequency_key] = None
            values[amplitude_key] = None
            continue
        target = peak_frequency * order
        if target > float(signal.frequency_hz[-1]):
            values[frequency_key] = None
            values[amplitude_key] = None
            warnings.append(f"harmonic_{order}_out_of_band")
            continue
        index = int(np.argmin(np.abs(signal.frequency_hz - target)))
        harmonic_amplitude = float(amplitudes[index])
        values[frequency_key] = float(signal.frequency_hz[index])
        values[amplitude_key] = harmonic_amplitude
        harmonic_amplitudes.append(harmonic_amplitude)

    if "thd_ratio" in selected:
        values["thd_ratio"] = (
            None
            if not significant
            else float(np.sqrt(np.sum(np.square(harmonic_amplitudes))) / peak_amplitude)
        )

    return {
        metric: None if values[metric] is None else _finite_metric(values[metric], metric)
        for metric in selected
    }, warnings


def _replace_voltage(signal: TimeSignal, voltage: np.ndarray) -> TimeSignal:
    if not np.all(np.isfinite(voltage)):
        raise DataError("analysis operation produced non-finite values")
    return TimeSignal(
        time_s=signal.time_s.copy(),
        voltage_v=np.asarray(voltage, dtype=np.float64),
        coherent_gain=signal.coherent_gain,
        window_name=signal.window_name,
    )


def _fir_cutoff(
    response: str, cutoff_hz: float | Sequence[float]
) -> float | list[float]:
    if response in {"lowpass", "highpass"}:
        if isinstance(cutoff_hz, Sequence) and not isinstance(cutoff_hz, (str, bytes)):
            raise DataError(f"analysis FIR {response} cutoff_hz must be a positive number")
        return _positive_finite(cutoff_hz, "analysis FIR cutoff_hz")

    if (
        not isinstance(cutoff_hz, Sequence)
        or isinstance(cutoff_hz, (str, bytes))
        or len(cutoff_hz) != 2
    ):
        raise DataError(f"analysis FIR {response} cutoff_hz must contain two frequencies")
    values = [_positive_finite(value, "analysis FIR cutoff_hz") for value in cutoff_hz]
    if values[1] <= values[0]:
        raise DataError("analysis FIR cutoff_hz must be strictly increasing")
    return values


def _positive_finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)):
        raise DataError(f"{name} must be a positive finite number")
    result = float(value)
    if not np.isfinite(result) or result <= 0:
        raise DataError(f"{name} must be a positive finite number")
    return result


def _uniform_sample_interval(signal: TimeSignal, operation: str) -> float:
    if signal.time_s.size < 2:
        raise DataError(f"{operation} requires at least two samples")
    intervals = np.diff(signal.time_s)
    sample_interval = float(np.median(intervals))
    if (
        not np.isfinite(sample_interval)
        or sample_interval <= 0
        or not np.allclose(intervals, sample_interval, rtol=1e-6, atol=0.0)
    ):
        raise DataError(f"{operation} requires uniformly sampled data")
    return sample_interval


def _selected_metrics(
    metrics: Iterable[str], allowed: frozenset[str], domain: str
) -> list[str]:
    selected = list(metrics)
    unsupported = [metric for metric in selected if metric not in allowed]
    if unsupported:
        raise DataError(f"unsupported {domain}-domain metric: {unsupported[0]}")
    if len(set(selected)) != len(selected):
        raise DataError(f"duplicate {domain}-domain metric")
    return selected


def _finite_metric(value: float, name: str) -> float:
    result = float(value)
    if not np.isfinite(result):
        raise DataError(f"analysis metric {name} is not finite")
    return result
