# 静态安全证据模块

## 服务端安全分析（证据包 2.14）

JS/TS 仅保留 Node.js、HTTP/Express 路由、进程/文件/网络等服务端规则和公共调用/值绑定。浏览器输入、React props/表单/hooks、Vue SFC/模板/ref、DOM 输出与专用规则已删除；不再使用 HTML 入口筛选、延后开关或另一套浏览器执行模式。

Node HTTP 注册由 `syntax_analysis/node_http.py:node_request_inputs()` 提供；同请求 data→end 的局部槽位连接由 `language_adapters/node_http.py:collect_request_body_boundaries()` 提供。后者输出既有公共 `IRValueBoundary`，仍由同一 CFG/DFG 和跨过程求解器消费。普通语言适配、Node 参数/返回/对象/集合传播继续保留，纯本地 CLI 不扩展。

`.vue` 不再作为支持的源码扩展名，也不再准备 HTML grammar。JS/TS 的普通语法和类型解析仍用于服务端；读取到 JavaScript 本身不证明其运行于服务器，网络/进程候选仍需核实真实入口与运行环境。HTTP 请求字段是输入，req/res/框架依赖并非整体不可信 Source。

旧产物需重新分析；2.13 的范围字段仅保留只读数据兼容，没有对应筛选执行代码。新规则包为 `javascript-typescript-core/1.4`，共同范围为 `server-analysis-only-v3`。旧实验、审计、原始项目及评分不改写，不能将新旧范围混算。

## 公共事实入口与有界展开（证据包 2.12）

`SecurityAnalysisService.analyze_with_semantic_index(project_root=..., semantic_index=...)` 可直接消费带文件范围的
公共语义索引，不需要完整依赖图。扫描、CFG/值流与调用解析增补共用原语法会话；旧调用图仅兼容缺失事实的旧记录。
ProgramGraph 构建后先通过公共 BoundedCallableResolver 补充有依据的目标，再执行同一个跨过程求解器。
未知字典展开不会被前端丢弃：参数规则投影到具体成员槽，`argument_slots` 有界迭代转发的成员需求，
继续复用集合到达定义/值流。位置专用参数、显式实参、重复关键字、兄弟键和覆盖均保持隔离。
键存在性、运行时映射协议、动态重复键未证明时，只允许 may 传播；这不等于闭合利用链或端点实际可调用。
候选携带新的调用绑定边界；已解析的动态调用不再继续引用旧的 symbol_not_found 作为当前断点。
该变更不调用 LLM、不启用第三方安全语义屏障；需要重新分析后用于新实验，旧实验内容不修改。

## Source/Sink 角色与匹配依据

Source 表示某处操作引入的值，包含函数形参、调用返回值、输出参数和属性读取；Sink 表示某处敏感操作消费的实参、接收者或属性写入值。两者均绑定操作位置和具体值槽，不是“变量一定是 Source、函数整体一定是 Sink”。`flow_analysis/program_graph.py` 的 `_source_seed/_sink_target` 分别选择生产和消费位置；同一变量覆盖后不能自动保留旧污点，同一 API 的不同参数也不能混用。

规则事实的 `name` 是展示名：参数 Source 常显示参数名，调用 Source 和 Sink 常显示 API 名，不能据此推断跟踪粒度。调用元数据 `name_match_basis=exact_name/name_suffix` 只说明名称匹配方式，不证明完整库身份或安全语义。Guard/Sanitizer 继续保留 `protection_effectiveness=not_verified`，并以 `observation_kind` 区分普通控制条件、依赖标记、候选校验和候选变换；不能将普通 if、Depends 或某个 resolve 方法当成已验证防护，也不因匹配名称截断传播。

## 事实与查询预算（证据包 2.11）

全部已识别 Source、Sink、Guard、Sanitizer 按事实 ID 保存；不再因 300/600 的前缀选择丢失端点。
`max_sources=300`、`max_sinks=600` 现在是结构查询的单批窗口大小，不是全项目事实总额。
`candidate_search.py` 分批枚举可达函数中的端点，不计算全量 Source×Sink 笛卡尔积；
`path_finder.py` 复用同一目标窗口的反向裁剪（最多两组缓存），每次路径查询仍受深度、状态及变体预算约束。
默认结构阶段最多 2048 次查询、30 秒；候选最多 500 条。耗尽时保存终止原因，不把未遍历窗口当成无路径。
已建立值流先按不同端点族进入候选；全部已建立值流仍保存，未进入候选的数量另计。
源码片段仅为首批端点及候选按需装配，避免为了保存事实而复制每处源码；未展开位置可用原始文件工具读取。

`max_fact_records=100000` 是事实存储的独立安全上限；扫描后即检查，超出直接报错，不能静默保存部分注册表。
`max_guards/max_sanitizers` 仍约束附加到候选的关联观察，全量事实继续留在注册表，不作为传播 kill 规则。
`coverage.facts_complete` 只表示全部**已识别**事实均已保存，不证明扫描器识别了所有来源。
`candidate_source_count/candidate_sink_count` 记录候选实际涉及的端点数；`flows_without_candidate` 记录仅持久化的值流数量；
`candidate_search` 记录窗口、查询/时间配额及结束原因，`windows_exhausted` 不证明全路径覆盖。
`dataflow_status` 区分阶段完成、分区部分失败、整体失败及旧记录未知；整体异常不再以 completed=true 的零值流包冒充成功。
异常保留失败阶段与不含输入值的 Pydantic 字段诊断，运行日志仅输出安全异常栈。
所有变化需重新分析后生效，旧产物、实验输入、报告和评分不自动修补。

## 当前交付：来源边界 v5（2026-10-26）

选择器版本为 `security-path-summary-v6-possible-connections`，延续下述路径族组织和排序。
`PathEdgeEvidence.connection_status` 是摘要及安全图共用的调用目标状态：`modelled` 仅表示
高置信、无已知缺口的模型内绑定；`possible` 保留 may、回退、低置信和截断；`unresolved`
表示目标未确认。族/路线的 `call_connection_counts` 按路线内调用边计数，族中共享调用可重复计数，
不是独立调用点、闭合传播或漏洞计数。完全未知的调用仍保留诊断，不补造目标。
绘图线型由独立的 `is_speculative_target` 判定：仅显式动态/虚派发或启发式目标使用虚线，
不因保守 may 或编译器回退使全部已有调用虚线化；摘要的状态、计数及选择版本保持不变。
API 效果摘要的建议契约仍与分析结果隔离，不因图或摘要增加这些状态就启用 LLM 建议传播或剪枝。
Source 定位额外保留 `source_kind` 和 `permission_requirement`；权限未验证时为 unknown，
CLI、环境配置或可能由管理员提供的值不会因此被丢弃，也不能直接升级为远程攻击者可控。
全部已识别 Source 事实仍保存；当前 `max_sources` 语义以上述 2.11 分批窗口为准。
该变化在重新分析时生效，旧产物缺失的 Source 不回填或伪造；其权限缺省按 unknown 展示。
路径/Source 存在、没有路径、没有告警或没有诊断均不能证明全覆盖或安全。
验收基于固定正反例的来源、断点、敏感参数和净化上下文；交付追踪属于 agents，不在静态分析中推断模型使用。

## 路径族组织（v4 引入）

证据存储仍为 2.10；v4 引入族目录，v5 沿用其 ID/分页契约。
有详情工具时初始摘要和 search 默认按 Source/Sink 事实的路径族导航，不展开路线步骤。
detail(族ID) 返回未报告路线目录；detail(路线ID) 仍提供完整步骤/源码/条件的分页。
族 ID 的作用域为当前项目快照，游标绑定快照、筛选、族和已报告路线；旧产物缺少族 ID 时只读计算，不回写。
族内目录的 via 是最多八个调用/返回定位，带 omitted_via；完整路线必须按路线 ID 读取，不将导航省略当作完整路径。
未配对危险操作保留独立事实 ID；族进度仅聚合已报告路线，不等于漏洞/路径完备性证明。
无详情工具的完整源码/累计预算条件继续直接交付有界路线，不发送无法解引用的族 ID。

`api_contracts.py` 增加类型化条件建议；独立知识 Agent 在 agents 层，LLM 接入在 llm 层。
本模块仍不导入/调用模型，不自动将知识建议编译成净化屏障或静态传播边。
以下 v3 章节描述路径保留能力；初始交付方式以本节为准。

## 当前增量：路径变体（证据包 2.10）

同一 Source/Sink 的值边序列、调用上下文与结构路线不再折叠成一个候选。
`path_group_id` 仅表示相同端点族，不表示已证明同一根因；各变体有独立候选/值流 ID，
继续通过同一 `SecurityEvidenceCatalog.detail()` 分页交付。服务先覆盖已建成值流的不同端点族，
再轮询其后续变体，剩余预算用于结构线索；不让低精度线索挤掉已建成传播链。
初始目录版本为 `security-path-summary-v3-variants`，不改变共同提示词、工具权限、调查轮次或报告机制。

`program_graph/value_paths.py` 是单路线和多路线共用的值查询；摘要生产者只请求一个结果，
安全消费者请求有界变体。默认每端点族保留 8 条，每 Source 跨过程最多 256 状态，
每次局部查询最多 2048 状态；结构查询默认 6 层、每目标 8 条、每 Source 2048 状态。
不同边序列保守地分别保留，只去重相同证据 ID，不宣称已证明安全语义等价。
循环、完整 CFG 分支、对象/工厂摘要内部的所有变体仍不穷举；预算与截断不是安全证明。

未知 API 的已跟踪实参、可解析目标和已知前缀进入传播诊断，返回关系仍为 unknown。
前缀仅保存定位/值槽元数据，最多 64 步并记录省略数；诊断还受原有分层留样预算约束。
可按 `_coverage` 的 unresolved 读取，不能把前沿当成已闭合 Source-to-Sink 路径。

`api_contracts.py` 定义 API 身份和逐返回分量的知识建议契约，不调用 LLM。
显式 LLM 入口在 `backend.app.llm.api_knowledge`，详见 LLM README。
建议没有自动注册为规则、没有进入静态产物或 A/B 输入；解释性 CWE 检索仍由 `knowledge.py` 负责。
Go `regexp.MatchString` 的待验证文本已修正为第二实参；Guard 仍为 not_verified，不自动 kill。

后文 2.7 等说明是先前的实体组织背景；当前版本以本节和契约代码为准。旧产物可读但不会补造新变体，
旧审计/实验记录不重建；新实验必须重新分析并记录新的证据/摘要版本。

语言源码适配实现已迁到 `language_adapters/`，公共安全 IR 仍由 `ir.py` 定义；旧 frontends 目录仅兼容导出。
相同任务的 Tree-sitter 解析由 syntax_analysis.session 有界复用；不改变 Source/Sink、传播规则或事实选择。

按需交付入口是 `evidence_query.SecurityEvidenceCatalog(raw_pack)`：`summary()` 调用公共路径选择器，`search()` 复用同一优先级目录、绑定快照和排除集合分页，`detail(id, section, offset, limit)` 解引用原候选或事实 ID，读取完整路径步骤或已有共享片段。不重新扫描、不调用 LLM、不把候选当结论。实际查询与已报告状态由 Agent 层负责；不能按需恢复保留阶段未保存的内容。旧 1.4 投影实现已经移除，普通 Agent、网页实验和命令行实验均使用这一入口。

特殊 ID `_coverage` 不参与候选选择：overview 返回全局覆盖及传播前沿计数，unresolved 分页提供全部局限、保留的传播前沿及扫描失败（不含内部异常正文），protections 返回规则覆盖配置而不是防护成立结论。前沿样本有界且明确标注截断，计数不是漏洞数；摘要省略的全局说明仍可查询。

证据包 2.8 增加 `DataFlowEvidence.path_conditions`，2.9 扩充表达式/多槽写回和 finally 的有界核验，兼容旧产物的空列表。`detail(id,
section="conditions", offset=0, limit=...)` 按同一 ID 分页提供核验摘要与分支条件位置；
`section_counts.conditions` 表示可读取条目数。初始路径目录仅增加已有核验状态，不塞入全图
或所有条件。函数内与已选跨过程路线均使用 program_graph 的唯一公共求解器；新路线核验
标记 `z3.scalar-route.v1`，替换旧“整体 unknown + 只查末段”的并行记录。直接实参/返回复制
按原调用上下文组合，已有对象/集合边界保留沿途片段但未知堆条件仍显式 unknown。
conditions 的 summary、waypoint、cfg_edge、segment、binding、branch 均按条目分页，
不把全部绑定或图边藏进一条无法续读的超大摘要。条件矛盾的原候选也保留，
便于审计原传播链与模型假设，不能把静态未知或有限核验当成“项目无风险”。

净化语义仍归各语言/库版本规则域：已有 `RulePackRegistry.register(RulePack)` 是显式规则
接入点，`CallRule` 描述 API 角色与适用语言，不等于净化有效。公共层不根据第三方函数名
生成安全结论，不联网猜测库行为，也不以 `SecurityKnowledgeProvider` 返回的 CWE 说明
代替经过验证的 API 模型。当前不接入外部库数据库；仓库内可见函数仍按普通项目代码分析。

初始摘要的唯一选择入口为 `evidence_selection.SecurityEvidenceSelector(raw_pack, paths=None).build(max_bytes=...)`，
输出 `security-path-summary-v2`，由 `encode_navigation()` 统一紧凑序列化和 UTF-8 计费。
普通 Agent、网页按需实验和累计预算补充实验均复用它，直接替换旧零散事实/实现组摘要，不再并行保留旧摘要引擎。
目录只包含 `propagation_path → structural_lead → operation_lead`；优先级是核验顺序，非漏洞概率。
同函数共现不证明调用关系，归入操作线索；Guard/Sanitizer、孤立 Source、规则说明及全局统计不填入摘要。
头部仅保留快照、类型/省略计数和必要语义，覆盖与完整局限通过 `_coverage` 查询。
短路径可展开全部原有步骤；长路径显式 `sequence_delivery=detail_only`，仍用原 ID 续读，不抽样首尾。
极长端点改为 ID 引用并标明 `locators_delivery=detail_only`，不以短的低优先级线索取代它。
不读取源码、不生成新关系、不实现反向切片，属于安全证据交付域。历史交付与当前设计见
[选择整改说明](../../../../docs/STATIC_EVIDENCE_SELECTION.md)。

本模块将依赖分析的结构拓扑、语言前端 IR、版本化安全规则和语言数据流分析组合成有界证据包，不执行项目代码，也不自行调用大模型。稳定入口为：

```python
pack = SecurityAnalysisService().analyze(
    project_root=project_root,
    dependency_graph=raw_multigraph,
    analysis_diagnostics=diagnostics,
    semantic_index=semantic_index,
)
```

新分析产物应传入 `semantic_index`；跨过程数据流优先消费其中的调用目标和解析置信度。为了
兼容旧产物，索引缺失时才回退到解析依赖图调用边。

简单对象识别由 `flow_analysis/objects.py` 组织构造器/工厂字段摘要，再由
`flow_analysis/object_references.py` 为已确认的局部直接引用别名建立共享字段身份。
Python、Java、JS/TS 共用公共 CFG、到达定义和值流算法，不新增语言私有污点引擎；
`flow_analysis/object_effects.py` 使用同一正常出口字段接口支持对象参数和接收者的有界写回；
已支持单类型工厂结果复用该接口。变换表达式不冒充对象，确定覆盖须由前端能力与 RD 共同支持，
异常边不经过后置定义。未知副作用、分支对象歧义和循环重复分配保留诊断。该能力只用于新分析的私有 Overlay，
不就地改变持久化 ProgramGraph，不回填旧实验；完整边界见该子目录 README。

JS/TS 新分析会在依赖图写边前优先采用官方 TypeScript 声明绑定；安全前端本身仍按已有
语法 IR 匹配规则，ProgramGraph/传播算法不变，不在这里再次启动编译器。工具版本、配置与
覆盖摘要保存在语义索引 backend_runs；路径边保留 resolution_method 与 fallback_reason。
声明绑定不等于唯一运行时实现，虚派发和缺依赖仍有显式边界。安装见
`backend/tools/typescript_semantics/README.md`。

`SecurityEvidencePack` 当前使用模式版本 `2.7`、IR 版本 `1.3`，并兼容读取 `2.1/2.2/2.3/2.4/2.5/2.6`；固定标记
`analysis_kind=static_security_evidence`。`structural_call_path` 只表示 Source 与 Sink
所在函数结构可达；`intra_procedural_dataflow` 表示单函数到达定义链，
`interprocedural_dataflow` 通过已解析调用的实参/形参绑定，或显式有界状态值边界连接不同函数，且最终变量进入规则指定的 Sink 实参。CFG 已参与计算，但尚未
证明具体路径在运行时可行，因此数据流仍保守标记为 `may_reach_sink`。

`detail(id, section=...)` 是唯一详细交付实现。overview 保留路径定位、原候选引用/前提及端点匹配上下文；flow 分页保留全部原步骤；snippets 用 `snippet_id` 引用同页 `snippets` 表，不重复嵌入正文；protections/unresolved 提供原事实、局限和未解析信息，不抽样路径或静默截断。旧 1.0～1.4 审计正文原样读取，不按新协议重建。旧格式说明仅作历史设计参考。

无 Sink/路径时返回空目录，不以孤立 Source、Guard 或统计填充；完整事实及已有片段仍可按已知 ID 读取。零条目不等于安全，统计与扫描局限通过 `_coverage` 查询。完整静态事实、坐标和规则描述继续保存在产物中。

`rule_coverage` 描述本次启用的规则、类别及框架匹配器，而非证明该框架存在或功能全覆盖。`coverage.entrypoints` 统计识别的入口记录，同一函数的不同路由可分别保留，不能代替所有 CLI/脚本/`input()` 入口；Sources/Sinks 是当前规则下的观察数，Candidates 是有界候选数而非漏洞数。零候选只能说明当前规则与扫描范围未产生候选，不能据此判定不存在 RCE 或其他安全问题。历史产物缺少规则描述时标为不可用，不根据新规则猜测历史覆盖。

## 数据所有权与去重

```text
program_index/ProgramIdentity
    ├── file_id / symbol_id / location_id / callsite_id
    ├── dependency_analyzer：拥有 contains/imports/inherits/calls
    └── security_analysis：拥有参数角色和未来值传播
```

依赖图调用边和 `IRCall` 使用相同 `callsite_id`。一个动态调用点可以拥有多个目标 `edge_id`，但只拥有一个调用点身份。安全数据流层不得再创建 caller-to-callee 结构边，只能引用 `callsite_id` 和已有 `edge_id`。

证据包 2.7 沿用顶层注册表：

- `entrypoints[id]`；
- `facts[fact_id]`；
- `call_edges[edge_id]`；
- `snippets[snippet_id]`。
- `dataflows[flow_id]`。

候选只保存这些实体的 ID，避免相同 Source、Sink、调用边和源码片段在多个候选中重复持久化。
片段 ID 基于实际路径、范围、正文、角色和截断/请求范围元数据，不再绑定某个事实 ID；同一物理资源可复用，不同角色/截断信息不能合并。`SourceSlicer` 在本次扫描内缓存已构造片段，避免每条候选及未配对事实重复切片；缓存与源码行缓存均不跨扫描。静态切片使用 `core.redaction`，不再独立维护一套脱敏。
候选配对预算优先已建立值流的 Source/Sink 端点，以及值流涉及函数内的 Guard/Sanitizer；全部已识别 Source 事实仍保留。
后两者仍为 `protection_effectiveness=not_verified`；函数关联不等于已经核实值、上下文和时序，
也不作为污点屏障。超限说明继续保留，完整扫描计数与实际保留数不可混用。
`DataFlowEvidence.value_boundary_ids` 是可选的状态绑定证据标识，旧产物缺省为空列表；
不会混入真正的 `call_edge_ids`。状态绑定保持 inferred/may，并将未解析原因和局限传入模型。

## 多语言流水线

JS/TS 复杂绑定通过公共 `value_binding` 协议进入程序图和安全 IR；Node.js/Express 服务端规则使用 `javascript-typescript-core/1.4`。各语言都不是完整覆盖，见 `docs/SECURITY_COVERAGE.md`。旧项目须重新分析才获得新规则事实。

```text
依赖图模块节点按语言分组
            ↓
FrontendRegistry → LanguageFrontend → SecurityProgramIR
            ↓
RulePackRegistry → SecurityRuleEngine
            ↓
Entrypoint / Source / Sink / Guard / Sanitizer
            ↓
ProgramGraphService → CFG → ReachingDefinitions → ValueFlow
            ↓
StructuralCallPathFinder + SourceSlicer
            ↓
SecurityEvidencePack 2.7
```

当前默认注册 Python、Java、Go、C/C++、JavaScript/TypeScript 前端。规则覆盖 Python 核心/FastAPI、Java 核心高价值 API、
Spring Web/Servlet、Go 标准库与 `net/http`、Gin、net、C/C++ 标准库/POSIX/SQLite，以及 JS/TS Node 平台和 Express 路由。依赖图中出现尚未注册的语言（目前 Rust）时，会写入
`unsupported_languages`、覆盖率和诊断，不会静默标记为已完成扫描。

## 文件与职责

| 文件 | 作用 |
| --- | --- |
| `contracts.py` | 证据实体、ID 引用候选、函数内/跨过程路径、覆盖率和 2.3 持久化契约。 |
| `ir.py` | 规则无关的 `SecurityProgramIR`、调用参数和源码位置。 |
| `registry.py` | 前端、语言语义和规则包注册表。 |
| `scanner.py` | 按依赖图语言范围编排全部已注册前端和规则包。 |
| `parameter_apis.py` | `forward_parameter_apis()` 消费既有调用索引、通用参数绑定和注册 Source 名称，有界转发参数 API 角色；不创建调用边或整对象污点。 |
| `language_adapters/base.py` | `LanguageFrontend` 抽象基类。 |
| `language_adapters/python.py` | Python AST 到通用 IR。 |
| `language_adapters/treesitter.py` | 多语言 Tree-sitter 文件编排、解析器池和失败隔离模板。 |
| `language_adapters/java.py` | Java 方法、注解、调用、条件和赋值目标到通用 IR。 |
| `language_adapters/go/` | Go 函数/方法、导入别名、参数类型、调用、条件和位置敏感赋值目标到通用 IR。 |
| `language_adapters/c_family.py` | C/C++ 共用声明符、namespace/class 作用域、头文件与词法名称过滤、精确调用返回值目标和诊断。 |
| `language_adapters/javascript.py` | JS/TS 共用 ESM/CommonJS、API 遮蔽、DOM/Express 接收者、精确属性读写和匿名回调身份的语法前端。 |
| `language_adapters/javascript_state.py` | 将已确认 React/Node 注册关系降低为有限状态值边界，不另建污点算法。 |
| `semantics/base.py` | `LanguageSemantics` 与参数绑定契约。 |
| `semantics/python.py` | Python 基础位置参数和关键字参数绑定。 |
| `semantics/java.py` | Java 位置实参与形参绑定。 |
| `semantics/go.py` | Go 普通位置实参与形参绑定。 |
| `semantics/c_family.py` | C/C++ 固定具名参数的位置绑定；与 Java、Go 共用 `PositionalLanguageSemantics`。 |
| `semantics/javascript.py` | JS/TS 复用位置绑定，仅绑定 spread 前的简单形参/实参；不假设异步/堆副作用。 |
| `flow_analysis/base.py` | 跨语言数据流分析器协议。 |
| `flow_analysis/call_graph.py` | 经语言语义执行实参到形参绑定；只读调用索引唯一实现位于 semantic_index.calls，旧名仅兼容导出。 |
| `flow_analysis/argument_slots.py` | 已解析调用的可变参数常量元素入口与 Go 精确多返回接收的私有 Overlay。 |
| `flow_analysis/call_results.py` | 统一单返回 Source、已解析项目调用及直接 return 的精确结果槽位；替代旧 Source 专用实现。 |
| `flow_analysis/objects.py` | `prepare_instance_fields()` 消费实例方法上下文、既有实例化目标及公共参数绑定，构建有限局部对象字段摘要与方法读取接口，不识别语言 AST 或新建调用目标。 |
| `flow_analysis/diagnostics.py` | 单次运行按原因分层的有界传播前沿、五类检查点定位、完整事件计数和省略说明，不生成安全结论。 |
| `language_adapters/python_callbacks.py` | 将明确 BackgroundTasks 注册的同文件静态回调参数转换为公共 may 值边界。 |
| `flow_analysis/call_effects.py` | 将规则声明的简单输出缓冲区写入降低为私有后置操作，复用公共 CFG/DFG。 |
| `flow_analysis/value_boundaries.py` | 将规则无关值边界接入公共 CFG/DFG 的私有 Overlay，保留原图和实际调用边身份。 |
| `flow_analysis/program_graph.py` | 把公共变量感知值流和已解析调用参数连接为安全 Source-to-Sink 证据。 |
| `rule_engine.py` | 与具体 AST 解耦的通用规则执行器。 |
| `rules/base.py` | `RulePack`、调用、访问、入口点和条件规则契约。 |
| `rules/python/standard_library.py` | Python 核心及常见 API 规则。 |
| `rules/python/frameworks/fastapi.py` | FastAPI 入口点和请求参数规则。 |
| `rules/java/standard_library.py` | Java 进程、SQL、文件、反序列化、网络等规则。 |
| `rules/java/frameworks/` | Spring 注解入口与 HttpServlet 继承式入口规则。 |
| `rules/go/standard_library.py` | Go 环境、HTTP、文件 Source 与进程、SQL、文件、网络、弱随机数 Sink。 |
| `rules/go/frameworks/net_http.py` | `http.HandleFunc/Handle` 注册式入口规则。 |
| `rules/c_family/standard_library.py` | 两语言共用的标准库/POSIX Source、Shell/exec、路径、格式字符串 Sink、比较 Guard 和 SQLite SQL 规则。 |
| `path_finder.py` | 引用依赖图 calls 边计算有界结构路径。 |
| `candidate_search.py` | 在完整事实注册表上分批、有界查询结构候选；明确记录未遍历窗口。 |
| `slicer.py` | 提取有界完整源码行并遮蔽常见凭据；实际显示行与请求行范围分开，超长单行显式省略。 |
| `knowledge.py` | 解释性 CWE/RAG 异步 Provider 预留接口，默认空实现；不绑定 API，不直接生成 Source/Sink/传播模型。 |
| `evidence_query.py` | `SecurityEvidenceCatalog`：既有产物的有界目录、快照绑定筛选及按 ID 分页详情。 |
| `evidence_selection.py` | `SecurityEvidenceSelector`：无源码正文的有界实现导航、原有关系完整投影与省略计数。 |
| `service.py` | 组合扫描、路径、切片和去重注册表。 |

## 入口点与 Source

入口点表示外部控制可以触发函数，Source 表示具体的不可信或外部数据值。两者不会混用：

- `GET /health` 是入口点，无请求参数时不是 Source；
- FastAPI `command: str` 路由参数是 Source；
- Spring 映射方法参数和 Servlet 请求参数是 Source，`HttpServletResponse` 等输出对象会被排除；
- Go `net/http` 处理器注册是入口点，`Request.FormValue/PostFormValue` 等具体读取调用是 Source；
- `os.getenv()` 返回值是外部配置 Source，利用仍取决于攻击者能否影响部署配置。

## 当前边界

- 已实现函数内变量级传播，并消费八语言 ProgramGraph 的字段/成员、静态下标和精确赋值配对；有界实参/形参、直接 return 和精确嵌套项目调用返回共用一个传播器。BackgroundTasks 的同文件静态函数及显式参数经公共 may 值边界连接；不模拟完整任务生命周期。另支持可变参数常量成员及 Go 直接多返回分量。词法路径不代表对象身份，尚未实现路径条件求解、完整堆/容器别名、动态下标及任意复杂表达式。
- 全部语言语义实现只建立调用实参和形参的语法绑定，不宣称值受污染。Java、Go、C、C++ 共用位置绑定实现，Python 保留关键字参数钩子。

2.6 首版实例字段能力：Python `__init__`、Java 显式构造器、JS/TS class constructor 的参数
可经直属字段传入简单读取方法或返回结果；分配位置产生稳定 `allocation_id`，不同局部实例和字段不混用。
入口为 `flow_analysis.objects.prepare_instance_fields(graph, semantic_index, binder, find_path, diagnostics)`，
返回 `(私有程序图视图, 初始化与工厂返回值边界)`；后者交给已有 `ValueBoundaryIndex`，
因此构造器内部直接使用实参的敏感操作也可追踪，不要求先能还原完整对象生命周期。
复用当前 `_path` 查询、CFG、到达定义和值流 Pass；公共 `ProgramArgumentBinder` 根据接收者上下文
统一排除隐式绑定参数，再使用语言参数语义，不靠 self 名称在对象引擎中分支。
字段来源取成功构造的正常出口，尊重安全常量、覆盖和不可达写入；摘要有独立 256 次参数×字段查询上限。
分配点为静态抽象而非运行时地址，全部为 inferred/may，不表示完整 points-to 或堆分析。

当前只承诺有唯一已解析构造器、具名局部实例及直属字段；Python、Java、JS/TS 已知局部对象
支持直接引用别名的字段共享和变量重绑定隔离。对象参数与接收者可在唯一目标、唯一实际类型下
通过正常出口写回直属字段，常量覆盖和条件写入分开处理。未支持的逃逸、嵌套对象修改和未知方法
不沿用旧摘要；分支对象歧义、循环内重复分配及引用预算超限也记录具体能力前沿。
已支持工厂直接返回新实例、返回未逃逸的局部实例、最多四层工厂转发，以及有限多类型的分支返回。
`flow_analysis.factory_returns.prepare_factory_returns` 只消费既有实例绑定与调用目标，逐个核对
可达 return 的定义来源；`instance_common` 提供共享绑定、结果槽位和私有图重算。
字段在实际 return 位置通过值边界传回，恢复匹配的调用上下文，不串联同一工厂的不同调用。
每个工厂最多 256 次返回身份查询，字段×返回数也不超过 256；只处理新发现工厂的真实调用者。
多类型返回保留 `FactoryVariant`，每个实际类型拥有独立字段子槽位，方法调用只能消费兼容类型的槽位；
普通字段读取可见其可能值并集，不能据此把一种类型的字段状态绑定给另一种类型的方法。
`ValuePathQuery` 是初始化与工厂共用的强类型路径查询协议，不新增第二套搜索实现。
不支持未知返回、容器包装、async/generator 返回包装、
嵌套实例化后的字段读取、构造器字段摘要中的项目调用返回、继承初始化、
装饰/动态类、JS 参数属性、完整字段别名及并发暂未建模。初始化体的实参传播与后续对象身份是
两个独立能力；不会把构造器实参或初始化 return 直接当成整个对象的返回污点。
`audio2sheet` 的 model_name→多分离器工厂→实例字段→对应 separate 敏感操作已能建立三条
新增 inferred/may 链；这不等于确认漏洞或可利用性。不求解 engine_name 等分支谓词，不回填旧实验。

字段链的 FlowStep 保留 `object_allocation_id/object_type_symbol/callsite_id/reference_edge_id`、构造器原始赋值位置及值映射；
按 ID 的 flow 详情和导航序列保留这些字段。已有 instantiates 边进入证据注册表供引用，但结构路径搜索
仍只遍历 calls，不伪造 caller→constructor 的普通调用边，也不自动认定防护或漏洞成立。
- Guard/Sanitizer 名称匹配仅保存观察，不再删除传播节点；在没有实际值、危险上下文和时序证明前，不能据此宣称防护有效。
- 完整依赖图、Repo Map、架构概述和系统生成命令不会写入安全证据包。
- 安全规则 IR 与 ProgramGraph 当前仍各自解析规则已覆盖语言的源码，但项目文件范围、符号和调用点身份已经统一；后续可在不改变契约的前提下共享语言前端语法缓存。

## C/C++ 首批能力与局限

`CSecurityFrontend/CppSecurityFrontend → SecurityRuleEngine → ProgramGraphSecurityFlowAnalyzer`
复用同一流水线。`getenv()` 返回值经赋值和已解析调用到 `system()/popen()` 的值流可验证；
环境变量保留 `external_configuration` 与攻击者可影响部署配置的前提。
`execl/execv/...` 只匹配可执行文件路径，固定程序的数据参数不自动等同于 Shell 注入；
`printf/fprintf/snprintf` 只匹配对应格式参数；`sqlite3_exec` 匹配第二个 SQL 参数。

库名匹配要求可观察的头文件，并排除扫描范围内同名函数、局部声明和宏，端点规则匹配置信度
为 `medium`。该过滤是保守语法依据，不是完整的预处理或编译器类型绑定；间接头文件、宏展开、
lambda、CLI 参数、指针别名和库函数副作用仍有缺口。可见的不支持调用会进入 `scan_failures`，
不能把 `completed` 理解为所有 C/C++ 语义已覆盖。

`fgets/fread/read/recv` 等输出缓冲区 Source 保存 `value_flow.output_arguments/output_targets`
和源码位置。已通过私有后置操作支持独立调用的具名缓冲区 may 写入；复杂指针等生成
`output_argument_binding_unresolved` 诊断，同操作多调用仍只保留局限/结构候选。
返回的数量/状态码不会替代缓冲区内容。通用 FILE/描述符读取使用 `unknown` 信任类别并
要求验证流来源，不能从读取 API 本身断言它就是攻击者控制的文件。

职责边界仍是：`projects` 保存/恢复产物，`project_analysis` 编排纯源码分析；
`security_analysis` 拥有安全角色和证据，`program_graph` 拥有 CFG/DFG，`syntax_analysis` 拥有
共享纯语法读取，`dependency_analyzer/semantic_index` 拥有跨文件调用解析。Agent 消费证据并验证
利用条件，不负责在模型对话中重新生成这些静态事实。

注册式入口规则会先按原有名称/后缀条件筛选可能的注册调用，再逐函数执行原匹配逻辑，避免对每个函数重复遍历所有无关调用；保持原调用顺序和首次匹配行为，不增删规则或改变安全结论。

## 继承与值存储的公共接入（ProgramGraph 1.13）

语言层只补语法/声明角色，`semantic_index.TypeHierarchy` 提供公共成员/基类查询，
`flow_analysis` 在私有图复用同一对象环境、参数绑定、正常出口与 RD/DFG。
本批支持有界继承方法选择与已解析父初始化、C++ 唯一多继承成员、Go/C/C++ 值结构体和直接指针，
并区分值参数、引用参数、Go 值/指针接收者。未知副作用不当作安全覆盖，声明无体不当作实现。
原规则、非原生解析和既有原生后端保留；没有新增另一套调用图或安全污点求解器。
具体范围/不支持项以 `flow_analysis/README.md` 的 1.13 小节与 `docs/SECURITY_COVERAGE.md` 为准；
1.13 本身没有启用内部子对象导出与组合引用写回；1.14 通过 ObjectSnapshot、ObjectReferenceWrite 和
ObjectCallCompiler 协议接入有界实现，工厂与普通对象调用共用身份重映射和原正常出口/DFG。
原始引用赋值及调用边会进入证据详情；未知分支、复杂混合写入与容器仍保留前沿。

1.16 将直接字面量集合的固定元素接入同一公共引用/正常出口接口，支持有界别名、逐元素参数写回、
新集合返回与已核实的借用返回。`flow_analysis/collections.py` 只适配分配事实；集合协议属于
`value_binding`，语法提取属于 `program_graph.frontends.collections`，规则没有复制出 dict/map 污点引擎。
准确边界见 flow_analysis README 的 1.16 节与 SECURITY_COVERAGE；动态集合/第三方 API 不因接口存在而全覆盖。
