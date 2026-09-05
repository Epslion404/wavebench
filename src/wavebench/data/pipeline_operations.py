"""Additional signal operators; independent of services and instruments."""
from __future__ import annotations

import re
from hashlib import sha256
from typing import Any

import numpy as np

from wavebench.data.signal_pipeline import (
    FrequencySignal, PsdSignal, TimeSignal, _uniform_sample_interval,
)
from wavebench.errors import DataError


BAND_METRICS = {"mean_square_v2", "rms_v", "noise_rms_v"}


def result_name(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) is None:
        raise DataError("result name must match ^[a-z][a-z0-9_-]{0,63}$")
    return value


def number(value: Any, name: str, *, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DataError(f"{name} must be a finite number >= {minimum}")
    value = float(value)
    if not np.isfinite(value) or value < minimum:
        raise DataError(f"{name} must be a finite number >= {minimum}")
    return value


def interval(value: Any, name: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 2:
        raise DataError(f"{name} must contain two frequency limits")
    result = [number(item, name) for item in value]
    if result[0] >= result[1]:
        raise DataError(f"{name} limits must increase")
    return result


def integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise DataError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def normalize_peaks(operation: dict[str, Any]) -> dict[str, Any]:
    if operation["polarity"] not in ("positive", "negative", "both"):
        raise DataError("peak polarity must be positive, negative or both")
    if operation["metrics"] != ["count"]:
        raise DataError("peaks metrics must explicitly select [count]")
    normalized = dict(op="peaks", name=result_name(operation["name"]),
                      polarity=operation["polarity"], metrics=["count"])
    for key in ("height", "prominence", "distance", "width"):
        normalized[key] = number(operation[key], key)
    if normalized["distance"] == 0:
        raise DataError("peak distance must be > 0")
    normalized["max_peaks"] = integer(operation["max_peaks"], "max_peaks", 1, 10000)
    return normalized


def detect_peaks(signal: TimeSignal | FrequencySignal | PsdSignal, operation: dict[str, Any]) -> dict:
    operation = normalize_peaks(operation)
    if isinstance(signal, TimeSignal):
        interval_s = _uniform_sample_interval(signal, "peak detection")
        axis, values = signal.time_s, signal.voltage_v
        spacing, domain, axis_unit, units = interval_s, "time", "s", "V"
    else:
        if operation["polarity"] != "positive":
            raise DataError("spectral peaks require positive polarity")
        axis = signal.frequency_hz
        values = signal.amplitude_v if isinstance(signal, FrequencySignal) else signal.psd_v2_per_hz
        spacing = float(axis[1] - axis[0])
        domain = "frequency" if isinstance(signal, FrequencySignal) else "psd"
        axis_unit, units = "Hz", "V" if domain == "frequency" else "V^2/Hz"
    from scipy import __version__ as scipy_version
    from scipy.signal import find_peaks

    candidates = []
    signs = (1, -1) if operation["polarity"] == "both" else ((1,) if operation["polarity"] == "positive" else (-1,))
    for sign in signs:
        indices, properties = find_peaks(
            values * sign, height=operation["height"] or None,
            prominence=(operation["prominence"], None), width=(None, None), rel_height=.5,
        )
        for i, index in enumerate(indices):
            width = float(properties["widths"][i] * spacing)
            if width < operation["width"]:
                continue
            candidates.append({
                "index": int(index), "position": float(axis[index]), "value": float(values[index]),
                "prominence": float(properties["prominences"][i]), "width": width, "polarity": sign,
            })
    candidates.sort(key=lambda peak: (-peak["value"] * peak["polarity"], peak["position"], -peak["polarity"]))
    # Higher signed height wins; equal heights keep the earlier sample deterministically.
    accepted = []
    blocked = np.zeros(len(axis), dtype=bool)
    for peak in candidates:
        if blocked[peak["index"]]:
            continue
        left = np.searchsorted(axis, peak["position"] - operation["distance"], side="right")
        right = np.searchsorted(axis, peak["position"] + operation["distance"], side="left")
        blocked[left:right] = True
        blocked[peak["index"]] = True
        accepted.append(peak)
    if any(not np.isfinite(row[key]) for row in accepted for key in ("position", "value", "prominence", "width")):
        raise DataError("peak properties must be finite")
    return {
        "schema": "wavebench.peaks.v1", "name": operation["name"], "domain": domain,
        "axis_unit": axis_unit, "value_unit": units, "scipy_version": scipy_version,
        "signal_sha256": sha256(np.asarray(np.column_stack((axis, values)), dtype="<f8").tobytes()).hexdigest(),
        "count": len(accepted), "retained_count": min(len(accepted), operation["max_peaks"]),
        "truncated": len(accepted) > operation["max_peaks"],
        "width_rule": "half_prominence", "endpoint_rule": "excluded",
        "peaks": accepted[:operation["max_peaks"]],
    }


def normalize_band(operation: dict[str, Any]) -> dict[str, Any]:
    name = result_name(operation["name"])
    band = interval(operation["band_hz"], "band_hz")
    if not isinstance(operation["exclude_hz"], list):
        raise DataError("exclude_hz must be an array of frequency intervals")
    excluded = [interval(item, "exclude_hz") for item in operation["exclude_hz"]]
    if any(low < band[0] or high > band[1] for low, high in excluded):
        raise DataError("excluded intervals must lie within band_hz")
    metrics = operation["metrics"]
    if (not isinstance(metrics, list) or not metrics
            or any(not isinstance(item, str) or item not in BAND_METRICS for item in metrics)
            or len(set(metrics)) != len(metrics)):
        raise DataError("band metrics must select distinct mean_square_v2, rms_v or noise_rms_v")
    if "noise_rms_v" in metrics and not excluded:
        raise DataError("noise_rms_v requires explicit signal exclusion intervals")
    return dict(op="measure_band", name=name, band_hz=band, exclude_hz=excluded, metrics=metrics[:])


def measure_band(signal: PsdSignal, operation: dict[str, Any]) -> tuple[dict, dict, list[str]]:
    operation = normalize_band(operation)
    low, high = operation["band_hz"]
    nyquist = .5 / signal.sample_interval_s
    if high > nyquist and not np.isclose(high, nyquist, rtol=1e-9, atol=0):
        raise DataError("band_hz exceeds the actual Nyquist frequency")
    frequencies = signal.frequency_hz
    selected = (frequencies >= low) & (frequencies <= high)
    for left, right in operation["exclude_hz"]:
        selected &= ~((frequencies >= left) & (frequencies <= right))
    count = int(np.count_nonzero(selected))
    spacing = float(frequencies[1] - frequencies[0])
    warnings = []
    if count:
        mean_square = float(np.sum(signal.psd_v2_per_hz[selected]) * spacing)
        if not np.isfinite(mean_square) or mean_square < 0:
            raise DataError("band measurement must be finite and nonnegative")
        rms = float(np.sqrt(mean_square))
    else:
        mean_square = rms = None
        warnings.append(f"{operation['name']}: no bins remain in the selected band")
    values = {"mean_square_v2": mean_square, "rms_v": rms, "noise_rms_v": rms}
    metrics = {f"{operation['name']}_{key}": values[key] for key in operation["metrics"]}
    return metrics, {
        "name": operation["name"], "selected_bins": count, "bin_spacing_hz": spacing,
        "integration": "sum_selected_bin_density_times_bin_spacing",
        "interval_rule": "closed_bin_centers", "average": signal.parameters["average"],
    }, warnings
