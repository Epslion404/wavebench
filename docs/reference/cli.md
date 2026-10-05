# CLI Reference

本页说明 WaveBench CLI 的入口、输出和副作用边界。完整参数以当前安装版本的 `python -m wavebench --help` 及各子命令 `--help` 为准。

## Synopsis

```bash
python -m wavebench --help
python -m wavebench run --help
python -m wavebench --json <domain> <command> ...
```

一级命令域包括 `scope`、`source`、`rf-source`、`power`、`dmm`、`sweep`、`run`、`capture`、`mcp`、`tui`、`net`、`doctor`、`plugin`、`capability` 和 `lock`。`run` 的当前子命令由 `python -m wavebench run --help` 输出。

## 副作用

| 类别 | 命令族 | 行为 |
| --- | --- | --- |
| 离线、只读本地文件 | `run schema`、`run check`、`run intent`、`run compare`、`run resume`、`capture inspect`、`capability explain`、`lock status` | 不连接仪器。 |
| 离线且可能写本地文件 | `run template --output`、`run report`、`run report-index` | 不连接仪器，但会创建模板、报告或索引文件。 |
| 连接读取或预检 | `doctor`、状态／身份查询、`scope observe`（不带 `--fetch-waveform`）、`run verify` | 会访问配置的仪器，不应改变实验设置。 |
| 可能改变状态或触发采集 | 输出和 setter、`scope auto`／`scope capture`、`scope observe --fetch-waveform`、非 fake TUI、`run plan` | 可能写入仪器、触发采集或切换输出。 |

`scope fetch` 读取已有波形，但仍是仪器 I/O；不要把它当作离线命令。每次硬件操作前确认接线、输入阻抗、输出状态和安全限制。WaveBench 不会自动执行 `*RST`，也不会因设置电压、幅度或频率而自动开启输出。

`scope observe` 是实验性命令：开发线已实现并有离线测试，但尚未随正式版本发布，兼容性和支持范围不作承诺。
它默认只读：只查询身份、通道状态快照和输入耦合安全，不读取波形。
`scope observe --fetch-waveform` 是显式写路径，可能停止正在运行的采集、修改波形传输
source/mode/format/points 并打开通道显示；它逐通道读取波形，因此多通道结果不保证来自同一次
acquisition，此时跨通道的相位、相关性、延迟和交点不会被计算（`correlation`、`intersections`
返回 `skipped`，相位为 `null`）。需要驱动可证明的同一次采集时，使用
`scope capture --synchronized`（通道和输出格式要求见其 `--help`）。

期望值检查通过 `--expect <file.toml>` 提供，需要 `--fetch-waveform`：

```toml
[channels.1]
frequency_hz = 1000
frequency_tolerance_ratio = 0.05
vpp_v = 3.3
duty_percent = 50
```

字段名、类型、有限性和取值范围由实现严格校验（`src/wavebench/data/expectations.py` 的 `validate_expectation()`）；
拼错字段名会直接报错，不会被静默忽略。上面的 TOML 只示范格式，字段全集以该实现为准。任何输入错误都在
打开仪器会话之前被拒绝，因此不会产生仪器写入。

`--target-cycles` 和 `--target-vertical-divisions` 必须为有限正数，并在加载配置或创建仪器服务
之前校验。生成的 focus 建议使用 `--vertical-scale CHANNEL=V_PER_DIV`；隐藏其他通道的参数为
`--hide-others`。建议不会自动执行。

期望值汇总的 `channels` 保留所有待验收通道。波形读取、安全检查或期望值计算失败的通道标记为
`unavailable`；没有可用检查结果时汇总为 `unavailable`，部分通道已有 `pass`／`warn` 结果时为
`partial`。已确认的 `fail` 仍优先返回 `fail`。未提供期望值或期望值没有可执行指标时保持 `skipped`。

## JSON 输出与退出码

将 `--json` 放在命令行任意位置可请求机器可读输出。成功结果使用 `wavebench.cli.result.v1`，包含 `status`、`exit_code` 和 `result`；错误使用 `wavebench.error.v1`。普通成功输出写入标准输出，普通错误写入标准错误。

错误分类和稳定退出码见[错误 Reference](errors.md)。`run plan` 即使保留了运行产物，只要存在失败 step 也会返回非零状态。

## 相关页面

- [无硬件快速开始](../getting-started/quickstart.md)
- [配置实验台](../getting-started/configure-bench.md)
- [执行一次实验](../how-to/run-an-experiment.md)
- [run plan Reference](run-schema.md)
- [插件 Reference](plugins/index.md)
