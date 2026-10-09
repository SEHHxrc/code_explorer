# 静态安全分析覆盖清单

## 评价口径

Python、Java、Go、C/C++、JS/TS 已接入安全规则，但任何语言都不是完整覆盖。
语言接入、API 匹配、值流建立、真实漏洞是四个不同指标。
dataflow_verified 只表示建立了当前模型中的变量路径，不证明运行时可行或攻击者可控。
CLI、环境、props 和存储须核实信任边界。可信人员设置启动参数本身不构成远程漏洞；
继续区分 observed/inferred/generated，不把生成命令当源码事实。

## 语言审计

| 语言 | 已有规则/入口 | 公共值流 | 主要缺口 |
| --- | --- | --- | --- |
| Python | FastAPI/APIRouter；CLI/环境/部分文件；进程、SQL、eval、反序列化、文件、模板、重定向、网络、弱随机数 | CFG/到达定义、字段/静态下标、有限参数/返回；位置限定/仅关键字参数、args/kwargs 常量成员、字面量展开、直接嵌套 Source | 动态导入/反射、堆/容器别名、动态展开/整集合传递、依赖注入深层语义、后缀匹配误报、净化上下文 |
| Java | Spring Web/Servlet；环境/请求/文件；Runtime、JDBC、文件、反序列化、网络、弱随机数 | 公共 CFG/DFG、签名身份、字段/静态下标、有限跨方法、可变参数常量成员/直接打包数组、直接嵌套 Source | classpath/字节码绑定、多态、反射、泛型、动态数组/整集合传递、堆/副作用、其他框架 |
| Go | net/http；环境/请求/文件；os/exec、database/sql、文件、网络、弱随机数；Context API 角色区分 | 并行绑定、字段/静态下标、有限跨函数、直接多返回分量、可变参数常量成员/字面量切片展开、直接嵌套 Source | goroutine/channel、接口目标、命名裸返回/多返回转发、动态切片展开、指针/别名、Gin/Echo/Fiber |
| C/C++ | 标准库/POSIX/SQLite；环境/标准输入/缓冲区 Source；Shell、可执行路径、文件、格式字符串、SQL | 声明符、字段/静态下标、有限直接调用；read/fgets/recv 等独立调用的具名输出缓冲区 may 写入、直接嵌套 Source | 完整预处理、宏、函数指针/虚函数/模板、指针算术/别名、复杂缓冲区/字节范围/通用副作用、argv、完整内存越界 |
| JS/TS | 浏览器、Node.js、Express、本轮 Vue/React 核心模式 | 公共 CFG/DFG、复杂解构、字段隔离、有限跨文件传播 | 异步/事件、闭包、堆别名、动态键、spread/rest 长度、完整 SQL/全部 npm 库 |
| Rust | 无安全规则包，报告 unsupported | 有 ProgramGraph | 本轮不扩展 |

所有语言仍缺完整控制依赖、路径求解和异常语义。调用目标唯一复用 dependency_analyzer/semantic_index。

## 核心语言高影响缺口补全

本轮优先补会中断常见 Source-to-Sink 路径、或错误扩散到安全值的功能，不以缺口数量为目标。

- Python：区分 `/`、`*`、`*args`、`**kwargs`；字面量序列/字典展开同时进入规则 IR 和程序图。
  args 的常量位置与 kwargs 的常量键分开，不把整个容器自动污染到安全兄弟成员。
- Java/Go：普通可变实参与直接打包数组/切片字面量共用 ParameterKind、BindingValue 和位置槽位。
  Java spread_parameter 的名字/类型读取由依赖语言之外的共享纯语法工具适配。
- Go：直接 return 的每个分量对应有序接收变量，不能把内容扩散到 error/安全分量；
  os.ReadFile/os.LookupEnv 的 Source 只选内容位置。CommandContext、SQL Context 和
  NewRequestWithContext 使用真实危险参数位置，规则版本为 go-core/1.1。
- C/C++：声明式输出参数经私有后置操作接入公共 CFG/DFG；返回状态码不是输入内容。
  read/fgets/fread/recv 等仅支持具名缓冲区和独立调用，保持 inferred/may、低置信度。
- 各核心语言：直接 `f(source())` 或精确关键字实参无需临时赋值；中间项目包装函数仍走
  已解析调用及实际返回路径。固定安全返回的包装函数不得把输入直接带到外侧 Sink。

这些适配不复制污点传播算法、不创建 calls 边、不修改原始持久化程序图。源码参数位置、
返回分量和声明类别是可选公共契约；旧数据缺省读取，但必须重新分析才能获得新证据。

未完成：通用嵌套调用结果/复杂表达式、动态容器展开、可变参数整集合传递、Go 裸命名返回及
多返回转发、指针/对象别名、可变库副作用、完整动态派发和上下文相关净化。
这些缺口可能影响真实项目，不能统一称为影响有限；后续按实际使用与路径断点优先补。
完整编译器/反射/事件循环和新增框架暂缓，不把无告警作为安全证明。

`test/test_taint_language_gaps.py` 覆盖内容/状态与安全兄弟值隔离、普通调用续接、固定安全返回、
不可达语句、Context 参数角色、IR/程序图绑定一致、公共契约往返和原图不可变。本轮全后端 251 项通过。

## 已有范围：Vue、React、Node.js

版本未指定，按 API/语法建立有限支持，未启动框架运行时，不构成全部版本认证。
保留已有 Express，其他框架先不扩展。Node.js 是运行平台。

| 范围 | 本轮支持 | 不确认危险/漏洞 | 未完整覆盖 |
| --- | --- | --- | --- |
| Vue | .vue 内联 JS/TS；单块 script setup 顶层值到 v-html；原生文本 input/textarea 的 v-model 到可写变量或显式 ref/shallowRef；defineProps 待验证 Source；vue.h 的 innerHTML | 普通插值、固定 HTML、安全兄弟 ref、默认认定 props 可控；trim/number 修饰符不等于安全净化 | Options API、双 script、外部 script、v-for/slot、动态类型/非文本输入、自定义组件/select、完整响应式/事件、跨组件、Router/Pinia/Nuxt、模板调用目标 |
| React | JSX/TSX 原生 DOM 的 dangerouslySetInnerHTML.__html；react.createElement；原生表单 onChange/onInput 的 target/currentTarget.value；简单 useState 直接 setter 值到同组件状态读取；明确 React 线索下的组件 props | 普通 children、固定 HTML、安全兄弟字段、自定义组件同名属性、事件其他字段；状态本身不是新 Source，props 不默认攻击者可控 | 函数式 updater、对象/数组字面量状态更新、完整 hooks/context/重渲染生命周期、HOC、跨组件、路由/SSR；未知 JSX 运行库、动态回调注册 |
| Node.js | http/https.createServer 的 handler、req.url/headers；已确认请求 data 首形参；同请求局部字符串/简单数组累积到 end 读取及普通函数调用；env/argv；同步/fs.promises 文件读取；进程/文件/网络/动态代码 | res/next Source、任意对象同名 data 事件、end 参数、默认 execFile/spawn 普通参数当 Shell 注入、可信配置自动当远程漏洞 | 完整事件时序、复杂数组控制流/重绑定、共享堆/Buffer 身份、其他事件/socket、fs.readFile 回调、Promise 时序、动态 options、其他数据库/框架 |

Vue 顶层绑定/编译宏与原始 HTML 的依据：
[script setup 官方说明](https://vuejs.org/api/sfc-script-setup.html)、
[v-html 官方说明](https://vuejs.org/api/built-in-directives.html#v-html)。
React 原始 HTML 属性与普通 children 分别处理，参考
[React 官方说明](https://react.dev/reference/react-dom/components/common#dangerously-setting-the-inner-html)。
Node Shell 行为参考 [child_process 官方说明](https://nodejs.org/api/child_process.html)。

## 按影响与使用频率收敛的实施顺序

1. **常见输入边界（本轮第一批）**：React 原生表单、Node HTTP body data、Vue 文本 v-model。
   现代 JSX 不要求显式 React import：可由观察到的 React 导入，或项目内最近 package.json 的
   明确 React 依赖提供线索；仅依赖配置且混有 Vue/Preact 时不推断，不执行构建配置。
   这属于有限静态运行库推断，不是完整编译器绑定或所有 JSX 运行库认证。
2. **常见传播桥接（已完成有限模型）**：React 表单→简单 useState setter→同组件状态读取；
   Node data→局部字符串/简单空数组累积→同请求 end。字符串使用公共 CFG 的退出值，
   保留分支、提前返回和确定安全覆写；数组仅支持直线 push。不是完整闭包、堆或事件循环分析。
3. **与实测有关的语义规则（后续）**：实际数据库/网络 API、必要调用目标和净化上下文修正。
   完整事件循环、全堆对象敏感、所有 npm 库、完整反射等暂缓；不因缺口数量多就全部实现。

Vue 事件写入由公共程序图标记为 inferred/may，而非把 HTML 标签顺序当执行顺序；
可能写入不杀死先前确定定义，模板读取与脚本 .value 使用同一 ref 身份。
安全证据及 LLM 端点保留 binding_certainty=may。模型仍须核实攻击者控制与实际执行条件。
参照 [Vue 表单说明](https://vuejs.org/guide/essentials/forms)、
[React input 说明](https://react.dev/reference/react-dom/components/input)、
[Node IncomingMessage 说明](https://nodejs.org/api/http.html#class-httpincomingmessage)。
能力边界放入 global limitations，不再为每个 JS/TS 文件无条件记录同一异步缺口；
不能将能力缺口诊断条数用作这些特性在项目中的使用频率。

## 解构与公共协议

已验证嵌套对象/数组、重命名、默认值、空槽、rest、静态计算键、赋值解构、TS 参数、
字段隔离、整体覆写和跨文件解构形参。JS/TS 的 obj["key"] 与 obj.key 规范为同一身份，
其他语言保持自己的下标语义。参考 [MDN 解构说明](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Operators/Destructuring)。

value_binding.PatternResolver 与 AST/图/规则无关；JS/TS 生成 BindingPattern/BindingValue，
程序图、安全赋值目标和调用边界共用投影器。其他语言可按自己的语义适配。
不透明对象的字段/默认值保留 may，未完整传播对象形状，不能断定字段缺失。
静态 rest 排除已选成员；未知 rest 保守诊断。动态键、数组 spread、rest 形参与预算超限记录
diagnostics，不伪造位置。对象 spread 可覆写之前成员，后续显式成员仍有优先级。
未知对象跨函数解构保守传播聚合值，不宣称字段精确。

## 验证与实验

### 公共值边界

语言适配器输出规则无关的 `IRValueBoundary`，由 `ValueBoundaryIndex` 匹配原始文件、
函数范围和精确行列；通过私有捕获/退出值 Overlay 复用同一到达定义和值流算法。
不修改原始 ProgramGraph、不创建新的 Source，不伪造 dependency calls 边。
边界与调用共用深度预算，结果保留 inferred/may、低置信度和具体局限。
`DataFlowEvidence.value_boundary_ids` 与真正的 `call_edge_ids` 分开，旧产物缺省为空列表。
公共协议可接其他语言，但不代表 Java 等语言已实现闭包适配。

匿名回调复用共享函数身份，使调用解析归属真实回调；公共程序图不再把嵌套函数体当父函数执行。
创建函数值不等于执行函数体。安全覆写、局部遮蔽、跨组件/请求隔离和不可达写入有反例测试。
对象/数组字面量状态更新及函数式 updater 显式报告未建模，避免错误扩散到安全兄弟字段。

test/test_javascript_bindings.py 覆盖协议、框架正反例、字段隔离、位置、跨文件、
Vue JS/TS、动态诊断、独立发现、递归契约 JSON 往返。
test/test_javascript_inputs.py 覆盖新增三类输入、现代 JSX、静态接收者、作用域/绑定反例、
原始坐标和 LLM 中的 may；test/test_framework_bindings.py 验证公共可能写入不误杀旧定义、
契约往返和旧节点默认行为。
test/test_javascript_state.py 覆盖新增状态/请求体桥接及普通调用续接；
test/test_value_boundaries.py 验证公共契约、原图不变和无边界时默认行为。
通过测试不意味着消除缺口或证明真实 LLM 质量。旧项目须重新导入/分析，恢复快照不重扫。
两组保持原始文件工具一致，仅实验组增加安全证据；保留 scan_failures/limitations，
无告警不能判为安全。另见 FUNCTIONAL_VALIDATION.md、EXPERIMENT_PROTOCOL.md。
