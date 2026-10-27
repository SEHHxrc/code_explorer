# 静态安全证据对照实验模块

当前按需协议为 `static-security-v21-chain-boundaries`，报告 `ledger-security-v7`、台账 `security-review-ledger-v2`；累计与完整源码条件分别为 v22/v23。初始交付路径族目录，族内按路线 ID 分批核验。
两组默认 12 次调查请求、上限 36；每批独立一次报告、最终一次汇总，报告不扣调查额度，格式重提交仍整次至多一次。
调查只提供调查工具，批次报告只提供提交工具（auto，或直接返回完整台账），汇总无工具。
旧调用及原生续接状态转换为带来源的证据结果，不删除源码定位；每轮实发 Schema、阶段和省略都按原有机制审计。
沿用 v19 无报告总字符上限及输入/传输/存储保护；族级状态不表示已穷举路径或确认漏洞。
第三方 API 知识 Agent 是独立显式任务，不混入本次静态输入、主分析调用额度或计费事件。
摘要与运行策略版本随新试次冻结；以下 v11/v14 等为历史说明，旧记录不回填。

### 路径边界与独立评价（v7）

`chain` 必填起终点及证据、范围 `local_segment/external_to_sink/not_applicable`。内部形参到敏感操作只能证明该局部区间；外部影响的建立/条件性判断须有外部到敏感操作的闭合链。未知条件使用 `kind/description`：`chain_gap` 强制 `open`，`deployment` 允许保留已建立源码机制的条件性影响，安全要求未知或未分类条件不能计入源码支持风险。不通过解析器猜测旧字符串的类型，也不修改模型原文。枚举约束不能理解文字中的错分类，语义仍需人工核实。

新盲评 `independent-review-v2` 分别保存发现广度、链路核验、结论可信度，无自动总分，不按风险/closed 数量奖励正确性。旧四项分数原样展示，不转换、不补填。六项 coverage 仅是附依据的模型检查声明，不是完整安全审查通过。

引用检查 v3 根据有响应回执的实际请求保留源码行及请求 ID：最终请求已展示、早期批次已展示、未确认展示分别处理。早期正文未重放只进入 `notices`，不计为未展示警告或自动幻觉；回查请求与对应批次后仍需判断引用是否支持断言。仅规划、仅工具返回且未续发、失败请求均不增加已展示集合。没有响应的请求保持未知，不据此断言平台未收到。源码脱敏版本加入运行策略哈希，新旧条件不能混算；旧产物的受损片段需重新静态分析后才会更新，旧实验审计不修补。

本模块比较“相同原始文件访问能力 + 静态安全证据”与“仅原始文件”的安全分析效果，不预设输赢。两组共享指令、模型、原始文件工具、批次报告规则和预算；实验组另有静态查询工具，两组实际工具契约分别冻结。独立随机化入队顺序与左右标签，不输入完整图、Manifest 或 Repo Map。静态分析由 `services/security_analysis` 负责，本模块只负责编排、输入隔离和评审。

历史 v14 条件使用 `static-security-v14-review-ledger` / `ledger-security-v6`：初始路径摘要和排序不变，两组共用结构化发现台账与六类覆盖核对。最终输出是 JSON，`findings.FindingLedger` 严格校验后生成四段报告，摘要编号和计数只来自状态。Schema 仍由该模型唯一生成，不维护第二份手写契约、不降低校验强度。源码支持风险不等于已确认可利用漏洞。模型 JSON 保留在审计，台账保留于 `findings.recorded` 或批次事件，原始文件组仍不访问派生索引。其共同默认 8 轮调查，可选 1～24；v6 额外允许整次运行至多一次格式重提交，两组规则相同，实际请求与成本全部记录。旧结果不回填，v5/v6 不混算。

### 格式重提交 v6

`findings.LedgerValidationError` 区分 JSON 语法、字段结构与状态一致性错误，提供有界的错误位置或字段路径，不携带输入值。共同指令明确 `reviewed_ranges/unreviewed/limitations` 与 `coverage` 同级。Schema 是提示词约束，不声称兼容端点强制支持结构化输出。

`AgentRunManager._generate()` 仅对正常结束、非空、无工具调用的实验台账语法/结构错误开放一次重提交；状态冲突、空白、截断、未知终止和协议违规不借此重试。原请求的完整已展示会话与原始失败回复保留，只追加格式诊断，不增加证据或调查。v20 批次报告重提交仍只提供提交工具；可接受严格校验的唯一提交调用，混合或调查调用拒绝。最终汇总重提交无工具，非分批条件仍按原冻结模式处理。若完整重提交超输入预算则跳过，不裁证据。重提交仍失败就拒绝，不循环；额度为每次运行一次，不是每批一次。

`_generate_once()` 统一发送、审计和完成事件，首次失败与重提交各计一次用量；原文不补括号、不搬字段、不补缺失状态。`report.format_rejected/format_retry_started/format_retry_skipped` 记录过程，指标及历史页面显示是否发生重提交。重提交是模型再次生成，不保证语义完全未变；严格台账与引用检查仍不证明结论正确。新的运行策略冻结 `report_format` 版本/额度，普通 Agent 不启用该流程；完整源码补充条件仍零工具，但可能有一次额外格式请求，不再承诺只有一次 HTTP 请求。

`citations.py` 新警告版本为 `mechanical-citations-v2`：分别记录引用语法不规范、唯一简称解析提示、同名歧义、真实路径/行范围错误、未展示源码或只在早期展示。简称唯一时仍提示不符合规范，并在原快照坐标上核对；歧义不猜测。原报告不改写，警告不自动否定答案，也不证明引用支持断言。`test/security_report_cases.json` 提供错误变量净化、白名单后执行、工厂隐藏实现、同接口不同根因的人工/模型评估样本；不嵌入生产提示词，不冒充自动语义验证。

v4 追加共同的逐轮阶段/剩余预算说明，冻结最终报告模式、阶段版本和调查轮次。两组不一致或 API/Worker 契约不一致时不能正式比较；最终报告失败不自动换模式、改结束状态或增加工具轮次。实际默认仍为 4 轮调查（可有多个调用）加一次收尾，未自动增加到 6 轮。历史 v3 记录不回填。`max_request_input_estimate` 是最大单次请求估算，与多轮累计估算区分；旧版错误的只读派生诊断不会改写原事件。

v5 使用安全输入 1.4：片段仅在同页 `snippets` 表保存一次，候选与观察保留引用，不删源码、安全角色、位置、路径和不确定性。格式版本写入共同运行策略与契约，不能与旧嵌入正文格式混算；两组的原始文件工具和权限不变。旧 2.1～2.3 静态产物可以直接生成新格式，不必为输入去重重扫；旧实验/审计不重建、不回填。单份片段的表结构可能略增开销，重复引用时才有体积收益。

## 入口与数据流

### 累计证据预算补充条件

`cumulative.py` / `static-security-v15-cumulative-ledger` 为另一独立研究条件。
`prepare_inputs(root, paths, pack, condition)` 冻结源码范围、原静态事实选择和共同累计字节额度；
`EvidenceAllowance` 为每个运行保存唯一源码行账本，`CumulativeReadTool` 与 `CumulativeSearchTool`
共用配额，搜索命中不能绕过限制。`CumulativeContextBuilder` 只发送共同文件清单与可选有界静态事实，
完整私有源码快照仅用于受控读取和审计。`cumulative_trial.py` 默认离线预检，显式 `--run-model`
才创建配对，复用同一 Worker、接入层与报告完整性检查；可用 `--project-id` 和 `--condition` 限定项目和条件。
该补充入口不替代网页默认按需分批流程、不修改实际 `.env`、不自动打分。理论容量与实际请求展示覆盖分开记录，
累计证据字节不是供应商 Token。条件定义、计费方式与复现入口见
[累计证据预算实验](../../../docs/CUMULATIVE_EVIDENCE_EXPERIMENT.md)。

诊断入口和 `prepare_inputs()` 继续选择 `security-path-summary-v2`，新配对使用独立协议 `static-security-v15-cumulative-ledger`。
新选择复用 `services/security_analysis/evidence_selection.py`，不是另建扫描器；选择器源码哈希、
原产物哈希、选择版本与字节上限进入私有冻结输入，漂移时发送前失败。保持同一共同指令、
原始文件工具、源码范围、轮次和累计总额度；静态正文按实际大小计费。
旧选择器及 `--selection` 切换已删除；`--static-max-bytes` 事前固定摘要上限（默认 8000 UTF-8 字节）。
复测 audio2sheet 时显式设为 7676，沿用上一轮比较额度，而不运行旧算法反推上限。
旧 v8～v13 标签和记录只读；重跑旧实现须使用当时另存的版本，不能用当前代码回填。网页默认 v14，不混算历史结果。
详见 [选择整改说明](../../../docs/STATIC_EVIDENCE_SELECTION.md)。

### 完整源码补充条件

`full_source.py` / `static-security-v16-full-source-ledger` 是独立研究条件，不替代网页当前的按需实验。

只验证共同报告链路时，可筛选既有极小样本，避免默认重跑三个项目：

```powershell
python -m backend.app.experiments.full_source_trial --case case_03 --repeats 1
# 明确允许真实模型调用时才加 --run-model；两组各一次报告，使用已有有限网络重试。
```

`--case` 可重复指定，不提供时维持全部样本的原行为；未知或空选择在导入项目及模型调用前拒绝。
此条件验证完整源码输入、共同 JSON 台账、校验、报告生成与持久化，不验证工具调用或分批汇总，也不自动评分代码安全性。
`prepare_full_source_inputs(root, paths, raw_pack, static_max_bytes=8000)` 接收明确的项目相对文件清单、
已有静态安全产物和独立摘要预算，两组共用完全相同的全文 JSON；仅证据组追加公共路径摘要。
它和累计预算条件共用 `freeze_source()` 冻结源码，共用唯一 `SecurityEvidenceSelector`，不再有事实优先的备用选择器。
不发送完整图、架构介绍或生成命令。源码最多 100 文件/128 KiB，UTF-8 全文原样保留；秘密、二进制或
预算不足直接拒绝，不截断、不放宽预算。省略路径计数明确；零工具条件不声称可以按 ID 续读，静态候选不等于漏洞。

两组均零工具、一次最终报告，不分批、不在空白后自动更换模式或再次生成；正常网络传输重试仍按共同策略记录。
输入、源码哈希和非认证模型配置指纹在创建时冻结，源码/配置漂移在发送前拒绝。冻结正文计入现有审计配额，
受相同访问权限、过期及删除策略约束，不进入普通历史/SSE。无新数据库表。完整源码行范围进入机械引用核对，
仍不证明断言语义正确。普通网页“开始配对”创建 v14；完整源码实验目前使用显式命令入口：

```powershell
# 只运行真实静态分析与输入预检，不调用模型、不创建配对
.venv/Scripts/python.exe -m backend.app.experiments.full_source_trial
# 明确授权后：3 个固定样本各 2 对，保存为新项目与新实验记录
.venv/Scripts/python.exe -m backend.app.experiments.full_source_trial --repeats 2 --run-model
```

`full_source_trial.py` 复用项目导入事务、Worker、审计、比较仓储及完整性检查，不另建模型接入循环。
两次重复平衡执行先后，左右顺序独立随机。研究核验清单位于样本根目录之外，绝不传入模型；
脚本不自动提交人工盲评分数。所有成功、失败和用量均保留在历史，汇总另存于 `backend/storage/experiments/`。
只适用于这类微型样本的探索性实验，不能据此推断全部小项目或大项目的普遍效果。

HTTP 入口位于 `api/experiment.py`，应用入口是 `ExperimentComparisonService.create(project_id, user_id, request)`。创建后，两个运行进入同一 Agent 持久化队列；前端通过比较 SSE 查看盲态结果，提交 `BlindReviewRequest` 后才能揭盲。

## 历史实验

`GET /api/experiments/projects/{project_id}/comparisons?offset=0&limit=20` 对项目所有权验证后分页返回全部试次（最多 50 条），包含失败/取消记录；摘要不加载报告、工具或完整审计。`GET /api/experiments/comparisons/{id}/history` 读取本人原结果、预算/模型条件、共同提示词与内容哈希、源码/产物/工具指纹及已保存评审。未评审仍盲态，已评审恢复原左右映射与评分；完整传输审计的本机管理员权限不变。

前端 `ExperimentHistory.vue` 在盲测页提供打开原结果、选两次试验并排阅读提示词/运行条件和报告。左右映射每次独立随机，未揭盲不能把跨试次左侧当作同一组；提示词哈希不同不证明仅提示词不同，须核对问题、模型、预算、源码/产物、工具、协议和重复试次。历史缺失信息明确未知，不用当前设置补填。新试次复用运行事件保存轻量 `experiment.snapshot`（只有脱敏共同指令及元数据），不新增数据库表；完整审计过期不删除这份新提示词快照。旧试次只读取尚存审计，过期后不能还原。项目删除仍统一清除这些记录。

## 按需静态证据与分批报告

`services/security_analysis/evidence_query.py` 拥有静态目录与查询，初始摘要直接复用 `evidence_selection.py` 的路径选择。上限 8000 UTF-8 字节，仅有三类有序路径/线索及最小元数据，无源码/孤立输入/条件/全局统计，不计为已读源码；短路径优先整条交付，长路径标记步骤未展开。`_coverage` 按需返回覆盖与完整局限。`search_static_security_evidence` 返回同一优先级的 ID、定位、筛选范围和快照绑定游标；`get_static_security_evidence` 按原 ID 分页提供 overview、flow、snippets、protections 或 unresolved，不抽样长路径。详情每页最多 24000 字符，过大明确拒绝而非切碎内容。保留阶段已丢弃内容不能按需恢复，源码反向切片未实现。

`agents/batching.py` 保存运行内选择与报告台账；已报告 ID 自动排除，可继续读取本批同一 ID 的其他组件。原始文件组按路径＋行范围记录，不能因为读过同一文件前半段而禁止后半段。`batch.completed` 在进入下一批前保存完整模型报告并通过 SSE 展示，失败/取消的未完批次由 `batch.pending` 标记，已有报告不删除。reported 只是已形成报告，不证明语义正确或完整审查。恢复用于查看；新运行不会继承旧运行的排除集合，本轮不新增断点续跑 API。

新批次重新开始原生消息链，只携带更新目录与带来源说明的报告台账；不能把不同小对话可能复用的调用 ID、孤立结果或加密推理项混拼。最终通过 `APPLICATION_BATCH_EVIDENCE` 交付此前实际展示的完整观察档案，最多 12000 字符并受公共请求预算约束；整条省略计数明确，引用检查仍核对实发范围，不证明语义。模型合并同根因，不简单拼接四段报告，不能提高历史报告的确认等级。全部实际请求、批次和失败成本保留，不保证按需一定省 Token。

```text
ComparisonRequest
  → ExperimentComparisonService
  ├─ security_evidence → 小目录 + 静态查询 + 原始文件工具 + 批次报告
  └─ baseline          → 相同原始文件工具 + 批次报告（临时对照）
  → AgentQueueWorker
  → collect_run_metrics()
  → BlindReviewRequest
  → reveal()
```

## 文件与核心类型

| 文件/类 | 作用 |
| --- | --- |
| `contracts.py` | `ComparisonRequest`、`LaneScores`、`BlindReviewRequest` 和公开错误。 |
| `service.py` / `ExperimentComparisonService` | 创建配对运行、随机化、查询、评分和揭盲。 |
| `repository.py` / `ExperimentRepository` | 比较记录、左右映射和评审结果的持久化边界。 |
| `context.py` | 共同安全指令、产物允许列表、`SecurityExperimentContextBuilder`，保证完整 JSON 预算。 |
| `tools.py` | 四个共同原始文件工具＋提交报告；实验组另注册两个静态查询工具，控制组禁止访问。 |
| `metrics.py` | 汇总时延、工具、真实供应商用量及其完整性、字符估算、终止原因和回答完整性。 |
| `report.py` | 当前协议标识、共同台账指令及生成报告的结构完整性检查。 |
| `findings.py` | `parse_ledger(text)` 验证根因/状态/六类覆盖，`FindingLedger.render()` 生成唯一计数与分区；不做语义判断。 |
| `cumulative.py` / `cumulative_trial.py` | 固定范围的累计字节配额、冻结选择与独立补充实验入口，默认不调用模型。 |
| `baseline/` | 带明显标记的临时原始文件对照组。 |

`ExperimentRepository.create_pair(record, request)` 在一个事务中创建两个运行、队列项和配对；Worker 按 strategy 构造隔离上下文。创建前两组都经过公共 `BudgetPlanner` 验证完整初始 JSON 和实际请求表示，避免只有对照组消耗模型。两组使用相同原生工具消息协议；真实 usage 与本地请求 Token 估算分别统计，保留估算方法和预算快照。窗口版本、输入/输出预算、分词策略或思考配置不一致，不进入正式配对评价；运行中预算改变同样拒绝。旧数据库 `graph_run_id` 为兼容列名，不表示新实验输入完整图；历史记录不回填。

旧运行可能使用 `static-security-v2` / `concise-security-v1`，不回填或修改原记录。供应商未返回 usage 时保留空值，不能用字符估算冒充真实用量；历史运行缺失终止元数据时标为未知。报告完整只说明正常终止且四段内容和结束标记存在，不等于漏洞已全部检出。

新请求预算版本为 `utf8-token-budget-v2`：输入按完整请求 JSON 的 UTF-8 字节估算，输出按可见文本和规范化工具调用内容的字节估算，不包含未公开的思考文本，不能代替供应商消耗。旧事件缺少输出字节数时继续标记为历史字符近似。失败或取消后的证据数也从上下文/工具/完成事件联合去重，不再因缺少最终完成事件显示为零；不改写原事件。

具体规则、指标含义、已知限制和删除清单见 `../../../docs/EXPERIMENT_PROTOCOL.md`，功能验证见 `../../../docs/FUNCTIONAL_VALIDATION.md`。若重复试验确认安全证据更优并结束对照实验，按清单移除临时对照代码；普通 Agent API 不直接依赖 baseline 包。
