import unittest

import numpy as np

from wavebench.data.signal_pipeline import (
    ANALYSIS_FREQUENCY_METRICS,
    FrequencySignal,
    detrend_linear,
    fft_signal,
    measure_frequency,
    measure_time,
    remove_dc,
    validate_waveform,
    window_signal,
)
from wavebench.errors import DataError


class SignalPipelineTests(unittest.TestCase):
    def waveform(self, samples: int = 1000, sample_rate_hz: float = 10_000.0) -> np.ndarray:
        time_s = np.arange(samples, dtype=float) / sample_rate_hz
        voltage_v = (
            1.5 * np.sin(2 * np.pi * 100.0 * time_s)
            + 0.15 * np.sin(2 * np.pi * 200.0 * time_s)
            + 0.075 * np.sin(2 * np.pi * 300.0 * time_s)
        )
        return np.column_stack((time_s, voltage_v))

    def test_waveform_validation_is_strict(self) -> None:
        valid = self.waveform(8)
        self.assertEqual(validate_waveform(valid).as_array().shape, (8, 2))

        invalid = (
            np.empty((0, 2)),
            np.zeros((4, 3)),
            np.array([["0", "1"], ["1", "2"]]),
            np.array([[0.0, 1.0], [1.0, np.nan]]),
            np.array([[0.0, 1.0], [0.0, 2.0]]),
            np.array([[0.0, 1.0], [-1.0, 2.0]]),
        )
        for data in invalid:
            with self.subTest(data=data):
                with self.assertRaises(DataError):
                    validate_waveform(data)

    def test_remove_dc_subtracts_arithmetic_mean(self) -> None:
        signal = validate_waveform(
            np.column_stack((np.arange(4, dtype=float), [1.0, 2.0, 3.0, 8.0]))
        )
        result = remove_dc(signal)
        np.testing.assert_allclose(result.voltage_v, [-2.5, -1.5, -0.5, 4.5])
        self.assertEqual(float(np.mean(result.voltage_v)), 0.0)

    def test_linear_detrend_removes_slope_and_intercept_on_centered_time(self) -> None:
        time_s = np.array([1000.0, 1000.2, 1000.5, 1001.0])
        residual = np.array([0.2, -0.1, -0.1, 0.2])
        residual -= np.mean(residual)
        centered_time = time_s - np.mean(time_s)
        residual -= np.dot(centered_time, residual) / np.dot(centered_time, centered_time) * centered_time
        voltage_v = 4.0 + 2.5 * centered_time + residual

        result = detrend_linear(validate_waveform(np.column_stack((time_s, voltage_v))))

        self.assertAlmostEqual(float(np.mean(result.voltage_v)), 0.0, places=12)
        self.assertAlmostEqual(float(np.dot(centered_time, result.voltage_v)), 0.0, places=12)
        np.testing.assert_allclose(result.voltage_v, residual, atol=1e-12)

    def test_numpy_window_definitions_and_coherent_gain(self) -> None:
        signal = validate_waveform(
            np.column_stack((np.arange(8, dtype=float), np.ones(8)))
        )
        for name, expected in (
            ("hann", np.hanning(8)),
            ("hamming", np.hamming(8)),
            ("blackman", np.blackman(8)),
        ):
            with self.subTest(name=name):
                result = window_signal(signal, name)
                np.testing.assert_array_equal(result.voltage_v, expected)
                self.assertEqual(result.coherent_gain, float(np.mean(expected)))
                self.assertEqual(result.window_name, name)

    def test_even_fft_preserves_dc_and_nyquist_without_double_scaling(self) -> None:
        samples = 8
        indices = np.arange(samples)
        time_s = indices / samples
        voltage_v = 3.0 + 2.0 * np.cos(2 * np.pi * indices / samples) + 5.0 * (-1.0) ** indices

        result = fft_signal(validate_waveform(np.column_stack((time_s, voltage_v))))

        self.assertAlmostEqual(float(result.amplitude_v[0]), 3.0, places=12)
        self.assertAlmostEqual(float(result.amplitude_v[1]), 2.0, places=12)
        self.assertAlmostEqual(float(result.amplitude_v[-1]), 5.0, places=12)

    def test_odd_fft_doubles_the_last_non_dc_bin(self) -> None:
        samples = 9
        indices = np.arange(samples)
        time_s = indices / samples
        voltage_v = 2.0 * np.cos(2 * np.pi * 4 * indices / samples)

        result = fft_signal(validate_waveform(np.column_stack((time_s, voltage_v))))

        self.assertAlmostEqual(float(result.amplitude_v[4]), 2.0, places=12)

    def test_fft_uses_window_coherent_gain(self) -> None:
        signal = validate_waveform(
            np.column_stack((np.arange(8, dtype=float) / 8.0, np.full(8, 2.5)))
        )
        windowed = window_signal(signal, "hamming")

        result = fft_signal(windowed)

        self.assertAlmostEqual(float(result.amplitude_v[0]), 2.5, places=12)
        self.assertEqual(result.coherent_gain, float(np.mean(np.hamming(8))))

    def test_fft_rejects_short_or_nonuniform_input(self) -> None:
        with self.assertRaisesRegex(DataError, "at least four"):
            fft_signal(validate_waveform(self.waveform(3)))

        data = self.waveform(8)
        data[4:, 0] += 1e-4
        with self.assertRaisesRegex(DataError, "uniformly sampled"):
            fft_signal(validate_waveform(data))

    def test_time_metrics_have_fixed_units_and_peak_semantics(self) -> None:
        signal = validate_waveform(
            np.column_stack((np.arange(4, dtype=float), [-2.0, -1.0, 1.0, 2.0]))
        )
        metrics = measure_time(
            signal,
            [
                "voltage_min_v",
                "voltage_max_v",
                "voltage_mean_v",
                "voltage_rms_v",
                "voltage_vpp_v",
            ],
        )
        self.assertEqual(metrics["voltage_min_v"], -2.0)
        self.assertEqual(metrics["voltage_max_v"], 2.0)
        self.assertEqual(metrics["voltage_mean_v"], 0.0)
        self.assertAlmostEqual(metrics["voltage_rms_v"], np.sqrt(2.5))
        self.assertEqual(metrics["voltage_vpp_v"], 4.0)

    def test_finite_input_cannot_emit_nonfinite_metrics(self) -> None:
        signal = validate_waveform(
            np.column_stack((np.arange(2, dtype=float), [-1e308, 1e308]))
        )

        with self.assertRaisesRegex(DataError, "not finite"):
            measure_time(signal, ["voltage_vpp_v"])

    def test_frequency_metrics_find_peak_harmonics_thd_and_noise_floor(self) -> None:
        spectrum = fft_signal(validate_waveform(self.waveform()))
        metrics, warnings = measure_frequency(spectrum, sorted(ANALYSIS_FREQUENCY_METRICS))

        self.assertEqual(warnings, [])
        self.assertAlmostEqual(metrics["peak_frequency_hz"], 100.0)
        self.assertAlmostEqual(metrics["peak_amplitude_v"], 1.5, places=12)
        self.assertAlmostEqual(metrics["harmonic_2_frequency_hz"], 200.0)
        self.assertAlmostEqual(metrics["harmonic_2_amplitude_v"], 0.15, places=12)
        self.assertAlmostEqual(metrics["harmonic_3_amplitude_v"], 0.075, places=12)
        self.assertAlmostEqual(
            metrics["thd_ratio"],
            np.sqrt(0.15**2 + 0.075**2) / 1.5,
            places=12,
        )
        self.assertLess(metrics["noise_floor_v"], 1e-13)

    def test_harmonics_outside_nyquist_are_null_with_warnings(self) -> None:
        samples = 80
        sample_rate_hz = 8000.0
        time_s = np.arange(samples) / sample_rate_hz
        voltage_v = np.sin(2 * np.pi * 2000.0 * time_s)
        spectrum = fft_signal(validate_waveform(np.column_stack((time_s, voltage_v))))

        metrics, warnings = measure_frequency(
            spectrum,
            [
                "harmonic_2_frequency_hz",
                "harmonic_2_amplitude_v",
                "harmonic_3_frequency_hz",
                "harmonic_3_amplitude_v",
                "thd_ratio",
            ],
        )

        self.assertAlmostEqual(metrics["harmonic_2_frequency_hz"], 4000.0)
        self.assertIsNone(metrics["harmonic_3_frequency_hz"])
        self.assertIsNone(metrics["harmonic_3_amplitude_v"])
        self.assertIn("harmonic_3_out_of_band", warnings)
        self.assertIn("harmonic_5_out_of_band", warnings)

    def test_silent_spectrum_has_no_peak_or_thd(self) -> None:
        time_s = np.arange(8, dtype=float) / 8.0
        spectrum = fft_signal(
            validate_waveform(np.column_stack((time_s, np.full(8, 1e-13))))
        )

        metrics, warnings = measure_frequency(
            spectrum,
            ["peak_frequency_hz", "peak_amplitude_v", "noise_floor_v", "thd_ratio"],
        )

        self.assertIsNone(metrics["peak_frequency_hz"])
        self.assertIsNone(metrics["peak_amplitude_v"])
        self.assertIsNone(metrics["thd_ratio"])
        self.assertEqual(metrics["noise_floor_v"], 0.0)
        self.assertIn("no_significant_non_dc_peak", warnings)

    def test_noise_floor_is_median_after_excluding_dc_and_main_peak(self) -> None:
        spectrum = FrequencySignal(
            frequency_hz=np.arange(5, dtype=float),
            spectrum_v=np.array([100.0, 10.0, 1.0, 3.0, 5.0], dtype=complex),
            samples=8,
            sample_interval_s=0.125,
            coherent_gain=1.0,
            window_name=None,
        )

        metrics, _ = measure_frequency(spectrum, ["noise_floor_v"])

        self.assertEqual(metrics["noise_floor_v"], 3.0)


if __name__ == "__main__":
    unittest.main()
