# C/C++ 安全规则

本目录只拥有安全知识声明，C 与 C++ 使用同一规则集合，无独立扫描器或 CFG/DFG 算法。

| 文件/对象 | 功能与入口 |
| --- | --- |
| `standard_library.py / C_FAMILY_STANDARD_LIBRARY_PACK` | `c-family-core/1.0`：环境变量、标准输入/流、输出缓冲区 Source；Shell/进程路径、文件路径、格式字符串 Sink；比较 Guard。 |
| `standard_library.py / C_FAMILY_SQLITE_PACK` | `c-family-sqlite/1.0`：`sqlite3_exec(db, sql, ...)` 的第二参数 SQL 角色。 |
| `__init__.py / C_FAMILY_RULE_PACKS` | 导出不可变规则包，由 `SecurityScanner` 注册。 |

规则依赖 `rules/base.py` 的公共 `CallRule/RulePack`。前端输出规范名称、可见头文件与名称遮蔽
信息；公共规则引擎检查这些条件。数据流由 `flow_analysis` 消费 `program_graph` 的值流，跨文件
调用使用现有 `semantic_index`，规则文件不读源码、不调用 LLM，也不保存项目产物。

`required_headers` 是保守词法条件，不能代替完整预处理/类型绑定；规则匹配置信度为 `medium`。
没有观察到头文件时，生成覆盖缺口诊断。`output_argument_positions` 标识库调用写入的缓冲区，
具名缓冲区的独立调用已经通过私有后置操作接入公共 CFG/DFG，内容可到 Sink，但仅为 may
候选；返回状态码不等于内容。复杂指针、字节范围、NUL 终止、成功条件和同操作多调用
顺序未建模，保留覆盖诊断/局限；不宣称实现完整内存安全或通用副作用分析。
环境变量仍要求攻击者能影响配置；通用输入流需验证来源。固定 exec 程序的数据参数不会自动
判定 Shell 注入，固定 printf 格式的数据参数不会自动判定格式字符串注入。

规则角色参考 [Clang 污点配置](https://clang.llvm.org/docs/analyzer/user-docs/TaintAnalysisConfiguration.html)
中对返回值、输出实参和 Sink 参数的区分；Shell 语义参考
[POSIX popen](https://pubs.opengroup.org/onlinepubs/7990949875/functions/popen.html)，SQL 参数位置参考
[SQLite exec](https://sqlite.org/c3ref/exec.html)。本模块仍使用本项目的公共静态分析实现，未接入
Clang 符号执行器。
