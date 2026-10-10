# 大模型接入整改与使用说明

## 本轮范围与结论

整改连接协议、消息历史、输入/输出预算、诊断和计数，不改变静态分析规则、不重跑 A/B 实验。初次整改按用户选择保留实际配置；随后用户确认部署窗口并授权提高输出，最新配置为：5500 字符输入保护保持，输出上限 8192，部署窗口声明 524288。应用输入预算仍为默认 18000。下面保留各阶段验证记录，最新结果见“单独提高输出与部署窗口声明”。修改环境变量后需重启 API/Worker，更新配置示例本身不会改变实际预算。

2026-10-09 使用现有端点、凭据和 Windows 系统代理查询 `/models`，只获得 ID `FW-GLM-5.3-noklok-1`，未获得容量、分词器或输出上限元数据。随后以进程内 `reasoning_effort=low`、最多 1024 输出 Token、无重试验证两次小型请求：工具调用、工具结果回传、随机校验值完整回答均通过；返回实际模型 `FW-GLM-5.3`。平台合计报告输入 254、输出 20 Token，未提供推理 Token 分项。未发送项目源码、未创建或改写实验记录。

这是小型原生协议验证，不是最大容量测量，也不能证明任意大项目报告一定完整。进程内探测参数没有写回 `.env`；正式运行仍按现有配置发送。

### 条件授权后的预算复核（2026-10-09）

用户随后允许在验证正常的前提下提高实际 `.env` 预算。微软 Foundry 官方模型卡确认目录模型 `FW-GLM-5.3` 支持 1,048,576 Token 窗口、131,072 Token 最大输出，并由 Fireworks 基础设施承载；这比仅根据官方 GLM 原版信息推测更有依据，但不能单独证明当前 `noklok-1` 准入部署没有额外限制。参见 [微软模型卡](https://ai.azure.com/catalog/models/FW-GLM-5.3?publisher=Fireworks) 和 [Foundry 接入说明](https://learn.microsoft.com/en-us/azure/foundry/how-to/fireworks/enable-fireworks-models)。

本轮不发送项目源码、不访问数据库，共进行了 7 次无重试生成请求：

- 临时应用输入估算预算 65,536、输出上限 8,192，约 60,455 UTF-8 字节估算的工具请求返回 503。
- 相同 8,192 输出参数的极小纯文本请求通过，真实输入 36、输出 31 Token。
- 临时输入估算预算 24,000、输出上限 8,192，约 18,890 字节估算的工具请求仍返回 503。
- 约 17 KB 的无工具合成长文本请求同样返回 503。
- 极小原生工具链两轮通过，真实用量分别为输入 182 / 输出 39 和输入 102 / 输出 44 Token。
- 相同约 18,890 字节估算的工具请求将输出上限改为 4,800，仍返回 503。

失败响应没有给出明确超窗错误码或请求 ID，因此不能将 503 判定为上下文超限，也不能确定是准入网关、模型服务、配额或临时负载造成。成功响应报告合计输入 320、输出 114 Token；失败请求的用量未知，不能按零计费。

由于长输入尚未验证通过、长输出阶段未能执行，实际 `.env` 保持不变；不能把“小请求接受 8192 参数”等同于“8192 Token 完整输出已验证”。旧 5500 字符保护和 2400 输出上限仍生效。后续应先取得部署/网关的明确限额或解决长请求 503，再使用验证脚本，避免自动缩减预算掩盖服务问题。

`test/validate_model_budget.py` 是显式、无项目内容的验证入口，不修改 `.env`：

```powershell
.\.venv\Scripts\python.exe -B -u test/validate_model_budget.py --run --input-budget 24000 --output-budget 4800
```

每次运行最多三次无重试生成，先验证首/中/尾标记和工具续接，再要求完整 600 行且平台报告输出超过旧 2400 Token。仅合成记录；网络或格式检查失败立即停止，不执行任何 Shell/Docker 工具，不保存思考内容。

### 单独提高输出与部署窗口声明（2026-10-09）

用户进一步确认部署设置 `context_length=524288`，本项目将其映射为 `CODE_EXPLORER_LLM_CONTEXT_WINDOW_TOKENS=524288`。它是运维提供的部署窗口声明（前端/指标来源为 `operator_configured`），不是 `/models` 元数据或 API 压力测试验证出的容量；这个本地参数不发送为 API 的 `context_length`，也不会改变 Azure/Fireworks 的实际部署配置。

新增 `--output-only` 模式，将长输出验证从长输入和工具链验证中独立出来，仅发送一次短输入请求，保留原输入保护及思考配置：

```powershell
.\.venv\Scripts\python.exe -B -u test/validate_model_budget.py --run --output-only --output-budget 8192
```

实际请求通过：响应模型 `FW-GLM-5.3`，600 行全部精确匹配，4799 字符，供应商报告输入 70、输出 3644、总量 3714 Token，`finish_reason=stop`，无拒绝、无截断。推理 Token 分项仍未提供；不能推算为零。输出超过旧 2400 上限，证明当前端点确实能完成较长生成；不代表已测满 8192 或 524288 的边界，也不能保证任意复杂报告都完整。

依据这次验证，实际 `.env` 只提高输出上限 2400→8192，并新增部署窗口声明 524288。保留旧输入字符保护 5500、默认应用输入预算 18000、默认余量 512、既有思考配置、模型名、密钥和准入地址，不重跑 A/B、不改历史记录。

Fireworks 将请求提示与最大输出共同约束在上下文窗口内；超限行为还取决于平台策略，不能将大窗口解释为无限输出。参见 [Fireworks 请求参数文档](https://docs.fireworks.ai/api-reference/post-chatcompletions)。本项目仍按 `min(应用输入预算, 窗口 - 输出上限) - 安全余量` 检查输入；这组配置计算得到 17488 的输入估算预算，5500 字符保护还会额外限制实际请求。此前较长输入的 503 尚未定位，登记窗口不意味着已解决它。

新的输出/窗口配置会改变实验条件；重启 API/Worker 后两组应共同使用，并创建新的实验批次，不把新旧预算运行直接混合配对。输出上限是最多生成量而非固定生成量，但更长回答可能增加时间与费用。

## 历史开源方案选择（已调整）

当前实现已取消下述 tiktoken/Hugging Face 分词依赖及其安装清单，统一使用标准库 UTF-8 字节估算，实际用量只读取供应商 `usage`。下述取舍和前文验证数值记录的是当时状态，不代表当前配置；当前接口与预算说明以 `backend/app/llm/README.md` 为准。

| 方案 | 可复用部分 | 本轮取舍 |
| --- | --- | --- |
| LiteLLM | 多供应商 API、模型表、分词和自定义分词器 | 适合未来扩展更多协议；未知模型会回退默认分词器，不能据此宣称第三方精确计数。本轮不引入网关、路由和新的重试策略。 |
| LangChain/LangGraph | 工具消息、状态和消息裁剪 | 适合复杂工作流；本项目已有持久化队列、取消、权限、只读工具、审计事件，本轮不同时迁移它们。其 Token 计数仍需要匹配分词器。 |
| tiktoken | 高效 OpenAI 文本分词 | 本轮直接复用，不重写分词算法；已知 OpenAI 名称可自动匹配词表。 |
| Hugging Face Tokenizers | 本地 JSON 分词器 | 本轮直接复用，支持核对后的其他模型词表，不加载权重、不执行远程代码。 |

当时曾提供独立分词依赖安装入口，现在已移除。旧显式词表配置需要移除，不能静默冒充仍在使用该分词器。

资料：[LiteLLM 计数与回退说明](https://docs.litellm.ai/docs/completion/token_usage)、[LangChain 消息裁剪接口](https://reference.langchain.com/python/langchain-core/messages/utils/trim_messages)、[tiktoken](https://github.com/openai/tiktoken)、[Tokenizers](https://huggingface.co/docs/tokenizers/python/latest/quicktour.html)。

## 公共模块边界

- `llm/settings.py`：无网络的连接配置、不可变预算；不依赖适配器。
- `llm/registry.py`：创建供应商实例；重新导出原配置 API，现有导入路径兼容。
- `llm/conversation.py`：公共消息、工具配对校验、Chat/Responses 序列化。
- `llm/tokens.py`：`TextTokenCounter`/`RequestTokenCounter` 协议及标准库请求估算，不再缓存或加载词表。
- `llm/budget.py`：构造上下文、整次请求计数、已知窗口输出预留、调用前检查及预算快照。
- `llm/providers/`：只序列化/调用/解析模型，不执行工具；普通 `generate()` 同样执行预算保护。
- `agents/conversation_window.py`：最新工具轮次优先，完整调用与结果配对，复用 `observations.py` 的整行/整条目压缩。
- `agents/orchestrator.py`：执行注册只读工具，保存完整结果；模型只能看到当前预算内消息。
- `experiments/`：沿用两组相同原始文件权限和报告格式；唯一输入差异仍是静态安全证据。

## 原生工具调用入口

```python
provider = create_model_provider()
messages = [{"role": "user", "content": "用户问题与项目上下文（不可信数据）"}]
turn = await provider.generate_with_tools(
    instructions="只读安全分析约束", messages=messages, tools=registry.schemas(),
)
# 执行必须经已注册工具、Pydantic 和路径/读取预算校验。
# 有调用时保留 turn.continuation，然后为所有调用加入匹配的结果。
messages.append(turn.continuation)
for call in turn.tool_calls:
    result = await registry.execute(call.name, tool_context, call.arguments)
    messages.append({"role": "tool", "tool_call_id": call.id,
                     "content": result.model_dump_json()})
turn = await provider.generate_with_tools(
    instructions="只读安全分析约束", messages=messages, tools=registry.schemas(),
)
```

上例只展示成功路径；正式编排器还把工具异常转换成匹配的结果，处理取消、预算和持久化。不能跳过未完成调用直接加入用户/助手消息。Chat 的 `reasoning_content`、Responses 原始输出项/加密推理续接状态只在当前运行内存中使用，不写报告、前端事件或实验记录。思考状态不是安全证据。标准协议见 [OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling)。

## 预算和真实用量

`MAX_INPUT_TOKENS` 是应用输入预算；`CONTEXT_WINDOW_TOKENS` 是经过核对后的平台窗口配置；`MAX_OUTPUT_TOKENS` 是单次输出上限。窗口已配置时：

```text
可用输入 = min(应用输入预算, 已配置窗口 - 最大输出) - 安全余量
```

每次输入包含系统指令、工具 schema、消息结构、原生历史、工具结果和续接状态。不再以字符除以四充当新请求计数。完整请求的本地计数依赖平台私有消息模板，因此即便词表匹配，也标记 `exact=false`。UTF-8 字节策略更保守，但不保证未知平台模板下的数学上界；安全余量不是能力证明。精确计费统计来自供应商 usage，缺失字段保留未知；失败重试的消耗可能不包含在成功响应中，不能把运行统计当成账单。

`model.started` 保存计数方法、预算、输出参数和思考配置；`model.completed` 保存真实用量与终止状态。两组预算/词表/窗口版本不同或运行中改变，不允许正式配对评分。新窗口版本 `native-tool-rounds-v1`；旧记录不回填。

旧字符保护非零时仍同时限制实际请求。关闭时设置 `MAX_CONTEXT_CHARS=0`，但本轮未改实际配置。未知平台容量不要填官方原版窗口；切换模型清除默认模型窗口/词表声明，应用保护仍生效。遇到 503 不自动降低预算，只有明确 `context_length_exceeded` 等错误才归类平台超窗；应用预算不足使用独立错误类型，在调用前停止。

## GLM 与当前第三方别名

[Z.ai GLM-5.3 官方文档](https://docs.z.ai/guides/llm/glm-5.3)标注原版 1M Token 上下文、128K 最大输出、始终启用思考和 `low/high/max` 强度；这些是官方平台原版信息，不是当前 `FW-...` 网关限额。当前网关目录未提供相同能力声明，不能自动映射为 1M。正式调用默认不发送强制 temperature 或关闭思考；按平台实际支持显式配置 `REASONING_EFFORT` 和 `OUTPUT_TOKEN_PARAMETER`。

## 诊断与传输安全

前端“测试连接”使用 `POST /api/models/probe`：最多两次生成、无自动重试，验证工具调用→回传→完整随机校验值，不探测最大窗口。思考/输出预算太小也可能导致未通过，不等于模型一定不支持工具。

只读目录检查：`python test/inspect_model_endpoint.py`。额外小型协议验证：`python test/inspect_model_endpoint.py --probe --reasoning-effort low`；需明确允许生成费用，不发送项目源码，不写数据库。命令不打印密钥或完整平台响应，使用 urllib 识别的系统/环境代理；Socks-only 代理需转换成可用 HTTP 代理。

当前端点使用 HTTP，代理不自动等于到平台的端到端 HTTPS。远程使用应向平台核对正式 HTTPS 地址，不能只把未知 IP 地址的 scheme 强改为 HTTPS，也不要关闭证书验证。前端已显示 HTTP 提醒。
