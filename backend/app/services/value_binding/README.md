# 结构化值绑定公共模块

## 入口与功能域

本模块属于静态分析公共基础设施，不读取项目文件、不依赖 AST、不创建调用边、不判断漏洞。
PatternResolver.resolve(pattern, value) 接受 BindingPattern/BindingValue，
默认 StructuredBindingResolver 返回 BindingResolution：
projections 保存输出、输入、源码身份和 must/may，diagnostics 保存未建模原因。

## 文件和依赖

| 文件 | 作用 |
| --- | --- |
| contracts.py | BindingPattern/PatternMember 描述目标；BindingValue/ValueMember 描述值；BindingProjection/BindingResolution 描述结果；PatternResolver 定义消费协议。 |
| resolver.py | StructuredBindingResolver 展开嵌套模式；append_selector 生成字段/位置身份。 |
| parameters.py | ParameterKind 描述位置限定、普通、仅关键字、可变位置/关键字参数；共享类别契约，不计算调用目标或污染。 |
| __init__.py | 公共导出。 |

依赖方向：JS/TS AST → syntax_analysis.javascript_bindings → 公共绑定 IR/投影器
→ program_graph 的定义/使用和值流、security_analysis 的事实定位和调用绑定。
两个前端共用算法。递归契约支持 ProgramGraph JSON 保存/恢复。
JS/TS 使用完整结构模式协议；Java/Go 的直接打包序列已复用 BindingValue/ValueMember，
Python/Java/Go 的声明类别共用 ParameterKind。其他绑定保留各语言的访问路径和配对钩子。
未来可按各语言语义适配，不要求迁移为 JS 的模式。

## 不确定性

固定字面量绑定即使 inputs 为空也保留，不能回退为整条语句全部输入。
null 不等于 missing，只有 missing 确定使用默认值；不透明对象的默认值保留 may。
静态 rest 排除已选字段；未知 rest 无法证明排除范围，保守诊断。
默认深度 24、投影上限 512。动态键不猜测。
不执行 getter、代理、迭代器或用户代码，不证明堆别名或运行时路径可行。
