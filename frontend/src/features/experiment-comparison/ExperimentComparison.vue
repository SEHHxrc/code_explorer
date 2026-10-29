<!-- TEMPORARY CONTROL GROUP / 临时对照组：本盲测 UI 包含仅原始文件的对照展示，实验结束可整体删除。 -->
<template>
  <el-card class="experiment-card">
    <template #header>
      <div class="header">
        <div>
          <span class="title">🧪 静态安全证据效果盲测</span>
          <span class="subtitle">同一问题、同一模型、相同调查及报告预算规则</span>
        </div>
        <el-tag v-if="comparison" :type="completed ? 'success' : 'primary'" size="small">
          {{ completed ? (comparison.reviewed ? '已保存盲评' : comparison.valid_for_review ? '等待盲评' : '运行失败或回答完整性未通过') : '两组运行中' }}
        </el-tag>
      </div>
    </template>

    <el-alert
      v-if="!modelStatus.configured"
      title="请先配置在线 API 或本地模型，盲测不会使用静态回退结果。"
      type="info"
      :closable="false"
      show-icon
    />

    <el-alert v-if="comparison?.quality_warnings?.length" :title="comparison.quality_warnings.join(' ')"
      type="warning" :closable="false" show-icon />
    <el-alert v-if="['static-security-v8-full-source', 'static-security-v13-full-source-paths', 'static-security-v16-full-source-ledger', 'static-security-v23-full-source-boundaries'].includes(comparison?.protocol)" type="info" :closable="false"
      title="完整源码补充实验：两组初始包含相同完整范围源码，零工具、单次报告，仅静态事实不同；不与按需分批试次混合统计。" />
    <el-alert v-if="comparison?.protocol === 'static-security-v9-cumulative-evidence'" type="info" :closable="false"
      title="累计证据预算实验：两组使用相同原始文件工具和累计字节配额；静态事实也占用配额。理论可读完与实际已展示不同，不与其他协议合并统计。" />
    <el-alert v-if="comparison?.protocol === 'static-security-v10-navigation'" type="info" :closable="false"
      title="静态导航选择实验：沿用累计配额、共同工具和报告规则，仅替换静态选择/表达；未连接的危险操作不是安全结论，不与旧事实包试次合并统计。" />
    <el-alert v-if="['static-security-v11-path-summary', 'static-security-v12-cumulative-paths'].includes(comparison?.protocol)" type="info" :closable="false"
      title="路径摘要协议：初始只提供传播路径、结构线索和操作线索；优先级是核验顺序而非漏洞概率。覆盖与局限按需读取，源码反向切片未启用；不与旧摘要试次混算。" />
    <el-alert v-if="['static-security-v14-review-ledger', 'static-security-v15-cumulative-ledger', 'static-security-v16-full-source-ledger'].includes(comparison?.protocol)" type="info" :closable="false"
      title="发现台账协议：两组共用六类覆盖核对，摘要计数和报告分区由台账状态生成；机械一致不代表结论正确。导航、轮次和预算未因此扩大，不与旧报告协议混算。" />

    <el-alert v-if="['static-security-v17-path-families', 'static-security-v18-source-delivery', 'static-security-v19-unbounded-batch-report', 'static-security-v20-isolated-report-stages', 'static-security-v21-chain-boundaries'].includes(comparison?.protocol)" type="info" :closable="false"
      title="路径族协议：先浏览同 Source/Sink 的路径族，再选择具体路线；查看族不表示已核验全部路线。两组默认 12 轮、上限 36 轮；第三方知识 Agent 单独调用，不混入本次静态输入。" />
    <el-alert v-if="comparison?.protocol === 'static-security-v18-source-delivery'" type="info" :closable="false"
      title="交付记录协议：工具返回、HTTP 发送尝试与模型请求/显式 ID 引用分开记录，均不等于模型实际使用或语义核实。每批台账及生成正文各限 16000 字符；无路径、告警或诊断不证明安全。" />
    <el-alert v-if="comparison?.protocol === 'static-security-v19-unbounded-batch-report'" type="info" :closable="false"
      title="批次报告无总字符上限：取消台账 JSON 和生成正文的 16000 字符限制，保留完整性、结构与状态校验，以及传输和审计安全配额。调查轮次、报告模式与模型配置不变；与旧限制条件分开统计。" />
    <el-alert v-if="['static-security-v20-isolated-report-stages', 'static-security-v21-chain-boundaries'].includes(comparison?.protocol)" type="info" :closable="false"
      title="阶段隔离协议：调查预算不扣批次报告；每批独立一次报告请求，最终另有一次汇总，格式重提交仍受共同额度限制。报告只提供提交工具，汇总无工具；历史调查调用转为带来源的证据，不混入旧调用或续接状态。无报告总字符上限，不与旧条件混算。" />

    <el-alert v-if="['static-security-v21-chain-boundaries', 'static-security-v22-cumulative-boundaries', 'static-security-v23-full-source-boundaries'].includes(comparison?.protocol)" type="info" :closable="false"
      title="台账 v2：明确路径起终点及局部/外部闭合范围，传播断点与部署条件分开。人工独立评价发现广度、链路核验和结论可信度；历史批次引用可追溯，不因最终请求省略就判为幻觉。与旧契约分开统计。" />

    <ExperimentHistory :project-id="projectId" :refresh-key="historyRefresh" @open="openHistory" />

    <div class="setup">
      <el-input
        v-model="question"
        type="textarea"
        :rows="3"
        maxlength="8000"
        show-word-limit
        placeholder="输入安全分析问题，例如：是否存在外部输入到命令执行或 HTML 注入的可利用路径？"
        :disabled="running"
      />
      <div class="setup-actions">
        <span>两组调查预算相同；批次报告与最终汇总另计，已报告证据不重复选择。</span>
        <el-input-number v-model="maxSteps" :min="1" :max="36" :disabled="running" aria-label="两组共同调查轮次" />
        <el-button
          type="primary"
          :loading="running"
          :disabled="!question.trim() || !projectId || !modelStatus.configured"
          @click="start"
        >
          开始配对实验
        </el-button>
      </div>
    </div>

    <div v-if="comparison" class="lanes">
      <section v-for="lane in laneNames" :key="lane" class="lane">
        <div class="lane-heading">
          <strong>{{ lane === 'left' ? '左侧答案' : '右侧答案' }}</strong>
          <el-tag size="small" :type="runTag(laneRun(lane)?.status)">
            {{ runStatus(laneRun(lane)?.status) }}
          </el-tag>
        </div>
        <div class="answer">
          {{ displayAnswer(laneRun(lane)?.answer) || laneRun(lane)?.error || (completed ? '没有返回答案' : '正在分析项目证据…') }}
        </div>
        <el-collapse v-if="laneMetrics(lane).batch_reports?.length" accordion
          :model-value="laneMetrics(lane).batch_reports.at(-1).batch_id">
          <el-collapse-item v-for="batch in laneMetrics(lane).batch_reports" :key="batch.batch_id"
            :name="batch.batch_id" :title="`第 ${batch.batch_id} 批报告（模型判断，非语义验证）`">
            <div class="answer">{{ displayAnswer(batch.report) }}</div>
          </el-collapse-item>
        </el-collapse>
        <el-alert v-if="laneMetrics(lane).pending_batch" type="warning" :closable="false"
          :title="`第 ${laneMetrics(lane).pending_batch.batch_id} 批尚未输出完整报告；此前批次仍保留。`" />
        <dl class="metrics">
          <div><dt>墙钟耗时</dt><dd>{{ metric(lane, 'duration_ms', ' ms') }}</dd></div>
          <div><dt>剔除前置拒绝后耗时</dt><dd>{{ metric(lane, 'adjusted_duration_ms', ' ms') }}</dd></div>
          <div><dt>已剔除拒绝/退避</dt><dd>{{ metric(lane, 'excluded_network_ms', ' ms') }}</dd></div>
          <div><dt>供应商输入 Token</dt><dd>{{ metric(lane, 'actual_input_tokens') }}</dd></div>
          <div><dt>供应商输出 Token</dt><dd>{{ metric(lane, 'actual_output_tokens') }}</dd></div>
          <div><dt>供应商总 Token</dt><dd>{{ metric(lane, 'actual_total_tokens') }}</dd></div>
          <div><dt>按冻结口径核算模型总 Token</dt><dd>{{ metric(lane, 'accounted_model_total_tokens') }}</dd></div>
          <div><dt>其中推理 Token</dt><dd>{{ metric(lane, 'reasoning_tokens') }}</dd></div>
          <div><dt>用量记录</dt><dd>{{ usageLabel(lane) }}</dd></div>
          <div><dt>供应商终止原因</dt><dd>{{ finishReasons(lane) }}</dd></div>
          <div><dt>回答完整性</dt><dd>{{ completenessLabel(lane) }}</dd></div>
          <div><dt>调查轮次上限</dt><dd>{{ laneMetrics(lane).execution_policy?.max_tool_rounds ?? '—' }}</dd></div>
          <template v-if="laneMetrics(lane).execution_policy?.investigation_budget_excludes_reports">
            <div><dt>调查请求（应用发起）</dt><dd>{{ laneMetrics(lane).phase_requests?.investigation ?? 0 }}</dd></div>
            <div><dt>批次报告请求（另计）</dt><dd>{{ laneMetrics(lane).phase_requests?.batch_report ?? 0 }}</dd></div>
            <div><dt>最终汇总请求（另计）</dt><dd>{{ laneMetrics(lane).phase_requests?.final_report ?? 0 }}</dd></div>
            <div><dt>台账重提交请求（另计）</dt><dd>{{ laneMetrics(lane).phase_requests?.report_format_retry ?? 0 }}</dd></div>
          </template>
          <div><dt>最终报告模式</dt><dd>{{ reportModeLabel(lane) }}</dd></div>
          <div>
            <el-tooltip content="所有请求中最大的单次完整请求估算；下方累计值是多轮之和，不是单次上下文大小">
              <dt>最大单次输入估算</dt>
            </el-tooltip>
            <dd>≈ {{ metric(lane, 'max_request_input_estimate') }}</dd>
          </div>
          <div>
            <el-tooltip content="新记录按每轮完整请求 JSON 的 UTF-8 字节保守估算，包含指令、工具定义和会话；旧记录沿用其历史估算方式，不是供应商真实 usage">
              <dt>累计输入估算</dt>
            </el-tooltip>
            <dd>≈ {{ metric(lane, 'estimated_input_tokens') }}</dd>
          </div>
          <div>
            <el-tooltip content="新记录按可见响应和规范化工具调用内容的 UTF-8 字节保守估算；旧记录缺少字节数时采用字符数 ÷ 4。不包含隐藏推理，不是供应商真实 usage">
              <dt>累计输出估算</dt>
            </el-tooltip>
            <dd>≈ {{ metric(lane, 'estimated_output_tokens') }}</dd>
          </div>
          <div><dt>工具调用</dt><dd>{{ metric(lane, 'tool_calls') }}</dd></div>
          <div>
            <el-tooltip content="仅对显式证据元数据按 path、line、symbol 去重；完整源码条件按固定范围与实发审计核对，0 不代表未提供源码。该计数不等于漏洞数量或答案正确性">
              <dt>去重证据条目</dt>
            </el-tooltip>
            <dd>{{ metric(lane, 'evidence_count') }}</dd>
          </div>
        </dl>
        <p class="audit-note">审计：{{ auditLabel(lane) }}。引用检查仅做机械核对，不代表结论正确。</p>
        <p v-if="laneMetrics(lane).report_format?.rejections?.length" class="audit-note">
          曾发生台账格式或状态一致性拒绝；重提交 {{ laneMetrics(lane).report_format.retries }} 次。
          原始回复及全部请求用量保留，未在本地修补 JSON 或自动改写状态；重提交不代表语义核验通过。
        </p>
        <ReportConflictDiagnostic v-if="comparison" :key="`${comparison.id}:${lane}`" :comparison-id="comparison.id" :lane="lane"
          :available="laneMetrics(lane).report_diagnostic?.available" :snapshot="laneMetrics(lane).report_diagnostic?.record"
          :model-configured="modelStatus.configured" />
        <p v-if="laneMetrics(lane).assumed_zero_token_attempts" class="audit-note">
          按本次冻结的当前端点声明，{{ laneMetrics(lane).assumed_zero_token_attempts }} 次 429/503 为执行前拒绝，假设模型 Token 为 0；这不是供应商返回的 usage，也不代表所有端点如此。未知成本尝试：{{ metric(lane, 'unknown_cost_http_attempts') }}。
        </p>
        <details v-if="laneMetrics(lane).findings_ledger" class="audit-note">
          <summary>发现台账与覆盖状态（模型判断，非语义验证）</summary>
          <pre class="audit-body">{{ JSON.stringify(laneMetrics(lane).findings_ledger.ledger, null, 2) }}</pre>
        </details>
        <p v-if="laneMetrics(lane).failure_diagnosis" class="audit-note">当前诊断：{{ laneMetrics(lane).failure_diagnosis.message }}</p>
        <details v-if="laneMetrics(lane).citation_check?.notices?.length" class="audit-note">
          <summary>历史证据来源 {{ laneMetrics(lane).citation_check.notices.length }} 项：此前已展示，最终请求省略；不作为未展示警告或幻觉判定</summary>
          <ul><li v-for="(notice, index) in laneMetrics(lane).citation_check.notices" :key="index">
            {{ notice.path }}:{{ notice.start_line }}–{{ notice.end_line }}；请求记录 {{ notice.request_ids?.join(', ') || '旧记录未关联' }}。需回查对应批次，不代表引用支持断言。
          </li></ul>
        </details>
        <ul v-if="laneMetrics(lane).citation_check?.warnings?.length" class="citation-warnings">
          <li v-for="(warning, index) in laneMetrics(lane).citation_check.warnings" :key="index">
            {{ warning.path }}<template v-if="warning.start_line != null">:{{ warning.start_line }}–{{ warning.end_line }}</template>：{{ citationReason(warning.reason) }}<template v-if="warning.resolved_path">（核对提示：{{ warning.resolved_path }}，未改写原文）</template>
          </li>
        </ul>
        <el-button v-if="reveal || (completed && !comparison.valid_for_review)" size="small" plain @click="inspectAudit(lane)">
          查看脱敏审计（管理员）
        </el-button>
        <el-tag v-if="reveal" class="reveal" effect="dark" :type="reveal[lane] === 'security_evidence' ? 'success' : 'warning'">
          {{ strategyLabel(reveal[lane]) }}
        </el-tag>
      </section>
    </div>
    <el-dialog v-model="auditVisible" title="实验审计：仅可见内容，非语义验证" width="85%" @closed="auditText = ''">
      <pre class="audit-body">{{ auditText }}</pre>
    </el-dialog>
    <details v-if="comparison?.lanes?.left?.prompt_snapshot" class="audit-note">
      <summary>历史提示词与运行条件（原快照，不是当前设置）</summary>
      <section v-for="lane in laneNames" :key="lane">
        <strong>{{ lane === 'left' ? '左侧' : '右侧' }}</strong>
        <pre class="audit-body">{{ comparison.lanes[lane].prompt_snapshot?.instructions || '提示词未记录或审计已过期。' }}</pre>
        <pre class="audit-body">{{ JSON.stringify(historyConditions(comparison.lanes[lane]), null, 2) }}</pre>
      </section>
    </details>

    <BlindReviewResult :review="comparison?.review" />
    <div v-if="completed && comparison.valid_for_review && !reveal" class="review">
      <h4>揭盲前评分</h4>
      <p>发现广度：核实的独立问题与有效调查面；链路核验：明确端点、连接和对应防护；结论可信度：断言与证据、未知条件相符。三项独立评分，不用条目数量或闭合数量代替核验。</p>
      <div class="score-grid">
        <div />
        <strong v-for="label in reviewDimensions" :key="label">{{ label }}</strong>
        <template v-for="lane in laneNames" :key="'scores-' + lane">
          <strong>{{ lane === 'left' ? '左侧' : '右侧' }}</strong>
          <el-rate v-for="(_, key) in reviewDimensions" :key="key" v-model="scores[lane][key]" />
        </template>
      </div>
      <div class="review-actions">
        <el-radio-group v-model="preferredLane">
          <el-radio-button value="left">左侧更好</el-radio-button>
          <el-radio-button value="tie">相当</el-radio-button>
          <el-radio-button value="right">右侧更好</el-radio-button>
        </el-radio-group>
        <el-input v-model="notes" placeholder="可选：记录判断依据" maxlength="4000" />
        <el-button type="success" :loading="reviewing" @click="submitReview">提交评分并揭盲</el-button>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiErrorMessage } from '../../services/httpClient.js'
import ExperimentHistory from './ExperimentHistory.vue'
import BlindReviewResult from './BlindReviewResult.vue'
import ReportConflictDiagnostic from './ReportConflictDiagnostic.vue'
import { historyConditions } from './history.js'
import { REVIEW_RUBRIC_VERSION, reviewDimensions, emptyReviewScores } from './reviewRubric.js'
import {
  createExperimentComparison,
  reviewExperimentComparison,
  streamExperimentComparison,
  readExperimentAudit,
  readExperimentHistory,
} from '../../services/experimentApi.js'

const props = defineProps({
  projectId: { type: String, default: '' },
  modelStatus: { type: Object, default: () => ({ configured: false }) },
})

const laneNames = ['left', 'right']
const question = ref('')
const maxSteps = ref(12)
const comparison = ref(null)
const reveal = ref(null)
const running = ref(false)
const reviewing = ref(false)
const auditVisible = ref(false)
const auditText = ref('')
const preferredLane = ref('tie')
const notes = ref('')
const scores = reactive({
  left: emptyReviewScores(),
  right: emptyReviewScores(),
})
let controller = null
let generation = 0
const historyRefresh = ref(0)

const completed = computed(() => comparison.value?.status === 'completed')
const laneRun = (lane) => comparison.value?.lanes?.[lane]?.run
const displayAnswer = (answer) => (answer || '').replace(/<!-- SECURITY_REPORT_END -->\s*$/, '')
const laneMetrics = (lane) => comparison.value?.lanes?.[lane]?.metrics || {}
const usageLabel = (lane) => ({ complete: '全部请求已报告', partial: '仅部分请求/字段，非完整总量', unavailable: '供应商未报告或历史未记录' }[laneMetrics(lane).usage_status] || '未知')
const completenessLabel = (lane) => ({ complete: '生成及格式完整（非覆盖完整）', incomplete: '未通过', unknown: '未知，不能认定完整' }[laneMetrics(lane).answer_completeness?.status] || '未知')
const finishReasons = (lane) => laneMetrics(lane).finish_reasons?.join(' / ') || '未记录'
const reportModeLabel = (lane) => ({ tools_none: '保留定义，禁用新工具', history_no_tools: '无工具定义，保留有效历史', isolated_text_no_tools: '隔离汇总，无工具；批次仅报告提交', single_shot_no_tools: '完整源码，零工具单次报告' }[laneMetrics(lane).execution_policy?.final_report_mode] || '历史未记录')
const auditLabel = (lane) => ({ complete: '可见传输记录已保存', incomplete: '审计不完整', expired: '正文已按保留策略过期清理', reserved: '已预留，等待运行', recording: '正在记录', unavailable: '历史未记录' }[laneMetrics(lane).audit?.status] || '历史未记录')
const citationReason = (reason) => ({ path_outside_snapshot: '路径不在允许范围', path_not_in_snapshot: '快照中无此文件',
  citation_syntax_invalid: '引用格式不规范，未认定为虚构证据', path_abbreviation_resolved: '路径简称可唯一对应，但不符合完整路径规范',
  path_ambiguous: '同名/简称对应多个文件，不能自动猜测',
  invalid_line_range: '行范围无效', line_count_unavailable: '无法核对行数', line_out_of_range: '行号超出文件',
  source_not_shown: '未确认对应源码已展示', seen_earlier_not_in_final_request: '曾展示，但最终请求中已省略' }[reason] || reason)

const inspectAudit = async (lane) => {
  const comparisonId = comparison.value?.id
  if (!comparisonId) return
  try {
    const { value } = await ElMessageBox.prompt('完整审计可能暴露组别。请输入本机管理员审计口令；口令仅用于本次请求，不会保存。', '显式读取审计', { inputType: 'password' })
    if (comparison.value?.id !== comparisonId) return
    const result = await readExperimentAudit(comparisonId, lane, value, !reveal.value)
    if (comparison.value?.id !== comparisonId) return
    auditText.value = JSON.stringify(result, null, 2)
    auditVisible.value = true
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(apiErrorMessage(error, '无法读取审计'))
  }
}
const metric = (lane, name, suffix = '') => {
  const value = comparison.value?.lanes?.[lane]?.metrics?.[name]
  return value === null || value === undefined ? '—' : String(value) + suffix
}
const runStatus = (status) => ({
  queued: '排队中', running: '分析中', completed: '已完成', failed: '失败', cancelled: '已取消',
}[status] || '等待中')
const runTag = (status) => ({
  completed: 'success', failed: 'danger', cancelled: 'warning', running: 'primary',
}[status] || 'info')

/**
 * TEMPORARY CONTROL GROUP / 临时对照组：
 * baseline 标签只为实验揭盲展示。确认安全证据增强更优并结束实验后清理。
 */
const strategyLabel = (strategy) => (
  ({ security_evidence: '静态安全证据组', graph: '旧版图实验（不能与新实验混合）', baseline: '临时原始文件对照组' }[strategy] || strategy)
)

const reset = () => {
  generation++
  controller?.abort()
  controller = null
  comparison.value = null
  reveal.value = null
  running.value = false
  auditVisible.value = false
  auditText.value = ''
  reviewing.value = false
  preferredLane.value = 'tie'
  notes.value = ''
  for (const lane of laneNames) Object.assign(scores[lane], emptyReviewScores())
}
watch(() => props.projectId, reset)

const openHistory = async (comparisonId) => {
  reset()
  const ticket = generation
  const stream = new AbortController()
  controller = stream
  running.value = true
  try {
    const result = await readExperimentHistory(comparisonId)
    if (ticket !== generation) return
    comparison.value = result
    question.value = result.question
    const recordedSteps = Object.values(result.lanes || {}).map((lane) => lane.metrics?.execution_policy?.max_tool_rounds)
    if (recordedSteps.length === 2 && Number.isInteger(recordedSteps[0]) && recordedSteps[0] >= 1 && recordedSteps[0] === recordedSteps[1]) maxSteps.value = recordedSteps[0]
    reveal.value = result.reveal
    if (result.review) {
      preferredLane.value = result.review.preferred_lane
      notes.value = result.review.notes || ''
    }
    if (result.status !== 'completed') await streamExperimentComparison(result.events_url, (snapshot) => {
      if (ticket === generation) comparison.value = snapshot
    }, stream.signal)
  } catch (error) {
    if (ticket === generation && error.name !== 'AbortError') ElMessage.error(apiErrorMessage(error, '读取历史实验失败'))
  } finally {
    if (ticket === generation) { running.value = false; controller = null; historyRefresh.value++ }
  }
}

const start = async () => {
  const text = question.value.trim()
  if (!text || running.value) return
  reset()
  const ticket = generation
  const projectId = props.projectId
  running.value = true
  const stream = new AbortController()
  controller = stream
  try {
    const result = await createExperimentComparison(projectId, { question: text, max_steps: maxSteps.value })
    if (ticket !== generation) return
    comparison.value = result
    historyRefresh.value++
    await streamExperimentComparison(comparison.value.events_url, (snapshot) => {
      if (ticket === generation) comparison.value = snapshot
    }, stream.signal)
  } catch (error) {
    if (ticket === generation && error.name !== 'AbortError') ElMessage.error(apiErrorMessage(error, '无法启动配对实验'))
  } finally {
    if (ticket === generation) { running.value = false; controller = null; historyRefresh.value++ }
  }
}

const submitReview = async () => {
  if (!comparison.value || reviewing.value) return
  if (laneNames.some((lane) => Object.values(scores[lane]).some((value) => value < 1 || value > 5))) {
    ElMessage.warning('请分别完成两组的三项评分。')
    return
  }
  reviewing.value = true
  const ticket = generation
  try {
    const result = await reviewExperimentComparison(comparison.value.id, {
      preferred_lane: preferredLane.value,
      rubric_version: REVIEW_RUBRIC_VERSION,
      left: { ...scores.left },
      right: { ...scores.right },
      notes: notes.value,
    })
    if (ticket !== generation) return
    reveal.value = result.reveal
    comparison.value.reviewed = true
    comparison.value.review = { preferred_lane: preferredLane.value,
      scores: { rubric_version: REVIEW_RUBRIC_VERSION, left: { ...scores.left }, right: { ...scores.right } }, notes: notes.value }
    historyRefresh.value++
    ElMessage.success('评分已保存，实验组身份已揭示。')
  } catch (error) {
    if (ticket === generation) ElMessage.error(apiErrorMessage(error, '保存盲评失败'))
  } finally {
    if (ticket === generation) reviewing.value = false
  }
}

onUnmounted(reset)
</script>

<style scoped>
.experiment-card { min-height: 260px; }
.header, .setup-actions, .lane-heading, .review-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.title { font-weight: 600; }
.subtitle { margin-left: 10px; color: #909399; font-size: 12px; }
.setup { display: flex; flex-direction: column; gap: 10px; }
.setup-actions { color: #909399; font-size: 12px; }
.lanes { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin-top: 16px; }
.lane { position: relative; min-width: 0; padding: 14px; border: 1px solid #dcdfe6; border-radius: 8px; background: #fafafa; }
.answer { min-height: 180px; max-height: 430px; overflow: auto; margin: 12px 0; padding: 12px; border-radius: 6px; background: #fff; white-space: pre-wrap; line-height: 1.65; word-break: break-word; }
.metrics { display: grid; grid-template-columns: repeat(5, 1fr); gap: 6px; margin: 0; }
.metrics div { padding: 7px; border-radius: 5px; background: #fff; text-align: center; }
.metrics dt { color: #909399; font-size: 10px; }
.metrics dd { margin: 3px 0 0; color: #303133; font-size: 12px; }
.reveal { margin-top: 12px; }
.audit-note, .citation-warnings { font-size: 12px; color: #909399; overflow-wrap: anywhere; }
.audit-body { max-height: 65vh; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; }
.review { margin-top: 18px; padding-top: 14px; border-top: 1px solid #ebeef5; }
.review h4 { margin: 0 0 12px; }
.score-grid { display: grid; grid-template-columns: 80px repeat(3, minmax(130px, 1fr)); align-items: center; gap: 9px; overflow-x: auto; }
.review-actions { margin-top: 14px; }
.review-actions .el-input { flex: 1; }
@media (max-width: 1000px) {
  .lanes { grid-template-columns: 1fr; }
  .metrics { grid-template-columns: repeat(3, 1fr); }
  .review-actions { align-items: stretch; flex-direction: column; }
}
</style>
