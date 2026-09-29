# 安全规则包

规则包只声明安全 API、入口点和调用边界值角色，不遍历文件、不解析 AST，也不计算调用路径。

- `base.py`：`RulePack`、`CallRule`、`AccessRule`、`EntrypointRule` 和 `CallCondition`；
- `python/standard_library.py`：Python 核心与常见第三方 API；
- `python/frameworks/fastapi.py`：FastAPI/APIRouter 入口和参数 Source；
- `java/standard_library.py`：Java 环境/HTTP/文件 Source，以及进程、SQL、文件、反序列化、网络和弱随机数 Sink；
- `java/frameworks/spring.py`：Spring Web 映射注解入口；
- `java/frameworks/servlet.py`：受 `HttpServlet` 继承约束的 `doGet/doPost/...` 入口；
- `go/standard_library.py`：Go 标准库首批高置信 Source、Sink 与 Guard；
- `go/frameworks/net_http.py`：`http.HandleFunc/Handle(path, handler)` 注册式入口；
- `python_rules.py`：旧 `PYTHON_CALL_RULES` 的兼容导出，新代码不应依赖它。

每个规则包拥有独立名称和版本，例如 `python-core/1.0`、`java-core/1.0`、
`go-core/1.0`、`go-net-http/1.0`。`RulePackRegistry` 按语言选择规则，并拒绝相同标识被静默覆盖。

CWE 映射只表示规则类别，不证明项目已存在该 CWE。后续语言规则应尽量使用完整限定名称、接收者类型、参数角色和静态条件，避免仅依赖宽泛方法名后缀。
