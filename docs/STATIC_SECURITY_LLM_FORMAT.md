# 面向大模型的静态安全证据格式

## 1. 设计结论

本项目不把完整依赖图、完整 AST、完整 SARIF 或全部源码直接交给模型，而使用两层协议：

1. `SecurityEvidencePack 2.3` 是服务器内部的完整、规范化、可恢复产物，并兼容读取 2.1/2.2；
2. `LLMSecurityEvidenceEnvelope 1.2` 是按问题、风险和字符预算生成的模型输入。

这种分层避免为了节省 Prompt 而删除原始静态分析信息，也避免把内部分析数据库原样发送给模型。

## 2. 调研依据

- SARIF 2.1.0 用 `result` 表示告警，用 `locations`、`relatedLocations` 和 `codeFlows/threadFlows` 表示位置与路径，用稳定指纹跨扫描识别相同结果；它还允许以共享位置表和索引降低重复数据。
- GitHub Copilot Autofix 并非只发送 SARIF。其模型输入还包含 Source、Sink、告警消息和流路径附近的短源码片段、涉及文件的少量文件头，以及规则帮助文本。
- IRIS 将每一条静态分析候选路径单独交给模型，突出 Source 与 Sink，Source/Sink 使用约正负五行上下文；长路径只选择最多约十个中间步骤，并优先保留函数调用。
- LLMxCPG 使用代码属性图寻找切片准则点，再以数据依赖和控制依赖生成前向/后向切片，说明图更适合作为切片基础设施，而不是 Prompt 本体。

## 3. 四项优先级如何落实

### 针对性

模型输入只包含安全候选，而不是项目通用架构摘要。每条候选至少具有：

- CWE、规则、类别和严重性；
- Source 及其信任类别；
- Sink 及其危险参数角色；
- Source、Sink 各自命中的规则标识；
- 有序 Def-Use 或结构调用步骤；
- Guard、Sanitizer、利用前提和未解析边界；
- Source、Sink 和必要中间点的短源码片段。

### 真实性

所有项目事实必须携带明确语义：

- `observed`：源码直接观察到的位置、表达式或声明；
- `inferred`：确定性静态分析推导出的关系；
- `structural_reachability_only`：只能证明函数调用结构可达；
- `intra_procedural_dataflow`：公共 ProgramGraph 在单个函数分区内建立了从 Source 到规则指定 Sink 实参的到达定义链；
- `interprocedural_dataflow`：除函数内链外，依赖分析还解析了调用目标，并将到达调用实参的值绑定到目标函数形参。

CFG 会参与到达定义计算，但当前不求解路径条件，也不证明某条分支在运行时可行，因此两类数据流仍只标记 `may_reach_sink`，不能表述为运行时必达。跨过程传播最多四层，覆盖已解析调用的实参到形参和局部变量直接接收的返回结果，不覆盖字段、容器、嵌套调用返回或完整对象别名。`generated` 命令、自然语言架构概述和模型生成结论不得进入静态安全证据。

### 全面性

完整产物保留覆盖率、规则包版本、未支持语言、解析失败、截断状态、全部事实和全部候选。Prompt 头部保留最多五十条紧凑覆盖盲区及其省略计数；因预算省略候选时必须返回 `omitted_findings` 和 `truncated=true`。模型可用只读工具 `get_static_security_evidence` 分页读取剩余候选。

### 信息密度

- 持久化层通过 ID 注册表去重；
- LLM 层按候选解引用，避免要求模型在多个注册表之间自行联结；
- Source 和 Sink 独立呈现，中间流只保留必要赋值或调用步骤；
- 长路径均匀采样且保留首尾，最多十个中间步骤；
- 使用紧凑但仍具语义的 JSON 字段名，不使用难以理解的单字母缩写；
- 达到预算时整条省略，绝不直接截断 JSON。

前三项优先于体积。如果删除字段会造成证据语义、位置、局限或利用前提缺失，则保留字段并减少候选数量。

## 4. LLM 输入示例

```json
{
  "schema_version": "1.2",
  "kind": "static_security_evidence",
  "source_schema_version": "2.3",
  "analysis": {
    "dataflow_scope": {
      "kind": "cfg_value_flow_with_bounded_calls",
      "languages": ["python"],
      "control_flow": "cfg_used_without_path_feasibility_proof",
      "interprocedural": "resolved_call_arguments_to_parameters"
    },
    "languages": ["python"],
    "rule_packs": ["python-core/1.0", "python-fastapi/1.0"],
    "coverage": {"files_scanned": 8, "candidate_count": 2, "dataflow_count": 1},
    "coverage_gaps": [],
    "omitted_coverage_gaps": 0
  },
  "findings": [{
    "finding_id": "candidate:...",
    "fingerprint": "candidate:...",
    "rule_id": "PY-SINK-SHELL",
    "cwe": "CWE-78",
    "category": "process_execution",
    "severity": "high",
    "claim": "interprocedural_dataflow",
    "taint_status": "may_reach_sink",
    "source": {
      "role": "source",
      "rule_id": "FASTAPI-ROUTE-PARAM",
      "name": "command",
      "symbol": "app.py::run",
      "location": {"path": "app.py", "line": 12},
      "trust_class": "untrusted",
      "provenance": "observed"
    },
    "sink": {
      "role": "sink",
      "rule_id": "PY-SINK-SHELL",
      "name": "subprocess.run",
      "location": {"path": "app.py", "line": 15},
      "value_role": {"argument_role": "sink", "arguments": [0]}
    },
    "flow": [{
      "order": 0,
      "kind": "call",
      "expression": "app.py::run -> worker.py::launch",
      "input_names": ["command"],
      "output_names": ["value"],
      "certainty": "must",
      "provenance": "inferred"
    }],
    "preconditions": [],
    "limitations": ["CFG 未证明路径条件可满足；跨过程返回仅覆盖局部变量直接接收的调用结果。"]
  }],
  "omitted_findings": 0,
  "truncated": false
}
```

## 5. 有限状态与回调绑定证据

React 简单 useState 和 Node 同请求局部 data→end 累积通过公共 `IRValueBoundary`
接入 CFG/DFG，不作为新 Source，也不是已解析的 calls 边。持久化数据流的可选
`value_boundary_ids` 与真实 `call_edge_ids` 分开，历史产物缺省为空列表，模式版本不变。

相关候选的 `confidence_dimensions.shared_state_binding=inferred_may`；分析范围包含
`shared_state=bounded_inferred_may_value_boundaries`。流步骤保留写入端与读取端两处
精确源码位置、输入输出槽位和具体局限。字符串累积读公共 CFG 的退出值；不得把
回调声明位置或函数退出槽位解释成源码中存在一次真实函数调用。
这类证据保持低置信度、`may_reach_sink`，不证明重渲染、事件调度、完整堆身份或运行时可利用性。
未建模原因继续传入候选局限和覆盖盲区；模型需要用原始源码验证利用前提。

## 6. 与 SARIF 的关系

新增调用边界证据仍使用相同信封：输出参数 Source 的 value_role 保存 output_arguments/
output_targets，保持 binding_certainty=may 和成功条件等局限；返回状态码不能作为内容。
Go 多返回 Source 保存 result_positions/result_count，以区分内容和错误/是否存在。
直接嵌套 Source 的 call-result 槽位是消费者符号值，不是项目源码变量；位置仍指向真实调用。
可变参数成员和返回分量不增加完整图输入，继续保留原始位置、绑定步骤、实际 calls 引用和局限。

该格式不宣称替代 SARIF。后续如需与 GitHub Code Scanning 或其他 SAST 平台交换结果，应单独提供 SARIF 导出器：

- `candidate_id` 对应稳定指纹；
- Source/Sink 和片段位置映射到 `locations`、`relatedLocations`；
- 数据流步骤映射到 `codeFlows.threadFlows.locations`；
- `importance`、`executionOrder` 和调用深度可在 CFG/跨过程分析完成后补齐。

LLM 输入继续使用本项目的紧凑格式，因为它需要显式真实性边界、利用前提、覆盖率和预算省略信息。
