# 启动只读 MCP 服务

WaveBench HTTP MCP 服务提供本机或受控网络中的离线信息和只读仪器观察。它不提供 raw SCPI、输出控制或 run 执行。

## 启动服务

```bash
python -m wavebench mcp serve \
  --config wavebench.toml \
  --token-env WAVEBENCH_MCP_TOKEN
```

服务默认监听 `127.0.0.1:8765`。认证 token 必须通过 `--token` 或 `--token-env` 提供；不要把 token 值写入配置、命令历史或公开文档。服务拒绝监听 `0.0.0.0` 或 `::`。

## 端点与工具

| 入口 | 认证 | 行为 |
| --- | --- | --- |
| `GET /health` | 不需要 | 服务健康检查。 |
| `GET /tools` | Bearer token | 列出只读工具。 |
| `POST /call`、`POST /mcp` | Bearer token | 调用 MCP 工具或 JSON-RPC 方法。 |

请求体上限为 1 MiB。所有工具都是只读的，不会改变仪器状态，也不会读取波形。工具的权威列表和元数据以 `GET /tools` 返回为准：

| 工具 | 行为 |
| --- | --- |
| `run.schema` | 返回 run plan schema。 |
| `run.check` | 只接受项目内的 `plans/*.toml`，离线解析并校验 run plan。 |
| `capture.inspect` | 只读取项目内的离线采集包摘要。 |
| `doctor.config` | 对配置中的仪器执行只读 doctor 检查，返回结构化记录。 |
| `scope.observe` | 读取示波器身份、每通道状态快照和输入耦合安全；不读取波形。 |
| `scope.advise` | 基于只读状态快照和调用方给出的 `expected_frequencies_hz` 建议显示/采集参数；不应用建议。 |

`doctor.config`、`scope.observe` 和 `scope.advise` 是实验性工具：开发线已实现并有离线测试，但尚未随正式版本发布，
支持范围不作承诺。

`scope.observe` 和 `scope.advise` 不读取示波器波形。波形摘要、期望值检查和跨通道关系分析需要
先显式读取波形（属于会改变仪器状态的写路径），请在操作者明确执行
[`scope observe --fetch-waveform`](../reference/cli.md) 时进行，不通过 MCP 暴露。

## Verification

访问 `GET /health` 确认服务已启动。需要调用受保护端点时，使用 Bearer token；认证失败或路径不在允许范围内时，先检查调用方配置，不要放宽监听地址或删除认证。
