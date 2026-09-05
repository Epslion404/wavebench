# 运行产物 Reference

## 独立离线分析

`analysis run` 在新输出目录写入 `analysis.json`、`manifest.json`、`metrics.json` 和 `exports/`。`analysis.json` 使用 `wavebench.analysis.v1`，包含总体状态、WaveBench 版本、规范化配方及其 SHA-256、来源与处理结果。manifest 使用 `wavebench.offline_pipeline.v1`，复用 stage 与数值字段；派生路径以该分析目录为基准。

离线来源记录 capture package 绝对路径、通道、包内相对 NPY 路径及原始摘要。来源没有状态字段时记录 `null`，不推断为采集成功。不生成虚构 run 或采集 step，既有 RunPlan 的产物 schema 与来源路径合同保持不变。

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

`manifest.json` 的 schema 为 `wavebench.analysis_pipeline.v1`。它记录来源 step 及状态、来源 capture package／metadata／NPY 的 run-relative POSIX 路径、原始 NPY 的 SHA-256、规范化算子、逐阶段状态、采样信息、窗与相干增益、警告、导出、数值定义和结构化错误。某个后续算子失败时，已经完成的导出和 filter stage 元数据会保留，并由 `partial` 和 `failed_stage` 标明部分结果。

存在成功 filter stage 时，manifest 条件性增加 `filters` 数组，并在对应 stage 中记录同一份滤波元数据。FIR 项包括响应、截止频率、tap 数、实际采样率、SciPy 版本、设计窗、缩放方式、执行函数、遍数、边界规则和单程名义群延迟。`coefficients_sha256` 是实际 FIR 系数转为 little-endian float64 连续字节后的 SHA-256。零相位 FIR 另外记录固定的 `method`、`padtype` 和 `padlen`。

IIR 项记录 design、响应、截止频率、原型阶数、变换后的数字滤波器阶数、实际采样率、设计函数、SOS section 数、SciPy 版本、稳定性、最大极点模、执行函数、遍数和边界规则。`sos_sha256` 是实际 SOS 转为 little-endian float64 连续字节后的 SHA-256；Chebyshev／Elliptic 的纹波或衰减参数只在适用时出现，零相位 IIR 另外记录实际 `padtype` 和 `padlen`。manifest 不写入完整 FIR 系数或 SOS。没有成功 filter stage 的既有流水线不增加 `filters` 字段。

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

时域 NPY 和 CSV 固定为 `time_s,voltage_v` 两列。FFT 频域 NPY 和 CSV 固定为 `frequency_hz,real_v,imaginary_v,amplitude_v` 四列。PSD NPY 和 CSV 固定为 `frequency_hz,psd_v2_per_hz` 两列。每个导出记录文件路径、列名和 SHA-256；路径相对于 run 目录并使用 POSIX 分隔符。来源 NPY 保持原样，处理器只读取 capture package 内经过边界校验的文件。

成功执行 PSD 时，manifest 条件性增加 `psd` 对象，并在对应 stage 中记录同一份元数据，输出域为 `psd`。该对象包括规范化参数、执行函数、SciPy 版本、实际采样率、周期窗标记、窗功率增益、窗 SHA-256、完整分段数和丢弃尾点数。窗 SHA-256 使用实际周期窗的 little-endian float64 字节计算。`bin_spacing_hz` 为采样率除以 `nfft`；`segment_frequency_scale_hz` 为采样率除以 `nperseg`，不表示加窗后的等效噪声带宽。

PSD 元数据同时记录单边密度缩放、`V^2/Hz` 单位和归一化公式。仅有一段或存在尾点时写入警告；后续导出失败仍保留成功 PSD 的元数据。没有成功 PSD 的流水线不增加 `psd` 字段，schema 继续使用 `wavebench.analysis_pipeline.v1`。PSD 不产生新的标量指标，未选择时域测量时 `metrics` 为空映射。HTML 报告显示 Welch 分段参数、警告和导出链接。

频域 `amplitude_v` 是单边峰值幅度，不是 RMS。`noise_floor_v` 是排除 DC 与主峰后的非 DC 幅度 bin 中位数，表示每 bin 峰值幅度，不表示积分噪声。THD 使用 Nyquist 范围内的 H2～H5。

HTML 报告在存在分析 step 时增加「信号处理 / Signal processing」区域。FIR 算子显示 family、响应、截止频率、tap 数和执行模式；IIR 算子显示 family、响应、截止频率、design、order 和执行模式。报告 manifest 条件性增加 `analysis_pipelines`，没有分析 step 的旧报告 manifest 不增加该字段。

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
