# 静态安全证据模块

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

`SecurityEvidencePack` 当前使用模式版本 `2.3`、IR 版本 `1.3`，并兼容读取 `2.1/2.2`；固定标记
`analysis_kind=static_security_evidence`。`structural_call_path` 只表示 Source 与 Sink
所在函数结构可达；`intra_procedural_dataflow` 表示单函数到达定义链，
`interprocedural_dataflow` 通过已解析调用的实参/形参绑定，或显式有界状态值边界连接不同函数，且最终变量进入规则指定的 Sink 实参。CFG 已参与计算，但尚未
证明具体路径在运行时可行，因此数据流仍保守标记为 `may_reach_sink`。

面向模型的入口是 `SecurityEvidencePromptBuilder.build()/render()`，输出 `LLMSecurityEvidenceEnvelope 1.2`。该投影按问题相关性和风险选择候选，预算不足时整条省略并报告数量，不会截断成无效 JSON。完整设计见 `docs/STATIC_SECURITY_LLM_FORMAT.md`。

## 数据所有权与去重

```text
program_index/ProgramIdentity
    ├── file_id / symbol_id / location_id / callsite_id
    ├── dependency_analyzer：拥有 contains/imports/inherits/calls
    └── security_analysis：拥有参数角色和未来值传播
```

依赖图调用边和 `IRCall` 使用相同 `callsite_id`。一个动态调用点可以拥有多个目标 `edge_id`，但只拥有一个调用点身份。安全数据流层不得再创建 caller-to-callee 结构边，只能引用 `callsite_id` 和已有 `edge_id`。

证据包 2.3 使用顶层注册表：

- `entrypoints[id]`；
- `facts[fact_id]`；
- `call_edges[edge_id]`；
- `snippets[snippet_id]`。
- `dataflows[flow_id]`。

候选只保存这些实体的 ID，避免相同 Source、Sink、调用边和源码片段在多个候选中重复持久化。
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
SecurityEvidencePack 2.3
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
| `python_scanner.py` | 旧 Python 专用入口的兼容门面。 |
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
| `slicer.py` | 提取有界源码片段并遮蔽常见凭据。 |
| `knowledge.py` | CWE/RAG 异步 Provider 预留接口；当前默认空实现。 |
| `llm_contracts.py` | 面向模型的自包含安全候选协议。 |
| `llm_context.py` | 问题相关排序、路径压缩、预算控制和 JSON 序列化。 |
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
- 已知返回值 Sanitizer 会截断当前函数内的简单名字传播；Guard 和复杂 Sanitizer 是否有效仍需路径条件分析或 Agent 验证。
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
