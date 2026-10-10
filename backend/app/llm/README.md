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
| `CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS` | 否 | 单次模型输出上限，默认 2400，安全范围 128～32768。 |
| `CODE_EXPLORER_LLM_CONTEXT_WINDOW_TOKENS` | 否 | 平台窗口的显式运维配置；未知时留空。仅属于默认模型，切换模型不能继承。 |
| `CODE_EXPLORER_LLM_TOKEN_MARGIN` | 否 | 模板和未知开销的安全余量，默认 512 Token。 |
| `CODE_EXPLORER_LLM_TOKENIZER` | 否 | `auto` 或 `utf8`，均使用标准库 UTF-8 字节保守估算，不依赖外部分词库。 |
| `CODE_EXPLORER_LLM_OUTPUT_TOKEN_PARAMETER` | 否 | Chat API 的 `max_tokens`（默认）或 `max_completion_tokens`。 |
| `CODE_EXPLORER_LLM_REASONING_EFFORT` | 否 | 供应商明确支持的思考强度；默认不发送，不自动禁用思考。 |

默认地址分别是 OpenAI 的 `https://api.openai.com/v1`、Ollama 的 `http://localhost:11434/v1` 和 vLLM 的 `http://localhost:8001/v1`。自定义兼容服务必须显式配置根地址。

统一入口 `BudgetPlanner` 对适配器实际构造的请求计数，包括系统指令、工具定义、原生历史、工具结果和续接状态。已知窗口时，输入预算不能超过 `窗口 − 最大输出 − 安全余量`；窗口未知时使用独立应用输入限制并明确标注未知。初始上下文按完整证据重建，并预留续接空间；每次调用前再次检查，预算不足不能发起请求。503 是网关/服务故障，不能自行判定为上下文超限，也不会自动改小预算。

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
| `diagnostics.py` | 最多两次生成验证工具闭环；模型目录保留允许字段的容量元数据，但不猜测缺失值。 |
| `providers/openai_provider.py` | OpenAI Responses API 实现。 |
| `providers/compatible_provider.py` | OpenAI Chat Completions 兼容实现。 |

## 工具调用怎样发生

`llm` 不执行工具。编排器向 `generate_with_tools(instructions=..., messages=..., tools=...)` 传入原生历史，再将调用交给 `ToolRegistry.execute()`。参数经 Pydantic 严格校验，未知字段和工具失败。Chat 保留 `assistant.tool_calls → tool.tool_call_id`；Responses 保留原输出项和 `function_call_output`。工具执行失败同样提供匹配结果，避免孤立调用。详见 `../agents/README.md`。

## Provider 协议差异

OpenAI Provider 调用 `{base_url}/responses`，使用完整 `input` 项并在 `store=false` 下申请加密推理续接状态；兼容 Provider 调用 `{base_url}/chat/completions`，使用 system/user/assistant/tool 消息。兼容响应中的 `reasoning_content` 在运行内存中回传，不展示或持久化。默认不强制 temperature、thinking 等可能不兼容的选项。二者都不启用流式输出，由编排器将最终文本切片成 SSE 增量事件。

`ModelResponseMetadata` 区分正常 `stop/completed`、`length/max_output_tokens` 等不完整终止与未知状态；空回答仍返回元数据，由编排器记录用量后判定失败。`ModelUsage` 仅接收供应商实际返回的非负整数，真实零值不会被当成缺失。缓存和推理 Token 是供应商细分计数，不应再加到已经包含它们的总用量上；供应商不返回这些字段时不能推算。

## 安全边界

API Key 只用于请求头，不进入公开配置和日志。网络错误不回传上游响应正文。模型输出不具有执行权限；只有已注册的只读工具能访问项目，而且读取路径、文件大小、行数、搜索量和敏感信息均受限制。

上游错误仅公开安全诊断字段。余额、额度和明确的上下文超限不重试；临时 429/502/503/504 最多重试两次。成功响应的 usage 不保证涵盖失败尝试的计费，不应冒充供应商账单。

`POST /api/models/probe` 最多两次生成、无重试：先要求调用验证工具，再回传只存在于工具结果的随机校验值，要求最终完整回答该值。只有工具调用、结果回传、正常终止均通过才标记 `agent_compatible`；它不探测最大窗口、不读取项目、不写实验记录。每次输出不超过 1024 Token，思考模型仍可能因过小上限未完成，此时只说明未通过当前探测条件，不证明不支持 Agent。`GET /api/models/status` 无外部请求；`GET /api/models` 只读取目录和允许容量字段，目录可见不等于支持工具。远程 HTTP 不保障密钥/源码传输安全，应优先使用平台正式 HTTPS 地址。
