# syntax_analysis 模块

本模块保存依赖图、ProgramGraph 以及未来其他静态分析共同使用的纯语法基础设施，不拥有符号
解析、调用目标绑定、CFG 或安全规则。

当前公共入口包括：

- `TreeSitterParserPool`：按线程和语言复用 Parser；
- `parser_runtime.py`：`prepare_parsers()` 是安装/迁移入口，固定并准备服务端解析器缓存；正常分析通过 `require_local_grammars()` 只使用本地资源，缺少资源立即失败，不逐文件联网重试。依赖图在分析前检查资源，单文件前端也使用同一运行约束。
- `SOURCE_LANGUAGE_BY_EXTENSION/extensions_for_language()`：所有分析器共用的语言扩展名目录；
- `IGNORED_SOURCE_DIRECTORIES`：回退源码发现的统一忽略目录；
- `text/field/fields`：容错读取节点源码和命名字段；
- `first_of/descend_for`：有界节点查找；
- `walk_named`：含根的源码顺序具名子树遍历；安全 IR 文件收集器复用，避免各语言复制相同循环；不涉及值传播或作用域判定。
- `normalize_type/split_qualified`：跨语言类型和限定名称文本归一化。
- `javascript.py`：JS/TS 函数名称、参数语法和按扩展名选择 TSX grammar 的共同入口；依赖图、安全前端与公共程序图复用匿名函数身份，三种静态分析复用 parser 选择。
- `javascript_bindings.py`：将对象/数组、默认值、rest 和静态计算键转换为公共绑定 IR，安全前端与程序图共同消费；函数值保持不透明，不把函数体当立即执行的表达式。`javascript_binding_scope(source, path)` 提供单文件绑定缓存，两种前端共用；统一装饰器按需创建模式、值与投影缓存，每类分别最多 1024 个 LRU 条目，只保存不可变结果，不再维护三套分发包装和私有计算入口。节点包含树身份，值/模式键包含递归深度，源码对象及路径不匹配时绕过缓存；Vue 模板切换不误用脚本结果。退出、嵌套作用域恢复或异常时清空节点引用，不常驻完整仓库 AST。调用表达式先选择实参与接收者分支，不再先构造随后被覆盖的通用子节点结果。
- `source_units.py`：Vue SFC 的脚本/模板表达式视图，保持原始 UTF-8 字节偏移；依赖图、程序图、安全前端共享，不编译或执行 Vue。
- `callback_inputs.py`：无 AST/图/安全规则依赖的回调参数输入契约，用回调和注册点原字节范围标识绑定，避免同名函数串联。其他语言可复用契约，尚未自动接入其框架。
- `javascript_inputs.py`：提取当前作用域内原生表单事件/已确认请求体回调；具名回调必须唯一且未重新绑定。`javascript_scope_nodes()` 排除嵌套函数体，供程序图及安全适配共享。end 注册用于值边界匹配，不把 end 参数当请求体 Source。项目运行库只读取根目录内有体量限制的 package.json，不运行包管理器或构建脚本。
- `vue_bindings.py`：script setup 的有限可写绑定、ref 自动解包、模板指令与表达式范围关联；ProgramGraph 和安全前端复用，不包含 Source/Sink 判定或事件调度。
- `parameters.py`：Python 声明参数类别及 Java 普通/可变参数名字和类型的纯语法读取；安全 IR 与公共图复用，不解析目标。
- `static_sequences.py`：static_sequence_value(node, identifiers) 将 Java/Go 的直接数组/切片字面量转成公共 BindingValue；不执行表达式或推断动态容器别名。
- `normalize_static_index/parse_static_index_literal`：跨语言静态下标字面量归一化，供
  ProgramGraph 与安全前端共同使用。
- `unwrap_c_declarator()`：C/C++ 指针、数组、引用与函数声明符的纯语法拆解，供 ProgramGraph
  和安全 IR 共用；返回名称节点与参数容器，不负责跨文件符号绑定或安全角色。

`dependency_analyzer` 继续拥有跨文件符号、导入、类型和调用目标解析；`program_graph` 继续拥有
函数内部控制流与到达定义。两者共享语法工具和稳定身份，但不会互相创建对方负责的边。

当前没有跨阶段长期保存完整 Tree-sitter AST。这样可以避免大型项目的语法树常驻内存；若后续
性能数据证明重复解析成为瓶颈，应增加按文件内容哈希和分析生命周期限定的 `SyntaxTreeCache`，
而不是把 AST 写入持久化产物。

## 离线性能核对

`test/benchmark_static_analysis.py` 在项目根目录运行，接受 `--project-root` 或只读查询本机数据库的 `--project-id`，以及显式 `--files` 清单。`--baseline-ref <受信提交>` 只在诊断进程中从 Git 加载本轮指定的优化模块，其他代码/解析器仍使用当前环境；不切换工作区、不写项目/数据库/实验记录、不调用模型或执行目标代码。产物只输出规模与完整 SHA-256，计时不包括产物哈希及解释器启动。对照须保持文件清单、源码哈希、解析器与依赖环境一致，固定诊断进程的 `PYTHONHASHSEED` 并反向安排重复顺序；不改实际服务 `.env`。

首先确认完整事实和 CFG/到达定义/值流产物哈希相同，再比较阶段时间。子集基准不能直接外推全项目提速；本轮没有改变扫描范围、候选上限、安全规则、深度或模型输入，也没有启用进程池、跨阶段 AST 缓存或长期增量缓存。
