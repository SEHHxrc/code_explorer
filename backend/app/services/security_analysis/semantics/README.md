# 语言数据传播语义

`LanguageSemantics` 为通用数据流引擎封装语言特有的调用边界规则。`bind_arguments()` 消费安全
IR，`bind_program_arguments()` 消费公共 ProgramGraph。Python 支持基础位置和显式关键字参数，
Java 与 Go 按声明顺序绑定普通位置参数。

该绑定是语法关系，不代表 Source 已传播到参数。Python 的 `*args`、`**kwargs` 和动态行为，
Java 的重载、可变参数、泛型擦除和动态派发，以及 Go 的 variadic 展开、接口派发和多返回值边界
尚未在此接口中完整解析；后续实现必须以置信度和未解析原因显式表示这些边界。
