# 依赖分析语言处理器

Handler 负责语言局部的 Tree-sitter 语法识别：遍历时将类、函数、变量、导入、调用和类型线索写入 `FileContext`。它们不解析跨文件目标，也不直接创建最终 NetworkX 图。

| 文件/类 | 覆盖语言与职责 |
| --- | --- |
| `base.py` / `BaseHandler` | 处理器基类、节点类型到回调的绑定、callee 拆分、内置调用和标准库判断。 |
| `python.py` / `PythonHandler` | Python import、类、函数、参数、赋值、类型推断和调用。 |
| `javascript.py` / `JavaScriptHandler`、`TypeScriptHandler` | JS/TS import、类/接口/类型/枚举、函数、成员、变量和调用。 |
| `java.py` / `JavaHandler` | package/import、类、字段、方法、局部变量、构造与调用。 |
| `go.py` / `GoHandler` | package/import、类型、字段、接口方法、函数、变量和复合字面量。 |
| `rust.py` / `RustHandler` | use/mod、结构体/枚举/trait 实现、函数、常量、宏和调用。 |
| `c_family.py` / `CHandler`、`CppHandler` | include、宏、typedef、记录、声明、函数，以及 C++ namespace/using/new。 |
| `__init__.py` | `HANDLERS` 注册表和公开导出，是语言选择入口。 |

新增处理器时，`register()` 应只绑定自己能稳定解释的节点；无法确认的类型或调用保留为待解析记录，由后续阶段统一处理。
