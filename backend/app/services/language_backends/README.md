# 原生语言后端

本目录适配可信语言工具，不保存项目，也不另建调用图或安全传播算法。

公共入口为 `project_resolvers(file_languages)`，返回 `(ProjectSemanticResolver, 文件清单)`。
协议及返回契约在 `semantic_index.resolution`；依赖分析器也允许直接注入实现该协议的 resolver。
更换工具进程、环境或协议解码内部实现，不需要修改 CFG/DFG、安全规则或证据消费者。

| 文件 | 功能 |
| --- | --- |
| `__init__.py` | 默认后端的唯一装配点，使用已有语言判定，不重复发现源码。 |
| `go.py` | `GoSemanticResolver.resolve()`：锁定 x/tools 的声明、类型及原生 SSA。 |
| `typescript.py` | `TypeScriptSemanticResolver.resolve()`：锁定的官方 TypeScript 工具。 |
| `go_environment.py` | `GoAnalysisConfig`：离线、只读、目标平台和缓存位置白名单。 |
| `worker.py` | `run_worker()`：唯一进程传输，统一时限、体积限制和异常码。 |
| `wire.py` | `decode_table_result()`：唯一共享表解码器，拒绝损坏引用和行结构。 |

依赖方向：本目录 → 公共语义契约、标准库、`backend/tools` 中的可信工具。
不依赖项目数据库、Agent、LLM、实验或 HTTP，不运行目标项目、安装其依赖或执行构建插件。

原生工具只是可选增强：成功绑定在统一解析点使用；不可用、超时、类型错误或目标未映射时，
保留原有 Python/Tree-sitter 非原生解析及降级原因。Go SSA 持久化事实由 `program_graph/value_query.py`
通过既有 SemanticSSAView 消费；适用函数替换 Def-Use 来源，不另建安全规则/跨过程污点求解器。
TS 提供声明/引用/调用绑定，不因安装编译器而被标为已经导出 SSA。
原 `semantic_index.go/typescript/go_environment/worker/wire` 只再导出同一个实现，不保留并行版本。
新调用使用本目录公共入口，测试注入内部实现时使用新路径。准备工具及 WSL 命令见 `backend/tools` 下对应 README。
