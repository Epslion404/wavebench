# WaveBench advisor 插件类别 RFC

> 状态：`Draft`
> 目标：为插件体系增加第二个类别 `advisor`，并把它与安全相关的通用能力（数据外发同意门、
> decision artifact）收归 Core
> 动机实现：TypeSafe Jev（System One model）这类输入应用状态、输出结构化判断的模型；
> 答案类型包括 Choice、Score 和 Noul
> 实施状态：本文中的 advisor API、CLI、配置和 artifact 均为提案，尚未实现

## 摘要

现有插件体系只有"仪器插件"一类：`kind`、capability 前缀、descriptor 校验和生命周期后校验全部绑定
仪器语义，因此一个只做判断、没有 SCPI、没有型号的 advisor 无法注册。本 RFC 提出**插件类别
（category）**这一层抽象，并新增第二个类别 `advisor`；同时把两类与安全相关的通用能力收归 Core，
插件只提供实现。Core 不为任何厂商加特例分支。

## 约束

- 运行期离线：不引入必须联网才能工作的默认路径；CI 无网络、无 API key。
- 可审计：任何会让数据离开本机的动作必须显式、留痕、可复核。
- 确定性边界：概率型输出不得影响 `run.json.status`、质量门、`auto_recover`、capability/access policy。
- 归属：Core 只做通用抽象；型号、厂商、私有协议留在插件。
- 测试：全部离线、可确定性复现；插件契约在加载期可校验。
- 兼容：保持现有 V2 仪器插件契约、安装账本与已安装插件的行为。

## 核心实现现状

设计基线为 V2 可执行插件。以下位置以仓库实现为事实源，不依赖易变的行号。

| 位置 | 现状 |
| --- | --- |
| `src/wavebench/instruments/api.py` | `InstrumentDescriptor` 使用 `wavebench.instrument.v2`，要求仪器 `kind`、非空 `models`、`capabilities`、`backends` 和可调用的 `factory` |
| `src/wavebench/instruments/registry.py` | 从 `wavebench.instruments` 发现 descriptor，校验 driver 身份、版本门与 capability，再纳入 instrument registry |
| `src/wavebench/instruments/factory.py` | 为配置选中的 driver 构造 `DriverContext`，通过 descriptor 的 factory 获得执行对象 |
| `src/wavebench/plugins/package_inspect.py` | 源包和 wheel 必须声明 `wavebench.instruments` entry point，执行离线包检查 |
| `src/wavebench/plugins/lifecycle.py` | 安装后使用 V2 registry 的 descriptor 加载及校验路径验证插件 |

`wavebench.drivers` / `wavebench.instrument.v1` 是已弃用的 metadata 路径，只用于兼容展示。
它不是 advisor 的加载或执行基础；本提案保持既有兼容行为，不扩展 V1，也不移除其运行时。
当前路径与信任边界见[插件模型](../../concepts/plugin-model.md)和
[插件 Reference](../../reference/plugins/index.md)。

## 当前问题

1. advisor 没有仪器 kind、没有型号、没有 SCPI，无法通过上述任何一条路径注册；
2. 若把 `advisor` 加入 `PluginKind`，`models` 必填与 `"{kind}."` 前缀规则会被迫为它让路，两套语义
   互相污染；
3. 数据外发目前没有 advisor 的 Core 级契约：请求中的状态、问题、候选项和评分标准都可能包含业务数据；
4. 判断结果没有统一 artifact 契约，无法与 run 目录、审计流程对齐。

## 公共模型

### 插件类别

```text
category = instrument | advisor
每个类别自带：entry point group、加载对象契约、validator、能力命名规则
```

`instrument` 保持现状；`advisor` 使用独立 entry point group `wavebench.advisor` 与独立 API 版本门
`wavebench.advisor.v1`。首版不引入第三个类别。

### Advisor 插件契约

```python
AdvisorPlugin(
    advisor_id: str,                 # 稳定 id
    display_name: str,
    provider: str,
    capabilities: tuple[str, ...],   # 实现能力以 "advisor." 开头；external_state 由 Core 派生
    summary: str,
    egress: EgressDeclaration,
    factory: Callable[[AdvisorContext], Advisor],
    api_version: str = "wavebench.advisor.v1",
    package: str = "wavebench",
    origin: PluginOrigin = "builtin",
)

EgressDeclaration(
    transmits_off_machine: bool,
    allowed_state_fields: tuple[str, ...],   # 允许出现的 state.fields 键
    endpoint_hosts: tuple[str, ...],         # transmits_off_machine=False 时必须为空
    purpose: str,
)
```

不要求 `models` / `manufacturer` / `kind`；不要求 capability 与仪器 kind 对齐。

### 能力命名

| capability | 含义 |
| --- | --- |
| `advisor.route` | 后续候选：在代码拥有的封闭选项集里选一个入口 |
| `advisor.triage` | 对 run/采集给出有序档位或人工复核 |
| `advisor.cause` | 后续候选：从封闭原因表给出候选原因与概率 |
| `advisor.rank` | 后续候选：对候选集重排 |
| `advisor.external_state` | 声明会把 state 发到本机之外（触发同意门） |

Core 拥有这些名字与各自答案契约（Choice / Score / Noul 形状）；插件只提供实现。

首版只冻结 `advisor.triage` 与外发声明；`route`、`cause` 和 `rank` 是后续候选，不进入首版
capability 白名单。`advisor.triage` 使用 Choice 答案，Score / Noul 形状作为后续兼容边界记录。

### 首版判断任务与规则基线

首版任务是为已有采集报告提供人工复核建议：封闭选项为 `inspect_summary`、
`inspect_waveform`、`insufficient_evidence`。输入只读取现有离线产物，不查询仪器。
建议不改变验收结果，不开启下一次采集，也不执行建议中的任何命令。

Core 根据产物构造 `state.fields`：`capture_available`（布尔）、`quality_warnings`
（现有质量分析产生的字符串列表）、`operator_note`（可选字符串）。文件路径、完整波形和设备
身份不自动加入请求。问题与三个选项的判据由版本化任务模板提供。

run 模式的状态由 Core 从目标产物构造；显式补充输入只能提供 `operator_note`，不能覆盖
`capture_available` 或 `quality_warnings`。任务模板规定字段来源及采集步骤选择规则；多个
候选无法唯一选择时拒绝准备，不默认取第一个。选择信息保存于本地 target，并受授权模板约束。
独立 fixture 使用 standalone 目标，不因内容与某个 run 相似就继承该 run 的授权。

`rule_advisor` 的确定性基线：采集不可用时返回 `insufficient_evidence`；有质量告警时返回
`inspect_waveform`；其它情况返回 `inspect_summary`。例如：

```json
{"capture_available": true, "quality_warnings": ["low_cycle_count"], "operator_note": ""}
```

该输入的规则答案为 `inspect_waveform`。离线 fixture 至少覆盖采集缺失、有告警、无告警、
缺字段和非法类型；缺字段与非法类型在调用前拒绝。基线返回对应选项的 one-hot 概率，表示
确定性规则输出，不代表经过校准的统计置信度。

Core 验证套件使用同一任务模板、请求和答案 validator 测试 `rule_advisor` 与独立实现。
外部模型的效果另用人工标注的留出报告集比较：记录选项准确率、需复核样本的漏检率、概率
校准、耗时与失败率，不只比较返回的 confidence。外部实现至少保持基线的复核召回率，并
在标注集上改善建议准确率，才可主张优于基线；样本规模、容许误差及标注协议需在效果评估前
确定。内置规则只证明合同可执行，不能单独证明新增通用类别的必要性。

### 最小执行协议

`wavebench.advisor` entry point 返回一个 `AdvisorPlugin` descriptor，或无参数、返回该
descriptor 的函数。entry point 名必须等于 `advisor_id`；重复 id、未知 API 版本、未知
capability 和不可调用的 factory 在加载期拒绝。独立 advisor registry 校验 descriptor，
不复用仪器的 `models` 或 backend 要求。

下面是拟定的类型协议；所有 JSON 对象均按后文 schema 校验后冻结为不可变值。

```python
class Advisor(Protocol):
    def prepare(self, request: AdvisorRequest) -> PreparedRequest: ...
    def execute(self, prepared: PreparedRequest, context: CallContext) -> AdvisorResponse: ...

# AdvisorRequest: schema_version, call_id, target, task_id, task_version,
#                state, questions, requested_model, inference_parameters
# AdvisorContext: 不含仪器对象、transport 或凭据的本地配置
# PreparedRequest: 规范化请求、HTTP method/URL、语义 headers、精确 body bytes；离线实现无 HTTP 字段
# CallContext: 单调时钟 deadline、显式授权的凭据引用；不提供仪器控制接口
# AdvisorResponse: schema_version, call_id, reported_model, answers
```

`schema_version` 首版为 `wavebench.advisor.request.v1` / `wavebench.advisor.response.v1`。
`state` 为含 `fields` 和逐字段来源 `provenance` 的对象；`questions` 是问题数组，`answers`
是以 question id 为键的对象。target 和 Core 的 call_id 只用于本地审计与关联，不进入
provider body；响应中的 call_id 由适配器从本次执行上下文补入。provider body 不自动携带
run 身份或本地 call 路径。首版 triage 只包含 id 为 `triage` 的一个 Choice 问题，其答案示例如下：

```json
{
  "schema_version": "wavebench.advisor.response.v1",
  "call_id": "<core-generated-call-id>",
  "reported_model": "rule_advisor",
  "answers": {
    "triage": {
      "type": "choice",
      "choice": "inspect_waveform",
      "probabilities": {"inspect_summary": 0, "inspect_waveform": 1, "insufficient_evidence": 0},
      "confidence": 1,
      "confidence_definition": "deterministic-rule.v1"
    }
  }
}
```

导入、entry point 求值、factory 构造、`prepare()` 和 CLI 预览阶段均不得联网、访问仪器或
读取凭据。插件配置必须显式传入；实现不能隐式读取额外文件或环境变量作为请求数据。
`prepare()` 在本地完成供应商格式转换；Core 校验其请求 envelope 及 body，并将精确内容
用于预览。只有 Core 完成授权与审计后才调用 `execute()`，此时才解析凭据引用。

`execute()` 必须使用已准备的 method、URL、语义 headers 和原始 body bytes，禁止再次
序列化、补充上下文、改变模型、自动重试或跟随重定向。仅允许传输层补充 Content-Length 等
机械 headers，以及显式凭据引用对应的认证 header；认证内容不得承载额外业务数据。
响应必须覆盖全部 question id，禁止未知答案；Core 独立校验后才生成建议。

Core 将加载、构造、准备和执行放入可终止的独立 worker，按阶段施加有限的超时；调用方只能设置
有限正数 `timeout_s`，默认 30 秒，限定加载与准备阶段的合计预算，以及授权后执行阶段的预算，
不含等待人工确认。人工确认后核验准备内容再执行。超时或取消终止 worker，不后台继续等待结果，
不自动重试；已经发送的远程请求无法撤销，artifact 标记 `delivery_unknown`。
加载失败、准备失败、执行异常、超时和响应校验失败都作为结构化失败记录，不伪造答案或回退为成功。

这是一份可信本地插件合同。worker 用于超时控制，不提供任意 Python 代码的网络隔离；
恶意插件仍可能自行联网。包的信任、安装策略和代码审查是前置条件，同意门不能充当沙箱。

### 请求与答案校验

请求中的 task 与 question id 非空且唯一；状态字段必须符合版本化任务 schema，拒绝未知字段、
重复 JSON key、非法类型及非有限数字。每个问题包含 `id`、`type`、`instructions` 与
`options` 或 `levels`；候选 id 唯一，至少两个选项或等级，判据必须为字符串。
Score 等级使用按顺序编号的 `0..n-1`，不能由插件改变顺序。

| 答案类型 | 必填字段 | 校验与解释 |
| --- | --- | --- |
| Choice | `choice`、`probabilities`、`confidence`、`confidence_definition` | choice 必须属于候选集且为最高概率选项；probabilities 必须恰好覆盖全部选项 |
| Score | `score`、`probabilities`、`confidence`、`confidence_definition` | 概率键恰好覆盖全部等级；score 是等级的概率加权平均，须位于等级范围内 |
| Noul | `probability_yes` | 只有 yes 概率，无供应商 confidence；不得填充或伪造 confidence |

所有概率和 confidence 都必须是 `[0,1]` 内的有限数字，bool 不视为数字；概率分布总和与 1 的
差必须不大于 `1e-6`，Core 不静默归一化。Score 均值误差同样不大于 `1e-6`。
缺失答案、未知选项、额外 question、非法数值、无效分布或无法识别的
`confidence_definition` 均使整次响应为 `invalid_response`，不接受部分答案。

供应商的 confidence 与最高概率不是同一指标。TypeSafe Jev 的 Choice / Score 返回
confidence，Noul 只返回概率，见[官方定义](https://docs.typesafe.ai/confidence)。适配器需声明
指标定义及版本；Core 的定义白名单包含 TypeSafe Choice / Score 和确定性规则输出，并
对已知公式从概率重新计算核对。provider 定义变化需更新适配器和合同版本，不能借用原定义名。

每个 question 的 Core 配置明确 `metric`、`review`、`accept`，满足
`0 <= review <= accept <= 1`，禁止依赖跨问题的隐式全局阈值。默认指标是 Choice 的
`top_probability`、Score 的 `confidence`、Noul 的 `distance_from_half = abs(2*p-1)`。
Choice 概率并列最高时强制人工复核；Noul 以 `p >= 0.5` 表示 yes、否则 no，恰好 0.5 也强制复核。

指标达到 accept 只标为 `suggestion_ready`；位于 review 与 accept 之间标为
`human_review`；低于 review 标为 `insufficient_evidence`。这些标签仅控制展示和人工复核，
不启动实验操作，不改变安全门或质量门。指标、阈值和定义版本均保存到 artifact。

### 数据外发同意门（Core 拥有）

默认关闭，由配置和交互式 CLI 显式开启。`transmits_off_machine` 是唯一外发判定来源；
`advisor.external_state` 由 Core 据此派生，不能由插件独立声明相反含义。本机以外的任何目的地
都属于外发；首版禁止未声明的本地代理调用，网络型实现只接受声明的 HTTPS endpoint。

流程固定为：构造输入快照 → 完整请求生成 → 校验 → 预览 → 授权 → 保存请求与授权记录 →
核验源绑定与请求 → 发送。run 输入快照按下节的绑定合同构造。
校验和预览覆盖整个请求，包括 state、问题说明、候选项、评分标准、模型与推理参数，而不只是
`state.fields`。未声明的字段或目的地在授权前拒绝。预览展示精确 body、method、URL、语义
headers、字节数、逐条 untrusted span 与来源，不显示认证秘密。

Core 将请求 envelope 按 UTF-8、键排序、无额外空格、拒绝 NaN 的规范 JSON 编码，其中 body
以 base64 保存，再计算 `request_sha256`；同时保存 body 的 `payload_sha256` 和字节数。
envelope 包含 advisor/package/API 版本、完整 URL、method、语义 headers、模型与精确 body，
避免仅绑定 host 或状态白名单。请求准备后不可修改；发送前 Core 从实际交给执行器的对象重新
计算摘要，与预览和授权记录核对。认证值不参与持久化摘要，凭据引用的身份参与绑定。

按次授权绑定 `call_id`、`request_sha256` 和本地 target；run 目标另绑定 `binding_sha256`。
按次授权只消费一次；摘要不同或记录无法保存则拒绝执行。
同意记录包含范围、target、操作者、时间、endpoint、字段白名单、摘要与字节数；
`--accept-external-state` 表示请求交互确认，不是跳过预览或确认的布尔授权。

run 级授权只允许相同 `binding_sha256`、本地 target 选择、advisor/package/API 版本、任务模板、
问题、候选项、判据、模型、method、URL、语义 headers 和凭据引用。用户必须在预览时显式列出
允许变化的补充 state JSON 路径，限定类型、取值范围或长度、单次最大字节数、最多调用次数与
过期时间；默认无可变路径。首版仅允许 `operator_note` 作为可变补充字段；源产物派生的字段
不能通过该规则绕过源绑定。更新采集或质量分析后须重新准备并授权。
每次调用保存自己的精确摘要，并验证与授权模板之间只有许可范围内的变化。本地 call_id、
审计时间与审计路径不参与 run 模板差异比较，也不能进入 provider body；按次授权仍绑定
本次 call_id。其它字段变化、
run 切换、源绑定变化、包升级、超限、过期或撤销都使授权失效。首版只允许完整补充字段值变化，
不支持任意 JSON 模板表达式；无 run 的调用只支持按次授权。次数检查与执行名额消费必须原子化，
并发调用不能同时使用最后一次额度；超时或发送结果未知时不返还额度。复制 `consent.json`
仅复制审计证据，不能据此导入或恢复有效授权。

`run plan`、CI、MCP 等非交互入口拒绝外部执行，也不接受已有 run 授权绕过限制；离线 advisor
和预览仍可使用。API key 永不进入请求业务字段、artifact、日志或错误信息；供应商异常原文及
响应 headers 不直接写盘，Core 只保存清理后的错误码、阶段和消息。

### run 输入快照绑定

绑定用于确认本地实验目录与此次实际消费的输入内容。现有 `run.json` 不保证携带唯一 run id；
目录名、experiment name、plan hash 及展示用 id 均不能替代授权绑定。Core 不修改已有
`run.json`，也不向原始产物补写 UUID；standalone 继续以 `call_id` 标识调用。

`run_binding` 的首版字段如下；`binding_sha256` 单独保存，不参与自身的计算。

| 字段 | 合同 |
| --- | --- |
| `schema` | 固定为 `wavebench.advisor.run_binding.v1` |
| `run_root` | Core 解析后的本机绝对目录；消除相对路径、`.`、`..` 与符号链接别名，使用本机路径规则，不自行将路径转为小写 |
| `task_id` / `task_version` | 定义输入提取规则的任务及版本 |
| `sources` | Core 生成的输入清单，按 `path` 排序且路径唯一；必须包含 `run.json` |

每个 source 包含 run 根目录内的 POSIX 风格相对 `path` 和 `state`。`state="present"`
另含原始字节的 `sha256`；`state="missing"` 不含摘要，只用于任务明确定义可缺失且其缺失
参与判断的输入。`run.json` 必须存在。无权限、越界、非普通文件或读取错误不能当作 missing。
解析后的输入路径必须仍在 run 根目录内；清单不接受插件提供的额外路径。

先对各个存在的源文件计算 SHA-256，再将绑定记录按 UTF-8、无 BOM、键排序、无额外空白、
保留 Unicode 字符且拒绝 NaN 的 JSON 编码计算 `binding_sha256`；摘要统一使用小写十六进制。
它是小型绑定记录的摘要，不是整个 run 目录的摘要。`call_id`、审计时间、输出路径及摘要自身
不进入记录。绑定结构、源路径和绑定摘要只保存在本地审计中，不进入 provider body。

#### 源范围与资源预算

源清单由版本化任务的 Core 输入构造器确定，覆盖构造 state 时实际使用的报告／metadata，
以及参与判断的缺失状态。字段来源与内容摘要必须使用同一次读取的字节；保存原始字节摘要
而非重新序列化后的 JSON 摘要。输入提取只消费已读取的快照，不再次从磁盘取另一份内容。

首版 triage 不递归扫描 run，不读取或散列完整波形、截图、视频；`capture_available` 只描述
任务检查的记录／metadata 是否可用，不宣称波形内容已完整验证。`decisions/`、独立审计目录、
本次生成文件和未消费的报告不进入清单。通用 package loader 若会读取额外的频响或校准文件，
不能直接把其所有读取作为 triage 的隐式依赖。

首版任务预算为最多 32 个 source 条目（含 missing）、每次快照读取累计最多 16 MiB 原始字节，
含 `run.json`。读取时执行累计字节上限，不能仅依赖读取前的文件大小；超限明确拒绝，不能截断
输入、跳过摘要或转用文件大小／mtime 作为身份。任务版本固定这些上限，插件和调用参数不能放宽。
文件读取还须有有限的阶段超时；复用 `timeout_s`，快照构造计入准备预算，发送前复核计入执行
预算，不包含人工确认等待。预算是应用层读取限制，不承诺 OS 内存硬限额。

初次读取同时用于解析和摘要；授权匹配直接比较该快照的绑定，不额外重复读取。保存请求与授权后，
执行前重新解析目标根目录、重建源清单并核验内容摘要和 missing 状态，只读取相关小型源文件。
正常调用只需构造和发送前复核两次有界快照读取；预览不执行发送前复核。实施验收记录代表性输入
的字节数与两次读取耗时，当前不宣称已经完成性能测量，也不引入摘要缓存。

#### 失效与一致性边界

复核发现根目录、清单、存在状态或内容变化时，停止本次调用；不能刷新输入后继续使用旧预览或
旧授权。读取不稳定、失败或超限时同样拒绝执行。执行器仅接收冻结请求，不允许重新读取源文件。
本合同不提供多文件原子快照或文件系统锁；最终复核后磁盘继续变化，不得改变本次发送字节，
审计始终描述本次使用的输入快照。

| 变化 | 绑定结果 |
| --- | --- |
| 相对／绝对路径或符号链接别名解析到同一根目录，源内容相同 | 相同；符号链接重新指向其他目录时失效 |
| run 移动、复制到另一目录，或同名目录位于不同位置 | 不同，须重新授权 |
| `run.json` 或任一已消费文件内容变化，包括只改 JSON 缩进 | 不同，须重新授权 |
| 可缺失输入由 missing 变为 present，或反向变化 | 不同，须重新授权 |
| 仅新增 advisor 审计文件、修改未消费文件或文件 mtime | 相同 |
| 同一路径下以完全相同字节替换文件 | 相同；本合同比较内容，不识别物理文件更替或实验发生次数 |

绑定相同只满足授权的一项条件，仍须检查完整请求／模板、目标选择、期限、撤销与次数限制。
路径绑定仅在本机解释，不承诺跨机器或移动目录后继承授权，也不建立全局实验身份。

### Decision artifact（Core 拥有）

- schema `wavebench.decision.v1`，写入对应 run 目录的 `decisions/`，或显式独立审计目录，附加式；
- 内容：target（本地 run 绑定及任务选择，或 standalone 调用）、advisory（requested/reported model、duration）、consent、state、
  questions、answers（含概率与 confidence）、thresholds、recommendations；
- 每问题的指标与 accept/review 展示阈值由 Core 配置拥有，不交给插件；
- 写盘沿用既有 Windows 原子替换与重试约定；文件名含 UTC 时间戳与 advisor id，独占创建；
- 本次不并入 `run report`。

`advisor ask` 必须恰好指定一个目标：`--run-dir` 指向含可验证 `run.json` 的已有 run，或
`--audit-dir` 指向显式的独立审计目录。前者由 Core 构造 `run_binding` 与 `binding_sha256`；后者的 target 为
`standalone`，含 Core 生成的 `call_id`。目标缺失、无效或不可写时，在插件加载前拒绝。

每次调用创建独占的 call 子目录；其中 `request.json` 保存校验后的请求、精确预览与摘要，
run 调用另保存完整绑定与源清单；`consent.json` 保存授权或拒绝，并关联 `binding_sha256`、
本地 target 与本次请求摘要，`result.json` 保存终态。每个文件只写一次，使用既有原子
写入约定；预览也保存 `preview_only` 结果。call 目录使用 UTC 时间、经校验的 advisor id
和随机 call id，不能由插件提供路径片段。

授权与请求记录必须在发送前持久化成功。终态覆盖 `completed`、`preview_only`、
`denied`、`prepare_failed`、`execution_failed`、`timeout`、`invalid_response`、`cancelled`；
同时记录 `delivery` 为 `not_sent`、`sent` 或 `delivery_unknown`；本地实现始终为 `not_sent`。
加载失败使用 `load_failed` 终态。发送后写终态失败时，CLI
报告审计失败并返回非零；已保存的请求与授权保留为未完成调用，后续检查只能报告未知状态，
不能推断成功或自动重新发送。加载或准备前失败可只生成失败结果；本地非法输入在创建 call
目录前返回错误，保证零插件调用。除 `completed`、`preview_only` 外的终态均返回非零。

发送前绑定复核失败使用 `denied` 终态：内容变化的原因为 `run_binding_changed`，读取失败或
资源超限的原因为 `run_binding_unverifiable`；二者均为 `delivery=not_sent`、零 execute。
复核阶段超时或取消仍使用 `timeout` / `cancelled` 终态，并明确 `delivery=not_sent`、零 execute。
结果记录原摘要、可计算时的新摘要、变化或无法读取的相对路径及阶段，不复制源文件内容。
已写入的请求和授权记录保持不变；初次快照错误在插件加载前拒绝，不冒充供应商执行失败。

### 内置确定性 advisor

Core 内置 `rule_advisor`：纯规则、零依赖、`transmits_off_machine=False`。用于：让新类别在 CI 与
离线下可测；提供效果比较的确定性基线；给第三方最小样例。合同测试中的第二个实现采用独立
fake provider，复用相同 validator，并记录 `prepare` / `execute` 次数与最终发送字节。

### CLI 面

```text
wavebench plugin list --category advisor
wavebench plugin doctor --category advisor
wavebench plugin package check <whl|dir>
wavebench plugin install|installed|remove <...>
wavebench advisor ask --advisor <id> --task capture-triage --run-dir <existing-run> \
        [--state <supplement.json>] [--preview | --accept-external-state] [--timeout-s 30]
wavebench advisor ask --advisor <id> --task capture-triage --state <fixture.json> \
        --audit-dir <audit-directory> \
        [--preview | --accept-external-state] [--timeout-s 30]
```

以上命令为提案语法。`advisor ask --preview` 不联网，打印完整预览并保存预览审计记录；
run 模式的 `--state` 仅接受可选 `operator_note`，其它字段拒绝；standalone 的 fixture
提供完整任务 state。补充输入读取后冻结，受完整请求及可变字段授权约束，不作为 run 源文件。
默认不加外发许可时只能执行本地 advisor。外部调用必须经交互确认；它与仪器 IDN probe
具有不同的数据外发边界，不能因 probe 为只读就视为已经获得外发授权。

## 兼容性

- 现有 V2 仪器插件的公开契约、entry point group 和账本解释保持兼容；包检查与生命周期新增独立 advisor 分支；
- 已安装插件不受影响；新增 advisor registry 不改变 V2 instrument registry 或 V1 metadata registry 的返回合同；
- 新类别使用独立版本门；advisor 插件对 Core 的版本门由实施版本决定；
- 一个包首版只允许声明一个类别；从"禁止混装"放宽到"允许"是向后兼容的，反向不是。

## 决策（提案结论）

| # | 决策 | 结论 |
| --- | --- | --- |
| 1 | 是否引入插件类别抽象 | 引入 `instrument` / `advisor` 两个类别，独立 group 与版本门；不把 `advisor` 加入 `PluginKind` |
| 2 | 一个 wheel 能否同时提供两类插件 | 首版禁止；混装给出明确错误 |
| 3 | advisor 能否声明第三方运行时依赖 | 允许声明；依赖只从打包元数据 `Requires-Dist` 读取，`plugin doctor` 报告缺失；`plugin install` 保持离线 `--no-deps` |
| 4 | 同意门粒度 | 默认按次绑定请求摘要与本地目标；run 级绑定目录及源快照，仅按显式模板允许补充字段变化，每次仍校验限额并记录精确摘要 |
| 5 | `advisor.external_state` 是否纳入 access policy | 本次不纳入；由 `[advisor]` 配置 + 同意门控制 |
| 6 | decision artifact 是否并入 `run report` | 本次不并入；只落盘，插件自渲染摘要 |

上述结论是本提案的裁决建议，`Draft` 状态下尚不构成对外承诺；第 5、6 条是未来设计，不作为当前能力。

## 验收门

- 离线：全部测试无网络、无 API key、无第三方 SDK；
- 契约：未知 capability、缺 `purpose`、`transmits_off_machine=False` 却给出 endpoint，均在加载期拒绝；
- 类别边界：一个包同时声明两个 entry point group 必须被拒绝；
- 依赖：只从 `Requires-Dist` 读取；doctor 报缺失但不联网；
- 执行协议：规则与 fake provider 独立实现接受同一请求、响应 validator；加载、构造、准备和预览阶段的网络与仪器调用次数为 0；
- 同意门：未同意、完整请求摘要变化、endpoint 变化、未注册字段、仅预览及发送前写盘失败时，execute 次数为 0；
- 请求一致性：fake provider 捕获的 method、URL、语义 headers 与 body bytes 必须与预览摘要一致；问题、候选项、判据或模型变化均不能复用按次授权；
- 同意范围：按次同意只消费一次；run 级仅允许声明的补充 state 路径变化，run／源快照切换、过期、包升级、超限与撤销均拒绝；非交互入口不复用授权；
- run 绑定：同目录同任务同源字节及等价路径绑定一致；同名不同目录、移动／复制目录、源内容或 missing 状态变化均失效；新增 decisions、修改未消费文件或 mtime 不影响绑定；同路径同字节替换视为相同；
- 快照复核：确认后源变化、读取失败或超限均为 not_sent、零 execute；结果保存失败原因及可用的摘要，不覆盖原请求／授权，不自动刷新或重发；run.json 原始字节不变；
- 输入与成本：run 模式拒绝 fixture 覆盖派生字段；摘要与解析消费同一份字节；条目数 32 与累计字节 16 MiB 的边界及超限均有离线用例，不遍历目录或读取大体积波形，记录代表性两次读取的耗时与字节数；
- 授权限额：两个并发调用不能同时消费最后一次额度；源绑定相同也不能绕过撤销、过期或请求模板变化，复制审计文件不能恢复有效授权；
- 答案：缺失与额外答案、未知选项、NaN/Inf、bool、错误分布和未知 confidence 定义均返回 invalid_response；并列最高与低置信指标不形成可直接采用的建议；
- artifact：run 与独立调用均独占创建记录，不改 `run.json`/`summary.csv`/`steps/*`；目标缺失或不可写时零插件调用；
- 失败：准备、执行、超时、取消及响应校验失败均保留审计终态；发送后结果写入失败保留未完成记录并返回非零，不自动重试；凭据不出现在记录或清理后的错误中；
- 边界：advisor 结果不得影响 `run.json.status`、质量门、`auto_recover`、capability/access policy；
- 文档：新增 Development 页与 Reference 章节，并在 [RFC 索引](../../rfcs/index.md) 登记状态。

## 分期实施

| 阶段 | 内容 |
| --- | --- |
| 1 | 独立 advisor registry + descriptor/factory/执行协议 + 请求与答案 validator + triage 规则与 fake provider |
| 2 | 完整请求同意门 + 有界 run 源快照绑定与发送前复核 + run/独立调用审计写入器 + 每问题阈值配置 + 超时与失败处理 |
| 3 | `package_inspect` / `lifecycle` 支持 advisor 包 + `advisor ask` |
| 4 | 文档（Development、Reference、概念页）与 RFC 索引登记 |

阶段 1 与 2 不依赖网络与第三方 SDK，可合并为一个可离线验证的改动；阶段 3 触及生命周期，建议单独评审。

## 不做的事

- advisor 不参与任何写路径、安全门、质量门、capability 判定；
- 不为某个厂商在 Core 内加特例分支；
- 不提供"advisor 自动执行建议"的路径：建议始终由操作者显式执行。

## 风险

- 这是 Core 公共契约扩展，审查成本高，且与既有 MCP/agent 叙事部分重叠；
- 类别抽象是长期承诺：第三个类别出现前不应再泛化；
- 若插件生态长期只有一个 advisor 实现，抽象可能被判定为过度设计；内置 `rule_advisor`
  只能用于合同与基线验证，类别必要性仍需独立实现和具体任务效果比较支持。

## 未核验

- 动机实现的第三方运行时依赖清单与许可证状态；
- 该实现的服务区域与网络可达性对本地实验网络的影响；
- 非英语 state 在该实现上的准确率表现。
