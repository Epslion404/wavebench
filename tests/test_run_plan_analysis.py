from pathlib import Path
import tempfile
import unittest

from wavebench.errors import ConfigError
from wavebench.services.run_plan import STEP_SCHEMAS, format_run_plan_schema, load_run_plan


class AnalysisPipelineRunPlanTests(unittest.TestCase):
    def write_plan(self, content: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "plan.toml"
        path.write_text(content, encoding="utf-8")
        return path

    def analysis_plan(
        self,
        operations: str,
        *,
        capture_id: str = "capture_main",
        capture_extra: str = "save_npy = true",
        analysis_extra: str = "",
        prefix: str = "",
        suffix: str = "",
    ) -> str:
        return f"""
{prefix}
[[steps]]
id = "{capture_id}"
kind = "scope.capture"
{capture_extra}

[[steps]]
id = "spectrum_main"
kind = "analysis.pipeline"
source = {{ step = "{capture_id}" }}
operations = [{operations}]
{analysis_extra}
{suffix}
"""

    def test_loads_pipeline_with_structural_ids_and_normalized_operations(self) -> None:
        plan = load_run_plan(
            self.write_plan(
                self.analysis_plan(
                    """
  { op = "measure", metrics = ["voltage_mean_v", "voltage_rms_v"] },
  { op = "remove_dc" },
  { op = "window", name = "hann" },
  { op = "fft" },
  { op = "measure", metrics = ["peak_frequency_hz", "thd_ratio"] },
  { op = "export", name = "spectrum", formats = ["npy", "csv"] },
""",
                    analysis_extra="""
[steps.expect]
peak_frequency_hz = { min = 990, max = 1010 }
thd_ratio = { max = 0.05 }
""",
                )
            )
        )

        capture, analysis = plan.steps
        self.assertEqual(capture.id, "capture_main")
        self.assertEqual(analysis.id, "spectrum_main")
        self.assertNotIn("id", capture.fields)
        self.assertNotIn("id", analysis.fields)
        self.assertEqual(analysis.fields["source"], {"step": "capture_main"})
        self.assertEqual(
            analysis.fields["operations"][-1],
            {"op": "export", "name": "spectrum", "formats": ["npy", "csv"]},
        )
        self.assertEqual(analysis.fields["expect"]["thd_ratio"], {"max": 0.05})

    def test_step_id_is_optional_and_validated_plan_wide(self) -> None:
        legacy = load_run_plan(
            self.write_plan(
                """
[[steps]]
kind = "sleep"
duration_s = 0.1
"""
            )
        )
        self.assertIsNone(legacy.steps[0].id)

        for bad_id in ("", "1capture", "Capture", "capture.main", "a" * 65):
            with self.subTest(bad_id=bad_id):
                path = self.write_plan(
                    f"""
[[steps]]
id = "{bad_id}"
kind = "sleep"
duration_s = 0.1
"""
                )
                with self.assertRaisesRegex(ConfigError, "id must match"):
                    load_run_plan(path)

        duplicate = self.write_plan(
            """
[[steps]]
id = "same"
kind = "sleep"
duration_s = 0.1

[[steps]]
id = "same"
kind = "sleep"
duration_s = 0.1
"""
        )
        with self.assertRaisesRegex(ConfigError, "duplicate step id"):
            load_run_plan(duplicate)

    def test_pipeline_source_must_be_earlier_explicit_npy_capture(self) -> None:
        cases = {
            "unknown": self.analysis_plan(
                '{ op = "measure", metrics = ["voltage_mean_v"] }'
            ).replace('step = "capture_main"', 'step = "missing"'),
            "save_npy": self.analysis_plan(
                '{ op = "measure", metrics = ["voltage_mean_v"] }',
                capture_extra="",
            ),
            "scope.capture": """
[[steps]]
id = "wait"
kind = "sleep"
duration_s = 0.1

[[steps]]
kind = "analysis.pipeline"
source = { step = "wait" }
operations = [{ op = "measure", metrics = ["voltage_mean_v"] }]
""",
            "earlier": """
[[steps]]
kind = "analysis.pipeline"
source = { step = "later" }
operations = [{ op = "measure", metrics = ["voltage_mean_v"] }]

[[steps]]
id = "later"
kind = "scope.capture"
save_npy = true
""",
        }
        for message, content in cases.items():
            with self.subTest(message=message):
                with self.assertRaisesRegex(ConfigError, message):
                    load_run_plan(self.write_plan(content))

    def test_pipeline_steps_must_form_contiguous_suffix(self) -> None:
        path = self.write_plan(
            self.analysis_plan(
                '{ op = "measure", metrics = ["voltage_mean_v"] }',
                suffix="""
[[steps]]
kind = "sleep"
duration_s = 0.1
""",
            )
        )
        with self.assertRaisesRegex(ConfigError, "contiguous suffix"):
            load_run_plan(path)

    def test_pipeline_rejects_safety_gate(self) -> None:
        path = self.write_plan(
            self.analysis_plan(
                '{ op = "measure", metrics = ["voltage_mean_v"] }',
                analysis_extra="safety_gate = true",
            )
        )
        with self.assertRaisesRegex(ConfigError, "unknown key.*safety_gate"):
            load_run_plan(path)

    def test_pipeline_operator_parameters_and_domains_are_strict(self) -> None:
        cases = {
            "operations must be a non-empty array": "",
            "operation must be a TOML table": '"remove_dc"',
            "unsupported op": '{ op = "smooth" }',
            "unknown field 'method'": '{ op = "remove_dc", method = "linear" }',
            "method must be 'linear'": '{ op = "detrend", method = "constant" }',
            "name must be one of": '{ op = "window", name = "bartlett" }',
            "metrics must be a non-empty array": '{ op = "measure", metrics = [] }',
            "metric 'peak_frequency_hz' requires frequency-domain data": (
                '{ op = "measure", metrics = ["peak_frequency_hz"] }'
            ),
            "metric 'voltage_rms_v' requires time-domain data": (
                '{ op = "fft" }, { op = "measure", metrics = ["voltage_rms_v"] }'
            ),
            "name must match": '{ op = "export", name = "../bad", formats = ["npy"] }',
            "formats must be a non-empty array": '{ op = "export", name = "data", formats = [] }',
            "format must be one of": (
                '{ op = "export", name = "data", formats = ["json"] }'
            ),
        }
        for message, operations in cases.items():
            with self.subTest(message=message):
                with self.assertRaisesRegex(ConfigError, message):
                    load_run_plan(self.write_plan(self.analysis_plan(operations)))

    def test_pipeline_rejects_duplicate_and_misordered_configuration(self) -> None:
        cases = {
            "at most once": (
                '{ op = "remove_dc" }, { op = "remove_dc" }, '
                '{ op = "export", name = "data", formats = ["npy"] }'
            ),
            "mutually exclusive": (
                '{ op = "remove_dc" }, { op = "detrend", method = "linear" }, '
                '{ op = "export", name = "data", formats = ["npy"] }'
            ),
            "must appear before window": (
                '{ op = "window", name = "hann" }, { op = "remove_dc" }, '
                '{ op = "export", name = "data", formats = ["npy"] }'
            ),
            "must appear before fft": (
                '{ op = "fft" }, { op = "window", name = "hann" }, '
                '{ op = "export", name = "data", formats = ["npy"] }'
            ),
            "duplicate metric": (
                '{ op = "measure", metrics = ["voltage_mean_v"] }, '
                '{ op = "measure", metrics = ["voltage_mean_v"] }'
            ),
            "duplicate export name": (
                '{ op = "export", name = "data", formats = ["npy"] }, '
                '{ op = "export", name = "data", formats = ["csv"] }'
            ),
            "duplicate export format": (
                '{ op = "export", name = "data", formats = ["npy", "npy"] }'
            ),
            "requires at least one measure or export": '{ op = "remove_dc" }',
        }
        for message, operations in cases.items():
            with self.subTest(message=message):
                with self.assertRaisesRegex(ConfigError, message):
                    load_run_plan(self.write_plan(self.analysis_plan(operations)))

    def test_pipeline_expect_requires_an_explicitly_measured_metric(self) -> None:
        path = self.write_plan(
            self.analysis_plan(
                '{ op = "export", name = "data", formats = ["npy"] }',
                analysis_extra="""
[steps.expect]
voltage_mean_v = { min = -0.1, max = 0.1 }
""",
            )
        )
        with self.assertRaisesRegex(ConfigError, "must be selected by a measure operation"):
            load_run_plan(path)

    def test_generated_schema_lists_pipeline_contract(self) -> None:
        schema = format_run_plan_schema()
        self.assertIn("analysis.pipeline", STEP_SCHEMAS)
        self.assertIn("[[steps]] optional structural field: id", schema)
        self.assertIn("analysis.pipeline metrics", schema)


if __name__ == "__main__":
    unittest.main()
