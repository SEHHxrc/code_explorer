# 安全语言前端

`LanguageFrontend` 把一种语言的源码转换为通用 `SecurityProgramIR`，不执行安全规则，也不解析跨文件调用目标。

当前 `PythonSecurityFrontend` 使用标准库 `ast`；`JavaSecurityFrontend` 与 `GoSecurityFrontend`
继承 `TreeSitterSecurityFrontend`，并与依赖图、ProgramGraph 共用线程本地 Tree-sitter 解析器池。
Java 前端提取方法、注解、继承类型和调用信息；Go 前端提取函数/接收者方法、导入别名、参数与
显式变量类型、调用实参、位置敏感赋值目标和条件。

文件范围来自依赖图模块节点；缺省扫描使用公共源码目录过滤。`symbol_id`、`location_id` 和
`callsite_id` 均使用 `program_index.ProgramIdentity`，其中 Java/Go 调用点必须与相应
ProgramGraph 前端对同一语法节点生成完全相同的 `callsite_id`。

新增语言时应：

1. Tree-sitter 语言优先继承 `TreeSitterSecurityFrontend`；非 Tree-sitter 实现继承 `LanguageFrontend`；
2. 注册到 `FrontendRegistry`；
3. 输出同一 IR 语义；
4. 将语言特有参数绑定放入 `LanguageSemantics`；
5. 不在前端中硬编码 Source/Sink 规则。
