# program_graph 模块

本模块是依赖图和静态安全分析之间的最小 CPG 公共层。它不替代前端依赖图，也不向浏览器返回函数内部的全部节点。

`value_paths.ValuePathSearch(function).search(...)` 查询既有值边的有界不同序列，返回
`ValuePaths(paths, truncated, explored_states)`；单路径和多路径共用算法。
函数实例内缓存邻接与目标反向可达区域，汇合点按路线保留值身份；不重复解析、不新增污点规则。
默认局部查询 2048 状态，拒绝同一路线重复值状态并披露循环/预算边界，不等于完整 CFG 路径枚举。

Go 原生 SSA 由 `language_backends` 接入，交换事实按已有函数符号保存于
`semantic_index.native_ssa`，可选 `SemanticSSAView` 提供查询。1.15 已通过 ValueFlowQuery
为可完整映射的函数选择原生 Def-Use；内存/框架等函数整函数回退公共求解，详见后文。
不能把工具运行成功当作所有函数已切换，更不另建 Go 污点求解器。

当前产物版本为 1.20，兼容读取 1.0～1.19。`value_flow.read_variable` 表示目标操作实际读取的值，
`target_variable` 表示操作写回的值：`result = sink(command)` 必须核实 command，而非 result。
`return_value_locations` 是公共返回表达式位置协议，用于精确绑定嵌套结果；Go 只覆盖显式返回分量。
Java 已移除重复的操作节点构造，直接复用公共 collector，避免漏掉新的共享字段。

## 控制依赖与有界条件核验（1.18）

所有语言复用同一流程：已有 Tree-sitter 树 → `ControlStatement.condition/scalar_values`
→ CFG → `ControlDependencePass` → 按实际候选查询 `PathConditionQuery`。不重新解析源码，
不按语言另写控制依赖或条件求解器；操作级赋值配对由 `_operation_bindings()` 唯一提取，
值流、集合装配与标量赋值共享单操作缓存。

- `conditions.py`：控制依赖树、分支位置和核验结果契约；表达式定义复用 `value_binding/expressions.py`，旧导入保持兼容，不依赖安全规则。
- `frontends/conditions.py`：标量名称、已绑定属性/固定下标、字面量、比较、not/and/or、加减乘的公共语法适配；
  调用、动态表达式、JS 松散相等和 Java 字符串引用相等显式为 unknown。
- `passes/control_dependence.py`：复用 NetworkX 的立即支配树，在反向 CFG 求后支配，
  按分支边沿后支配树生成 `control_dependence` Overlay。保留 true/false/异常等分支标签；
  合流之后不错误继承分支依赖，早退之后的代码依赖对应未退出分支。
- 不可达节点单独记录；不能到达真实出口的区域逐节点增加仅用于计算的虚拟逃逸边，
  后支配父为虚拟出口时写空 ID，不伪造源码位置或终止性证明。
- `path_conditions.py`：复用项目 `.venv` 中的 `z3-solver`；每次查询独立 Context。
  在实际 CFG 路线上版本化赋值、保留复制关系，检查指定值流节点能否共同经过，
  以及到达定义是否被确定覆盖。短路 RHS 类型域只在 RHS 执行时约束。
  返回见证路线或矛盾前缀、条件源码位置、探索数、假设、未知原因和预算状态。
- 默认每查询最多 256 个状态、每节点访问 2 次、求解搜索 250ms；循环、调用副作用、
  不支持语法和超限显式 unknown，不以超限推断无路径。不对全仓库枚举所有执行路径。

稳定公共入口：

```python
from backend.app.services.program_graph import ConditionExpression, PathConditionQuery

# 必须为同一函数内按顺序核验的真实节点 ID；可传已选择的 value_edges 检查定义存活。
proof = PathConditionQuery().prove(function, [source_node_id, sink_node_id], value_edges=selected_edges)

# 调用者提供与实际危险上下文对应的必要条件，不能以净化函数名称冒充谓词。
required = ConditionExpression(kind="compare", operator="ge", operands=(
    ConditionExpression(kind="name", name="value"), ConditionExpression(kind="literal", value=0),
))
guard = PathConditionQuery().implies(function, sink_node_id, required)
```

`satisfiable_under_model` 只表示原始标量抽象中的可满足路径；`infeasible_under_model` 只表示
该模型中的矛盾；`unknown` 不作可行/不可行判断。数学整数不涵盖机器整数溢出，原始值相等
不涵盖自定义比较/引用相等/隐式转换；假设随结果保留。`implies()` 先核验目标可达，再搜索
违反必要条件的反例，避免不可达代码导致空真。编译器绑定场景的额外谓词必须使用使用点
实际绑定的值名，不补猜缺失声明。

原始图与安全私有 Overlay 分开：RD 重算会清空旧控制依赖；安全侧完成所有对象/集合/调用
后置节点及值后端选择后，统一重算控制依赖。控制依赖不自动变成隐式污点传播，也不删除
未证明的候选；尚未证明完整跨过程路径条件、循环不变式和所有隐式异常。

### 已有跨过程链路的条件衔接（`z3.scalar-route.v1`）

`path_composition.py` 的 `ConditionRoutePart/ConditionRouteQuery` 只消费已选路线和已有绑定：
每段包含函数激活帧、开始/结束节点、既有值流边及入口复制，不自行解析调用关系。
公共编译层为这些片段构建有界临时 CFG，再调用同一个 `PathConditionQuery`，不维护第二套
SMT 或语言专用证明器。直接具名实参复制到形参，直接返回值复制到真实接收槽；调用前
在输入读取处暂停，返回后恢复原调用者帧，不重跑其入口或提前执行返回赋值。
同名局部变量按激活帧隔离，其他直接绑定的条件参数也随调用传递，不只绑定污点参数。

1.19 增加调用点 `positional_conditions/keyword_conditions`，直接从原 AST 保存实参表达式，
补全已观察整数/字符串/布尔字面量的条件绑定，不猜默认实参，也不从变量使用表重建表达式。
旧产物缺省空列表/字典；缺失处保持 unknown，不能补造历史参数值。
1.20 在同一绑定层增加字面量默认参数（仅实际省略时）、受支持标量变换、已知聚合返回的全部分量，
包括没有污点但会控制危险操作的布尔分量。共享表达式不再由安全层从变量名猜测重建。
唯一对象/固定集合元素的确定写回一起带回同次正常出口的兄弟字段，复用已有身份和 must-write 事实；
弱写、未知键、逆向调用的缺失前缀、未知别名及事件 may 边界仍为 unknown，不宣称完整堆证明。
接收者已由实例字段契约确认时，不把 self/this 当作漏传实参；其字段可组合，但对象自身真值仍 unknown。
未知表达式随值版本传播，仅在影响条件时贡献 unknown；未知调用仍使已有值失效，确定覆盖后可重新获得精度。
没有新增并发、递归摘要或第三方库语义。
构建上限为 32 段、10000 节点（含绑定操作）；达到上限即 unknown，后续求解仍受原状态/时间
预算约束。安全证据只保存原节点/边 ID、激活帧、绑定和条件，不保存或发送临时全图。
安全传播侧同端点的首条路线若在模型内矛盾，会继续核验已有备选调用（默认最多 8 条矛盾
路线）；超限记录 condition_alternative_route_limit 和截断，不作所有路线不可行的结论。
候选选择不再依赖流 ID 的哈希排序覆盖结果，而优先交付已有模型内可满足的路线。

算法接口依据：[NetworkX 支配分析](https://networkx.org/documentation/stable/reference/algorithms/dominance.html)、
[Z3 求解器与未知结果](https://z3prover.github.io/papers/programmingz3.html)。

### 共享 finally 与异常写入顺序（1.20）

`passes/cfg.py` 为 try 内的调用保留正常和可能异常边；失败 RHS 的异常边用 `source_before_write`
读取操作前状态，RD、对象引用、有限键求值、条件查询采用同一顺序。typed catch 不假装穷尽所有异常。
`finally` 只有一份块，通过 `completion_push/completion_pop` 保存并恢复正常、return、throw、break、continue。
`passes/return_capture.py` 在进入清理前冻结返回表达式，finally 中新的 return/throw 覆盖原完成原因；
不会重复调用或让清理后的局部变量冒充原返回值。组合查询按激活帧隔离完成 token。
普通 RD/支配仍是共享 CFG 上的保守近似，只有路径查询按完成栈配对；不宣称上下文敏感的完整异常分析。
只因 try 内调用生成可能异常，不补全下标越界、算术异常、异常类型分派、Go defer/panic 等语言全部语义。
循环条件出口成为回边时仍保留 true/false 谓词，另以 `loop_back` 标记回边，避免回边标签覆盖条件。
求解器每次求解最多使用查询剩余时间，不再另设 30ms 小上限；查询总上限仍为 250ms，超限仍 unknown。

1.7 增加 `ControlStatement/ProgramGraphNode.return_binding` 与调用点 `result_pattern`：
前者引用 `value_binding.ReturnBinding` 区分单个值（可包含聚合成员）、原生多返回分量和未知形状；
后者复用 `BindingPattern` 表达整体接收或解包，不靠接收变量数量猜测语言 ABI。
Python `return x, "fixed"` 是一个带位置成员的值，Go `return x, "fixed"` 是两个 ABI 分量。
旧图没有这些字段时只复用已有返回分量，不补造历史结构；需要重建才能获得 Python 聚合值精度。
返回成员中的直接调用保存调用身份，由消费者绑定精确结果槽，不将该调用的实参冒充其返回。
公共操作节点会把值投影的输入/输出同步纳入 Def/Use，避免成员投影遗漏到达定义边。

1.8 增加节点 `definition_declarations/use_declarations`，分别保存写入/读取的声明根；
`binding_gaps` 保留缺失、歧义或失败。`passes/declaration_bindings.py` 只消费公共声明/引用事实，
按文件、所属函数和精确范围投影，不按名字/最近行号补造绑定。无原生事实的语言保持旧路径。
`ProgramGraphService.analyze(..., semantic_index=SemanticIndex或Artifact)` 可显式接入；安全服务自动传入。
同一操作同名实体无法区分时保守回退，不能因有索引便宣称全覆盖。
到达定义使用声明根+静态访问路径执行 KILL；定义版本仍为操作节点+值槽。
值流边 `source_value_id/target_value_id/read_value_id` 分别表示源定义、目标结果及实际读取身份，
变量名仍用于展示；公共身份协议位于 `value_binding/identities.py`，不是新的语言私有图。
`declaration_bindings` Overlay 标识避免安全消费者重复投影；旧直接调用方可在私有图按需补齐。

1.9 增加函数 `object_assignment` 能力声明：Python、Java、JS/TS 的已适配实例语法声明为
`reference`；其他语言与旧产物缺省为 `unknown`，不把 Go 结构体值复制猜作引用复制。
节点的 `definition_value_ids/use_value_ids` 可为已确认字段提供操作级精确身份，优先于声明根派生；
安全消费者在私有副本将唯一已知分配的直属字段映射到同一身份，原始变量名和位置仍保留。
`object_alias_origins` 与调用点的 `receiver_alias_origins/receiver_allocation_id` 保存真实别名赋值
及分配依据，不代表完整堆或运行时地址。正常持久化图不预填这些安全分析 Overlay。

1.10 用节点 `definition_object_slots/use_object_slots` 显式保存不可变的公共 `ObjectFieldSlot`，
分别对应操作后写入与操作前读取；包含 allocation_id、直属 field_name 和可选 owner_symbol。
新对象 Overlay 不重复填 1.9 的字符串身份表，`value_identity` 统一转换为原 RD/DFG 消费的稳定身份；
旧精确值表继续兼容。类型字段渲染也统一到 `value_binding.objects`，证据不再从新身份字符串反解对象。
字段默认空表，正常生产者图不预填安全字段绑定；本次不新增跨函数写回或其他语言对象语义。

1.11 调用点增加前端直接观察的 `positional_references/keyword_references`：只有 AST 确认的
直接名称（允许括号）保留引用名，变换表达式为空；普通变量使用列表不能代替引用身份。
`normal_exit_writes_modelled` 表示前端允许从当前正常出口计算确定写入；缺少此能力的旧图缺省 False。
公共 collector 有新增降级诊断时不开放强更新，不能把该标记当完整 CFG、可达性或安全证明。
私有调用点 `argument_field_bindings` 是目标 method_id → 对象形参字段 → 调用前字段槽，
与原接收者映射一起由同一参数绑定器消费。字段写回仅正常返回后生效，原生产者图不预填该映射。

1.12 的 `ControlValueTransfer/ProgramValueTransfer.reference_source` 记录 AST 确认的直接名称或
纯字段 RHS，括号可去除，但调用、转换、运算、下标不能冒充对象引用。调用实参的引用事实同步
支持纯字段路径；Python、Java、JS/TS 只在语言钩子输出此事实，组合关系仍由同一公共 CFG 引用
环境求解。旧产物字段缺省为空，只有原直接名称规则保持兼容，不反推旧嵌套引用。
安全消费者用私有 Overlay 逐跳绑定 `a.child.command` 最终所在对象的直属字段槽，不把它编码成
父对象的 `child.command` 字段。分配、组合引用与末级标量污点是不同事实，不新增结构调用边。

1.3 增加公共 `InstanceMethodContext(owner_symbol, receiver, is_constructor, shared_fields)`，
由 `frontends/objects.py` 的 `InstanceSyntax` 模板及 Python/Java/JS 子类提取，TS 继承 JS 运行时语义。
所有 collector 仍继承同一 `TreeSitterFunctionCollector`；可选 `instance_syntax` 钩子提供语言差异，
没有再建立一套对象专用文件遍历或图算法。Java 根据直接字段声明规范省略 this 的访问，
参数和方法内局部声明保守遮蔽同名字段，static 存储不按实例隔离。
旧图缺省上下文为 None，不靠 self/this 拼写推断历史实例。C/Go/Rust 的构造/工厂语义和
C++ 构造器列表等本轮尚未适配，不能因为共用协议就宣称已经支持。

`InstanceFieldEffect/FieldValueOrigin` 是安全消费者私有 Overlay 中的字段摘要与原始赋值位置。
正常持久化 ProgramGraph 不预填这些摘要；公共 CFG/到达定义和值流 Pass 被复用，原图不就地修改。

1.4 增加 `return_value_forms`（reference/call/other），由共享 Tree-sitter collector 按真实 AST
分类，Java 仅声明自己的调用节点类型。工厂分析不能再因 `return [worker]` 包含 worker 就猜测
直接返回了实例。`deferred_return` 只记录 async/generator 返回包装，防止误当同步对象，不实现并发。
旧产物缺少形态时仍可读取，但不据此补猜工厂身份；须重建程序图才能获得此新能力。

1.5 的 `ProgramCallSite.receiver_field_bindings` 是消费者私有的“目标 method_id → 接收者字段 →
类型化值槽位”接口；None 保留旧绑定方式，空表表示当前对象模型没有兼容目标。
安全消费者仅过滤已有调用目标，不从这里新增结构调用边。类型槽位是普通字段的子访问路径，
父字段/整对象覆盖沿用公共到达定义杀死规则；原始生产者程序图不预填此映射。
`InstanceFieldEffect.object_type_symbol` 保留实际类型供证据溯源，不代表完整运行时类型分析。

当前 `ProgramGraphService.analyze(project_root, dependency_graph=..., languages=...)`
已经覆盖依赖分析器支持的全部语言：Python、Java、JavaScript、TypeScript、Go、C、C++ 和
Rust，并生成按函数分区的：

- 公共操作节点；
- 控制流 `cfg` 边；
- 词法变量级 `reaching_def` 边；
- 带输入变量、输出变量、变换类别和调用点的 `value_flow` 边；
- 文件、函数和失败覆盖率；
- 明确的分析局限。

`dependency_analyzer` 仍唯一负责跨文件符号和调用目标解析；`program_graph` 使用 `symbol_id`
与其连接。Java、Go、C、C++ 和 Rust 的 `method_id` 尽可能包含参数类型；动态语言和缺少可靠
签名的函数使用源码位置区分重复绑定，因此重载或同名重新声明不会互相覆盖。
跨过程消费者优先通过 `semantic_index` 查询依赖分析已经解析的调用目标，不再自行解释
NetworkX 边字段；ProgramGraph 仍只负责函数内控制流和到达定义。
`security_analysis` 已消费本模块的 CFG/DDG Overlay，并只请求已有安全规则覆盖的语言，
避免为不可能生成安全事实的语言重复解析项目。

## 多语言实现

- 所有语言首先继承 `ProgramGraphFrontend`，统一文件编排、路径规范化、失败隔离和覆盖率统计；
- 八种语言全部继续继承 `TreeSitterProgramGraphFrontend`，统一解析器池、恢复性语法诊断和
  collector 调用生命周期；
- 所有 collector 也继承 `TreeSitterFunctionCollector`；Python、Java 覆盖较多语言钩子，以表达
  关键字参数、Python 异常、Java 重载、catch 参数等语义，但不再拥有独立的遍历契约；
- JavaScript、TypeScript、Go、C、C++ 和 Rust 使用共享 collector 基类和薄适配器描述函数
  身份、声明绑定、访问路径、接收者及语言特有作用域；它们与 Python/Java 一样生成精确
  `value_flow`，而不是只满足节点和 CFG 协议；
- 注册表测试直接比较 `dependency_analyzer.EXT_MAP` 的语言集合，防止以后新增依赖语言时
  ProgramGraph 静默落后。

JS/TS 额外复用 `syntax_analysis/javascript.py` 的函数/参数语法，匿名回调使用源码位置身份，
TypeScript `.tsx` 使用 TSX grammar。spread 之后实参位置未知，停止绑定而非伪造位置映射。
复杂解构、实参和形参通过 `value_binding` 协议处理，固定字段的空输入显式保留，避免安全兄弟字段获得污点。
JS/TS 的 `obj["key"]` 与 `obj.key` 规范为同一词法字段；其他语言保留自己的下标语义。
Vue `.vue` 与其他分析器共用 `source_units`，单个 `<script setup>` 建立生成的 `vue_setup` 函数分区，脚本操作后接入 `v-html` 读值；分区范围覆盖原文件但不伪装源码中真的声明了该函数。
匿名回调的函数内 CFG/DFG 不代表异步回调调用图、闭包捕获或完整 Promise 时序已实现。
公共 Def/Use、调用点和结构绑定遍历止于嵌套函数作用域，不把函数创建当函数体执行。
安全消费者可通过显式 `IRValueBoundary` 在私有副本中绑定捕获槽位和 CFG 退出值，复用
本模块 Pass；不修改持久化图、不为父函数伪造回调执行，也不把该有限模型当完整事件时序。

依赖图与 ProgramGraph 还共同使用 `syntax_analysis` 中的扩展名目录、忽略目录、线程本地
Tree-sitter Parser 池、节点字段/文本读取和类型名称归一化。ProgramGraph 不再导入
`dependency_analyzer` 的私有 AST 工具。

## 文件职责

| 文件/目录 | 作用 |
| --- | --- |
| `contracts.py` | 可持久化的函数分区、节点、边、覆盖率协议。 |
| `control_ir.py` | 语言前端与通用 CFG Pass 之间的结构化控制 IR。 |
| `frontends/` | 八种依赖分析语言到公共控制 IR 的降级入口。 |
| `frontends/access_paths.py` | 跨语言 `AccessPath` 模型、稳定序列化和 `AccessPathResolver` 结构协议。 |
| `frontends/base.py` | 所有语言共同继承的文件编排模板基类。 |
| `frontends/treesitter.py` | 八种语言共享的 Tree-sitter 解析、控制语句、调用点和词法 Def/Use 基础。 |
| `frontends/objects.py` | `InstanceSyntax` 公共模板；子类只说明类成员、接收者、静态过滤和构造器语法，不进行污点传播。 |
| `frontends/storage.py` | C/C++/Go 的声明、存储分配、取址、解引用及复制语法事实；不包含别名或污点求解器。 |
| `frontends/selectors.py` | 各语言共用字面量/名称/有限选择表达式与访问位置身份，输出公共选择器事实，不在 AST 遍历时猜测变量值。 |
| `frontends/languages.py` | JavaScript、TypeScript、Go、C、C++、Rust 的薄语言适配器。 |
| `frontends/python.py` | Python 语言 collector 与薄前端。 |
| `frontends/java.py` | Java 语言 collector 与薄前端。 |
| `passes/cfg.py` | 通用顺序、分支、循环、return、throw、break、continue 和保守 try/catch CFG。 |
| `passes/reaching_definitions.py` | 基于 CFG 的通用到达定义工作列表算法；一次索引已定义父访问路径的后代，避免每个写入重扫全部变量。保留原工作列表、传播方程、收敛条件、`may` 写入、父字段/容器覆盖、可达性和传播深度。 |
| `passes/value_flow.py` | 将 Def-Use 增强为包含变量改名与变换语义的值流 Overlay。 |
| `passes/selectors.py` | 在原 CFG 与声明身份上有界求值和投影动态键；不支持的表达式转为未知，复用原 RD/DFG，不另建污点求解器。 |
| `registry.py` | 语言前端注册表。 |
| `service.py` | 文件范围复用、前端编排、Overlay 构建与覆盖率汇总入口。 |

## 当前边界

可选 parameter_kinds、实参源码范围、positional_spread_positions、result_targets 和
return_values 保存声明类别、展开位置与 Go 有序返回分量；历史字段缺失时保持旧行为。
1.6 明确 result_targets 中的空串是前端观察的丢弃槽，保留位置但不创建变量或恢复返回值。
Go `_` 不进入操作的 Def/Use，不能把无关丢弃赋值、range 或调用返回串起来；Python 的 `_` 仍是普通变量。
这些是跨语言契约，语言前端只适配自己能确认的语法。
安全消费者按原始实参范围绑定直接嵌套 Source，在私有图添加可变参数成员定义、简单输出
缓冲区写入；仍使用公共 Pass，不修改本模块原始持久化图，也不创建依赖调用边。
Go 仅显式直接 return 和有序接收得到分量隔离，裸命名返回、隐式转发仍有局限。

公共 `ControlStatement/ProgramGraphNode` 提供默认 must 的 certainty；历史图缺少字段时仍按
原来的确定写入处理。框架可能写入可显式标记 inferred/may，通用到达定义算法保留之前定义，
值流 Overlay 延续 may。Vue setup 的有限文本 v-model 使用这个公共能力；它不是按模板源码
排列事件，也不模拟完整响应式调度。ref 词法身份由 syntax_analysis.vue_bindings 统一映射。

八种语言现在都对可由语法确定的声明和赋值执行输入输出配对：Python 序列解包、Java 多
declarator、JavaScript/TypeScript declarator、Go 并行绑定、C/C++ init declarator 与 Rust
let/tuple pattern 均不会再把同一语句中的兄弟值互相污染。普通赋值、复合赋值和更新表达式会
保留目标旧值及右值；调用返回值与确实无法精确配对的多目标表达式仍标记为 `may` 并走公共
保守回退。
公共 `AccessPathResolver` 协议要求语言前端把本语言 AST 转换成结构化字段/下标路径，再由
公共模型稳定序列化。Python/Java 的属性字段，JavaScript/TypeScript 的 member/subscript，
Go 的 selector/index，C/C++ 的 field/subscript 和 Rust 的 field/index 都会转换成
`obj.field`、`payload["key"][0]`、`values[0]` 等变量身份，避免兄弟字段或静态元素交叉污染。
调用点也统一排除被调用名称，单独保存接收者和逐实参变量。这属于词法访问路径敏感，不等同于
完整堆对象敏感分析；动态或复杂下标会显式记录分析局限。安全消费者已通过公共字段身份接口
支持 Python/Java/JS/TS 局部实例引用及 C/C++/Go 的有界值存储/直接指针；任意堆与容器别名仍未解析。
尚未实现支配/后支配、控制依赖、路径条件求解、完整异常语义和完整对象传播。跨过程层目前由
安全分析消费者完成有限深度的实参到形参与直接返回值传播。各语言尚未精确展开的
`switch/match/select`、循环更新、
后置条件等未支持语法会显式进入函数局限，不能作为确定性路径前提；finally 完成栈见 1.20 节。

## 继承与存储事实（1.13）

沿用唯一 CFG/RD/DFG，不把每种语言改成独立的污点引擎：

- `has_body` 区分真实方法体与 Java 接口/抽象声明；声明不是空的安全实现。
- `initializer_kind` 标记语法确认的父/同类初始化委派，目标必须来自已有调用解析。
- `storage_allocations` 使用 `value_binding.storage.StorageAllocation` 保存声明类型与值/堆/标量单元类别。
  `storage_mode` 区分值复制、引用复制和取址；显式解引用为规范路径中的 `*` 段。
- `storage_parameters` 区分形参/Go 接收者的传值与引用角色，`addressed_argument_positions`
  区分传入指针值与取得变量存储地址。语言全局 `object_assignment` 不能代替这些操作级事实。
- 安全消费者将值结构体、内嵌值与直接指针映射到原 `ObjectFieldSlot`；私有
  `storage_invalidations` 标注未知副作用，后续未解析访问不能退回同名词法槽。
- 规则声明的输出写入先进入私有图，再统一进行存储/字段绑定；状态码与输出内容仍隔离。

公共类型层次来自 `semantic_index.TypeHierarchy`；本模块不重新猜父类、调用目标或运行时地址。
旧图缺省新字段仍可读取；缺失存储/初始化事实不会凭旧图伪造。具体支持边界见
`security_analysis/flow_analysis/README.md` 的 1.13 小节。

1.14 增加仅供私有视图使用的 `object_reference_bindings` 和调用效果已编译标记；
工厂/引用写回通过同一个引用环境消费。`FieldValueOrigin` 可携带已有调用点/边及实际类型，
用于关系改变的原始源码证据，不新增结构调用关系。存储字段类型保留原始指针/引用修饰，
不能仅使用依赖查找的规范化类型判断内嵌值布局。范围见 flow_analysis README 的 1.14 小节。

## 值查询后端选择（1.15）

`ValueFlowQuery(index).project(graph)` 通过已有 `SemanticSSAView` 接口消费原生事实，不启动工具、
不重新扫描项目。安全分析在调用结果、对象效果和框架值边界装配完成之后调用一次；因此后续 Pass
不会把原生结果重新覆盖为公共求解结果。

- `passes/ssa_values.py` 是只读交换事实到公共 Def-Use 的适配，按参数、精确 DebugRef/调用位置
  和编译器值身份/φ 操作数定位定义；不是名称级 SSA 模拟或新的污点引擎。
- 可完整映射的 Go 函数采用 `native_ssa_def_use`。它只保留已有 CFG，用原生 Def-Use
  **替换**公共到达定义和值流，再复用 ValueFlowPass 的读/写/调用角色映射；不并集两套边。
  接收者与普通实参、多返回分量继续沿公共跨函数契约处理。
- TS 的声明/引用绑定约束公共 CFG/DFG，标为 `compiler_bound_cfg`，不冒称 TS SSA。
  无原生事实时使用 `common_cfg`；有部分绑定时明确 `partial_compiler` 和缺口类别。
- `FunctionProgramGraph.value_backend` 记录实际来源、身份来源与具体回退原因。旧图缺省 None，
  不伪造原生成功。重新投影已选择原生的函数但不再提供事实时，连边与标签一起回退。

原生投影当前支持无内存标量、φ、普通调用/返回及已经精确映射的表达式。Store/Load/FieldAddr、
闭包、invoke/动态调用、协程/defer、未知操作或私有对象/框架内存 Overlay 使整函数回退，
已有对象字段功能继续由公共模型处理。**回退不代表丢弃函数，也不表示该函数安全。**
这不是完整编译器 CFG 替换、堆别名求解或路径可行性证明，结果仍为 inferred/may。
每函数超过 20,000 原生指令或 5,000 公共节点时同样明确回退，不截取部分边冒充完整投影。
原生引用按真实行范围索引，不为源码空白行建立稠密表；调用位置表按本函数只建立一次。

## 固定集合元素（1.16）

`frontends/collections.py` 在同一已解析 AST 上把 Python dict/list/tuple、JS/TS object/array、
Java 数组初始化、Go map/slice/定长数组字面量降为 `collection_allocations` 与逐元素赋值。
不再把一个元素的输入传播到整集合；同键字面量采用最后一次值，JS 数组空洞保留真实位置。
已有声明配对、访问路径、返回绑定和调用点协议复用，不额外扫描项目或生成第二份图。

元素值槽为 `value_binding.objects.CollectionElementSlot`，与原 ObjectFieldSlot 共用 RD/DFG 身份协议。
别名和嵌套元素由安全消费者的原引用环境处理；Go map/slice 共享元素，固定数组保留值复制及传值角色。
直接返回集合字面量使用私有返回根，原 ReturnBinding 的单值/解包语义保留。

每语句展开最多 256 个值槽、16 层，超限/动态展开保留不完整标记；不是任意下标、集合 API、
迭代器或完整堆分析。未新增 C/C++/Rust 集合分配前端，原词法静态下标功能保持。

## 有界动态键（1.17）

Python/Java/Go/JS/TS 的索引表达式先保存为位置私有选择器，不把键变量自身当成被选择元素的值。
`SelectorResolutionPass` 复用公共 CFG 和读/写声明身份，仅解释字面量、名称赋值、条件选择与分支合并。
唯一常量精确投影，最多 8 个有限候选保留 may 关系，未知/超限使用 `[?]`；不执行算术、函数或任意项目代码。
可变键字面量也沿相同表达式路径接入。没有动态访问的函数跳过求值并移除无用常量事实，避免扩大产物。

普通 certainty=may 描述输入输出关系的不确定性，并不表示赋值没执行。新增 weak_update 专门表达
有限/未知目的键的弱写；原确定赋值、普通返回值覆盖和框架节点 may 语义保留。
未知元素读取由既有 RD 合并同一分配的可能元素，原引用环境将未知写投影到该分配观察到的固定元素，
后续明确的固定键覆盖仍能切断旧值。SummarySlot 与真实字符串 `"?"` 不混合，读写/调用/返回名称同步投影。
复杂嵌套动态对象引用、跨函数键常量特化、全局常量及任意符号求值仍非完整覆盖。
