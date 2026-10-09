# 语言数据传播语义

`LanguageSemantics` 为通用数据流引擎封装语言特有的调用边界规则。`bind_arguments()` 消费安全
IR，`bind_program_arguments()` 消费公共 ProgramGraph。Python 支持位置限定、仅关键字及
args/kwargs 的常量成员；Java、Go、C 与 C++ 共用 `PositionalLanguageSemantics`，Java/Go
通过 ParameterKind 声明可变参数。普通位置和可变序列槽位使用同一算法；各语言仍
分别注册，使未来差异可在自己的适配器中覆盖。该实现属于安全分析的调用边界语义，不属于项目
仓储或通用 CFG Pass。

字面量展开由语法前端提供；Java 打包数组和 Go 直接字面量切片共用 BindingValue 与常量
元素投影，两个入口使用相同绑定实现。安全常量为空输入而非未知污染。
该绑定不代表 Source 已传播；动态容器、整集合传递、Java 泛型/动态派发、Go 动态切片、
接口派发与裸命名返回/多返回转发尚未完整解析。直接多返回分量由公共图及 flow_analysis
按有序结果槽位处理，不由参数语义重新解释调用目标。局限必须保留给模型。
C/C++ 只绑定已有的固定具名参数；可变参数、C++ 默认参数、隐式 this、引用写回和指针别名
尚未展开，不会为额外实参补造形参。

`javascript.py` 的 JS/TS 适配器通过 `value_binding` 公共投影器绑定嵌套解构形参。`ArgumentBinding.source_identifiers` 区分字段输入与整个实参，空字段输入不能回退为聚合值；不透明对象仍保守传播并标 may。spread 后位置不确定，不继续绑定，rest 形参实参数组尚未建模。Promise/回调、闭包、堆别名和中间件链仍不完整，不能把语法关系当成运行时保证。
