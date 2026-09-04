# 运行产物 Reference

本页说明 `run plan` 写入的运行产物入口。字段的 machine source 是 `src/wavebench/services/run_artifacts.py` 和对应的 typed result；不要从旧 Guide 推断新增或可选字段。

## 输出

成功或失败的 run 在写入运行目录后会产生以下文件：

```text
<run-dir>/
  plan.toml            原始 plan 存在时的副本
  run.json             运行级结构化记录
  summary.csv          面向快速查看和表格导入的摘要
  steps/
    00_<kind>.json     单个 step 记录
  processing/          仅在存在 analysis.pipeline 时生成
    01_<step-id>/
      manifest.json
      metrics.json
      exports/
```

`run report <run-dir>` 只读取已有产物并生成离线报告，不连接仪器，也不修改原始采集数据；它会在运行目录或显式输出位置写入派生的 HTML，使用 `--pdf` 时还会写入 PDF。

## `run.json`

以下字段始终由 writer 写入：

| 字段 | 含义 |
| --- | --- |
| `status` | 运行级状态。 |
| `experiment` | plan 中的实验名称和标签。 |
| `plan` | plan 文件路径字符串。 |
| `steps` | 每个已记录 step 的结构化记录。 |

`error`、`restore`、`provenance`、`source_operations` 和 `rf_source_operations` 仅在对应条件满足时出现。`source_operations` 与 `rf_source_operations` 只接受带有已知 schema 的类型化 operation artifact。

## step 记录

每个 `steps/<index>_<kind>.json` 记录包含 `index`、`kind`、`status`、`fields` 和 `artifact`。step 声明 ID 时还会包含 `id`；没有 ID 的旧记录不增加该字段，文件名仍保持原格式。具体 `artifact` 形状取决于 step；采集、频响、DMM、Source V2、RF Source 和离线分析不共享一张人工字段表。

## 信号处理派生产物

每个 `analysis.pipeline` step 使用独立目录：

```text
<run-dir>/processing/<index>_<step-id-or-analysis_pipeline>/
  manifest.json
  metrics.json
  exports/
    <name>.npy
    <name>.csv
```

`manifest.json` 的 schema 为 `wavebench.analysis_pipeline.v1`。它记录来源 step 及状态、来源 capture package／metadata／NPY 的 run-relative POSIX 路径、原始 NPY 的 SHA-256、规范化算子、逐阶段状态、采样信息、窗与相干增益、警告、导出、数值定义和结构化错误。某个后续算子失败时，已经完成的导出会保留，并由 `partial` 和 `failed_stage` 标明部分结果。

`metrics.json` 的 schema 为 `wavebench.analysis_metrics.v1`，结构如下：

```json
{
  "schema": "wavebench.analysis_metrics.v1",
  "metrics": {
    "peak_frequency_hz": 1000.0,
    "thd_ratio": null
  }
}
```

指标值只写有限 JSON 数字或 `null`，不写 `NaN`、`Infinity`。step artifact 的 `metrics` 保留同一份小型映射；`expect` 继续使用既有 `{ min, max }` 结果结构，因此 `summary.csv` 的 expectation 列和 HTML 验收表不需要另一套解释。

时域 NPY 和 CSV 固定为 `time_s,voltage_v` 两列。频域 NPY 和 CSV 固定为 `frequency_hz,real_v,imaginary_v,amplitude_v` 四列。每个导出记录文件路径、列名和 SHA-256；路径相对于 run 目录并使用 POSIX 分隔符。来源 NPY 保持原样，处理器只读取 capture package 内经过边界校验的文件。

频域 `amplitude_v` 是单边峰值幅度，不是 RMS。`noise_floor_v` 是排除 DC 与主峰后的非 DC 幅度 bin 中位数，表示每 bin 峰值幅度，不表示积分噪声。THD 使用 Nyquist 范围内的 H2～H5。

HTML 报告在存在分析 step 时增加「信号处理 / Signal processing」区域，并在报告 manifest 中条件性增加 `analysis_pipelines`。没有分析 step 的旧报告 manifest 不增加该字段。

## `summary.csv`

当前 writer 的列顺序为：

```text
index, kind, status, package, metadata, quality_status, quality_warnings,
recovered, expect_status, expect_failures, expect_fft_status, expect_fft_failures
```

`summary.csv` 适合快速查看和表格导入。需要保留完整字段、条件字段或错误 evidence 的自动化工具应优先读取 `run.json` 和对应 step JSON。

## 多个 run 的离线索引

`run report-index` 读取一个或多个已有 run 目录，并在 `--output` 目录写入 `manifest.json`、`manifest.csv` 和 `index.html`。它不连接仪器；输出中的生成时间不应被当作实验时间或原始测量证据。

运行产物与 scope capture package 是不同层次的对象：run 目录记录 plan 和 step 关系，capture package 保存单次波形及其 metadata。需要分析具体采集字段时，以对应的 typed result、package loader 和 `metadata.json` 为准。

## 相关页面

- [执行一次实验](../how-to/run-an-experiment.md)
- [从模板到报告](../tutorials/from-template-to-report.md)
- [run plan 排错](../how-to/troubleshooting.md)
- [run plan Reference](run-schema.md)
