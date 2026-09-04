# run plan Reference

本页说明如何查询 WaveBench 当前支持的 run plan 结构。完整的 step、必填字段、可选字段和简要行为由离线命令生成，不在 Guide 中复制维护。

## Synopsis

```bash
python -m wavebench run schema
python -m wavebench run template --list
python -m wavebench run template <name> --print
```

三个命令都不连接仪器。`run schema` 输出顶层 TOML 表以及按 step kind 排序的字段清单；`run template --list` 列出当前模板；`--print` 将一个模板的 TOML 输出到标准输出。

当前安装版本的完整离线输出也以版本控制形式保存在[生成的 run plan schema](generated/run-schema.md)。该页由源码生成；本页只说明查询方式和使用边界。

## 使用顺序

1. 先运行 `run schema`，确认安装版本接受的 `kind` 和字段。
2. 再用 `run template --list` 选择接近实验目标的保守模板。
3. 使用 `run check` 检查实际 plan；它不能替代连接预检或实验台安全确认。

## 事实来源与边界

当前 schema 的 canonical source 是 `src/wavebench/services/run_plan.py` 中的 step schema 以及 `python -m wavebench run schema` 的输出。模板名称与默认内容来自 template registry。页面中的计划片段只能说明一个任务，不能作为完整字段表或型号 capability 的来源。

实际执行步骤、连接预检和副作用见[执行一次实验](../how-to/run-an-experiment.md)。字段错误和 schema 变更的排查见[run plan 排错](../how-to/troubleshooting.md)。

## 稳定 step ID 与离线分析

每个 `[[steps]]` 都可以声明结构字段 `id`。ID 必须匹配 `^[a-z][a-z0-9_-]{0,63}$`，并在同一个 plan 内唯一；没有 ID 的既有 plan 无需迁移。`id` 不属于 step 的执行参数，因此不会出现在 `RunStep.fields` 中。

`analysis.pipeline` 使用稳定 ID 引用同一 plan 内更早的 `scope.capture`：

```toml
[[steps]]
id = "capture_main"
kind = "scope.capture"
channel = 1
save_npy = true
on_failure = "continue"

[[steps]]
id = "spectrum_main"
kind = "analysis.pipeline"
source = { step = "capture_main" }
operations = [
  { op = "measure", metrics = ["voltage_mean_v", "voltage_rms_v", "voltage_vpp_v"] },
  { op = "remove_dc" },
  { op = "filter", family = "fir", response = "bandstop", cutoff_hz = [49.0, 51.0], numtaps = 101, mode = "zero_phase" },
  { op = "window", name = "hann" },
  { op = "fft" },
  { op = "measure", metrics = ["peak_frequency_hz", "peak_amplitude_v", "noise_floor_v", "thd_ratio"] },
  { op = "export", name = "spectrum", formats = ["npy", "csv"] },
]

[steps.expect]
peak_frequency_hz = { min = 990, max = 1010 }
thd_ratio = { max = 0.05 }
```

来源 capture 必须显式设置 `save_npy = true`。首版不接受历史 capture package 路径，也不接受其他 step 类型或后续 step 作为来源。所有 `analysis.pipeline` 必须形成 plan 的连续末尾部分；硬件步骤、恢复、会话关闭和租约释放完成后，才会执行离线分析。分析 step 支持 `on_failure`，不支持 step 局部 `safety_gate`，也不会触发硬件安全门。

## 算子合同

`operations` 是有序的 TOML 内联表数组。当前允许以下算子：

| 算子 | 参数 | 输入／输出域 |
| --- | --- | --- |
| `remove_dc` | 无 | 时域 → 时域 |
| `detrend` | `method = "linear"` | 时域 → 时域 |
| `filter` | `family = "fir"`、`response`、`cutoff_hz`、奇数 `numtaps`、`mode` | 时域 → 时域 |
| `window` | `name = "hann|hamming|blackman"` | 时域 → 时域 |
| `fft` | 无 | 时域 → 频域 |
| `measure` | 非空 `metrics` 数组 | 观察当前域，不改变数据 |
| `export` | 安全的 `name`；`formats` 为 `npy`、`csv` 的非空子集 | 导出当前域，不改变数据 |

`remove_dc`、`detrend`、`window` 和 `fft` 各至多出现一次；`remove_dc` 与 `detrend` 互斥。`filter` 可以重复，从而按声明顺序串联多个 FIR stage。去直流、去趋势和滤波必须位于窗口之前，所有时域变换必须位于 FFT 之前。测量指标和导出名称在同一流水线内不得重复，流水线至少包含一个 `measure` 或 `export`。

### FIR 滤波

FIR 算子同时支持四种响应：

- `lowpass`／`highpass` 使用单个有限正数 `cutoff_hz`。
- `bandpass`／`bandstop` 使用两个有限正数组成的严格递增数组 `cutoff_hz`。
- `numtaps` 必须是大于等于 3 的奇数。
- `mode` 必须显式设置为 `causal` 或 `zero_phase`。

实际采样率由来源 NPY 的时间轴计算。时间轴必须等间隔，所有截止频率必须严格低于 Nyquist 频率；这两个条件依赖采集结果，因此在离线分析 step 执行时校验并形成结构化产物。

`causal` 使用 Hamming 设计窗的 `scipy.signal.firwin` 和零初始状态的单向 `lfilter`，保留起始暂态及名义群延迟。`zero_phase` 固定使用 `filtfilt` 的奇延拓、`method = "pad"` 和 `padlen = 3 * numtaps`，因此至少需要 `3 * numtaps + 1` 个采样点。零相位模式的有效幅频响应为单向 FIR 幅频响应的平方；两种模式都保持样本数和时间轴，不自动裁剪或补偿时间。

FIR 需要可选分析依赖：

```bash
python -m pip install -e ".[analysis]"
```

只有 Plan 包含 FIR 算子时，`run check` 才检查 SciPy；缺少依赖时会在租约、session 和仪器 I/O 之前失败。未使用 FIR 的 NumPy 流水线不需要 SciPy。

时域指标为 `voltage_min_v`、`voltage_max_v`、`voltage_mean_v`、`voltage_rms_v` 和 `voltage_vpp_v`。频域指标为 `peak_frequency_hz`、`peak_amplitude_v`、`noise_floor_v`、`thd_ratio`，以及 `harmonic_2`～`harmonic_5` 的 `frequency_hz` 和 `amplitude_v` 字段。`[steps.expect]` 只能引用流水线中已显式选择的测量指标。

完整示例见 `plans/example_signal_processing_pipeline.toml`。数值定义和派生产物结构见[运行产物 Reference](artifacts.md)。旧 `scope.capture` 的 `expect_fft` 保持原有算法，不由新流水线重定义。
