from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from wavebench.services.run_artifacts import RunStepRecord
from wavebench.services.run_pipeline import execute_analysis_pipeline
from wavebench.services.run_plan import RunStep


class AnalysisPipelineArtifactTests(unittest.TestCase):
    def source(
        self,
        root: Path,
        data: np.ndarray,
        *,
        npy_metadata_path: str | None = None,
        status: str = "ok",
    ) -> tuple[RunStep, RunStepRecord, Path]:
        package = root / "raw" / "capture"
        package.mkdir(parents=True)
        npy_path = package / "ch1.npy"
        np.save(npy_path, data)
        metadata = package / "metadata.json"
        metadata.write_text(
            json.dumps({
                "operation": {"channel": 1},
                "files": {"npy": npy_metadata_path or str(npy_path)},
            }),
            encoding="utf-8",
        )
        source_step = RunStep(
            index=0,
            kind="scope.capture",
            fields={"save_npy": True},
            id="capture_main",
        )
        source_record = RunStepRecord(
            index=0,
            kind="scope.capture",
            status=status,
            fields=source_step.fields,
            artifact={"package": str(package), "metadata": str(metadata)},
        )
        return source_step, source_record, npy_path

    def pipeline(
        self,
        operations: list[dict[str, object]],
        *,
        expect: dict[str, dict[str, float]] | None = None,
    ) -> RunStep:
        fields: dict[str, object] = {
            "source": {"step": "capture_main"},
            "operations": operations,
        }
        if expect is not None:
            fields["expect"] = expect
        return RunStep(
            index=1,
            kind="analysis.pipeline",
            fields=fields,
            id="spectrum_main",
        )

    def waveform(self, *, nonuniform: bool = False) -> np.ndarray:
        samples = 1000
        time_s = np.arange(samples, dtype=float) / 10_000.0
        if nonuniform:
            time_s[500:] += 1e-5
        voltage_v = 0.5 + np.sin(2 * np.pi * 100.0 * time_s)
        return np.column_stack((time_s, voltage_v))

    def test_success_writes_versioned_metrics_manifest_and_frequency_exports(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run"
            run_dir.mkdir(parents=True)
            source_step, source_record, source_npy = self.source(root, self.waveform())
            source_before = sha256(source_npy.read_bytes()).hexdigest()
            step = self.pipeline(
                [
                    {"op": "measure", "metrics": ["voltage_mean_v"]},
                    {"op": "remove_dc"},
                    {"op": "window", "name": "hann"},
                    {"op": "fft"},
                    {
                        "op": "measure",
                        "metrics": [
                            "peak_frequency_hz",
                            "peak_amplitude_v",
                            "noise_floor_v",
                            "thd_ratio",
                        ],
                    },
                    {"op": "export", "name": "spectrum", "formats": ["npy", "csv"]},
                ],
                expect={"peak_frequency_hz": {"min": 99.0, "max": 101.0}},
            )

            artifact = execute_analysis_pipeline(
                run_dir=run_dir,
                step=step,
                source_step=source_step,
                source_record=source_record,
            )

            processing = run_dir / "processing" / "01_spectrum_main"
            manifest = json.loads((processing / "manifest.json").read_text(encoding="utf-8"))
            metrics = json.loads((processing / "metrics.json").read_text(encoding="utf-8"))
            exported_npy = processing / "exports" / "spectrum.npy"
            exported_csv = processing / "exports" / "spectrum.csv"
            self.assertEqual(manifest["schema"], "wavebench.analysis_pipeline.v1")
            self.assertEqual(manifest["status"], "ok")
            self.assertFalse(manifest["partial"])
            self.assertEqual(manifest["source"]["step"], "capture_main")
            self.assertEqual(manifest["source"]["npy_sha256"], source_before)
            self.assertEqual(manifest["window"]["name"], "hann")
            self.assertAlmostEqual(manifest["window"]["coherent_gain"], np.mean(np.hanning(1000)))
            self.assertEqual(metrics["schema"], "wavebench.analysis_metrics.v1")
            self.assertAlmostEqual(metrics["metrics"]["peak_frequency_hz"], 100.0)
            self.assertEqual(artifact["expect"]["status"], "ok")
            self.assertEqual(artifact["analysis_pipeline"]["manifest"], "processing/01_spectrum_main/manifest.json")
            self.assertEqual(np.load(exported_npy).shape, (501, 4))
            self.assertEqual(
                exported_csv.read_text(encoding="utf-8").splitlines()[0],
                "frequency_hz,real_v,imaginary_v,amplitude_v",
            )
            self.assertEqual(sha256(source_npy.read_bytes()).hexdigest(), source_before)
            self.assertFalse(list(processing.rglob("*.tmp")))
            for export in manifest["exports"]:
                export_path = run_dir / export["path"]
                self.assertEqual(export["sha256"], sha256(export_path.read_bytes()).hexdigest())

    def test_source_expectation_failure_still_allows_complete_npy(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run"
            run_dir.mkdir(parents=True)
            source_step, source_record, _ = self.source(root, self.waveform(), status="failed")

            artifact = execute_analysis_pipeline(
                run_dir=run_dir,
                step=self.pipeline([
                    {"op": "measure", "metrics": ["voltage_mean_v"]},
                ]),
                source_step=source_step,
                source_record=source_record,
            )

            self.assertEqual(artifact["analysis_pipeline"]["status"], "ok")
            self.assertEqual(artifact["analysis_pipeline"]["source_status"], "failed")

    def test_metadata_path_traversal_is_a_structured_pipeline_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run"
            run_dir.mkdir(parents=True)
            outside = root / "raw" / "outside.npy"
            outside.parent.mkdir(parents=True)
            np.save(outside, self.waveform())
            outside_before = sha256(outside.read_bytes()).hexdigest()
            source_step, source_record, _ = self.source(
                root,
                self.waveform(),
                npy_metadata_path="../outside.npy",
            )
            step = self.pipeline(
                [{"op": "measure", "metrics": ["voltage_mean_v"]}],
                expect={"voltage_mean_v": {"min": -1.0, "max": 1.0}},
            )

            artifact = execute_analysis_pipeline(
                run_dir=run_dir,
                step=step,
                source_step=source_step,
                source_record=source_record,
            )

            self.assertEqual(artifact["analysis_pipeline"]["status"], "failed")
            self.assertEqual(artifact["analysis_pipeline"]["failed_stage"], "source")
            self.assertIsNone(artifact["metrics"]["voltage_mean_v"])
            self.assertEqual(artifact["expect"]["checks"]["voltage_mean_v"]["reason"], "unavailable")
            manifest = json.loads(
                (run_dir / artifact["analysis_pipeline"]["manifest"]).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["stages"][0]["status"], "failed")
            self.assertIn("must not contain '..'", manifest["error"]["message"])
            self.assertEqual(sha256(outside.read_bytes()).hexdigest(), outside_before)

    def test_completed_time_export_survives_later_fft_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run"
            run_dir.mkdir(parents=True)
            source_step, source_record, _ = self.source(
                root, self.waveform(nonuniform=True)
            )
            step = self.pipeline([
                {"op": "export", "name": "time", "formats": ["npy", "csv"]},
                {"op": "fft"},
                {"op": "measure", "metrics": ["peak_frequency_hz"]},
            ])

            artifact = execute_analysis_pipeline(
                run_dir=run_dir,
                step=step,
                source_step=source_step,
                source_record=source_record,
            )

            processing = run_dir / "processing" / "01_spectrum_main"
            manifest = json.loads((processing / "manifest.json").read_text(encoding="utf-8"))
            metrics = json.loads((processing / "metrics.json").read_text(encoding="utf-8"))
            self.assertEqual(artifact["analysis_pipeline"]["status"], "failed")
            self.assertTrue(manifest["partial"])
            self.assertEqual(len(manifest["exports"]), 2)
            self.assertTrue((processing / "exports" / "time.npy").is_file())
            self.assertEqual(
                (processing / "exports" / "time.csv")
                .read_text(encoding="utf-8")
                .splitlines()[0],
                "time_s,voltage_v",
            )
            self.assertEqual(np.load(processing / "exports" / "time.npy").shape, (1000, 2))
            self.assertIsNone(metrics["metrics"]["peak_frequency_hz"])
            self.assertEqual(manifest["failed_stage"], "operations[1]")
            self.assertEqual(manifest["stages"][-1]["status"], "skipped")

    def test_completed_export_is_recorded_if_a_later_format_write_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run"
            run_dir.mkdir(parents=True)
            source_step, source_record, _ = self.source(root, self.waveform())
            step = self.pipeline([
                {"op": "export", "name": "time", "formats": ["npy", "csv"]},
            ])

            with patch(
                "wavebench.services.run_pipeline._atomic_write_csv",
                side_effect=OSError("disk full"),
            ):
                artifact = execute_analysis_pipeline(
                    run_dir=run_dir,
                    step=step,
                    source_step=source_step,
                    source_record=source_record,
                )

            manifest = json.loads(
                (run_dir / artifact["analysis_pipeline"]["manifest"]).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["status"], "failed")
            self.assertTrue(manifest["partial"])
            self.assertEqual([item["format"] for item in manifest["exports"]], ["npy"])

    def test_missing_source_record_still_writes_null_metrics_and_manifest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run"
            run_dir.mkdir(parents=True)
            source_step = RunStep(
                index=0,
                kind="scope.capture",
                fields={"save_npy": True},
                id="capture_main",
            )
            step = self.pipeline([
                {"op": "measure", "metrics": ["voltage_mean_v"]},
            ])

            artifact = execute_analysis_pipeline(
                run_dir=run_dir,
                step=step,
                source_step=source_step,
                source_record=None,
            )

            self.assertEqual(artifact["analysis_pipeline"]["status"], "failed")
            metrics_text = (
                run_dir / artifact["analysis_pipeline"]["metrics"]
            ).read_text(encoding="utf-8")
            self.assertIn('"voltage_mean_v": null', metrics_text)
            self.assertNotIn("NaN", metrics_text)


if __name__ == "__main__":
    unittest.main()
