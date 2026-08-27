# Dependency Analyzer

该功能包负责多语言、跨文件静态依赖分析。公共入口为：

```python
from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
```

## 数据流

```text
CollectionPhase
  -> IndexingPhase
  -> ImportResolutionPhase
  -> TypeResolutionPhase
  -> GraphResolutionPhase
```

- `constants.py`：语言标准库、内置符号、扩展名和图节点映射。
- `models.py`：定义、引用、导入和作用域帧。
- `context.py`：线程独占的单文件解析上下文。
- `ast_utils.py`：Tree-sitter 节点和类型文本工具。
- `handlers/`：按语言提取定义、引用和导入。
- `phases/`：按分析阶段建立索引并解析跨文件关系。
- `analyzer.py`：稳定门面、配置、进度和阶段编排。

## 扩展语言

1. 在 `constants.py` 增加扩展名及运行库定义。
2. 在 `handlers/` 新增继承 `BaseHandler` 的处理器。
3. 在 `handlers/__init__.py` 注册语言键并导出处理器。
4. 若模块解析规则不同，在 `phases/imports.py` 增加专用解析分支。
5. 添加定义、调用、导入和标准库分类测试。

阶段 mixin 通过 `UnifiedCodeAnalyzer` 共享一次运行的索引状态。阶段之间只能按上述
顺序依赖，不应从 Handler 反向导入分析器或阶段模块，以免形成循环依赖。
