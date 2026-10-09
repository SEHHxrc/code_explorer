# 原型功能性与安全实验可行性验证

## 本轮验证边界

本轮目标是验证完整流程可落地，不判断哪组模型更好。模型使用确定性替身，无真实 API 消耗；项目、产物和 SQLite 全部位于临时目录，不修改用户现有项目、数据库和 `.env`。

上一轮结果：后端 172 项、前端 6 项测试和生产构建通过。扩展 JS/TS 解构及 Vue/React/Node 规则后，后端 189 项通过；常见输入边界与公共可能写入阶段为 208 项；本轮有限状态/回调值边界补全后，后端 227 项通过，Ruff 与后端基础类型检查通过。本轮未改前端，也未重跑前端构建。类型检查关闭缺失外部导入/类型 stub 报告，不等同严格模式全量证明。

新增 `test/test_javascript_security.py` 验证浏览器输入→DOM、Express 命名/匿名路由、ESM/CommonJS、Node 命令执行、TSX、跨文件传播、同一行/兄弟变量隔离、API 遮蔽与动态 Shell 选项等。

`test/test_javascript_inputs.py` 验证 React 表单/现代 JSX、Node 请求体 data 回调、Vue 文本
v-model/ref 的正反例与 may 的 LLM 投影；`test/test_framework_bindings.py` 单独验证公共可能
写入不会误杀此前定义及契约兼容。规则包版本升级为 javascript-typescript-core/1.2。
该输入边界阶段不验证完整 hooks、闭包重渲染、跨回调累积、Promise 调度或运行时漏洞可利用性。

本轮 `test/test_javascript_state.py` 与 `test/test_value_boundaries.py` 新增 19 项：React
表单值→简单 useState→HTML Sink；Node 同请求字符串/简单数组累积→end→Sink 及普通
函数调用续接；安全覆写、局部遮蔽、跨请求/组件隔离、不可达写入、原图不变、契约往返和
LLM may 投影。匿名回调拥有真实调用作用域，父函数图不执行嵌套函数体。
对象/数组字面量状态更新、函数式 updater 明确报告缺口；不验证完整 hooks、对象堆身份、
Promise/事件循环时序或真实漏洞可利用性。没有这些路径的候选不代表项目安全。

新增 `test/test_security_experiment_workflow.py` 通过真实项目生命周期入口完成：ZIP 导入、源码清洗、依赖图、公共 CFG/DFG、安全证据保存、项目库存与快照恢复、实验随机入队、相同工具/指令与证据隔离、模型工具循环、累计指标、Agent 历史证据恢复、盲评/揭盲、HTTP/SSE 授权边界、失败回滚、项目及关联数据删除。失败和取消明确拒绝效果评分，测试允许两组答案相同。

## 自动化执行

核心语言补全阶段新增 `test/test_taint_language_gaps.py` 的 24 项测试，全后端达到 **251 项**。
覆盖 Python 参数类别/字面量展开、Java/Go 可变参数元素隔离与打包序列、Go 多返回分量及
Context API、C/C++ 输出缓冲区、各核心语言直接嵌套 Source、固定安全包装函数、不可达写入、
契约兼容与私有 Overlay 不修改原图。Ruff 与基础类型检查通过。
只解析源码，不启动被分析程序或框架；语法路径不等同编译器类型证明或真实漏洞。

在项目根目录、使用有效的 Python 环境执行。现有后端依赖之外，HTTP 联调测试需要 `httpx==0.28.1`，仅作为测试依赖。

```powershell
python -m pip install httpx==0.28.1
python -m unittest discover -s test -p 'test_*.py'
ruff check backend test/test_javascript_security.py test/test_security_experiment_workflow.py
Set-Location frontend
npm run test:unit
npm run build
```

本轮 `.venv` 解释器已可用。tree-sitter-language-pack 1.13.7 按需下载 grammar，在受限网络下无法获取，因此验证使用隔离安装的 0.13.0 离线 grammar、Tree-sitter 0.26.0 和测试工具；未修改 requirements、.venv、.env 或用户数据库。1.13.7 的 grammar 下载和兼容仍需正常网络下确认。

## 仍需人工或真实运行环境验证

1. 启动正常后端、前端和 Agent Worker，登录并上传新的 JS/TS 样例；旧项目不会自动重扫，新规则验证应重新导入。
2. 检查图浏览、文件树、右栏联动、上传/删除/再次上传、页面尺寸和滚动体验。前端单元测试和生产构建不等同浏览器交互验收。
3. 在 Agent 页面测试实际 Provider 的工具调用兼容、选定模型、回答与恢复；本轮没有联网调用模型，不能保证某第三方 API 运行无误。
4. 用相同固定项目/问题跑真实配对，多次重复并人工核实。先使用有真值的漏洞/修复样例，再扩展到真实仓库；完整要求见 `EXPERIMENT_PROTOCOL.md`。
5. Docker 执行任务必须在启动 Docker Engine、配置 Worker 和显式授权后单独验收。本轮只读诊断发现 Docker CLI 存在但 Linux Engine 管道不可连接，未启动容器或执行用户脚本。

## 分析能力限制

安全规则覆盖 Python、Java、Go、C/C++、JS/TS，Rust 报告未支持。复杂解构复用 value_binding 公共协议，Vue/React/Node 有核心语法/API 支持，但异步/闭包/堆别名、Options API、组件间传播或框架包装仍不完整。所有语言都不是全量扫描器，详见 SECURITY_COVERAGE.md。

任何 `may_reach_sink` 都是静态候选，不等于攻击者可以利用的真实漏洞。未发现候选也不能判定安全。实验流程成功、静态解析覆盖和 LLM 判断准确性必须分别报告。
