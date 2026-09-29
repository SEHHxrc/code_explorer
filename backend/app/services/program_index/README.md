# 程序公共身份

本模块是依赖图、安全 IR 和未来数据流图的共享身份层，不负责解析或安全判断。

- `file_id`：规范化项目相对路径；
- `symbol_id`：文件身份与嵌套作用域组成的 FQN；
- `location_id`：源码范围身份；
- `callsite_id`：只由调用表达式源码范围生成，与解析目标无关。

同一个动态调用点可以对应多个依赖图 `edge_id`，但这些边必须共享一个 `callsite_id`。数据流层通过 `callsite_id` 和 `edge_id` 引用调用结构，不得重新创建 caller-to-callee 结构边。
