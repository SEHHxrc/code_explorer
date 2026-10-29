# 项目智能体模块

该模块把“用户问题 + 项目静态分析产物 + 受限只读工具”组织成可持久化、可取消、可通过 SSE 恢复的智能体运行。模型可在线或离线部署；没有配置模型或请求关闭模型时，会返回静态上下文结果。

共同范围现为 `server-analysis-only-v3`，普通 Agent 和两组共用：从服务端 API 输入开始，浏览器交互/DOM/React/Vue 不在当前审查范围，相关分析实现已删除。CLI 客户端的后端行为从服务端请求分析；纯本地 CLI 不扩展，服务端启动命令行程序的执行链继续保留。范围版本及提示词哈希随新运行冻结，历史不回填。

## 对外入口

HTTP 入口是 `api/agent.py:create_agent_run()`：请求体使用 `AgentRunRequest`。

历史恢复入口为 `GET /api/agent/projects/{project_id}/runs` 和 `GET /api/agent/runs/{run_id}/snapshot`。前者只发现普通 Agent 运行，后者从持久化事件重建问题、最终回答、展示时间线与去重证据；A/B 临时运行继续由实验模块隔离管理。

| 参数 | 类型与限制 | 作用 |
| --- | --- | --- |
| `question` | `str`，1～8000 字符 | 用户对当前项目的问题。 |
| `use_model` | `bool`，默认 `true` | 是否调用已配置的大模型；为 `false` 时走静态结果。 |
| `max_steps` | `int`，1～36，普通 Agent 与实验默认 12 | 模型—工具循环的最大轮数；两组统一预算，旧记录不回写。 |
| `model` | `str \| null`，最长 200 字符 | 可选的本次运行模型；为空时使用服务端环境变量中的默认模型。 |

真正的运行入口是 `AgentRunManager.start(run_id, project_id, user_id, project_root, artifact, request)`。队列 Worker 使用 `AgentRunStore.claim_next()` 原子认领后调用它；开发环境也可由 FastAPI 生命周期启动内嵌 Worker。

## 运行时序

### 独立第三方 API 知识 Agent

入口为 `POST /api/agent/api-knowledge`，沿用现有用户上下文依赖；不读取或更改项目。
请求包含 `api: ApiKnowledgeRequest`、`max_model_requests: 1..4=2`、可选 `model`。
Python 入口为 `ApiKnowledgeAgent(provider, max_model_requests=2).run(api)`。
`api` 包含 language/package/qualified_name/library_version/argument_count/return_mode/return_count，
不允许提供端点、凭据、项目源码或实际实参值。示例：

```json
{"api":{"language":"python","package":"待核实包名","qualified_name":"包名.filter","argument_count":1,"return_mode":"single","return_count":1},"max_model_requests":2}
```

该任务有独立模型请求额度，不创建/推进代码分析运行，不消耗 `max_steps`；未自动注册到主 Agent 或 A/B 工具。
它复用 `llm.api_knowledge.LlmApiKnowledgeProvider`，仅格式/槽位失败可在自己的额度内重新提交，
空白、截断、拒答不自动重放。可重试的 429/网络故障由已有 `llm.http` 分类并有限退避处理，
不会叠加第二层网络重试；共享平台凭据仍可能共享真实限流，并非平台额度隔离。

返回 `proposed/unresolved/failed`、结构化 proposal、每次逻辑请求 attempts、供应商 metadata/usage、
实际 HTTP transport_attempts 和 retry_waits。网络重传不是新增 Agent 逻辑轮次；失败尝试用量未知时总数保持 null。
传输观察器在任务内隔离并在退出时恢复，不污染父分析审计；日志只保存任务 ID、状态与计数。
本入口直接返回结果，不创建持久化队列或知识数据库，也没有知识历史界面。
来源始终是 `llm_proposal`；类型化条件只允许槽位比较，不执行模型代码，不自动注册净化/传播模型。

### 项目分析

```text
POST /api/agent/projects/{project_id}/runs
  → AgentRunStore.create() 持久化 run 与 queue job
  → AgentQueueWorker.claim_next() 认领并续租
  → ProjectContextBuilder.build() 生成 manifest + 静态路径摘要 + 相关 repo map（实验剔除 manifest/repo map）
  → create_model_provider(request.model) 选择在线或本地模型
  → AgentRunManager.generate_with_tools()
      ├─ 无 tool_calls：保存答案并完成
      └─ 有 tool_calls：ToolRegistry.execute()
           → Pydantic 严格校验参数
           → 只读工具返回内容、证据和截断标记
           → 保留 assistant 工具调用与全部对应 tool 结果
           → 原生消息进入下一轮；预算不足按完整轮次压缩
  → 每轮追加共同阶段/剩余调查轮次，消息配对与阶段开销一起验证预算
  → 达到步数上限时按显式最终报告模式保留消息链、禁止新工具，生成汇总
  → 持久化 run.completed/run.failed 等事件
  → SSE 以事件游标返回前端
```

`reporting.py` 提供共同阶段消息和运行策略：剩余工具轮次包含当前请求，一轮可能有多个工具调用。实验报告 v6 在原报告请求之外，整次运行至多一次格式重提交；只处理正常结束的 JSON 语法/字段结构错误，不新增调查、证据或工具权限，不切换供应商模式，不在本地修补 JSON。状态矛盾、空白、拒答、输出耗尽与未知终止不借此重试；输入放不下完整历史及原回复时跳过。每次原文与元数据保留，所有请求均计入用量；普通问答不启用。`_generate_once()` 是唯一审计/完成事件入口，避免重计成本。格式检查与重提交都不等于语义验证。

## 文件与核心类

当前按需实验 v21 沿用 `batching.py` 的 `AnalysisBatches`：先调查、独立报告、更新目录，再开始下一批。两组采用相同阶段和预算规则，台账不证明漏洞结论。`max_steps` 只限制调查请求；每批另有一次报告请求，最终另有一次汇总，整次运行至多再有一次既有格式重提交。报告不会扣掉下一批的调查轮次，也不会因最后一次读取用尽调查次数而没有请求额度。普通 Agent 的自由问答及完整源码/累计条件保留原有流程，不暗中迁移历史实验。共同报告 v7/台账 v2 明确起终点与局部/外部闭合范围，区分传播、部署及安全要求未知；历史源码按实际请求回执追溯，新人工评分独立保留三个维度。字段与边界见 `../experiments/README.md`。

### 每轮工具与报告历史

工具 Schema 不自动成为会话历史；本应用不依赖服务端会话，每次请求都会单独发送当轮 `tools`。
`reporting.phase_tools()` 是按需实验唯一的阶段允许列表：调查仅提供读取/查询工具，批次报告仅提供
`submit_analysis_batch`，最终汇总不提供工具。调查结束时模型只需停止调用并说明可收尾；最后两轮额外提醒闭合当前链路。
批次报告请求使用 auto（只有提交工具），允许单独调用该工具或直接返回完整台账；混入读取/搜索调用时全部拒绝，
错误的 batch/ledger_json 字段不静默转换。调查阶段调用隐藏的报告工具同样不执行。

报告历史使用 `conversation_window.round_messages(report_only=True)`，在同一预算选择器中把已有工具结果
转换为 `APPLICATION_BATCH_EVIDENCE` 用户消息。只保留结果、来源 observation_id、源码、定位和省略信息；
不携带原 assistant 草稿、tool_calls、工具名称/参数包装、provider_items 或隐藏推理续接状态，避免孤立工具结果。
源码本身的 API/函数名不删除。该投影不是 LLM 语义压缩，也不把不可信观察变为指令或已验证事实。
同一标记沿用路径交付观察和源码引用机械检查，发送记录仍不能证明模型使用。
本次真正保留的投影可预览 shown_scopes，但预览不推进展示/交付状态；模型返回后才更新批次约束。
输入放不下完整最新观察时明确失败，不为腾空间伪造交付、截断源码或复用旧工具能力。

请求 step 对各阶段递增，调查另有 investigation_round；`model.started.phase` 独立记录调查、批次报告、汇总和格式重提交。
`phase_requests` 是应用发起计数，不是 HTTP 重传、平台执行次数或 Token 用量；所有阶段和失败成本仍照常累计。
每批最多一次主报告，批次数受调查推进约束，主报告请求上界为 max_steps，汇总一次；没有无限追加模型调用。

| 文件/类 | 作用 |
| --- | --- |
| `contracts.py` | `AgentRunRequest`、`AgentClaim`、`ContextPacket`、`ToolResult`、`AgentEvent`、`AgentRunView`、历史摘要与快照等边界模型。 |
| `context_builder.py` / `ProjectContextBuilder` | 验证 Manifest，复用公共静态路径摘要，再加入有界仓库地图；实验上下文另行隔离。 |
| `orchestrator.py` / `AgentRunManager` | 执行模型—工具循环、收集证据、输出事件、处理取消与错误。 |
| `observations.py` | 将完整工具结果投影为预算内的结构化观察窗口；保留证据定位，按完整源码行压缩并报告省略。 |
| `conversation_window.py` | `ToolRound`/原生消息窗口；调用和全部结果整轮保留或整轮省略，复用观察正文压缩。 |
| `path_delivery.py` / `PathDeliveryObserver` | 在真实 HTTP 边界旁路记录路径 ID、分段/分页、正文哈希及尝试结果；转交既有审计/退避回调，不保存源码或认证信息，不判定模型使用。 |
| `instructions.py` | 普通 Agent 共用指令，避免上下文构造器与编排器循环导入。 |
| `run_store.py` / `AgentRunStore` | 保存运行、队列租约、事件和取消状态，并生成不含工具正文的历史快照。 |
| `worker.py` / `AgentQueueWorker` | 认领队列、续租并调用编排器；`python -m backend.app.agents.worker` 是独立进程入口。 |
| `policy.py` | 安全解析项目内相对路径，并对读取文本做敏感信息脱敏。 |
| `tools/` | 模型可请求的只读工具和严格参数模型。 |

共享片段格式的观察压缩必须连同其依赖资源一起选择，不能保留候选 ID 而省略对应片段却不说明；未选候选的独占正文也不保留。引用检查直接识别同页片段正文，单独的引用 ID 不证明模型已看到源码。

### 路径交付记录（v18 引入，v20 兼容报告证据投影）

`paths.returned` 只表示工具生成了该结果；只有传输层回调才写 `paths.send_attempt`，
记录 request/attempt、路径或族 ID、表示类型、section、实际条目 ID/顺序、分页端点、省略标记及正文哈希。
该事件仍是发送尝试，不证明平台接纳或模型阅读；`paths.request_resolved` 区分收到响应与失败，`model_use` 始终 unknown。
工具生成后立即取消、输入预检失败或没有下一次请求，都不会为该页补造发送记录。
目录/overview/部分 flow 页不是完整路径；完整原文在受控审计中按请求 ID 核对，普通历史只保留这些元数据。
`paths.model_observed` 另外记录模型请求的 ID、提交工具声明关联的 ID、可见正文中的显式 ID；
文件引用不自动推导为某条路线被使用，批次 `evidence_ids` 只是关联集合，不是使用证明。
原有源码引用机械检查继续独立运行，不证明结论语义正确。

v19 删除批次 JSON、生成正文和提交工具 Schema 的 16000 总字符上限；对应常量、错误分支和限长提示一并删除。
`execution_policy.batch_report_max_chars=null` 明确表示本版本没有批次总字符限制，不能与旧记录缺少该字段混同。
模型输出 Token 配置仍由 llm 层管理，空值省略请求上限；这不承诺平台无限输出。输入预算、超时、传输体积、
审计存储配额及台账字段/数量/状态约束仍有效。空白、截断、无效 JSON 和无依据的范围不能因取消长度限制而通过。
旧 `batch.report_rejected` 事件仅保留历史回放支持，不再产生；旧失败记录不重写。
v19 当时只移除字符上限，保留原 tools_none/history_no_tools 收尾；v20 按需实验固定使用上文的阶段工具隔离。
实际 .env 的旧收尾选择仅作为 configured_final_report_mode 记录，在按需实验中不再改变隔离策略；
非分批流程仍按原配置运行。没有失败后自动换模式或执行隐藏工具的兜底。
真实模型/网络的等待发生在请求内还是退避内仍通过既有请求、响应和 retry_wait 审计区分。

## 上下文来源与依赖

`ProjectContextBuilder` 读取分析产物中的 `manifest`、`security_evidence` 和 `repo_map`；工具还可分页读取静态安全候选、查询依赖图和读取经清洗后的项目目录。`AgentRunManager` 依赖 `llm.create_model_provider()`、`ToolRegistry` 和 `AgentRunStore`。静态证据中的结构可达候选与函数内 Def-Use 使用不同 claim，系统提示禁止把前者表述为污点流。

前端选择的模型属于单次运行参数，不会修改服务器 `.env`。`AgentRunStore` 先把选择写入运行记录，`AgentQueueWorker` 认领任务后再恢复到 `AgentRunRequest`，因此独立 Worker 或进程重启不会把它替换回默认模型。

预算统一交给 `llm.budget.BudgetPlanner`，以实际协议请求估算 Token，已知窗口时预留输出和安全余量。`MAX_INPUT_TOKENS` 是应用输入预算；旧 `MAX_CONTEXT_CHARS` 非零时额外生效。完整工具结果仍写入 `tool.completed`，窗口仅收缩模型侧正文，不切碎 JSON 或源码行。没有模型的静态回退不受模型预算影响；实验则始终预检，不允许静态回退充当模型试次。

新窗口版本 `native-tool-rounds-v2` 优先最新工具轮次，旧轮次整体省略。调用与对应结果不可拆开裁剪；最新整轮连定位信息也放不下时，在下一次模型请求前明确失败。压缩复用 `observations.py` 保留全部证据、实际行范围、续读提示和省略标记。Chat 的思考字段、Responses 的原生/加密续接状态仅在内存保留，不属于安全证据。`context.window` 记录省略情况，`model.started` 保存预算快照与估算方法；两组窗口、预算、分词策略不同不可进入严格配对评价，旧记录不回填。

非分批流程在步骤用尽后按冻结的 `tools_none/history_no_tools` 模式构造最终请求，显式要求不再调用工具；格式重提交也遵循同一模式。若平台仍返回工具调用，不执行额外工具，以 `unexpected_tool_calls` 安全失败；空白、拒答和输出提前终止分别记录原因、元数据和不完整状态，不能通过盲评。按需 v20 则使用上文的独立报告阶段，不复用此旧收尾路径；两组采用相同规则，格式重提交额度不增加调查步骤。

## 安全保证

系统提示明确把仓库内容、README 和工具输出视为不可信数据。模型不能选择任意 Python 函数或宿主命令，只能按注册表调用工具。工具是只读的，并受路径边界、字节数、行数、文件数、耗时和结果数限制；错误通过公开消息返回，不暴露内部路径或密钥。

实验策略的权限与普通 Agent 不同：`security_evidence/baseline` 使用共同安全指令、原始文件工具与批次报告工具；实验组额外获得有界静态目录和 ID 查询，控制组没有派生索引访问能力。实验不使用 Manifest、Repo Map 或依赖图工具，并在 Provider 缺失时失败而非静态回退。`model.completed.metadata` 保存响应 ID、真实返回模型名、终止原因、状态与供应商实际用量（未提供时为空）；`run.completed` 保存运行终止原因和回答完整性。规范化字符数仍用于独立估算，不能当成真实 Token usage。恢复快照保留这些诊断字段、批次报告和全部去重证据，但不携带工具源码正文。旧图任务拒绝续跑，历史记录不被重写。
