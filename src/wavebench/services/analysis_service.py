"""Offline recipes reuse the RunPlan operator contract and execution engine."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import tomllib
from typing import Any

import numpy as np

from wavebench import __version__
from wavebench.data.packages import load_capture_package
from wavebench.data.signal_pipeline import validate_waveform
from wavebench.errors import ConfigError, DataError
from wavebench.services.run_pipeline import (
    _atomic_write_json, _resolve_package_member, _sha256_file,
    ensure_operation_dependencies, execute_pipeline,
)
from wavebench.services.run_plan import normalize_analysis_operations


RECIPE_SCHEMA = "wavebench.analysis_recipe.v1"
RESULT_SCHEMA = "wavebench.analysis.v1"


def load_analysis_recipe(path: str | Path) -> dict[str, Any]:
    try:
        fields = tomllib.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read analysis recipe: {exc}") from exc
    if fields.get("schema") != RECIPE_SCHEMA:
        raise ConfigError(f"analysis recipe schema must be {RECIPE_SCHEMA}")
    if set(fields) - {"schema", "operations", "expect"}:
        raise ConfigError("analysis recipe has unknown fields")
    if "operations" not in fields:
        raise ConfigError("analysis recipe requires operations")
    normalize_analysis_operations("recipe", fields)
    ensure_operation_dependencies(fields["operations"])
    return fields


def load_analysis_source(capture: Path, channel: int) -> tuple[dict[str, Any], np.ndarray]:
    if isinstance(channel, bool) or not isinstance(channel, int) or channel < 1:
        raise ConfigError("analysis channel must be a positive integer")
    package_path = capture.resolve()
    _resolve_package_member(package_path, "metadata.json", label="metadata")
    try:
        package = load_capture_package(package_path)
    except (TypeError, ValueError, KeyError) as exc:
        raise DataError(f"invalid capture metadata: {exc}") from exc
    candidates = [item for item in package.channels if item.channel == channel]
    if len(candidates) != 1:
        raise DataError(f"capture must contain exactly one channel {channel}")
    raw = candidates[0].files.get("npy")
    if not isinstance(raw, str) or not raw:
        raise DataError("selected capture channel has no NPY")
    path = _resolve_package_member(package_path, raw, label="NPY")
    try:
        waveform = np.load(path, allow_pickle=False)
        validate_waveform(waveform)
    except (OSError, ValueError) as exc:
        raise DataError(f"cannot load capture waveform: {exc}") from exc
    return {
        "kind": "capture_package", "package": str(package_path), "channel": channel,
        "npy": path.relative_to(package_path).as_posix(), "npy_sha256": _sha256_file(path),
        "status": package.metadata.get("status") if isinstance(package.metadata.get("status"), str) else None,
    }, waveform


def check_analysis(capture: Path, channel: int, recipe: Path) -> dict[str, Any]:
    fields = load_analysis_recipe(recipe)
    source, data = load_analysis_source(capture, channel)
    return {"schema": "wavebench.analysis_check.v1", "status": "ok", "source": source,
            "samples": len(data), "recipe": fields}


def run_analysis(capture: Path, channel: int, recipe: Path, output: Path) -> dict[str, Any]:
    fields = load_analysis_recipe(recipe)
    capture = capture.resolve()
    output = output.resolve()
    if output.exists():
        raise ConfigError("analysis output must be a new directory")
    if output == capture or capture in output.parents or output in capture.parents:
        raise ConfigError("analysis output must be separate from the capture package")
    if any((parent / "run.json").exists() for parent in output.parents):
        raise ConfigError("analysis output must not modify an existing run")
    source = {"kind": "capture_package", "package": str(capture), "channel": channel, "status": None}
    artifact = execute_pipeline(
        run_dir=output, processing_dir=output, fields=fields, source=source,
        load_source=lambda: load_analysis_source(capture, channel),
        schema="wavebench.offline_pipeline.v1",
    )
    failed = artifact["analysis_pipeline"]["status"] == "failed"
    if "expect" in artifact:
        failed = failed or artifact["expect"]["status"] != "ok"
    result = {
        "schema": RESULT_SCHEMA, "status": "failed" if failed else "ok",
        "wavebench_version": __version__, "source": source, "recipe": fields,
        "recipe_sha256": sha256(json.dumps(fields, sort_keys=True, allow_nan=False).encode()).hexdigest(),
        "artifact": artifact,
    }
    _atomic_write_json(output / "analysis.json", result)
    return result
