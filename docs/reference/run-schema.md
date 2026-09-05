# run plan Reference

## 独立离线配方

`analysis` 命令直接处理历史 capture package，不需要仪器配置。显式选择一个通道，配方只包含 `schema = "wavebench.analysis_recipe.v1"`、`operations` 和可选 `[expect]`，共用下文的算子与验收合同。示例为 `plans/example_analysis_recipe.toml`。

```bash
wavebench analysis check --capture data/capture --channel 1 --recipe plans/example_analysis_recipe.toml
wavebench analysis run --capture data/capture --channel 1 --recipe plans/example_analysis_recipe.toml --output data/analysis_trial_1
```

输出必须是新的独立目录，不能位于来源 capture package 或既有 run 内。再次分析应使用另一个输出目录。来源读取或算子失败写入分析产物；配置和输出目录不合法时在执行前拒绝。验收失败时命令返回非零状态。

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
  { op = "filter", family = "iir", design = "butterworth", response = "highpass", cutoff_hz = 20.0, order = 4, mode = "causal" },
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
| `filter` | FIR 或 IIR 的判别式设计参数 | 时域 → 时域 |
| `smooth` | 方法、奇数窗口长度、模式和边界，见下文 | 时域 → 时域 |
| `window` | `name = "hann|hamming|blackman"` | 时域 → 时域 |
| `fft` | 无 | 时域 → 频域 |
| `psd` | Welch 分段参数，见下文 | 时域 → PSD |
| `measure` | 非空 `metrics` 数组 | 观察当前域，不改变数据 |
| `measure_band` | `name`、`band_hz`、`exclude_hz`、`metrics` | 观察 PSD，不改变数据 |
| `peaks` | 命名检测、筛选条件和数量上限，见下文 | 观察当前域，不改变数据 |
| `export` | 安全的 `name`；`formats` 为 `npy`、`csv` 的非空子集 | 导出当前域，不改变数据 |

`remove_dc`、`detrend`、`window` 和 `fft` 各至多出现一次；`remove_dc` 与 `detrend` 互斥。`filter` 可以重复，从而按声明顺序串联多个 FIR／IIR stage。去直流、去趋势和滤波必须位于窗口之前，所有时域变换必须位于 FFT 之前。测量指标和导出名称在同一流水线内不得重复，流水线至少包含一个 `measure` 或 `export`。

### 时域平滑

```toml
{ op = "smooth", method = "moving_average", window_length = 5, mode = "causal", boundary = "edge" }
{ op = "smooth", method = "savgol", window_length = 11, polyorder = 2, mode = "centered", boundary = "reflect" }
```

`method`、`window_length`、`mode` 和 `boundary` 必填。窗口为 3～1001 的奇数，输入必须等间隔且长度不小于窗口。`savgol` 另需 `polyorder`，为 0～5 且小于窗口长度的整数；移动平均不接受该字段。平滑必须在整段 window、FFT 和 PSD 之前，可串联多个 stage。

`centered` 使用左右等长窗口，边界可选 `reflect`（不重复端点的反射）或 `edge`（首末值延拓）。`causal` 仅使用当前及过去样本，起始处只允许 `edge`，不允许引入未来样本的反射。输出样本数和时间轴不变，不自动补偿延迟。

移动平均各点等权；因果模式名义群延迟为 `(window_length - 1) / 2` 个样本。Savitzky–Golay 使用零阶导数系数，居中模式在窗口中点评价，因果模式在末点评价；因果模式不声明固定群延迟。系数非有限或常量增益校验失败时明确失败，不静默修正。移动平均仅使用 NumPy，Savitzky–Golay 按需检查 SciPy 的 `savgol_coeffs`。

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

Plan 包含 FIR、IIR 或 PSD 算子时，`run check` 检查 SciPy；缺少依赖时会在租约、session 和仪器 I/O 之前失败。仅使用 NumPy 算子的流水线不需要 SciPy。

### IIR 滤波

IIR 继续使用同一个 `filter` 算子。`family = "iir"` 时必须声明 `design` 和 `order`：

```toml
{ op = "filter", family = "iir", design = "butterworth", response = "lowpass", cutoff_hz = 5000.0, order = 4, mode = "causal" }
{ op = "filter", family = "iir", design = "chebyshev1", response = "highpass", cutoff_hz = 100.0, order = 4, ripple_db = 1.0, mode = "zero_phase" }
{ op = "filter", family = "iir", design = "chebyshev2", response = "bandpass", cutoff_hz = [100.0, 5000.0], order = 6, attenuation_db = 40.0, mode = "causal" }
{ op = "filter", family = "iir", design = "elliptic", response = "bandstop", cutoff_hz = [49.0, 51.0], order = 6, ripple_db = 1.0, attenuation_db = 60.0, mode = "zero_phase" }
```

参数按 `design` 严格区分：

- `butterworth` 不接受 `ripple_db` 或 `attenuation_db`。
- `chebyshev1` 必须且只接受 `ripple_db`。
- `chebyshev2` 必须且只接受 `attenuation_db`。
- `elliptic` 必须同时接受 `ripple_db` 和 `attenuation_db`，且纹波必须小于衰减。
- `order` 必须是 1～12 的整数；`ripple_db` 位于 `(0, 20]`，`attenuation_db` 位于 `(0, 200]`。

四种设计都支持低通、高通、带通和带阻，并固定使用 SciPy 的 SOS 输出。设计后会校验二阶节形状、有限系数、单位化分母和所有极点严格位于单位圆内。`order` 对低通／高通表示数字滤波器阶数，对带通／带阻表示原型阶数；带型变换后的数字滤波器阶数为 `2 * order`。

单程临界频率的含义随设计而异：Butterworth 是 `-3 dB` 点；Chebyshev I 和 Elliptic 是通带纹波边缘；Chebyshev II 是阻带衰减边缘。`cutoff_hz` 始终表示单程 SciPy 设计参数，零相位输出不会重新把它解释为最终 `-3 dB` 点。

`causal` 使用 `sosfilt` 和全零初始状态。`zero_phase` 使用 `sosfiltfilt` 与固定奇延拓；padding 长度根据实际 SOS 明确计算并写入 manifest，输入点数必须大于该长度。零相位的有效幅频响应仍是单程幅频响应的平方。两种模式都保留原时间轴和样本数。

FIR、IIR 和 PSD 共用 `.[analysis]` 可选依赖。`run check` 根据 Plan 实际选择的算子参数检查所需 SciPy 函数。

### Welch 功率谱密度

PSD 算子将时域数据转换为单边功率谱密度，单位为 `V²/Hz`。全部参数必须显式声明：

```toml
{ op = "psd", method = "welch", window = "hann", nperseg = 256, noverlap = 128, nfft = 256, detrend = "none", average = "mean" }
{ op = "export", name = "density", formats = ["npy", "csv"] }
```

| 参数 | 合同 |
| --- | --- |
| `method` | 固定为 `welch` |
| `window` | `hann`、`hamming` 或 `blackman`，每段使用周期窗 |
| `nperseg` | 每段样本数，整数且至少为 4 |
| `noverlap` | 相邻段重叠样本数，整数且满足 `0 <= noverlap < nperseg` |
| `nfft` | 每段 FFT 长度，整数且不小于 `nperseg`；较大值只做补零 |
| `detrend` | `none`、`constant` 或 `linear`，在每段加窗前执行 |
| `average` | `mean` 或经过偏差修正的 `median` |

PSD 可以跟在去直流、去趋势或 FIR／IIR 之后，但不能跟在整段 `window` 或 `fft` 之后。每条流水线至多有一个 PSD；PSD 之后允许 `export`、`measure_band` 和 `peaks`，至少执行其中一个。需要同时生成 FFT 和 PSD 时，使用两个分析 step 引用同一个 capture。PSD 的频带测量不复用 FFT 的峰值幅度、THD 或噪声底。

### 通用峰值检测

```toml
{ op = "peaks", name = "tones", polarity = "positive", height = 0.01, prominence = 0.01, distance = 10, width = 0, max_peaks = 20, metrics = ["count"] }
```

所有字段必填。`height`、`prominence`、`width` 非负，零值表示不设对应下限；`distance` 必须为正；`max_peaks` 为 1～10000 的整数。时域支持 `positive`、`negative` 和 `both`，FFT／PSD 只允许 `positive`。极性表示局部极大／极小方向，不保证电压绝对正负；例如负直流偏置上的局部极大值，在 `height = 0` 时也会保留。负峰在电压取反后检测，结果仍保存原始电压。高度和显著性单位随域为 V 或 V²/Hz；距离和宽度单位在时域为秒、频域为 Hz。时域要求等间隔采样。

先按高度、显著性、半显著性宽度筛选，再以带极性的高度降序、位置升序确定间隔竞争和输出顺序，最后截断至上限。负峰的高度按取反后的值排序，正负峰共同竞争间隔。端点不视为峰，平台峰选择中间样本，偶数长度时取靠前样本；不对峰位置做亚 bin 插值。峰宽使用半显著性高度的插值交点。

`metrics = ["count"]` 显式生成 `<name>_count`，表示截断前、筛选后峰数量，可用于 `expect`。峰列表写入独立 JSON／CSV；空列表是合法结果。检测不改变信号数据域，可继续变换、测量或导出。报告只在峰表的信号摘要与绘制曲线一致时标记峰。

### PSD 频带验收

```toml
{ op = "measure_band", name = "audio", band_hz = [20, 20000], exclude_hz = [[990, 1010]], metrics = ["mean_square_v2", "rms_v", "noise_rms_v"] }
```

所有字段必填；没有排除频带时显式填写 `exclude_hz = []`。频带边界必须非负且严格递增，排除区间必须位于测量频带内；运行时测量频带不得超过实际 Nyquist。按闭区间选择 bin 中心，并按闭区间排除；结果为剩余 bin 密度之和乘 bin 间距，DC 与偶数点 Nyquist 均使用完整 bin 权重。空结果写入 `null` 和警告。

`mean_square_v2` 单位为 V²，`rms_v` 为其平方根，单位为 V；没有负载信息时不转换为瓦特。`noise_rms_v` 使用同一积分，但要求显式提供非空信号排除区间，其噪声含义依赖声明的频带选择。所有指标均应用 `exclude_hz`；需要未排除的带内 RMS 时另建一个名称不同的测量。

上例产生 `audio_mean_square_v2`、`audio_rms_v` 和 `audio_noise_rms_v`，可在 `[steps.expect]` 或离线配方 `[expect]` 中使用 min/max。命名测量不可重名。积分沿用所选 Welch 均值或中位数估计，不额外重标定。

运行时按实际时间轴检查等间隔采样，容差为 `rtol=1e-6, atol=0`。样本数小于 `nperseg` 时失败，不自动缩短段长。只处理完整段；不足一段的尾点不补齐，并在 manifest 中记录数量。仅有一段时仍可导出，同时记录没有跨段平均的警告。

数值实现使用 [SciPy Welch](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.welch.html)，固定 `scaling="density"` 和单边输出。每段按 `sample_rate_hz × sum(window²)` 归一化，仅将非 DC、非偶数点 Nyquist 的 bin 功率乘 2。`detrend="none"` 不隐式去直流。频率 bin 间距为 `sample_rate_hz / nfft`，补零不会改善由段长与窗决定的分辨能力。

时域指标为 `voltage_min_v`、`voltage_max_v`、`voltage_mean_v`、`voltage_rms_v` 和 `voltage_vpp_v`。频域指标为 `peak_frequency_hz`、`peak_amplitude_v`、`noise_floor_v`、`thd_ratio`，以及 `harmonic_2`～`harmonic_5` 的 `frequency_hz` 和 `amplitude_v` 字段。`[steps.expect]` 只能引用流水线中已显式选择的测量指标。

完整示例见 `plans/example_signal_processing_pipeline.toml`。数值定义和派生产物结构见[运行产物 Reference](artifacts.md)。旧 `scope.capture` 的 `expect_fft` 保持原有算法，不由新流水线重定义。
