# syntax_analysis 模块

本模块保存依赖图、ProgramGraph 以及未来其他静态分析共同使用的纯语法基础设施，不拥有符号
解析、调用目标绑定、CFG 或安全规则。

当前公共入口包括：

- `session.py`：`syntax_session()/reuse_syntax` 管理单任务的有界语法复用；`parse_tree()` 按真实
  内容哈希、文件身份、grammar 和 Parser 池身份复用树，`source_view()` 复用 Vue 等源码视图。
  每个消费者获得独立 Tree.copy，避免跨线程共享可变句柄；上下文通过现有 copy_context 传入文件线程池。
  默认总共最多 4096 条、64 MiB 估算存储（不是进程 RSS 硬限制），满额后不留存新项但继续正常分析，
  避免整仓顺序重扫的 LRU 冲刷；结束/异常清空。
  各消费者仍读取当前文件、执行原有权限/根目录/大小检查；不按 mtime 缓存文件访问，不跨任务持久化树。
  Python ast 和官方编译器 AST 不强行共用；本轮只减少同一 Tree-sitter 语法的重复解析，不合并不同 IR。
  生产 Pipeline 的 diagnostics.syntax_reuse 记录命中、未命中及估算占用，不含源码、路径或内容哈希。

- `go.py`：`go_function_name/go_callable_parts` 提供普通函数、接收者方法和匿名函数的位置身份，三种分析共用；函数体不能误归属外层路由注册函数。

- `TreeSitterParserPool`：按线程和语言复用 Parser；
- `parser_runtime.py`：`prepare_parsers()` 是安装/迁移入口，固定并准备服务端解析器缓存；正常分析通过 `require_local_grammars()` 只使用本地资源，缺少资源立即失败，不逐文件联网重试。依赖图在分析前检查资源，单文件前端也使用同一运行约束。
- `SOURCE_LANGUAGE_BY_EXTENSION/extensions_for_language()`：所有分析器共用的语言扩展名目录；
- `IGNORED_SOURCE_DIRECTORIES`：回退源码发现的统一忽略目录；
- `text/field/fields`：容错读取节点源码和命名字段；
- `semantic_children(node)`：只读具名非 extra 子节点；注释不占调用实参、返回分量等运行时槽位。各语言前端复用，不各写注释类型过滤。
- `first_of/descend_for`：有界节点查找；
- `walk_named`：含根的源码顺序具名子树遍历；安全 IR 文件收集器复用，避免各语言复制相同循环；不涉及值传播或作用域判定。
- `normalize_type/split_qualified`：跨语言类型和限定名称文本归一化。
- `javascript.py`：JS/TS 函数名称、参数语法和按扩展名选择 TSX grammar 的共同入口；依赖图、安全前端与公共程序图复用匿名函数身份，三种静态分析复用 parser 选择。
  `javascript_parameters()` 排除注释和 TypeScript 的 `this` 类型伪参数；`javascript_parameter_type()` 只提取简单/限定/泛型基名，不对联合类型或同名本地类型作运行时身份保证。
- `javascript_bindings.py`：将对象/数组、默认值、rest 和静态计算键转换为公共绑定 IR，安全前端与程序图共同消费；函数值保持不透明，不把函数体当立即执行的表达式。`javascript_binding_scope(source, path)` 提供单文件绑定缓存，两种前端共用；统一装饰器按需创建模式、值与投影缓存，每类分别最多 1024 个 LRU 条目，只保存不可变结果，不再维护三套分发包装和私有计算入口。节点包含树身份，值/模式键包含递归深度，源码对象及路径不匹配时绕过缓存。退出、嵌套作用域恢复或异常时清空节点引用，不常驻完整仓库 AST。调用表达式先选择实参与接收者分支，不再先构造随后被覆盖的通用子节点结果。
- `callback_inputs.py`：无 AST/图/安全规则依赖的回调参数输入契约，用回调和注册点原字节范围标识绑定，避免同名函数串联。其他语言可复用契约，尚未自动接入其框架。
- `parameters.py`：Python 声明参数类别及 Java 普通/可变参数名字和类型的纯语法读取；安全 IR 与公共图复用，不解析目标。
- `python_names.py`：从既有 AST 建立文件私有词法导入/遮蔽环境；函数、类、lambda、推导式不泄漏局部导入，普通方法闭包跳过类命名空间。不读取/重新解析源码，不处理任意运行时导入、猴子补丁、跨文件可变全局效果或赋值别名；不是 Python 原生引用/SSA 后端。
- `static_sequences.py`：static_sequence_value(node, identifiers) 将 Java/Go 的直接数组/切片字面量转成公共 BindingValue；不执行表达式或推断动态容器别名。
  序列成员不计注释；JS 解构/字面量同样跳过 extra 节点，但仍按真实逗号保留数组空槽，不能用注释覆盖已保存值。
- `normalize_static_index/parse_static_index_literal`：跨语言静态下标字面量归一化，供
  ProgramGraph 与安全前端共同使用。
- `unwrap_c_declarator()`：C/C++ 指针、数组、引用与函数声明符的纯语法拆解，供 ProgramGraph
  和安全 IR 共用；返回名称节点与参数容器，不负责跨文件符号绑定或安全角色。

`dependency_analyzer` 继续拥有跨文件符号、导入、类型和调用目标解析；`program_graph` 继续拥有
函数内部控制流与到达定义。两者共享语法工具和稳定身份，但不会互相创建对方负责的边。

当前跨阶段复用由 `session.py` 统一拥有，按内容哈希和分析生命周期限定；不把 AST 写入持久化
产物，也不在任务结束后长期保存。没有另设第二套 `SyntaxTreeCache` 或全局完整仓库缓存。

## 离线性能核对

`test/benchmark_static_analysis.py` 在项目根目录运行，接受 `--project-root` 或只读查询本机数据库的 `--project-id`，以及显式 `--files` 清单。`--baseline-ref <受信提交>` 只在诊断进程中从 Git 加载本轮指定的优化模块，其他代码/解析器仍使用当前环境；不切换工作区、不写项目/数据库/实验记录、不调用模型或执行目标代码。产物只输出规模与完整 SHA-256，计时不包括产物哈希及解释器启动。对照须保持文件清单、源码哈希、解析器与依赖环境一致，固定诊断进程的 `PYTHONHASHSEED` 并反向安排重复顺序；不改实际服务 `.env`。

首先确认完整事实和 CFG/到达定义/值流产物哈希相同，再比较阶段时间。`--syntax-reuse on|off`
分别使用正常额度与最小留存额度，比较同一实现的解析复用效果；未命中时分析照常进行。
子集基准和解析次数下降不能直接外推全项目提速。本轮没有改变扫描范围、候选上限、安全规则、
深度或模型输入；没有启用进程池或长期增量缓存。

`node_http.py` 只提取 Node HTTP/Express 请求 handler 与 data/end 注册；通用 `javascript_scope_nodes()` 已归入 `javascript.py`。Vue 混合源码视图与浏览器回调识别代码已删除。
