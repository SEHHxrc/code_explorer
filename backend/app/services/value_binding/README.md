# 结构化值绑定公共模块

`expressions.py` 是公共不可变标量表达式契约（名称、字面量、逻辑/比较和受支持算术），供语法前端、
参数/返回值投影和程序图条件查询共同使用。`BindingValue/BindingProjection.expression` 保留实际变换，
不能把只依赖一个输入的表达式当成原值复制。求解仍属于 program_graph，不在本模块实现第二套。

## 入口与功能域

本模块属于静态分析公共基础设施，不读取项目文件、不依赖 AST、不创建调用边、不判断漏洞。
PatternResolver.resolve(pattern, value) 接受 BindingPattern/BindingValue，
默认 StructuredBindingResolver 返回 BindingResolution：
projections 保存输出、输入、源码身份和 must/may，diagnostics 保存未建模原因。

## 文件和依赖

| 文件 | 作用 |
| --- | --- |
| contracts.py | BindingPattern/PatternMember 描述目标；BindingValue/ValueMember 描述值；BindingProjection/BindingResolution 描述结果；PatternResolver 定义消费协议。 |
| resolver.py | StructuredBindingResolver 展开嵌套模式；append_selector 生成字段/位置身份。 |
| parameters.py | ParameterKind 描述位置限定、普通、仅关键字、可变位置/关键字参数；共享类别契约，不计算调用目标或污染。 |
| returns.py | ReturnBinding 区分一个值与原生多返回 ABI；resolve_return(binding, receiver) 校验已知序列接收数量并复用 StructuredBindingResolver，输出投影和诊断。 |
| identities.py | DeclarationBoundValues 协议、variable_root/value_identity；依次消费结构化对象字段槽、精确读/写身份、声明根及规范静态路径，未知用 lexical 槽；不包含 CFG 或编译器对象。 |
| objects.py | ObjectFieldSlot、CollectionElementSlot、CollectionSummarySlot 分别表示直属字段、固定元素和未知键摘要；StorageValueSlot 为唯一联合接口，不把未知键当真实字符串键。 |
| collections.py | CollectionAllocation 交换布局、键语义、可变性与引用/值角色；CollectionCallView/CollectionEffect/CollectionEffectProvider 为集合操作模型接口，不识别安全 Source/Sink。 |
| selectors.py | SelectorExpression 只交换字面量、名称、有限选择和未知；不执行表达式、不推断任意运行时值。 |
| storage.py | 不可变 StorageAllocation：声明类型、值/堆/标量单元分配种类；只交换事实，不执行内存布局、引用或污点求解。 |
| access_paths.py | AccessPath/AccessPathResolver 与 render_access_path：跨语言字段/常量下标身份；canonical_path/member_path/parent_path 共用规范路径装配，正确处理键内点号；attribute_path 仍限纯属性，均不执行任意源码。 |
| __init__.py | 公共导出。 |

依赖方向：JS/TS AST → syntax_analysis.javascript_bindings → 公共绑定 IR/投影器
→ program_graph 的定义/使用和值流、security_analysis 的事实定位和调用绑定。
两个前端共用算法。递归契约支持 ProgramGraph JSON 保存/恢复。
JS/TS 使用完整结构模式协议；Java/Go 的直接打包序列已复用 BindingValue/ValueMember，
Python/Java/Go 的声明类别共用 ParameterKind。其他绑定保留各语言的访问路径和配对钩子。
未来可按各语言语义适配，不要求迁移为 JS 的模式。

返回边界也使用相同 `BindingValue/BindingPattern`，不建立另一套元组绑定算法。
Python `pair = f()` 的整体接收会投影到 pair 及已知 pair[0]/pair[1]；
`a, b = f()` 则投影到两个名称。Go 多返回必须逐分量接收，空名称保留丢弃位置。
未知形状、错误的已知接收数量和预算限制保留诊断，不把空结果等同于安全。
本模块只表达已有结构，不自动追踪任意容器别名、动态迭代器或堆对象。

ProgramGraph 1.16 在同一引用环境接入有界集合元素：`CollectionElementSlot(allocation_id, selector)`
的 selector 严格区分整数与字符串，Python `0` 和 `"0"` 不混合；JavaScript 前端按属性键规则归一化。
节点原 `definition_object_slots/use_object_slots` 字段保存二者的联合类型，历史 ObjectFieldSlot 不变。
新语法分配通过 CollectionAllocation 交换；旧图缺省为空，不从旧产物猜测新身份。
集合别名、参数写回与返回使用现有 ObjectReferenceResolver、正常出口和对象快照，不增加独立污点引擎。

1.17 增加未知选择器 `[?]`，与真实键 `"?"` 和私有位置选择器均隔离。唯一常量键精确处理，
有限键与未知写采用弱更新；未知读合并同一分配的可能元素，不把键自身视为元素内容。
`ProgramValueTransfer.weak_update` 表示目的槽可能未被写入，与输入→输出关系的 certainty=may 不同，
不能据此弱化所有普通函数返回赋值。实际键求值属于 program_graph，引用/元素效果仍属于 flow_analysis。
CollectionEffectProvider 当前仅用 Python 字面量原生 list 的单次 append 打样，其余效果枚举是扩展契约，
并不表示 remove/slice/reorder/iterate 已实现。

`ObjectFieldSlot(allocation_id, field_name, owner_symbol=None)` 表达已有对象字段身份，不表示受污或安全。
`value_id` 为现有 RD/DFG 输出稳定字符串；实际消费者直接读取 `allocation_id`，不再拆解字符串找对象。
ProgramGraph 1.10 节点的 `definition_object_slots/use_object_slots` 分别保存操作后的写入身份与
操作前的读取身份；结构化槽优先于旧 `definition_value_ids/use_value_ids`，后者仍兼容通用精确槽及旧产物。
新对象 Overlay 不同时保存重复的字符串身份表。`a.field` 与直接引用别名 `b.field` 可映射到同一字段槽；
变量展示名不变，定义版本仍由操作节点区分。引用状态、来源与查询协议属于
`security_analysis/flow_analysis/object_contracts.py`，引用环境及其 CFG 求解属于
`security_analysis/flow_analysis/object_references.py`，不迁入本模块。

嵌套组合仍复用同一个直属 `ObjectFieldSlot`：`parent.child.command` 先解析 child 的对象身份，
再使用该子对象的 command 槽；不是增加一个包含点号的 field_name，也不等同于继承成员解析。
普通引用组合本身不推导值复制或基类身份。新增存储语法事实由 `flow_analysis/object_storage.py`
映射到已知布局，值复制和引用仍复用 `ObjectReferenceIndex`；类型层次/基类子对象查询属于
`semantic_index`，不在这里加入另一套语义求解器。

## 不确定性

固定字面量绑定即使 inputs 为空也保留，不能回退为整条语句全部输入。
null 不等于 missing，只有 missing 确定使用默认值；不透明对象的默认值保留 may。
静态 rest 排除已选字段；未知 rest 无法证明排除范围，保守诊断。
默认深度 24、投影上限 512。动态键不猜测。
不执行 getter、代理、迭代器或用户代码，不证明堆别名或运行时路径可行。
