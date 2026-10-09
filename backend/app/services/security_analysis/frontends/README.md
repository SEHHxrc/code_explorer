# 安全语言前端

`LanguageFrontend` 把一种语言的源码转换为通用 `SecurityProgramIR`，不执行安全规则，也不解析跨文件调用目标。

当前 `PythonSecurityFrontend` 使用标准库 `ast`；Java、Go、C/C++、JavaScript/TypeScript 前端
继承 `TreeSitterSecurityFrontend`，并与依赖图、ProgramGraph 共用线程本地 Tree-sitter 解析器池。
Java 前端提取方法、注解、继承类型和调用信息；Go 前端提取函数/接收者方法、导入别名、参数与
显式变量类型、调用实参、位置敏感赋值目标和条件。
C/C++ 使用同一 `c_family.py` collector，复用 `syntax_analysis.unwrap_c_declarator()`，保留
namespace/class 和限定方法身份、直接调用返回值目标以及头文件/同名声明过滤信息。
前端仅记录这些语法属性，Source/Sink 的库名、参数角色和头文件要求由规则包声明。
Java 参数读取复用 `syntax_analysis.parameters.java_parameter_parts()`，避免遗漏
spread_parameter 的名字/类型；Python/Go 安全 IR 同样记录 ParameterKind。
Java/Go 的直接打包数组/切片通过共享 static_sequence_value() 提供 BindingValue；
Python 字面量 *序列/**字典同时规范到规则 IR 和程序图，以免规则条件看不到 shell 等参数。

`javascript.py` 同时提供 `JavaScriptSecurityFrontend` 和 `TypeScriptSecurityFrontend`，共享 ESM/CommonJS 别名、平台全局遮蔽、有限工厂接收者、属性读写和路由注册。`syntax_analysis/javascript.py` 的函数名称/参数和 TSX parser 选择同时被公共程序图复用；匿名回调使用 `anonymous@行:列` 独立身份，可做函数内分析，但不能宣称已有完整回调调用图。Express 的 req 指定字段才是 HTTP Source，res/next 不是 Source。规则仍声明在独立规则包内。

JS/TS 复杂解构通过 `syntax_analysis.javascript_bindings` 和 `value_binding` 复用；Vue SFC 使用公共源码视图，脚本及模板保留原始行列。React 原始 HTML 与 Node HTTP handler 只提取语法事实，安全类别由独立规则声明。

`TreeSitterSecurityFrontend.collect_file_context()` 为适配器提供受控项目上下文，默认转发
`collect_unit()`，不保存跨项目可变状态。JS/TS 将观察到的回调注册转换为
`CallbackParameterInput`；React 表单只绑定 value 字段，Node 请求体只绑定 data 首形参。
Vue 模板指令共享 `vue_bindings`，有限 v-model 写入以 may 进入公共图与安全事实，
规则通过 `JS-SOURCE-INPUT-EVENT/JS-SOURCE-NODE-BODY/JS-SOURCE-VUE-MODEL` 声明信任边界。
回调内分析不等于完整事件、闭包、state 或响应式生命周期分析，覆盖边界见 SECURITY_COVERAGE.md。

`javascript_state.py` 的入口 `collect_javascript_value_boundaries()` 接收当前函数 AST、
源码、相对路径、作用域位置、已确认回调注册，以及 API 资格化/位置回调；返回
`IRValueBoundary` 列表和覆盖缺口。仅适配 React 简单 useState 的直接值更新、Node 同请求
局部字符串/简单数组的 data→end 捕获。它复用共享语法及结构绑定，不执行污点传播、
不重新解析调用目标。实际值流由 `flow_analysis/value_boundaries.py` 接入公共 CFG/DFG。
函数式或对象/数组字面量状态更新等明确报告未建模，不当作已证明安全。

文件范围来自依赖图模块节点；缺省扫描使用公共源码目录过滤。`symbol_id`、`location_id` 和
`callsite_id` 均使用 `program_index.ProgramIdentity`，所有 Tree-sitter 前端调用点必须与相应
ProgramGraph 前端对同一语法节点生成完全相同的 `callsite_id`。

新增语言时应：

1. Tree-sitter 语言优先继承 `TreeSitterSecurityFrontend`；非 Tree-sitter 实现继承 `LanguageFrontend`；
2. 注册到 `FrontendRegistry`；
3. 输出同一 IR 语义；
4. 将语言特有参数绑定放入 `LanguageSemantics`；
5. 不在前端中硬编码 Source/Sink 规则。
