# semantic_index 模块

本模块保存依赖图与 ProgramGraph 之间真正可复用的“语义事实”，而不是再定义一种图。
它通过 `SemanticIndexView` 接口提供只读查询，内部当前使用 Pydantic 产物和轻量内存索引。

## 当前事实

- `CallableFact`：函数、方法和构造器定义；
- `CallSiteFact`：调用位置、调用者、名称、接收者及未解析原因；
- `ResolvedTargetFact`：调用候选目标、解析方法、置信度、may/must 和截断状态；
- `VariableTypeFact`：作用域内变量的声明或保守推断类型。
- `ParameterFact`：ProgramGraph 中的形参、位置、类型和参数槽位；
- `ReturnFact`：每处 `return` 的值名称、源码位置和统一返回槽位；
- `ValueSlotFact`：`PARAMETER_SLOT`、`RETURN_SLOT`、`CALL_RESULT_SLOT` 三类跨图值接口。

`DependencyAnalysisSemanticProvider` 生成 1.0 基础事实。它只读取依赖分析器已有的定义、引用、
变量类型表和 MultiDiGraph，不重新解析源码。`ProgramGraphSemanticProvider` 再通过
`SemanticIndexEnricher` 接口追加 1.1 值传播事实，同样不重新读取源码。项目分析会持久化
增强后的索引；安全分析优先从该索引取得跨过程调用目标和返回接口，旧调用方仍可回退到
依赖图边与 ProgramGraph 节点。

## 边界

`semantic_index` 不拥有 AST、CFG、依赖边或安全结论，也不要求生产者继承公共基类。未来语言
前端只需实现 `SemanticIndexProvider`/`SemanticIndexEnricher` 或输出同一事实契约。当前返回
传播只覆盖局部变量直接接收的调用结果；字段、容器元素、对象别名和嵌套调用表达式仍保守
降级，避免把语言私有语法对象泄漏到公共层。
