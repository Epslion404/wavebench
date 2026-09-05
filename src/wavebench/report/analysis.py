"""Read-only visualisation of persisted pipeline exports."""
from __future__ import annotations

from html import escape
import json
from pathlib import Path
from typing import Any

import numpy as np

from wavebench.errors import ConfigError
from wavebench.services.run_pipeline import _sha256_file, _atomic_write_bytes


COLUMNS = {
    ("time_s", "voltage_v"): (1, "time_s", "V"),
    ("frequency_hz", "real_v", "imaginary_v", "amplitude_v"): (3, "frequency_hz", "V"),
    ("frequency_hz", "psd_v2_per_hz"): (1, "frequency_hz", "V²/Hz"),
}


def artifact_file(root: Path, raw: str) -> Path:
    if not isinstance(raw, str) or not raw or Path(raw).is_absolute() or ".." in Path(raw).parts:
        raise ValueError("artifact path must be relative to its result directory")
    path = (root / raw).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("artifact escapes its result directory or is missing")
    return path


def analysis_entries(root: Path) -> list[tuple[Path, str, dict[str, Any]]]:
    try:
        if (root / "analysis.json").is_file():
            result = json.loads((root / "analysis.json").read_text())
            if result["schema"] != "wavebench.analysis.v1":
                raise ValueError("unsupported analysis schema")
            return [(root, root.name, result["artifact"])]
        run = json.loads((root / "run.json").read_text())
        return [(root, f"{root.name}/{step.get('id', step['index'])}", step["artifact"])
                for step in run["steps"] if step["kind"] == "analysis.pipeline"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ConfigError(f"cannot read analysis results in {root}: {exc}") from exc


def display_samples(data: np.ndarray, maximum: int = 1200) -> np.ndarray:
    """Preserve extrema within display buckets; never use this data for metrics."""
    if len(data) <= maximum:
        return data
    indices = {0, len(data) - 1}
    edges = np.linspace(0, len(data), (maximum - 2) // 2 + 1, dtype=int)
    for start, stop in zip(edges[:-1], edges[1:]):
        chunk = data[start:stop, 1]
        indices.update((start + int(np.argmin(chunk)), start + int(np.argmax(chunk))))
    return data[sorted(indices)]


def curves_svg(curves: list[tuple[str, np.ndarray]], x_label: str, units: str) -> str:
    width, height, pad = 900, 300, 55
    xmin = min(float(data[0, 0]) for _, data in curves)
    xmax = max(float(data[-1, 0]) for _, data in curves)
    ymin = min(float(np.min(data[:, 1])) for _, data in curves)
    ymax = max(float(np.max(data[:, 1])) for _, data in curves)
    if ymax == ymin:
        margin = max(abs(ymin) * .05, 1e-12)
        ymin, ymax = ymin - margin, ymax + margin
    colors = ("#2563eb", "#dc2626", "#059669", "#9333ea", "#d97706")
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(x_label)} / {escape(units)}">',
             '<rect width="900" height="300" fill="#f8fafc"/>']
    for fraction in np.linspace(0, 1, 5):
        x, y = pad + fraction * (width - 2 * pad), height - pad - fraction * (height - 2 * pad)
        parts.append(f'<text x="{x:.1f}" y="270" font-size="11" text-anchor="middle">{xmin + fraction * (xmax-xmin):.5g}</text>')
        parts.append(f'<text x="50" y="{y:.1f}" font-size="11" text-anchor="end">{ymin + fraction * (ymax-ymin):.5g}</text>')
    for index, (label, data) in enumerate(curves):
        sampled = display_samples(data)
        points = " ".join(f"{pad+(x-xmin)/(xmax-xmin)*(width-2*pad):.2f},{height-pad-(y-ymin)/(ymax-ymin)*(height-2*pad):.2f}"
                          for x, y in sampled)
        parts.append(f'<polyline points="{points}" fill="none" stroke="{colors[index % len(colors)]}" stroke-width="1.5"><title>{escape(label)}</title></polyline>')
    parts.append(f'<text x="450" y="294" text-anchor="middle">{escape(x_label)}</text><text x="55" y="20">{escape(units)}</text></svg>')
    parts.append("<ul>" + "".join(f'<li style="color:{colors[i % len(colors)]}">{escape(label)}</li>' for i, (label, _) in enumerate(curves)) + "</ul>")
    return "".join(parts)


def render_analysis_sections(entries: list[tuple[Path, str, dict[str, Any]]], *, details: bool = True) -> str:
    groups: dict[tuple, list[tuple[str, np.ndarray]]] = {}
    sections: list[str] = []
    for root, label, artifact in entries:
        try:
            pipeline = artifact["analysis_pipeline"]
            manifest = json.loads(artifact_file(root, pipeline["manifest"]).read_text())
            if details:
                sections.append(f"<h3>{escape(label)}</h3><pre>{escape(json.dumps(artifact, indent=2, ensure_ascii=False))}</pre>")
            sections.append(f'<p>{escape(label)}: sampling={escape(json.dumps(manifest.get("sampling")))}</p>')
            source = manifest["source"]
            seen: set[tuple] = set()
            for item in sorted(manifest["exports"], key=lambda x: x.get("format") != "npy"):
                try:
                    columns = tuple(item["columns"])
                    column, x_label, units = COLUMNS[columns]
                    identity = (item["name"], columns)
                    if identity in seen:
                        continue
                    path = artifact_file(root, item["path"])
                    if _sha256_file(path) != item["sha256"]:
                        raise ValueError("export SHA-256 mismatch")
                    data = (np.load(path, allow_pickle=False, mmap_mode="r") if item["format"] == "npy"
                            else np.loadtxt(path, delimiter=",", skiprows=1, ndmin=2))
                    if data.ndim != 2 or data.shape[1] != len(columns) or len(data) < 2:
                        raise ValueError("invalid export shape")
                    if np.iscomplexobj(data) or not np.all(np.isfinite(data)) or not np.all(np.diff(data[:, 0]) > 0):
                        raise ValueError("invalid export values or axis")
                    seen.add(identity)
                    key = (source.get("npy_sha256") or label, source.get("channel"), columns)
                    groups.setdefault(key, []).append((f"{label}: {item['name']}", data[:, [0, column]]))
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    sections.append(f'<p class="warning">{escape(label)}: curve unavailable: {escape(str(exc))}</p>')
        except (OSError, ValueError, TypeError, KeyError) as exc:
            sections.append(f'<p class="warning">{escape(label)}: analysis unavailable: {escape(str(exc))}</p>')
    for (_, _, columns), curves in groups.items():
        _, x_label, units = COLUMNS[columns]
        sections.append(curves_svg(curves, x_label, units))
    if entries and not groups:
        sections.append("<p>No usable curve exports / 没有可用的曲线导出</p>")
    return "".join(sections)


def write_analysis_report(paths: list[Path], output: Path) -> Path:
    entries = [entry for root in paths for entry in analysis_entries(root.resolve())]
    output = output.resolve()
    if output.exists():
        raise ConfigError("analysis report output must be a new file")
    if any((parent / "metadata.json").is_file() for parent in output.parents):
        raise ConfigError("analysis report must not modify a source capture package")
    for root, _, artifact in entries:
        try:
            manifest = json.loads(artifact_file(root, artifact["analysis_pipeline"]["manifest"]).read_text())
            package = Path(manifest["source"]["package"])
            package = package if package.is_absolute() else root / package
            if output.is_relative_to(package.resolve()):
                raise ConfigError("analysis report must not modify a source capture package")
        except (OSError, ValueError, KeyError, TypeError):
            pass  # Broken manifests are displayed as per-entry errors below.
    html = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<title>Signal processing</title><style>body{max-width:1100px;margin:2em auto;font:16px sans-serif}svg{width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere}.warning{color:#b45309}</style>'
            '<h1>信号处理 / Signal processing</h1>' + render_analysis_sections(entries) + '</html>')
    output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(output, html.encode("utf-8"))
    return output
