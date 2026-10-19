# 静态安全证据模块

按需交付入口是 `evidence_query.SecurityEvidenceCatalog(raw_pack)`：`summary()` 调用公共路径选择器，`search()` 复用同一优先级目录、绑定快照和排除集合分页，`detail(id, section, offset, limit)` 解引用原候选或事实 ID，读取完整路径步骤或已有共享片段。不重新扫描、不调用 LLM、不把候选当结论。实际查询与已报告状态由 Agent 层负责；不能按需恢复保留阶段未保存的内容。旧 1.4 投影实现已经移除，普通 Agent、网页实验和命令行实验均使用这一入口。

特殊 ID `_coverage` 不参与候选选择：overview 返回全局覆盖，unresolved 分页提供全部局限和扫描失败（不含内部异常正文），protections 返回规则覆盖配置而不是防护成立结论。摘要省略的全局说明仍可查询，不能把启用规则当作实际框架使用或完整覆盖。

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

`SecurityEvidencePack` 当前使用模式版本 `2.4`、IR 版本 `1.3`，并兼容读取 `2.1/2.2/2.3`；固定标记
`analysis_kind=static_security_evidence`。`structural_call_path` 只表示 Source 与 Sink
所在函数结构可达；`intra_procedural_dataflow` 表示单函数到达定义链，
`interprocedural_dataflow` 通过已解析调用的实参/形参绑定，或显式有界状态值边界连接不同函数，且最终变量进入规则指定的 Sink 实参。CFG 已参与计算，但尚未
证明具体路径在运行时可行，因此数据流仍保守标记为 `may_reach_sink`。

`detail(id, section=...)` 是唯一详细交付实现。overview 保留路径定位、原候选引用/前提及端点匹配上下文；flow 分页保留全部原步骤；snippets 用 `snippet_id` 引用同页 `snippets` 表，不重复嵌入正文；protections/unresolved 提供原事实、局限和未解析信息，不抽样路径或静默截断。旧 1.0～1.4 审计正文原样读取，不按新协议重建。旧格式说明仅作历史设计参考。

无 Sink/路径时返回空目录，不以孤立 Source、Guard 或统计填充；完整事实及已有片段仍可按已知 ID 读取。零条目不等于安全，统计与扫描局限通过 `_coverage` 查询。完整静态事实、坐标和规则描述继续保存在产物中。

`rule_coverage` 描述本次启用的规则、类别及框架匹配器，而非证明该框架存在或功能全覆盖。`coverage.entrypoints` 是已识别的外部触发函数数，不能代替所有 CLI/脚本/`input()` 入口；Sources/Sinks 是当前规则下的观察数，Candidates 是有界候选数而非漏洞数。零候选只能说明当前规则与扫描范围未产生候选，不能据此判定不存在 RCE 或其他安全问题。历史产物缺少规则描述时标为不可用，不根据新规则猜测历史覆盖。

## 数据所有权与去重

```text
program_index/ProgramIdentity
    ├── file_id / symbol_id / location_id / callsite_id
    ├── dependency_analyzer：拥有 contains/imports/inherits/calls
    └── security_analysis：拥有参数角色和未来值传播
```

依赖图调用边和 `IRCall` 使用相同 `callsite_id`。一个动态调用点可以拥有多个目标 `edge_id`，但只拥有一个调用点身份。安全数据流层不得再创建 caller-to-callee 结构边，只能引用 `callsite_id` 和已有 `edge_id`。

证据包 2.4 使用顶层注册表：

- `entrypoints[id]`；
- `facts[fact_id]`；
- `call_edges[edge_id]`；
- `snippets[snippet_id]`。
- `dataflows[flow_id]`。

候选只保存这些实体的 ID，避免相同 Source、Sink、调用边和源码片段在多个候选中重复持久化。
片段 ID 基于实际路径、范围、正文、角色和截断/请求范围元数据，不再绑定某个事实 ID；同一物理资源可复用，不同角色/截断信息不能合并。`SourceSlicer` 在本次扫描内缓存已构造片段，避免每条候选及未配对事实重复切片；缓存与源码行缓存均不跨扫描。静态切片使用 `core.redaction`，不再独立维护一套脱敏。
`DataFlowEvidence.value_boundary_ids` 是可选的状态绑定证据标识，旧产物缺省为空列表；
不会混入真正的 `call_edge_ids`。状态绑定保持 inferred/may，并将未解析原因和局限传入模型。

## 多语言流水线

JS/TS 复杂绑定通过公共 `value_binding` 协议进入程序图和安全 IR；Vue/React/Node 核心规则使用 `javascript-typescript-core/1.2`。各语言都不是完整覆盖，见 `docs/SECURITY_COVERAGE.md`。旧项目须重新分析才获得新规则事实。

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
SecurityEvidencePack 2.4
```

当前默认注册 Python、Java、Go、C/C++、JavaScript/TypeScript 前端。规则覆盖 Python 核心/FastAPI、Java 核心高价值 API、
Spring Web/Servlet、Go 标准库与 `net/http`、C/C++ 标准库/POSIX/SQLite，以及 JS/TS 浏览器 DOM、Node 平台和 Express 路由。依赖图中出现尚未注册的语言（目前 Rust）时，会写入
`unsupported_languages`、覆盖率和诊断，不会静默标记为已完成扫描。

## 文件与职责

| 文件 | 作用 |
| --- | --- |
| `contracts.py` | 证据实体、ID 引用候选、函数内/跨过程路径、覆盖率和 2.3 持久化契约。 |
| `ir.py` | 规则无关的 `SecurityProgramIR`、调用参数和源码位置。 |
| `registry.py` | 前端、语言语义和规则包注册表。 |
| `scanner.py` | 按依赖图语言范围编排全部已注册前端和规则包。 |
| `frontends/base.py` | `LanguageFrontend` 抽象基类。 |
| `frontends/python.py` | Python AST 到通用 IR。 |
| `frontends/treesitter.py` | 多语言 Tree-sitter 文件编排、解析器池和失败隔离模板。 |
| `frontends/java.py` | Java 方法、注解、调用、条件和赋值目标到通用 IR。 |
| `frontends/go.py` | Go 函数/方法、导入别名、参数类型、调用、条件和位置敏感赋值目标到通用 IR。 |
| `frontends/c_family.py` | C/C++ 共用声明符、namespace/class 作用域、头文件与词法名称过滤、精确调用返回值目标和诊断。 |
| `frontends/javascript.py` | JS/TS 共用 ESM/CommonJS、API 遮蔽、DOM/Express 接收者、精确属性读写和匿名回调身份的语法前端。 |
| `frontends/javascript_state.py` | 将已确认 React/Node 注册关系降低为有限状态值边界，不另建污点算法。 |
| `semantics/base.py` | `LanguageSemantics` 与参数绑定契约。 |
| `semantics/python.py` | Python 基础位置参数和关键字参数绑定。 |
| `semantics/java.py` | Java 位置实参与形参绑定。 |
| `semantics/go.py` | Go 普通位置实参与形参绑定。 |
| `semantics/c_family.py` | C/C++ 固定具名参数的位置绑定；与 Java、Go 共用 `PositionalLanguageSemantics`。 |
| `semantics/javascript.py` | JS/TS 复用位置绑定，仅绑定 spread 前的简单形参/实参；不假设异步/堆副作用。 |
| `flow_analysis/base.py` | 跨语言数据流分析器协议。 |
| `flow_analysis/call_graph.py` | 已解析调用边的正向/反向索引和经语言语义执行的实参到形参绑定。 |
| `flow_analysis/argument_slots.py` | 已解析调用的可变参数常量元素入口与 Go 精确多返回接收的私有 Overlay。 |
| `flow_analysis/call_sources.py` | 将直接嵌套 Source 精确绑定到实参槽位，不把包装函数结果当 Source 原值。 |
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
| `slicer.py` | 提取有界完整源码行并遮蔽常见凭据；实际显示行与请求行范围分开，超长单行显式省略。 |
| `knowledge.py` | CWE/RAG 异步 Provider 预留接口；当前默认空实现。 |
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

- 已实现函数内变量级传播，并消费八语言 ProgramGraph 的字段/成员、静态下标和精确赋值配对；当前安全规则覆盖的 Python、Java、Go、C、C++ 可执行最多四层的已解析调用实参到形参、直接返回值到调用结果变量传播。另支持直接嵌套单返回 Source、可变参数常量成员及 Go 直接多返回分量。词法路径不代表堆对象敏感分析，尚未实现路径条件求解、对象/字段/容器别名分析、动态下标元素身份、通用嵌套调用结果和完整对象传播。
- 全部语言语义实现只建立调用实参和形参的语法绑定，不宣称值受污染。Java、Go、C、C++ 共用位置绑定实现，Python 保留关键字参数钩子。
- 当前规则标记的返回值 Sanitizer 会截断函数内简单名字传播，但尚未按目标 Sink 上下文证明净化有效，可能漏链。
  Guard/Sanitizer 匹配不能直接作为防护成立结论；该上游边界不能由交付选择器修复。
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
