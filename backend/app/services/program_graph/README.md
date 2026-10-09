# program_graph 模块

本模块是依赖图和静态安全分析之间的最小 CPG 公共层。它不替代前端依赖图，也不向浏览器返回函数内部的全部节点。

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
| `frontends/languages.py` | JavaScript、TypeScript、Go、C、C++、Rust 的薄语言适配器。 |
| `frontends/python.py` | Python 语言 collector 与薄前端。 |
| `frontends/java.py` | Java 语言 collector 与薄前端。 |
| `passes/cfg.py` | 通用顺序、分支、循环、return、throw、break、continue 和保守 try/catch CFG。 |
| `passes/reaching_definitions.py` | 基于 CFG 的通用到达定义工作列表算法。 |
| `passes/value_flow.py` | 将 Def-Use 增强为包含变量改名与变换语义的值流 Overlay。 |
| `registry.py` | 语言前端注册表。 |
| `service.py` | 文件范围复用、前端编排、Overlay 构建与覆盖率汇总入口。 |

## 当前边界

可选 parameter_kinds、实参源码范围、positional_spread_positions、result_targets 和
return_values 保存声明类别、展开位置与 Go 有序返回分量；历史字段缺失时保持旧行为。
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
堆对象敏感分析；动态或复杂下标会显式记录分析局限，对象别名、堆身份以及字段/指针/容器别名
尚未解析。
尚未实现支配/后支配、控制依赖、路径条件求解、完整异常语义和完整对象传播。跨过程层目前由
安全分析消费者完成有限深度的实参到形参与直接返回值传播。各语言尚未精确展开的
`switch/match/select`、循环更新、
后置条件和 `finally` 突然退出重放会显式进入函数局限，不能作为确定性路径前提。
