# 多语言依赖分析器

该功能包负责多语言、跨文件静态依赖分析。公共入口为：

```python
from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer

graph = UnifiedCodeAnalyzer(project_root, max_workers=4).run_full_analysis()
```

构造参数包含项目根目录、并行文件数，以及可选的内置符号/标准库过滤策略；`run_full_analysis()` 输出原始节点、边和统计字典，`get_progress()` 返回当前阶段进度。

## 阶段与依赖方向

```text
CollectionPhase
  → IndexingPhase
  → ImportResolutionPhase
  → TypeResolutionPhase
  → GraphResolutionPhase
```

阶段 Mixin 通过 `UnifiedCodeAnalyzer` 共享一次运行的索引状态，只能按上述方向依赖。语言 Handler 只把语法树转换为定义、引用和导入记录，不能反向导入分析器或阶段模块。

## 顶层文件

| 文件/类 | 作用 |
| --- | --- |
| `analyzer.py` / `UnifiedCodeAnalyzer` | 稳定门面、配置、进度、并发和阶段编排。 |
| `models.py` | `Definition`、`Reference`、`ImportRec` 和作用域 `Frame`。 |
| `context.py` / `FileContext` | 单文件源码、语法树、作用域栈、类型绑定和提取结果。 |
| `constants.py` | 扩展名、语言标准库、内置符号和图节点映射。 |
| `ast_utils.py` | Tree-sitter 节点字段、文本和类型提取工具。 |
| `handlers/` | 按语言把语法节点提取成中间记录。 |
| `phases/` | 建立全局索引并解析导入、类型、调用和图关系。 |
| `__init__.py` | 仅导出 `UnifiedCodeAnalyzer`。 |

## 扩展语言

1. 在 `constants.py` 增加扩展名及运行库定义。
2. 在 `handlers/` 新增继承 `BaseHandler` 的处理器。
3. 在 `handlers/__init__.py` 注册语言键并导出处理器。
4. 若模块解析规则不同，在 `phases/imports.py` 增加专用分支。
5. 添加定义、调用、导入和标准库分类测试。

分析器不会执行项目代码。原始图随后由 `project_analysis.GraphExchangeNormalizer` 生成前端 DTO，并由 Manifest、Agent 与实验模块复用。
