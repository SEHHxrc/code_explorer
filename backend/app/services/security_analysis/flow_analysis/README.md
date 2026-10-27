# 数据流分析器

## 路径保留增量（证据包 2.10）

`program_graph.py` 的局部查询委托公共 `ValuePathSearch`，移除原有单路径 BFS 副本；
最终 Sink、调用实参、返回接收、已有值边界均沿不同值边继续展开。
`variants.py` 只去重同一 flow_id，按端点族有界保留较短/较长路线；不再因第一条 SAT 停止，
也不因端点相同而合并不同条件和调用前缀。默认保留 8 条、每 Source 跨过程 256 状态，
超过预算仍保留原路线与 unknown/截断信息。单分配对象/工厂摘要生产者目前仍只查询一条，
同一值边序列对应的完整 CFG 分支、循环和所有堆变体尚不穷举。
`FlowDiagnostics` 对未知 API 保存受污实参和有界已知前缀；只表示关系前沿，不推断 API 净化或返回透传。
路径缓存、预算和诊断状态使用单次分析上下文隔离；成功或异常结束都解除引用，
同一分析器并发处理不同项目时不共享运行状态，也不跨项目持有整张图。

Guard/Sanitizer 仍是观察。此批只校正参数定位、扩展已有值流路线，不启用按函数名切断传播，
也不让 LLM 知识建议直接修改确定性分析结果。

本目录把跨语言 `ProgramGraph` 的 CFG/值流 Overlay 与安全规则产生的
Source、Sink、Sanitizer 事实连接起来。语言前端只负责把源码降低到公共图协议，
这里不再为 Python、Java 等语言分别实现一套值传播算法。

ProgramGraph 1.20 / 证据包 2.9：最终安全私有 CFG 完成后统一重算控制依赖，
已有候选的函数内共同路线由 `program_graph.PathConditionQuery` 按需核验，结果保存于
`DataFlowEvidence.path_conditions`。本目录不另建 SAT 求解器，不把 Guard 名称当 kill。
`condition_routes.py` 从既有参数绑定、返回接收和对象/集合边界维护当前激活片段，交给
公共 `ConditionRouteQuery`；直接标量参数/返回可组合约束，未知边界保留具体原因。
新的 `z3.scalar-route.v1` 替换旧整体 unknown/末段局部双重记录，不保留第二套跨过程求解器。
矛盾模型结果不删除原候选；支持域、预算、激活帧/绑定、见证/矛盾前缀和源码位置均按 ID 分页。

| 文件 | 作用 |
| --- | --- |
| `base.py` | 定义 `DataFlowAnalyzer` 和公共 `ValuePathQuery` 协议，后者复用唯一局部路径算法。 |
| `condition_routes.py` | 将既有直接参数、返回和共享值边界转换为公共条件片段；不解析新调用或推断第三方净化语义。 |
| `call_graph.py` | 通过 `LanguageSemantics` 绑定实参和目标形参；旧调用索引名称仅再导出 semantic_index.calls 的唯一实现。 |
| `argument_slots.py` | prepare_argument_slots() 输入原图、调用索引和绑定器，只在私有入口定义可变参数常量成员，不再承担 Go 专用返回逻辑。 |
| `call_results.py` | prepare_call_results() 统一按精确实参/return 位置及聚合成员调用身份绑定结果；所有项目调用必须经过真实返回，不透传原始实参。所有可能 return 共有的接收路径建立私有子定义，避免常量兄弟分量被根值污染。 |
| `return_bindings.py` | 将新旧程序图统一投影到公共返回契约，选择真实接收模式、替换聚合成员的精确调用结果；不另建污点算法。 |
| `objects.py` | prepare_instance_fields() 返回私有字段 Overlay 和初始化实参值边界；字段摘要取正常出口，初始化体复用 ValueBoundaryIndex。同一算法供已适配语言使用，不拥有 AST、Source/Sink 规则或调用目标解析。 |
| `object_contracts.py` | LocalInstance、ObjectBinding、ObjectReferenceResolver 及 ObjectRootEffect；统一引用、直属字段读写接口、正常出口写入类别，不拥有 CFG 算法。 |
| `instance_common.py` | 结果槽位、引用赋值判定、逃逸/未知修改拒绝和私有图重算，初始化与工厂返回复用；实例/引用类型已移至 object_contracts，类型子槽渲染唯一位于 value_binding.objects。 |
| `object_references.py` | ObjectReferenceIndex：在公共 CFG 上计算名称与字段引用，逐跳绑定嵌套路径的最终直属字段；constructor_reference_fields 复用同一算法提取构造器纯引用存储，不创建调用目标、不另做污点传播。 |
| `object_effects.py` | ObjectCallEffects：对唯一项目调用核对对象根、入口字段和正常出口写回；对象参数与接收者共用摘要、既有参数绑定器及值边界，不另建污点算法。 |
| `object_snapshots.py` | 快照字段列举与调用上下文重映射；工厂和引用写回复用同一装配方式，保留内部共享、环与已绑定外部对象。 |
| `object_reference_effects.py` | 用符号形参查询正常出口引用来源，仍调用 ObjectReferenceIndex；不把临时形参身份当项目对象或第二套别名算法。 |
| `object_types.py` | 消费公共类型层次，区分实际对象类型与实现声明类型，过滤已有方法目标、选择有体初始化方法；字段隐藏暂留显式前沿。 |
| `constructor_effects.py` | 既有父/同类初始化调用的正常出口字段投影；复用参数绑定、字段摘要与调用后操作，不重扫 AST 或新增结构调用边。 |
| `object_storage.py` | 物化已知结构体/标量存储和内嵌值子对象、标记未知调用副作用；引用、复制与污点仍交给公共环境/原 Pass。 |
| `collections.py` | collection_instances() 只把公共集合分配事实适配为原 LocalInstance，不创建第二套引用或污点求解。 |
| `collection_effects.py` | BuiltinCollectionEffects 实现效果模型接口，prepare_collection_effects() 用原引用环境与正常后置操作打样局部原生列表的单次 append；其他操作保留未建模前沿。 |
| `post_calls.py` | normal_exit_values / append_post_operations：观察正常出口值、在调用正常返回后定义输出；构造器、对象写回及具名输出缓冲区复用，异常边不经过后置写入。 |
| `factory_returns.py` | prepare_factory_returns() 从直接或局部实例返回建立最多四层多类型字段接口，FactoryVariant 保留每类实际 return；不按工厂名/返回注解猜类型。 |
| `diagnostics.py` | FlowDiagnostics 按原因分层保留有界前沿、完整事件计数和省略量；不是独立漏洞或漏边数。 |
| `call_effects.py` | CallOutputEffects.prepare() 将已声明具名输出缓冲区在调用后定义为 may 值；状态码与内容分开，公共 Pass 重算私有图。 |
| `value_boundaries.py` | 消费语言无关的 IRValueBoundary；按原始位置定位函数，在私有 Overlay 中绑定捕获槽位和退出值，复用公共到达定义和值流。 |
| `program_graph.py` | 优先基于公共 `value_flow` 边，并兼容旧 `reaching_def` 产物，生成函数内及有界跨过程安全数据流证据。 |

当前处理：

- 框架参数等函数入口 Source；
- 安全规则命中的调用返回值 Source；
- 公共 ProgramGraph 中已经建立的变量定义、使用、改名和保守变换关系；
- 规则指定的 Sink 位置参数和关键字参数；
- 接收者型 Sink；
- 按配置预算进行已解析调用实参到形参传播，当前默认 8 层调用/静态值边界；
- 有界状态/回调值边界，与普通调用共用深度预算；
- 净化匹配只保留观察，不当作整节点传播屏障。

CFG 已参与到达定义计算，但尚未证明具体分支条件在运行时可满足，因此所有结果仍保守标记为
`may_reach_sink`。当前消费已注册语言公共 ProgramGraph 生成的字段/成员、静态下标
和精确赋值路径，并支持有限深度的直接跨函数返回值传播；已支持有限具名局部分配的构造器参数→直属字段→简单方法读取，
已支持唯一已知局部实例的直接名称引用别名；尚不覆盖完整对象/堆身份、字段或容器别名、动态下标元素身份、任意嵌套表达式和完整对象传播。语言特有控制语义若只能保守降低，
会记录在函数局限和证据中。

直接嵌套单返回 Source、已解析项目调用与直接 return 已有限支持；复杂表达式仍非完整模型。
Python args/kwargs、Java/Go 可变序列支持常量成员；Java 打包数组和 Go 字面量切片展开共用
BindingValue，安全常量也有独立定义。Go 直接 return 的分量按有序接收槽位分别回传，
裸命名返回和隐式多返回转发仍未建模。所有这些能力沿用同一传播算法与深度预算。

Python 直接元组/列表返回已支持单变量整体接收后按位置读取、直接/嵌套解包、具名聚合值后续解包、
星号尾部接收及返回成员里的直接 Source/项目调用。Source 在调用者或被调者都使用同一调用栈传播。
`return_binding_not_modelled` 记录未知形状、解包数量不符、预算等具体原因。
这仍不是完整容器分析：动态构造、聚合值经任意别名或包装函数转发后的成员精度、负下标归一化、
任意表达式里的嵌套调用及带后缀的星号解包尚不保证；未建立路径不能当作安全证明。
Go/TS 已有声明/引用通过公共 Pass 绑定值槽；路径工作列表使用边的值身份，变量名仅用于展示及
端点参数角色匹配。定义版本仍由节点区分，不把声明 ID 当 SSA 值。未绑定/歧义时保守 may，
`variable_identity_not_mapped` 在 value_identity 检查点记录；Python/Java 等缺少该事实时保留非原生路径。
声明身份绑定本身不是全语言身份覆盖或原生 SSA 消费，也不新增调用目标；局部对象引用由下面的独立有界 Overlay 提供。

`DataFlowAnalyzer.analyze()` 可接收 `semantic_index` 和 `value_boundaries`，后者缺省为空。
可选 `diagnostics=FlowDiagnostics()` 由调用方按运行创建，服务将结果保存在证据包的
`flow_diagnostics`；缺少种子、无可追踪敏感值、未解析调用、构造器堆效果和深度前沿分别记录。
常量 Sink 也可能没有可追踪变量，不能把该诊断当作解析失败。没有匹配到路径也不证明安全。

新诊断格式为 `propagation-frontier-v2`：默认总定位上限 200，每个原因最多 20 条；总额已满时，
后出现的少数原因可替换高频原因的多余定位，不让先扫描的分配事件占满列表。`counts` 是完整
事件次数，`sample_counts/omitted_counts` 分别表示实际保留/省略的定位数；样本不是均匀抽样。
`checkpoint` 区分 source、value_identity、call_return、field_memory、sink_argument、evidence_delivery；未知原因
保留为 unclassified。`diagnostic_kind` 区分映射缺口、未建模、预算及无可追踪值。
这些只分类实际记录的诊断，不表示五个检查点均已验证，也不把常量/无变量与模型失败混为安全结论。
`_coverage` 的 overview 提供完整计数，unresolved 分页提供实际保留的定位；旧 v1 产物原样读取，
不重新分类或改写旧记录。新增诊断元数据不改变 Source/Sink、调用边或求解路径。

对象分配、参数和字段隔离由公共 InstanceMethodContext 与字段摘要表达，语言适配在
`program_graph/frontends/objects.py`，不在本目录维护 Python/Java/JS 三套传播器。
构造器摘要只在既有 CFG 上查询成功出口字段来源，原始赋值位置进入证据；每个构造器最多
256 次参数×字段路径查询，超限明确诊断。逃逸、可变或未知方法接收者不建立完整实例链。
唯一已解析初始化目标的参数可经既有值边界进入构造器体，包括嵌套创建和工厂内创建；
初始化 return 不映射为分配结果。工厂的直接实例返回、局部实例返回和有限转发已共用同一方法读取接口；
全部可达 return 必须有已知实例来源；不同实际类型保留独立变体和字段子槽位，未知或包装返回明确诊断。
读取方法通过 ProgramCallSite.receiver_field_bindings 消费对应类型槽位，不能混入其他子类同名字段。
普通字段读取保留可能值并集；父字段覆盖复用公共访问路径杀死规则。方法目标仍必须已被依赖层解析，
不会因为模型知道某个类型就凭方法名生成新的调用边；仍有歧义或未知副作用时保留缺口。
继承初始化、复杂构造后的字段读取、静态共享字段、完整跨层方法副作用与并发仍是能力边界。
唯一已解析调用的直属字段参数写回与接收者修改见下方 1.11 阶段，不代表任意堆写入。
调用 Sink 核实 `read_variable`，属性输出核实 `target_variable`；不新增可能绕过覆盖写入的
同名透传边。最终 Sink 步骤的 `argument_bindings` 保存本次实际到达的位置/关键字/接收者。
`ValueBoundaryIndex` 不修改持久化 ProgramGraph；无边界时直接保留原图。
当前 JS/TS 前端提供简单 React useState、同请求 Node data→end 模型，字符串使用 CFG
退出值、数组仅支持直线累积。边界不是新的 Source 或实际 calls 边：证据使用独立
`value_boundary_ids`，并保留 inferred/may、低置信度及局限。它不能证明事件顺序、重渲染
或完整堆行为；公共协议可供其他语言适配，但没有自动实现其他语言的闭包分析。

工厂返回边界通过 `resume_callsite_id` 核对现有调用栈；匹配返回不额外消耗进入调用的深度预算。
函数内新产生的 Source 可反向到已解析调用者，但仍受预算约束。`source_reads_input=False`
用于核实直接创建并返回的写回字段，保留该终点初始化赋值、原始构造器位置和实例化边证据。
不能把仅存在于容器/表达式中的变量当成返回实例，也不把协程/Promise/迭代器当同步实例。

## 局部对象引用（ProgramGraph 1.9）

`ObjectReferenceIndex(function, instances, diagnostics=None)` 输入已有 CFG 和已识别的
`LocalInstance`，不重新解析源码。语言仅声明 `object_assignment=reference`，Python、Java、
JS/TS 共用同一工作列表算法。直接 `b=a`、多级别名、通过任一别名读写直属字段、根变量重新绑定
均按分配位置字段身份处理，再复用原到达定义和值流 Pass；不把整个对象标成污点。
已支持工厂的调用结果可建立局部别名，但工厂体内 `alias=worker; return alias` 的返回身份导出
仍未覆盖，不能从调用方别名能力推断工厂别名返回也已支持。

别名赋值位置作为 inferred/may 步骤进入方法读取、直接字段 Sink 和跨过程传值证据，
保留原始表达式，不把分支来源集合冒充唯一执行顺序。字段绑定只接受唯一且不含未知的分配；
分支歧义、循环内可能重复的分配、对象逃逸和未知副作用均明确保留缺口，不等于安全。
循环外分配经回边继续使用时，未计算的前驱不会被误作真实未知分支。

默认最多 100000 次状态更新、100000 个前后状态条目、每状态值最多 64 个别名来源节点。
超限清空精确引用结果并记录 `instance_alias_budget_exceeded`；歧义或重复分配记录
`instance_alias_not_resolved`，不静默保留部分精确映射。Go 值结构体/指针、容器内引用、
跨函数对象参数写回、修改方法、静态/全局共享状态、完整堆与并发尚未在此阶段扩展。

## 对象公共查询接口（ProgramGraph 1.10）

对象编排入口 `prepare_instance_fields` 通过结构化 `ObjectReferenceResolver` 协议消费现有
`ObjectReferenceIndex`，不要求实现继承某个 CFG 类；后续原生后端可以按能力提供相同接口。

| 方法 | 输入 | 输出与边界 |
| --- | --- | --- |
| `at(node, name, write=False)` | 公共操作、展示名、读/写方向 | ObjectBinding：候选分配与别名来源；unique_allocation 仅唯一非未知时有值。 |
| `possible(node, name)` | 操作前的对象名 | 唯一已知分配的 LocalInstance 实际类型变体；未知/多分配不猜测目标。 |
| `alias_origins(node, name)` | 操作前的对象名 | 原始赋值位置列表，分支合并为 may 证据，不证明执行顺序。 |
| `field_binding(node, name, write=...)` | 操作、规范访问路径及读/写方向 | StorageValueSlot 或 None；当前为字段/固定元素/未知元素摘要的联合类型，中间引用不唯一时不冒充精确对象。 |
| `bind_fields(function)` | 相同函数分区的私有视图 | 绑定结构化字段槽并复用原 RD/DFG 的新图，不修改输入图。 |

`ObjectFieldSlot` 的定义和稳定类型槽渲染位于 `value_binding.objects`；别名、方法及工厂不再
各自维护字段身份编码。证据优先直接读取结构化分配身份，历史 1.9 私有 Overlay 才走单一字符串兼容入口。
正常生产者图只定义可缺省字段，不填安全 Overlay；旧产物继续读取，不重建历史记录。
1.10 阶段只收敛接口；1.11 阶段按下面的有界契约增加实际写回。未建模调用、嵌套对象修改和
未知副作用仍拒绝沿用旧字段摘要，不能仅凭接口存在宣称完整堆链路闭合。

## 公共值时序与对象调用写回（ProgramGraph 1.11）

公共层只跟踪已绑定值如何进入操作、到达正常出口以及回到调用后的定义，不判断漏洞，
不把 Guard/Sanitizer 名称当作清洗证明，不因语法中出现一个变量就认定对象别名。
`ProgramArgumentBinder.bind_references(call, target)` 复用原参数语义，只接受前端通过 AST 确认的
`positional_references/keyword_references`；同一变量出现在不同参数位置仍逐位置核对。
普通标量、参数重绑定、变换表达式、接收者身份与对象字段写入不是同一件事。

入口仍为 `prepare_instance_fields(graph, index, binder, find_path, diagnostics)`；内部
`ObjectCallEffects` 按需产生 `ObjectRootEffect(root, fields, written_fields, must_write, normal_exit_id)`。
它只缓存每函数/对象根的有界接口，不缓存整份临时函数图。

1. 核对现有唯一项目调用目标、已知唯一局部分配和实际类型；构造器结果与已支持单类型工厂结果
   共用权限回调，不能仅因调用已解析就忽略逃逸。
2. 对象参数字段通过调用点 `argument_field_bindings` 绑定；接收者沿用 `receiver_field_bindings`。
   字段入口复用原 RD/DFG，普通实参仍由原绑定器处理，不把整个对象标污。
3. `normal_exit_values(function, names)` 生成私有正常出口观察点；原异常出口保留。
   `normal_exit_writes_modelled=False` 的旧/不完整前端不能强更新；确定覆盖必须由当前正常出口
   到达定义支持，条件写入保留原值，不按描述文字猜测确定性。
4. `append_post_operations(function, operations)` 在正常返回后定义各字段，调用前读取及异常边
   均不能经过该写入。构造器和输出缓冲区也复用公共时序实现。
5. 字段从被调函数正常出口经既有 `IRValueBoundary` 回到精确后置节点，`resume_callsite_id`
   核对原调用上下文；被调者内部新产生的 Source 也能回传。原始字段赋值、调用点、分配及类型
   进入证据，不把生成的观察/写回节点冒充源码中的新操作。

边界的 `source_node_id/target_node_id` 可精确定位同一源码范围的多个私有操作，显式 ID 失效即拒绝，
不退回行号猜测。每对象根及每调用绑定的字段总量上限 256；预算、别名冲突及未支持副作用保留诊断。
支持 Python、Java、JS/TS 已适配的直属字段、常量覆盖、条件写入、复合赋值、别名及跨文件，
不等于模型已验证路径运行可行。

当前拒绝：同一对象占据多个对象形参/接收者、对象根重绑定、局部对象引用在被调者中再次逃逸、
嵌套闭包或未知对象调用、同操作多调用、递归/多层对象修改、动态/嵌套字段、多实际类型的写回及异步包装。
工厂体内先修改对象再返回、工厂别名返回、容器、静态/全局共享状态、Go 值结构体/任意指针和并发
仍未在本批打通；未建立路径或确定覆盖不能解释为安全证明。非原生解析和既有规则保留。

## 公共嵌套组合（ProgramGraph 1.12）

入口与查询协议不变，不增加独立图或污点求解器。语法前端只增加直接引用 RHS 事实；同一公共
引用环境现在同时维护名称→对象与字段→对象，仍只在唯一非未知分配上绑定末级直属字段。
`ObjectFieldSlot` 保持原结构；组合与继承不是同一种关系。1.12 本身不展开祖先实现；继承与值存储由下节 1.13 增加。

- 支持 Python、Java、JS/TS 的构造器形参直接保存、局部字段赋引用、子对象再取局部别名，
  以及已有唯一构造器的 `parent.child = new Child(...)` / Python 对应分配。
- 多层读取、末级标量写入、常量覆盖、共享子对象及字段/局部根重绑定使用原 RD/DFG。
  被重新赋值的是组合引用时，不清除仍被其他引用持有的旧子对象状态。
- 已有唯一项目目标的对象参数、接收者沿用 `ObjectCallEffects`：可以读取/写回已知组合路径的
  末级标量，也可把子对象作为直接实参；调用内改变组合引用、创建子对象别名后间接修改等仍拒绝。
- 构造器引用摘要只接受当前正常出口一致的纯存储，复用引用环境，不凭普通污点依赖推断对象身份。
  真实构造器字段赋值与调用方组合赋值位置进入既有证据步骤及按 ID 分页，不把生成节点当源码。
- 对象环不展开堆，只按请求路径逐跳查询；默认最多 16 段。深度、引用状态预算或中间对象歧义
  显式诊断，未建链不证明安全。保留原 100000 次更新、100000 状态项、64 来源节点上限。

同对象多参数/接收者（包括两个根共享已参与接口的子对象）继续保守拒绝，本轮未扩展；容器、
动态属性、属性描述器/代理、构造器内修改传入子对象、组合对象经工厂返回时的内部引用导出、
跨过程组合引用写回、任意堆、完整 SSA 污点和并发仍有边界；继承、值结构体和指针新增的有界能力见下节。
旧图可读但不补造新引用事实。

验证：后端完整离线回归 763 项通过；最后的程序图/返回/对象/工厂兼容专项 80 项通过，均无跳过。
未调用真实模型、未修改实际配置或旧实验；本批未重跑真实项目，不将用例覆盖等同于完整堆分析。

## 继承、多继承与值存储（ProgramGraph 1.13）

`semantic_index` 统一拥有类型/直属字段、继承/实现/嵌入与 C++ 虚基类事实。
`TypeHierarchy` 同时供依赖成员查找和本目录的对象消费者使用：Python 用 C3；Java 类实现与
接口声明分开；C++ 沿基类子对象支路查找，非虚菱形保留不同子对象，虚基类共享身份。
不唯一的成员不按遍历先后选定；抽象声明没有方法体时不能当作已实现。

已接入并有正反例的范围：

1. Python 继承初始化及多层/多继承方法选择；Python/Java/JS/TS 已解析显式父初始化的字段来源，
   子类后续常量覆盖保留。`constructor_effects` 复用真实调用点、原参数绑定与正常出口摘要。
2. C++ 已知局部结构体的唯一继承字段与实例方法，包括简单虚菱形；裸的直接成员字段排除局部遮蔽后
   与 `this` 读取统一。非虚菱形歧义不合并。Java 同名隐藏字段暂不按一个槽合并，显式报告前沿。
3. Go/C/C++ 直接声明/已知字面量的值结构体、内嵌值复制及指针字段浅复制；`b=a` 不等于 `p=&a`。
   名称、字段引用、存储单元与其中的指针值由同一 `ObjectReferenceIndex` 区分。
4. 直接取址、显式解引用、C/C++ `->` 与 Go 隐式字段解引用；已知标量变量取址与写回。
   唯一项目函数的值参数只修改副本，指针参数/C++ 引用及 Go 指针接收者回到原存储；
   Go 值接收者不写回调用方。仍使用原字段接口与 `IRValueBoundary`，不新增调用边。
5. 未知调用可能修改存储时，在该调用后失效引用；读写仍留下诊断，不把旧字段或文本同名当精确证据。
   明确规则声明的输出写入先进入图，再投影到已知值字段；输出状态码不被当作内容。

默认仍限制 16 层嵌套、每函数 256 个存储对象与每调用 256 个字段，沿用原引用状态预算。
没有类型布局的自定义类型不当作标量；旧调用方不传类型索引时继续原保守行为。

这不是完整语言内存模型：未实现任意指针运算/转换、指向字段内部的地址、数组指针、联合体别名、
malloc/free 与生命周期、C++ 用户自定义复制/移动和完整初始化列表、基类指针调整/限定成员访问、
完整虚派发支配关系、Go 嵌入成员自动提升、Python 全部协作式 super/元类/描述器。
部分原本合法但无法唯一证明的成员会留前沿，不宣称漏检已消失。
1.13 本身没有启用组合对象经工厂返回的内部引用导出及组合引用写回；下节 1.14 接入有界实现。
这些能力只扩展值/字段跟踪，不认定防护成立、运行时可达或漏洞可利用。

## 工厂对象快照与组合引用写回（ProgramGraph 1.14）

旧的按返回变量名枚举构造器方式已替换为 `ObjectReferenceResolver.snapshot`：每个可达 return
查询当时的引用环境，导出根与可达对象、规范路径、类型变体、共享字段引用。SnapshotObject 按分配去重，
环只保留引用，不递归复制；SnapshotReference 只描述关系，字段值仍通过既有 IRValueBoundary 传递。
ObjectSnapshot 不包含全仓库对象、源码或漏洞结论，不是新建 CPG/污点求解器。

- 支持已知工厂返回内部组合、内部字段作为返回根及已核实别名；保留原四层工厂深度上限。
  导入按调用上下文重映射新分配，内部共享仍共享，不把两次工厂调用混在一起。
- `ObjectRootEffect.reference_writes` 区分正常出口改指向哪个形参/子路径，或导出哪个局部新对象。
  调用方重新核对实际对象和目的字段；新对象引用已知外部形参时保留原身份，不克隆外部存储。
- Python、Java、JS/TS 的参数/接收者以及 Go/C/C++ 直接指针字段均复用 ObjectCallEffects。
  值参数与 Go 值接收者仍不写回原存储；替换引用不等于清空旧对象，旧别名和其他组合关系保留。
- 先编译局部已知调用，再按新工厂结果逐轮接入后续效果。ObjectCallCompiler 协议将权限证明与
  编译交给同一实现；私有 object_effect_applied/arguments/receiver 标记避免重复生成后置节点。
  前一引用写回进入环境后再绑定后续调用，正常出口观察点不被反当作新的源码访问。
- 引用写入用私有 `object_reference_bindings` 表达，不把对象地址或对象整体当污点内容。
  新对象的字段值仍由正常出口观察和原值边界传输；异常边不经过正常写回。
- 真实引用赋值的原始文件、行列、已有 callsite/edge 进入 FieldValueOrigin 和 FlowStepEvidence，
  不把生成节点当新的源码事实；按 ID 分页保留这些信息。

现有边界：同一对象占多个已参与接口的参数/接收者仍拒绝；同一个被调函数内“替换引用后继续操作其
深层字段”、局部别名间接改写、递归/未知调用、容器/动态键和完整并发没有因此变为全覆盖。
工厂借用未绑定形参/全局对象的内部关系不能自动克隆为新对象；已知工厂局部分配与引用写回中的
外部实际对象绑定不是全局 points-to。Go 同名覆盖内置函数导致实参被语法树识别为类型表达式时，
当前公共语法前端仍可能缺少运行时使用点，需要后续编译器语义校准。

默认每份快照最多 256 个可达对象/引用、最多 16 层路径，工厂字段×返回预算仍为 256；
调用效果最多 64 轮，未知引用分支与预算留下诊断，不挑第一个目标、不把无链解释为安全。
原非原生分析与原生声明工具均保留。旧图的新字段缺省为空/False，恢复历史不补造新事实；
要获得新关系需重新分析项目。安全证据格式仍使用现有 ID/位置/步骤机制，不向模型发送整份堆快照。

## 端到端断点与值查询来源（ProgramGraph 1.15）

安全编排在全部私有值槽/边界准备后调用 `program_graph.value_query.ValueFlowQuery`，后续路径工作列表
仍只读取同一份公共边及真实 callsite/slot。适用函数使用原生 Go Def-Use，TS 使用原生身份+公共求解；
内存等不适用函数整函数回退，原对象字段/工厂/写回不因引入 SSA 丢失。详见 ProgramGraph README。

`flow_diagnostics.value_backends` 保存每个方法的实际选择，`value_backend_counts` 只是后端使用计数，
不是完成率/漏洞数。原诊断事件采样与计数不被这些元数据污染。`SecurityEvidenceCatalog.detail`
的 `_coverage` overview 仅带紧凑计数，不内嵌整仓库函数表；诊断调用方可用 `section="backends"`
分页读取完整选择。此诊断分页没有改变 Agent 工具参数枚举、实验提示词或初始路径导航。

`test/fixtures/chain_acceptance.json` 固定 9 组源码正反例，`test/test_chain_acceptance.py` 逐项核对
入口/Source、已解析调用、对象/框架边界、真正敏感参数和证据 ID 完整分页；固定安全值、状态分量、
无关实参及普通转义显示不能凭附近存在输入就被算作同一条值链。框架缺版本时只声明 API 模式验证。
`test/test_value_query.py` 单独使用真实 Go/TS 工具验证原生消费、匿名身份、φ/覆盖、接收者/返回槽及回退。
测试期望位于模型不可见的测试目录，不向 LLM 增加样本答案清单；真实模型实验本批不重跑。

## 集合元素与借用返回（ProgramGraph 1.16）

这里的集合是 dict/map/list/array 等数据结构，不涉及 Docker。新增 `collections.collection_instances`
只把公共分配事实适配为 LocalInstance，不创建另一套引用工作列表。字段、元素、嵌套对象和集合
共用 ObjectReferenceIndex；`field_binding` 返回 StorageValueSlot（ObjectFieldSlot 或 CollectionElementSlot）。
固定键/下标逐跳定位末级分配，真实别名赋值和原源码位置仍进入既有 ID 证据。

- Python、Java、JS/TS、Go 的直接字面量固定元素支持局部别名、覆盖和独立分配隔离。
- 唯一项目调用沿原 ObjectCallEffects 接入逐元素入口/正常出口写回；Go map/slice 按引用共享，
  固定数组复制与值参数不写回原数组。新增固定字典键按实际分配记录，不按全项目键集合展开。
- 新集合工厂返回复用 ObjectSnapshot，按调用隔离新分配；直接返回字面量保留原单值/多返回契约。
- `object_reference_effects.reference_interface` 一次公共引用查询同时输出组合引用写回和借用返回。
  所有正常出口明确返回同一已绑定形参/静态子路径时，调用后复用原分配而不是克隆对象；
  返回路径前缀若在函数内重绑、混合/隐式返回或 Go 值参数，则不猜测借用返回。
- Python 已知完整序列的负下标按长度归一；Python 整数/字符串键隔离，JS 数字/字符串属性键按语言归一。
  字符串键中的点号与类型子槽标记不被错误拆分。

边界仍包括动态下标、切片、append/pop/reverse 等结构变化、迭代、Java Map/List API、C/C++ 数组指针、
未接入的集合构造器/外部 API、动态对象、任意别名和并发。未知调用后失效已知引用并诊断，不沿用旧槽
证明精确链路；不完整字面量记录 collection_layout_incomplete。没有路径不等于安全，也不意味着漏洞不存在。
原非原生解析与原生语义工具保留，未更改 LLM 提示词/预算、实际 .env、旧实验记录或自动运行新 A/B。

## 收紧范围后的动态键与集合效果（ProgramGraph 1.17）

动态键常量解释位于 program_graph.passes.selectors，本模块消费其唯一键/有限键/未知键投影。
`CollectionSummarySlot` 与真实字符串键隔离；未知读只合并同一集合的元素内容，未知写弱更新该分配
实际观察的固定槽与摘要槽。后续确定覆盖仍生效；对象引用被未知键改写后不沿用旧 child 身份。
跨函数未知元素写回沿原 ObjectCallEffects 的正常出口和值边界接入，不另建 dict/map 污点算法。

`value_binding.collections.CollectionEffectProvider` 是操作模型接口，输入语言、调用视图和已核实集合布局，
返回 CollectionEffect 或 None；None 仍走原未知副作用诊断，不表示安全/只读。
`collection_effects.prepare_collection_effects` 用原引用环境、未知调用检查和 append_post_operations 装配效果，
只打样 Python 字面量原生可变 list 的单次 append：支持直接别名、固定元素写入、常量反例、后续覆盖，
并区分 append 返回 None 与追加内容。模型版本保存在私有 call.collection_effect_model，避免平行布尔标记。

循环/多次追加、不完整长度、未知修改后的列表、同操作多个调用、参数在本操作被覆盖以及 return 内调用
仍不猜测；追加改变长度后，负下标退为 may 元素读取，而不是沿用旧长度证明精确位置。
其他语言的集合变更、切片、排序、删除和迭代只预留效果契约，未声称已实现；没有展开标准库源码。
没有动态访问或候选集合操作的函数跳过新增求值/效果准备，减少无效工作。

普通传播默认预算从 4 提高到 8，仍保留到界诊断；结构调用路径搜索默认 6、工厂快照转发默认 4 不变。
已建立数据流直接生成候选路径，不受结构搜索 6 层二次截断。未增加递归摘要、完整闭包、并发或完整 PDG。
测试检查 8 层已解析调用正例、固定安全实参反例与 9 层终止；预算触顶事件本身不等于独立漏报。
旧图新字段缺省可读取，但不重建历史实验；新能力需要重新分析，模型提示词/.env/旧记录不修改。
1.17 完整后端离线回归 823 项通过、零失败/错误/跳过；最终接收模式同步另做 16 项定向回归通过。
测试阻止外部模型 HTTP，没有运行真实 A/B，不把用例通过解释为所有动态集合或真实项目已全覆盖。
