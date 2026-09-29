# syntax_analysis 模块

本模块保存依赖图、ProgramGraph 以及未来其他静态分析共同使用的纯语法基础设施，不拥有符号
解析、调用目标绑定、CFG 或安全规则。

当前公共入口包括：

- `TreeSitterParserPool`：按线程和语言复用 Parser；
- `SOURCE_LANGUAGE_BY_EXTENSION/extensions_for_language()`：所有分析器共用的语言扩展名目录；
- `IGNORED_SOURCE_DIRECTORIES`：回退源码发现的统一忽略目录；
- `text/field/fields`：容错读取节点源码和命名字段；
- `first_of/descend_for`：有界节点查找；
- `normalize_type/split_qualified`：跨语言类型和限定名称文本归一化。

`dependency_analyzer` 继续拥有跨文件符号、导入、类型和调用目标解析；`program_graph` 继续拥有
函数内部控制流与到达定义。两者共享语法工具和稳定身份，但不会互相创建对方负责的边。

当前没有跨阶段长期保存完整 Tree-sitter AST。这样可以避免大型项目的语法树常驻内存；若后续
性能数据证明重复解析成为瓶颈，应增加按文件内容哈希和分析生命周期限定的 `SyntaxTreeCache`，
而不是把 AST 写入持久化产物。
