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
    turn = await provider.generate_with_tools(
        instructions="系统约束",
        prompt="用户问题和可信项目上下文",
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
| `CODE_EXPLORER_LLM_MAX_CONTEXT_CHARS` | 否 | 应用侧整次 Agent 请求的近似字符预算，默认 18000，安全范围 2000～200000。 |
| `CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS` | 否 | 单次模型输出上限，默认 2400，安全范围 128～32768。 |

默认地址分别是 OpenAI 的 `https://api.openai.com/v1`、Ollama 的 `http://localhost:11434/v1` 和 vLLM 的 `http://localhost:8001/v1`。自定义兼容服务必须显式配置根地址。

`MAX_CONTEXT_CHARS` 不是模型声明的 Token 上下文窗口，也不能扩大模型能力。它是跨供应商的保守保护值：编排器会为系统指令、工具 Schema 和后续工具结果预留空间，并在超限时保留静态上下文首尾及最近的工具证据。字符与 Token 没有稳定的一一对应关系，中文、代码和不同分词器的换算比例都不同；接入小上下文模型时应主动调低该值。部分第三方兼容网关会在长输入时返回 502/503/504 或直接重置连接，这种情况可先降至 4000～8000，再根据智能体时间线显示的安全请求规模逐步调高。`MAX_OUTPUT_TOKENS` 会映射为 Responses API 的 `max_output_tokens` 或兼容 Chat Completions 的 `max_tokens`。

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
| 项目概览 | `services.project_overview.generate_project_overview(manifest, repo_map, use_model=True)` | `ProjectManifest`、仓库地图、模型开关 | 调用 `provider.generate()`；无 Provider 时返回确定性报告。 |
| 项目问答 | `AgentRunManager.start(run_id, project_id, user_id, project_root, artifact, request)` | 运行/项目/用户标识、可信项目根、分析产物、`AgentRunRequest` | 通过持久化 Worker 执行多轮模型—工具循环。 |
| 直接模型调用 | `provider.generate(instructions=..., prompt=...)` | 系统约束与用户提示 | 返回 `ModelResult`。 |
| 直接工具调用轮次 | `provider.generate_with_tools(instructions=..., prompt=..., tools=...)` | 系统约束、提示、JSON Schema 工具数组 | 返回可能包含 `ToolCall` 的 `ModelTurn`。 |

## 类型与文件

| 文件/类型 | 作用 |
| --- | --- |
| `base.py` / `ModelProvider` | 抽象模型接口；定义 `generate()`、能力声明和带工具生成的默认回退。 |
| `base.py` / `ModelResult` | 普通生成结果：`text`、`provider`、`model`。 |
| `base.py` / `ModelTurn` | 单轮 Agent 结果：文本和 `ToolCall` 列表。 |
| `base.py` / `ToolCall` | 模型请求执行的 `id`、工具名和 JSON 参数。 |
| `base.py` / `ProviderCapabilities` | 声明流式、工具调用和结构化输出能力。 |
| `registry.py` / `ModelConfiguration`、`ModelLimits` | 经过归一化的连接配置和受边界保护的输入/输出预算。 |
| `http.py` | 以标准库 `urllib` 发起异步封装的 JSON POST，并将网络错误转换成脱敏异常。 |
| `diagnostics.py` | 显式最小函数工具探测和 `GET /models` 可见模型目录；不保存或返回密钥。 |
| `providers/openai_provider.py` | OpenAI Responses API 实现。 |
| `providers/compatible_provider.py` | OpenAI Chat Completions 兼容实现。 |

## 工具调用怎样发生

`llm` 只负责把工具定义发送给模型并解析 `ToolCall`，不会直接执行工具。执行权在 `agents.orchestrator.AgentRunManager`：它把 `agents.tools.ToolRegistry.schemas()` 传入 `generate_with_tools()`，再将模型返回的名称和参数交给 `ToolRegistry.execute()`。工具参数由 Pydantic 严格校验，未知字段和未知工具都会失败；执行结果作为下一轮 `TOOL_OBSERVATIONS` 反馈给模型。完整流程见 `../agents/README.md` 和 `../agents/tools/README.md`。

## Provider 协议差异

OpenAI Provider 调用 `{base_url}/responses`，使用 `instructions`、`input` 和 Responses API 的函数工具格式；兼容 Provider 调用 `{base_url}/chat/completions`，使用 system/user messages 和 Chat Completions 工具格式。二者都不启用流式输出，由编排器将最终文本切片成 SSE 增量事件。

## 安全边界

API Key 只用于请求头，不进入公开配置和日志。网络错误不回传上游响应正文。模型输出不具有执行权限；只有已注册的只读工具能访问项目，而且读取路径、文件大小、行数、搜索量和敏感信息均受限制。

上游错误会解析为安全的 HTTP 状态、`error.type`、`error.code`、`Retry-After` 和请求 ID。余额、组织/项目消费上限和使用额度错误不会自动重试；临时 429 以及 502、503、504 网关故障最多退避重试两次。智能体页面的连通性测试调用 `POST /api/models/probe`，请求体可带 `model`，它会对当前选择发起一次最小函数工具请求，同时验证生成能力和 Agent 工具协议，可能产生少量费用，并设置 10 秒用户冷却；配置状态接口本身不代表模型已连通。`GET /api/models` 返回凭据可见目录，但目录中的模型不一定都支持当前 Agent 所需的文本生成和工具调用，需通过连通性测试确认。
