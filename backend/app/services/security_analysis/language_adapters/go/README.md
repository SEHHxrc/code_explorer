# Go 安全语言适配器

稳定入口仍为 `GoSecurityFrontend.build(project_root, file_paths=...)`，原导入方式不变。
新代码从 `security_analysis.language_adapters.go` 导入；原 frontends.go 仅转出同一实现。
读取本地 Tree-sitter AST，输出公共 `SecurityProgramIR`，不需要原生 Go 工具成功，不执行项目。

| 文件 | 职责 |
| --- | --- |
| `__init__.py` | 公共文件编排、失败隔离、收集与路由关联的装配。 |
| `collector.py` | 函数/匿名处理器、参数、调用、赋值、条件及字段位置的 IR。 |
| `api.py` | 根据真实导入、显式类型、有限已知返回值识别 API 角色；区分内容和状态。 |
| `routes.py` | 按同包、接收者类型、唯一声明或匿名函数位置关联注册；歧义留诊断。 |

共同声明身份在 `syntax_analysis.go`，由依赖图、程序图和安全前端复用。
Source/Sink、CWE 和参数角色在 `security_analysis/rules/go`，不进入本目录。
所有规则仍复用同一个引擎、输出参数 Overlay 和公共 CFG/DFG，不另写 Gin 污点求解器。

支持常见 Gin 路由/静态 Group、Query/Param/表单/头/正文/绑定输出，以及标准 HTTP 路由、
Query/Header/Form/字段/Cookie/上传名、ReadAll/JSON/XML Decode、具名连接缓冲区 Read。
内容、bool/error/字节数分别建模；不把整个 Context、ResponseWriter 或附近校验自动当作污点/净化。

局限：外部 Group 完整前缀未知；自定义工厂、动态注册、复杂 Handler 包装、接口动态实现、任意
指针别名/库副作用、goroutine/channel、闭包捕获值调度和完整堆模型未覆盖。新建出站 Request
不冒充入站 Source；仅 Request 类型的形参仍需核实真实调用边界。处理器解析失败会登记诊断，不代表安全。
