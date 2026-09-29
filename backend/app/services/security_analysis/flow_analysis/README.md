# 数据流分析器

本目录把跨语言 `ProgramGraph` 的 CFG/值流 Overlay 与安全规则产生的
Source、Sink、Sanitizer 事实连接起来。语言前端只负责把源码降低到公共图协议，
这里不再为 Python、Java 等语言分别实现一套值传播算法。

| 文件 | 作用 |
| --- | --- |
| `base.py` | 定义 `DataFlowAnalyzer` 协议。 |
| `call_graph.py` | 索引已解析调用边，并通过 `LanguageSemantics` 绑定实参和目标形参。 |
| `program_graph.py` | 优先基于公共 `value_flow` 边，并兼容旧 `reaching_def` 产物，生成函数内及有界跨过程安全数据流证据。 |

当前处理：

- 框架参数等函数入口 Source；
- 安全规则命中的调用返回值 Source；
- 公共 ProgramGraph 中已经建立的变量定义、使用、改名和保守变换关系；
- 规则指定的 Sink 位置参数和关键字参数；
- 接收者型 Sink；
- 最多四层的已解析调用实参到形参传播；
- 已知 Sanitizer 所在节点的截断。

CFG 已参与到达定义计算，但尚未证明具体分支条件在运行时可满足，因此所有结果仍保守标记为
`may_reach_sink`。当前消费公共 ProgramGraph 为 Python、Java、Go 生成的字段/成员、静态下标
和精确赋值路径，并支持有限深度的直接跨函数返回值传播；尚不覆盖对象/堆身份、字段或
容器别名、动态下标元素身份、嵌套调用返回和完整对象传播。语言特有控制语义若只能保守降低，
会记录在函数局限和证据中。
