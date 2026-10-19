# 大模型接入模块

`llm` 为智能体和项目概览提供统一模型接口，同时支持 OpenAI 在线 API、Ollama、vLLM 及其他 OpenAI Chat Completions 兼容服务。上层只依赖 `ModelProvider`，不会直接依赖具体 SDK。

## 最重要的入口

1. `registry.get_model_configuration()`：读取环境变量并返回可公开的配置状态。
2. `registry.create_model_provider(model=None)`：根据配置创建 `ModelProvider`；可用请求级模型覆盖默认模型，未配置时返回 `None`。
3. `ModelProvider.generate(...)`：普通文本生成。
4. `ModelProvider.generate_with_tools(...)`：允许模型返回结构化工具调用。

```python
provider = create_model_provider()
if provider is not None:
    from backend.app.llm.conversation import ModelMessage

    turn = await provider.generate_with_tools(
        instructions="系统约束",
        messages=[ModelMessage(role="user", content="用户问题和项目证据（不可信数据）")],
        tools=tool_registry.schemas(),
    )
```

智能体的模型切换调用 `create_model_provider(request.model)`。该覆盖只存在于本次 Provider 实例中，不写回环境变量，也不影响项目概览或其他并发运行。

## 连接参数

| 环境变量 | 必需条件 | 说明 |
| --- | --- | --- |
| `CODE_EXPLORER_LLM_PROVIDER` | 是 | `openai`、`ollama`、`vllm` 或 `compatible`；默认 `disabled`。 |
| `CODE_EXPLORER_LLM_MODEL` | 是 | 服务端可识别的模型名称。 |
| `CODE_EXPLORER_LLM_API_KEY` | OpenAI 必需 | Bearer Token；本地兼容服务可为空。 |
| `CODE_EXPLORER_LLM_BASE_URL` | `compatible` 必需 | API 根地址；OpenAI/Ollama/vLLM 有默认值。 |
| `CODE_EXPLORER_LLM_MAX_INPUT_TOKENS` | 否 | 应用输入预算，默认 18000；不是模型窗口，也不是精确计数声明。 |
| `CODE_EXPLORER_LLM_MAX_CONTEXT_CHARS` | 否 | 旧字符保护，默认 0（关闭）；旧 .env 中的非零值仍同时生效。 |
| `CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS` | 否 | 未配置默认 2400；显式留空则省略输出上限，由平台决定，并非无限输出。有限值为 128～32768，非法值明确报错。 |
| `CODE_EXPLORER_LLM_OUTPUT_RESERVE_TOKENS` | 否 | 省略输出上限时的本地窗口预留，默认 32768，范围 128～1000000；不发送给平台、不限制实际输出。有限模式仍按实际上限预留。 |
| `CODE_EXPLORER_LLM_REQUEST_TIMEOUT_SECONDS` | 否 | 连接/响应头与非流式 socket 等待，默认 600 秒，范围 1～3600；Provider 构造时冻结，不发送平台，不是整个 Agent 的截止时间。 |
| `CODE_EXPLORER_LLM_CHAT_STREAM` | 否 | `1/true` 启用 Chat Completions SSE，默认 `0` 兼容未验证端点；Responses 不启用 SSE，失败不自动改协议重发。 |
| `CODE_EXPLORER_LLM_STREAM_INCLUDE_USAGE` | 否 | 默认 `1`，流式请求发送 `stream_options.include_usage=true`；若平台不支持此字段应明确设为 `0`。缺失 usage 保持未知。 |
| `CODE_EXPLORER_LLM_STREAM_IDLE_TIMEOUT_SECONDS` | 否 | 响应建立后的流式空闲 socket 超时，默认 120 秒，范围 1～3600；持续收到数据不会消耗这个空闲上限。 |
| `CODE_EXPLORER_LLM_REQUEST_TOTAL_TIMEOUT_SECONDS` | 否 | 单次尝试总预算，默认 1800 秒，范围 1～7200，含此前响应头等待；不是全部调查轮次或所有重试的总时限。已建立 socket 有总读取看门狗；DNS/建连仍受系统与 socket 超时约束。 |
| `CODE_EXPLORER_LLM_CONTEXT_WINDOW_TOKENS` | 否 | 平台窗口的显式运维配置；未知时留空。仅属于默认模型，切换模型不能继承。 |
| `CODE_EXPLORER_LLM_TOKEN_MARGIN` | 否 | 模板和未知开销的安全余量，默认 512 Token。 |
| `CODE_EXPLORER_LLM_TOKENIZER` | 否 | `auto` 或 `utf8`，均使用标准库 UTF-8 字节保守估算，不依赖外部分词库。 |
| `CODE_EXPLORER_LLM_OUTPUT_TOKEN_PARAMETER` | 否 | Chat API 的 `max_tokens`（默认）或 `max_completion_tokens`。 |
| `CODE_EXPLORER_LLM_REASONING_EFFORT` | 否 | 供应商明确支持的思考强度；默认不发送，不自动禁用思考。 |
| `CODE_EXPLORER_LLM_FINAL_REPORT_MODE` | 否 | `tools_none`（默认：保留定义并发送 `tool_choice=none`）或 `history_no_tools`（不提供定义/工具选择字段，保留有效配对历史）。由 Agent 冻结，不作为同名 API 参数发送，不在失败后切换。 |
| `CODE_EXPLORER_LLM_PRE_MODEL_REJECTION_ENDPOINT_SHA256` | 否 | 管理员声明的供应商/端点/模型指纹；匹配时仅把明确 429/503 当作执行前拒绝，单列零模型 Token 假设与时间剔除。默认空，换端点/模型失效。不发送平台，不改供应商 usage、重试或预算。 |

收尾兼容性用 `test/probe_final_report_modes.py --send` 显式验证：两次合成请求、无重试、不执行工具、不读取实验记录；默认不带 `--send` 只打印计划。测试分别核对真实调用载荷、供应商结束原因、非空文本和报告契约；不会把 `tool_calls` 改成 `stop`。小用例通过不保证大项目正常收尾。当前端点固定用例两模式都可完成，不足以据此强制改实际配置。

旧单变量输出预算/模式/超时诊断入口已退役：它按旧版非分批协议重建首请求，不能与当前分批、完整源码和 SSE 冻结条件混用。新的试验统一通过现行实验入口运行并记录实际配置，不再维护一套历史 Worker/审计补丁编排；旧 JSON、SQLite 与源码归档保留，不修改或重新计分。

机制验证使用 `python -m backend.app.experiments.full_source_trial --case case_03 --reuse-project-id <已有项目ID> --repeats 3 --stop-after-valid-pair --cooldown-seconds 60 --run-model`：复用源码与固定样本逐字相同的项目，避免重复导入；保存每一侧结果，首个完整有效配对后停止，达到次数上限也停止。不因报告失败修改提示词或预算。成功即停属于机制诊断而非无偏效果排名，所有失败及成本必须一并报告。默认不加 `--run-model` 仅预检。

只读原始请求重放仍使用 `test/probe_static_request_diff.py`，仅显式选择两组原始消息的 JSON/SSE 和连接路径，默认只计划一个静态 SSE 请求；已移除不能固定实际生成工作的“固定短台账”变体。

默认地址分别是 OpenAI 的 `https://api.openai.com/v1`、Ollama 的 `http://localhost:11434/v1` 和 vLLM 的 `http://localhost:8001/v1`。自定义兼容服务必须显式配置根地址。

统一入口 `BudgetPlanner` 对适配器实际构造的请求计数，包括系统指令、工具定义、原生历史、工具结果和续接状态。已知窗口时，输入预算不能超过 `窗口 − 输出预留 − 安全余量`；有限模式的输出预留就是最大输出，空值模式则使用独立本地规划值。窗口未知时使用独立应用输入限制并明确标注未知。初始上下文按完整证据重建，并预留续接空间；每次调用前再次检查，预算不足不能发起请求。503 是网关/服务故障，不能自行判定为上下文超限，也不会自动改小预算。

### 省略应用输出上限

在环境文件中显式留空，与完全不配置该变量不同：

```ini
CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS=
CODE_EXPLORER_LLM_OUTPUT_RESERVE_TOKENS=32768
```

此时实际请求中删除对应的 `max_tokens`、`max_completion_tokens` 或 `max_output_tokens` 字段，不发送 `null`、0 或虚构的大数。平台可能采用自己的默认上限、仍然截断或拒绝请求；本地预留只用于输入规划，不能保证未知长度的输出一定放得下。例如声明窗口及应用输入预算均为 524288、预留 32768、余量 512 时，本地输入估算上限为 491008。

审计、运行历史和实验指标记录 `output_limit_mode=provider_default`、`output_reserve_basis=planning_only_unknown_output` 及实际预留值，预算版本为 `utf8-token-budget-v3-provider-default`；有限模式保留原 `utf8-token-budget-v2`。旧记录不回填为“无限输出”。这属于新的实验条件，两组必须使用相同模式和预留值，不能与旧条件混算。审计里的配置空值不是 HTTP 中发送了空值。

方法参数 `max_output_tokens=None` 表示继承 Provider 实例配置，而非覆盖有限实例为无限；正整数可用于有界探测。连通性探测仍显式使用不超过 1024 的上限，最终报告兼容性探测不超过 4096；容量/长输出验证必须显式指定有限输出，不进行无上限探测。预留参数始终严格验证，有限模式虽然不使用它参与窗口扣减，也不能填写非法值。选择修改实际配置后，API 与 Worker 都需要重启。

所有模型统一使用完整紧凑 JSON 的 UTF-8 字节数进行输入保守估算，明确标记 `exact=false`；不加载词表、不下载分词资源，也不声称这是数学意义上的平台容量保证。真实消耗只使用供应商 `usage`，缺失值保持未知，不用本地估算补成实际消耗。旧 `tiktoken:*` 和非空 `CODE_EXPLORER_LLM_TOKENIZER_PATH` 配置会被明确拒绝，需移除，不能静默假装仍使用原词表。保留 `RequestTokenCounter` 注入协议供以后扩展。

例如部署声明窗口为 524288、应用输入预算为 524288、输出上限为 8192、余量为 512 时，实际输入估算上限为 515584；旧字符保护必须另设为 0 才不会继续限流。窗口是运维声明，不代表已通过 API 验证整个容量。API 与独立 Worker 都需使用同一配置，并在修改后重启。

在线 OpenAI 示例：

```powershell
$env:CODE_EXPLORER_LLM_PROVIDER = "openai"
$env:CODE_EXPLORER_LLM_MODEL = "你的模型名称"
$env:CODE_EXPLORER_LLM_API_KEY = "你的 API Key"
```

本地 Ollama 示例：

```powershell
$env:CODE_EXPLORER_LLM_PROVIDER = "ollama"
$env:CODE_EXPLORER_LLM_MODEL = "本地已部署的模型名称"
$env:CODE_EXPLORER_LLM_BASE_URL = "http://localhost:11434/v1"
```

## 上层调用入口

| 场景 | 函数/方法 | 必要参数 | 行为 |
| --- | --- | --- | --- |
| 项目概览 | `services.reports.overview_report.render_deterministic_overview(manifest)` | `ProjectManifest` | 输出确定性架构概览，不调用模型，与安全实验分离。 |
| 项目问答 | `AgentRunManager.start(run_id, project_id, user_id, project_root, artifact, request)` | 运行/项目/用户标识、可信项目根、分析产物、`AgentRunRequest` | 通过持久化 Worker 执行多轮模型—工具循环。 |
| 直接模型调用 | `provider.generate(instructions=..., prompt=...)` | 系统约束与用户提示 | 返回 `ModelResult`。 |
| 直接工具调用轮次 | `provider.generate_with_tools(instructions=..., prompt=..., tools=...)` | 系统约束、提示、JSON Schema 工具数组 | 返回可能包含 `ToolCall` 的 `ModelTurn`。 |

## 类型与文件

| 文件/类型 | 作用 |
| --- | --- |
| `base.py` / `ModelProvider` | 抽象模型接口；`request_payload()` 为预算和发送共用纯序列化器，工具会话不允许静默文本降级。 |
| `base.py` / `ModelResult` | 普通生成结果：`text`、`provider`、实际 `model` 和响应元数据。 |
| `base.py` / `ModelTurn` | 单轮结果及内存续接消息 `continuation`；思考状态不进入前端、证据和事件。 |
| `conversation.py` / `ModelMessage` | 会话接口、调用/结果配对校验及 Chat/Responses 序列化。 |
| `tokens.py` / `TokenCounter` | 无外部依赖的请求估算；保留可替换协议，不缓存项目文本。 |
| `budget.py` / `BudgetPlanner` | 唯一请求预算入口：完整初始证据构造、输入/输出预留、调用前检查和配置快照。 |
| `response_metadata.py` | 归一化 Responses/Chat 终止原因、响应状态、拒答、usage 及缓存/推理 Token；缺失值保持未知。 |
| `base.py` / `ToolCall` | 模型请求执行的 `id`、工具名和 JSON 参数。 |
| `base.py` / `ProviderCapabilities` | 声明流式、工具调用和结构化输出能力。 |
| `settings.py` / `ModelConfiguration`、`ModelLimits` | 纯配置与不可变预算契约，不依赖适配器；`registry.py` 重新导出原有配置入口并创建 Provider。 |
| `http.py` | 以标准库 `urllib` 发起异步封装的 JSON POST，并将网络错误转换成脱敏异常。 |
| `streaming.py` / `ChatStream` | 唯一 Chat SSE 组装器，按工具索引合并参数、保留原始结束原因/usage/内存推理续接；不执行工具或修补报告。 |
| `response_reader.py` / `ResponseReader` | JSON/SSE 共用有界读取、空闲超时与总读取看门狗；不改系统代理、不引入额外 HTTP 依赖。 |
| `diagnostics.py` | 最多两次生成验证工具闭环；模型目录保留允许字段的容量元数据，但不猜测缺失值。 |
| `providers/openai_provider.py` | OpenAI Responses API 实现。 |
| `providers/compatible_provider.py` | OpenAI Chat Completions 兼容实现。 |

## 工具调用怎样发生

`llm` 不执行工具。编排器向 `generate_with_tools(instructions=..., messages=..., tools=...)` 传入原生历史，再将调用交给 `ToolRegistry.execute()`。参数经 Pydantic 严格校验，未知字段和工具失败。Chat 保留 `assistant.tool_calls → tool.tool_call_id`；Responses 保留原输出项和 `function_call_output`。工具执行失败同样提供匹配结果，避免孤立调用。详见 `../agents/README.md`。

## Provider 协议差异

OpenAI Provider 调用 `{base_url}/responses`，使用完整 `input` 项并在 `store=false` 下申请加密推理续接状态，暂时保持 JSON 响应；兼容 Provider 调用 `{base_url}/chat/completions`，使用 system/user/assistant/tool 消息，可按显式配置启用上游 SSE。兼容响应中的 `reasoning_content` 仅在运行内存中续接，审计投影排除隐藏正文。默认不强制 temperature、thinking 等可能不兼容的选项。

上游流式传输不等于把原始台账 JSON 或隐藏推理实时展示给前端：先组装完整回复、保留真实 `finish_reason` 和供应商 usage，再由原有 Agent 校验并生成报告。工具分片只组装、不执行；收到完整调用后仍经现有工具注册表和参数校验。需要 `[DONE]` 和供应商终止原因，EOF、错误帧、非法参数和超限均明确失败，不把终止原因改为 `stop`。HTTP 正文最多 32 MiB，SSE 单行/单事件最多 1 MiB，超限拒绝，不静默截断。

单次响应建立后的总读取看门狗可中断持续滴流，空闲超时则只限制长时间没有新数据；Windows 主动关闭 socket 引起的 10053 等错误仍按总时限归因。DNS/连接阶段不由 Python 强制杀死，仍受操作系统解析器及 socket 等待约束，不能把配置总预算宣称为绝对进程截止。显式 `timeout` 探测会同时收紧空闲与总预算。平台若忽略 `stream=true` 而直接返回 JSON，只读取该次响应并记录 `server_ignored_stream`，不会补发第二次请求。

取消会通知后台读取并尝试 shutdown 已建立 socket，禁止遗留线程继续发布结果或向已封存审计追加 response/failed；系统阻塞可能延后释放，不承诺立即终止 DNS/底层连接，也不等于平台已停止计算或计费。Worker 的心跳独立于模型读取，每秒续租，无需因长生成扩大任务租约。

`execution_policy.transport` 和完整源码实验配置哈希冻结上述传输条件；两组必须一致，新旧策略不能直接混算。实际 `stream`/`stream_options` 进入请求审计。早先诊断脚本 `test/probe_static_request_diff.py` 也复用生产读取与组装，不维护第二个 SSE 实现。

`ModelResponseMetadata` 区分正常 `stop/completed`、`length/max_output_tokens` 等不完整终止与未知状态；空回答仍返回元数据，由编排器记录用量后判定失败。`ModelUsage` 仅接收供应商实际返回的非负整数，真实零值不会被当成缺失。缓存和推理 Token 是供应商细分计数，不应再加到已经包含它们的总用量上；供应商不返回这些字段时不能推算。

## 安全边界

API Key 只用于请求头，不进入公开配置和日志。网络错误不回传上游响应正文。模型输出不具有执行权限；只有已注册的只读工具能访问项目，而且读取路径、文件大小、行数、搜索量和敏感信息均受限制。

公共代码收敛在既有契约内：`ToolCall.from_arguments()` 统一两种协议的参数对象解析，`ModelLimits.output_budget()` 统一状态/事件的输出预算元数据。工厂将冻结预算注入适配器，避免再次读取环境；直接构造适配器仍可自行读取。协议序列化、原生历史、超时与重试仍分别保留，不能为了减少代码而消除平台差异或审计检查。

上游错误仅公开安全诊断字段。余额、额度和明确的上下文超限不重试；初始临时 429/502/503/504 最多重试两次。429 识别 `RateLimitReached` 等明确速率码，优先遵守秒数/HTTP 日期形式的 `Retry-After`，缺失或无效时约等待 30、60 秒；平台要求超过 60 秒则停止本次请求，不截短等待后提前重试。已经建立的流式请求中断、协议错误及总时限耗尽不自动重放，不拼接不同请求的片段；失败审计保留已收到的 partial_usage（如有），不能将未知用量当零。初始网络/网关错误保留有限退避，显式零重试仍生效。等待可取消，不增加调查轮次、不改变输出预算。成功 usage 不保证涵盖失败尝试的计费，不应冒充供应商账单。

传输层为每次尝试计量响应头等待、首帧/首段可见正文、完整响应读取和失败阶段，通过可选观察器写入实验私有审计；运行日志只含安全元数据，不输出 URL、凭据或隐藏推理。响应头等待包含本机连接、发送、代理和服务等待，不能单凭该阶段定位根因。socket 默认现为 600 秒，不把它误写成总截止时间。计量不增加探测请求；JSON/协议/报告解析失败不重传，不自动切换协议或纠正结束状态。

经平台明确确认后，可加载部署使用的 .env，调用 `settings.endpoint_accounting_fingerprint()`，将其结果配置到上述声明变量。
`settings.pre_model_rejection_policy(model)` 返回绑定策略，创建实验时冻结；审计按实际尝试及其对应退避计时，不把失败发送当作源码已经展示。
`duration_ms` 始终保留墙钟值，`adjusted_duration_ms` 只扣已确认拒绝/退避，不扣成功请求传输或未知超时。
供应商 `actual_*` / `usage_status` 保持原值；按声明核算值另列，模型 Token 为零不等于证明平台所有费用均为零。历史不回填。

`POST /api/models/probe` 最多两次生成、无重试：先要求调用验证工具，再回传只存在于工具结果的随机校验值，要求最终完整回答该值。只有工具调用、结果回传、正常终止均通过才标记 `agent_compatible`；它不探测最大窗口、不读取项目、不写实验记录。每次输出不超过 1024 Token，思考模型仍可能因过小上限未完成，此时只说明未通过当前探测条件，不证明不支持 Agent。`GET /api/models/status` 无外部请求；`GET /api/models` 只读取目录和允许容量字段，目录可见不等于支持工具。远程 HTTP 不保障密钥/源码传输安全，应优先使用平台正式 HTTPS 地址。
