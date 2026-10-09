# 数据流分析器

本目录把跨语言 `ProgramGraph` 的 CFG/值流 Overlay 与安全规则产生的
Source、Sink、Sanitizer 事实连接起来。语言前端只负责把源码降低到公共图协议，
这里不再为 Python、Java 等语言分别实现一套值传播算法。

| 文件 | 作用 |
| --- | --- |
| `base.py` | 定义 `DataFlowAnalyzer` 协议。 |
| `call_graph.py` | 索引已解析调用边，并通过 `LanguageSemantics` 绑定实参和目标形参。 |
| `argument_slots.py` | prepare_argument_slots() 输入原图、调用索引和绑定器，在私有入口定义可变参数常量成员；Go 多返回接收不再默认把实参透传给全部结果。 |
| `call_sources.py` | prepare_call_sources() 按精确实参位置绑定直接嵌套 Source 的结果槽位，不绕过项目包装函数返回分析。 |
| `call_effects.py` | CallOutputEffects.prepare() 将已声明具名输出缓冲区在调用后定义为 may 值；状态码与内容分开，公共 Pass 重算私有图。 |
| `value_boundaries.py` | 消费语言无关的 IRValueBoundary；按原始位置定位函数，在私有 Overlay 中绑定捕获槽位和退出值，复用公共到达定义和值流。 |
| `program_graph.py` | 优先基于公共 `value_flow` 边，并兼容旧 `reaching_def` 产物，生成函数内及有界跨过程安全数据流证据。 |

当前处理：

- 框架参数等函数入口 Source；
- 安全规则命中的调用返回值 Source；
- 公共 ProgramGraph 中已经建立的变量定义、使用、改名和保守变换关系；
- 规则指定的 Sink 位置参数和关键字参数；
- 接收者型 Sink；
- 最多四层的已解析调用实参到形参传播；
- 有界状态/回调值边界，与普通调用共用深度预算；
- 已知 Sanitizer 所在节点的截断。

CFG 已参与到达定义计算，但尚未证明具体分支条件在运行时可满足，因此所有结果仍保守标记为
`may_reach_sink`。当前消费公共 ProgramGraph 为 Python、Java、Go 生成的字段/成员、静态下标
和精确赋值路径，并支持有限深度的直接跨函数返回值传播；尚不覆盖对象/堆身份、字段或
容器别名、动态下标元素身份、嵌套调用返回和完整对象传播。语言特有控制语义若只能保守降低，
会记录在函数局限和证据中。

直接嵌套单返回 Source 已有限支持；通用嵌套调用结果和复杂表达式仍非完整模型。
Python args/kwargs、Java/Go 可变序列支持常量成员；Java 打包数组和 Go 字面量切片展开共用
BindingValue，安全常量也有独立定义。Go 直接 return 的分量按有序接收槽位分别回传，
裸命名返回和隐式多返回转发仍未建模。所有这些能力沿用同一传播算法与深度预算。

`DataFlowAnalyzer.analyze()` 可接收 `semantic_index` 和 `value_boundaries`，后者缺省为空。
`ValueBoundaryIndex` 不修改持久化 ProgramGraph；无边界时直接保留原图。
当前 JS/TS 前端提供简单 React useState、同请求 Node data→end 模型，字符串使用 CFG
退出值、数组仅支持直线累积。边界不是新的 Source 或实际 calls 边：证据使用独立
`value_boundary_ids`，并保留 inferred/may、低置信度及局限。它不能证明事件顺序、重渲染
或完整堆行为；公共协议可供其他语言适配，但没有自动实现其他语言的闭包分析。
