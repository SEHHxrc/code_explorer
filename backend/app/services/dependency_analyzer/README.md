# 多语言依赖分析器

该功能包负责多语言、跨文件静态依赖分析。公共入口为：

```python
from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer

graph = UnifiedCodeAnalyzer(project_root, max_workers=4).run_full_analysis()
```

构造参数包含项目根目录、并行文件数，以及可选的内置符号/标准库过滤策略；`run_full_analysis()` 输出符号、无损 `MultiDiGraph`、公共 `semantic_index`、统计和诊断字典，`get_progress()` 返回当前阶段进度。调用边带有调用点、解析方法、目标范围、`must/may`、置信度和截断信息；未解析引用不会被静默丢弃。所有引用边通过 `program_index.ProgramIdentity` 生成与目标无关的 `callsite_id`，一个虚调用点的多个目标边共享该身份，但各自保留独立 `edge_id`。语义索引由同一次解析的定义、引用、变量类型表和调用边投影生成，不会为了共享事实再次解析源码。

`max_virtual_targets` 配置每个调用点最多展开的覆写目标，默认 128，允许 1～512；不再固定只保留八个。
超出仍显式标记 may 边截断，并保存 available/retained、最多 256 个省略目标的符号及省略列表自身是否截断。
它是有界静态派发候选，不证明运行时会选择哪个实现；更改后需重新分析。搜索状态和路径变体预算仍由传播域控制。

Go 与 JS/TS 在写调用边前分别调用各自的项目语义后端，`CompilerBindingIndex` 统一映射声明、
类型和回退诊断，不复制一套 Go 调用解析消费者。Go 原生 SSA 按同一声明身份附在语义索引，
不替代这里的跨文件调用图；未安装工具时仍保守回退。准备工具见
`backend/tools/go_semantics/README.md`。

默认装配点为 `language_backends.project_resolvers(file_languages)`，本模块不再导入某个语言的
工具私有实现。`semantic_resolver=` 保留注入协议，原有 Tree-sitter/Python 非原生解析继续可独立运行。
Go 普通/接收者/匿名函数的词法身份复用 `syntax_analysis.go`，匿名处理器的调用不归到注册函数。

## 阶段与依赖方向

```text
CollectionPhase
  → IndexingPhase
  → ImportResolutionPhase
  → TypeResolutionPhase
  → GraphResolutionPhase
```

阶段 Mixin 通过 `UnifiedCodeAnalyzer` 共享一次运行的索引状态，只能按上述方向依赖。语言 Handler 只把语法树转换为定义、引用和导入记录，不能反向导入分析器或阶段模块。
JS/TS 匿名回调复用 `syntax_analysis.javascript_function()` 的位置身份，并拥有真实函数
作用域，使回调中的调用归属实际 caller；具名变量函数沿用声明身份，不重复生成子函数。

`phases/imports.py` 统一解析 TypeScript 相对导入中的运行时扩展名：`.js` 对应已存在的
`.ts/.tsx` 实现，`.mjs/.cjs` 对应 `.mts/.cts`，`.jsx` 对应 `.tsx`。TS 优先源码实现；
普通 JS 保留实际 JS 文件，不被同名 TS 替换。不把声明文件当可执行函数体，不实现完整
tsconfig、package exports、路径别名或打包器解析。规则参考
[TypeScript 扩展名替换说明](https://www.typescriptlang.org/docs/handbook/modules/reference.html#file-extension-substitution)。
安全层继续消费这些既有调用目标，不为相同缺口再维护一套模块解析。

## 顶层文件

| 文件/类 | 作用 |
| --- | --- |
| `analyzer.py` / `UnifiedCodeAnalyzer` | 稳定门面、配置、进度、并发和阶段编排。 |
| `models.py` | `Definition`、`Reference`、`ReferenceResolution`、`ImportRec` 和作用域 `Frame`。 |
| `context.py` / `FileContext` | 单文件源码、语法树、作用域栈、类型绑定和提取结果。 |
| `compiler.py` / `CompilerBindingIndex` | 将工具声明绑定映射到已有符号，选择调用结果、保留回退原因并整合类型事实。 |
| `constants.py` | 扩展名、语言标准库、内置符号和图节点映射。 |
| `ast_utils.py` | 兼容导出层；通用节点读取和类型归一化来自 `services/syntax_analysis`。 |
| `handlers/` | 按语言把语法节点提取成中间记录。 |
| `phases/` | 建立全局索引并解析导入、类型、调用和图关系。 |
| `__init__.py` | 仅导出 `UnifiedCodeAnalyzer`。 |

## 编译器调用解析

JS/TS 在写入 calls/instantiates 边前优先消费公共 ProjectSemanticResolver 的声明绑定。
成功绑定不再运行旧接收者/同名目标推测；未支持场景和其他语言继续使用既有解析。回退记录
fallback_reason；图、语义索引和安全分析共享 callsite_id、edge_id、目标及不确定性，
不事后向安全模块追加另一套调用目标。工具配置见 backend/tools/typescript_semantics/README.md。

## 扩展语言

1. 在 `constants.py` 增加扩展名及运行库定义。
2. 在 `handlers/` 新增继承 `BaseHandler` 的处理器。
3. 在 `handlers/__init__.py` 注册语言键并导出处理器。
4. 若模块解析规则不同，在 `phases/imports.py` 增加专用分支。
5. 添加定义、调用、导入和标准库分类测试。

扩展名、忽略目录和 Parser 生命周期属于 `syntax_analysis` 公共层；语言 Handler、符号索引和
调用目标解析仍属于本模块。新增语言时还必须为 ProgramGraph 注册相应 collector/adapter，
注册表契约测试会阻止两个模块的语言集合静默分叉。

分析器不会执行项目代码。持久化产物保留多重边，供后续安全路径计算；安全 IR 和未来数据流通过 `callsite_id`/`edge_id` 引用这些结构边，不会复制 caller-to-callee 关系。`project_analysis.GraphExchangeNormalizer` 只为前端聚合同类平行边，并通过 `occurrence_count` 保留调用次数。结构调用路径不等同于已经验证的数据流或污点路径。

类型成员查找复用 `semantic_index.TypeHierarchy`：Python 为 C3，C++ 区分非虚基类支路与共享虚基类；
歧义不取遍历中的第一个成员。`Definition.virtual_bases` 与图中的 `virtual` 只保存源码基类修饰；
类型事实由现有定义和继承边投影，不另扫源码。Java/JS/TS 的显式父/同类初始化写入既有调用关系，
安全模块据此消费初始化体，不伪造新的结构边。未知第三方父类、复杂虚派发及动态构造仍有边界。
