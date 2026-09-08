# 信号处理流水线开发状态

本页供开发者核对信号处理的实施范围、验证边界与剩余交付工作。状态核对日期为 2026-09-08；对应 Core 开发提交 `7cd0a61` 及此前流水线提交。

**状态：Proposed / Future，开发分支已实现，尚未发布。** MVP 至 Phase 6、资源控制 R0～R3 的约定实现已完成；平台验收与发布准备尚未全部完成。

## 已实现范围

| 阶段 | 已实现内容 | 合同入口 |
| --- | --- | --- |
| MVP | 独立 `analysis.pipeline`、稳定 step ID、硬件释放后的分析后缀、去直流／去趋势／三种窗／FFT／测量／导出、独立派生产物 | [RunPlan 合同](../reference/run-schema.md) |
| Phase 2 | FIR、IIR、显式因果／零相位模式、Welch PSD；SciPy 按需检查 | [算子合同](../reference/run-schema.md) |
| Phase 3～4C | 历史采集包离线分析、曲线比较、PSD 频带验收、多峰检测、平滑、重采样 | [离线分析产物](../reference/artifacts.md) |
| R0／R1 | 统一预算、FIR tap／FFT 长度预检、受限 NPY 读取、分块导出／哈希／报告读取 | [资源预算](../reference/run-schema.md) |
| R2 | mean Welch 逐段累计、因果 FIR／IIR 跨块传递状态 | [资源预算及算法证据](../reference/run-schema.md) |
| R3A／R3B | 可选独立进程、取消／超时、部分产物保存、Linux cgroup v2 与 Windows Job Object 后端 | [进程监督](../reference/run-schema.md) |
| Phase 5 | 显式频带与门限的 SNR／SINAD／SFDR、串行批量分析、摘要校验恢复与累计预算 | [高级谱质量](../reference/run-schema.md)、[批量分析](../reference/run-schema.md) |
| Phase 6 | 独立 `analysis.pair`、同步证据校验、整数时延、H1／相干性、有效区掩码、报告与 RunPlan 集成 | [双通道分析](../reference/run-schema.md) |
| 真实同步采集适配 | 可选 `scope.capture_synchronized`、同次冻结采集证明、真实包双通道离线分析 | [同步证据](../reference/artifacts.md) |

分析保持原始文件只读，派生数据写入独立目录。旧 `capture inspect --fft` 与 `expect_fft` 保持原算法；新流水线的前处理由配方显式声明。

## 示例与验证入口

[示例目录](https://github.com/Scaxlibur/wavebench/blob/Scaxlibur/feat/signal-processing-pipeline/plans/README.md)包含完整说明，按用途选择：

- [完整 RunPlan](https://github.com/Scaxlibur/wavebench/blob/Scaxlibur/feat/signal-processing-pipeline/plans/example_signal_processing_pipeline.toml)：一次采集后分别演示滤波 FFT、平滑／重采样／多峰和 PSD 频带验收。
- [独立处理配方](https://github.com/Scaxlibur/wavebench/blob/Scaxlibur/feat/signal-processing-pipeline/plans/example_processed_recipe.toml)：对历史包重复分析，生成独立产物。
- [高级指标、批量与双通道演示](https://github.com/Scaxlibur/wavebench/blob/Scaxlibur/feat/signal-processing-pipeline/plans/README.md)：包含合成数据生成、离线处理及报告命令。
- [真实同步采集 RunPlan](https://github.com/Scaxlibur/wavebench/blob/Scaxlibur/feat/signal-processing-pipeline/plans/example_synchronized_pair.toml)：依赖支持同步采集的驱动和事先准备好的外部激励。
- [资源配置](https://github.com/Scaxlibur/wavebench/blob/Scaxlibur/feat/signal-processing-pipeline/plans/example_analysis_resources.toml)与[执行配置](https://github.com/Scaxlibur/wavebench/blob/Scaxlibur/feat/signal-processing-pipeline/plans/example_analysis_execution.toml)：分别设置预算和进程监督。

示例链接指向开发分支，需在对应提交推送后才能通过远端查看；本地检出可直接读取仓库中的 `plans/`。

真实采集示例会操作示波器。离线分析、报告和合成示例无需连接仪器；资源／执行配置与处理配方不是 RunPlan，须使用对应入口。

## 验证记录与边界

- 最近产品代码的 Linux 全量回归：2443 passed、4 skipped、221 subtests passed；RTM 插件包回归为 205 passed。生成文档、Ruff、文案检查、文档审计和 MkDocs 严格构建均通过。这是既有验收记录，不表示每次文档修改都重跑全量测试。
- 历史实机包已验证单通道高级指标、批量恢复和报告；缺少同步证据的双通道包按合同拒绝。
- 真实同步采集已完成代表性对照，并用保存的采集包完成双通道分析与报告，原始文件摘要不变。具体型号、固件、采集模式及验证范围由[插件同步采集文档](https://github.com/Scaxlibur/wavebench-instrument-plugins/blob/4a45880/packages/wavebench-rohde-schwarz-rtm2000/doc/RTM2000_SYNCHRONIZED_CAPTURE.md)维护；该提交的验证不构成其它型号或精密通道校准承诺。
- R3 的 Windows 原生测试留待 PR workflow；当前本机没有可写 cgroup 委派，真实 cgroup 测试跳过，已有受控测试与拒绝路径验证。预算估算不等于操作系统硬内存保证；平台后端能力不足时拒绝启用硬限额。
- median Welch、零相位滤波、全局峰属性和整段 FFT 仍按预算执行，未实现通用外存算法。这符合 R2 的范围，不承诺任意大数据都能处理。

## 剩余交付工作

1. 在后续 PR 执行 Windows CI，并在具备委派权限的 Linux 环境验证真实 cgroup 限制。
2. 正式合并／发布前核对 Core 与插件版本、兼容关系及最终提交的检查结果。
3. 扩展真实采集的固件、记录模式或型号时补充对应证据；通用源恢复在 USER 状态下的能力衔接问题另行处理，当前仍须遵守示例中的恢复限制。

处理图、任意 Python 回调、算子插件、跨设备时钟同步、自动 deskew、MIMO、实时流处理、GPU 和分布式执行未纳入本轮交付。是否扩展由后续需求决定。
