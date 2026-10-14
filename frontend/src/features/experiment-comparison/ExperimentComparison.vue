<!-- TEMPORARY CONTROL GROUP / 临时对照组：本盲测 UI 包含仅原始文件的对照展示，实验结束可整体删除。 -->
<template>
  <el-card class="experiment-card">
    <template #header>
      <div class="header">
        <div>
          <span class="title">🧪 静态安全证据效果盲测</span>
          <span class="subtitle">同一问题、同一模型、同一步骤预算</span>
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
        <span>展示和入队顺序均已随机化；两组指令、模型、原始文件工具和步骤预算相同。</span>
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
        <dl class="metrics">
          <div><dt>耗时</dt><dd>{{ metric(lane, 'duration_ms', ' ms') }}</dd></div>
          <div><dt>供应商输入 Token</dt><dd>{{ metric(lane, 'actual_input_tokens') }}</dd></div>
          <div><dt>供应商输出 Token</dt><dd>{{ metric(lane, 'actual_output_tokens') }}</dd></div>
          <div><dt>供应商总 Token</dt><dd>{{ metric(lane, 'actual_total_tokens') }}</dd></div>
          <div><dt>其中推理 Token</dt><dd>{{ metric(lane, 'reasoning_tokens') }}</dd></div>
          <div><dt>用量记录</dt><dd>{{ usageLabel(lane) }}</dd></div>
          <div><dt>供应商终止原因</dt><dd>{{ finishReasons(lane) }}</dd></div>
          <div><dt>回答完整性</dt><dd>{{ completenessLabel(lane) }}</dd></div>
          <div><dt>调查轮次上限</dt><dd>{{ laneMetrics(lane).execution_policy?.max_tool_rounds ?? '—' }}</dd></div>
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
            <el-tooltip content="包含初始静态端点和工具结果，按 path、line、symbol 去重；不等于漏洞数量、模型采纳量或答案正确性">
              <dt>去重证据条目</dt>
            </el-tooltip>
            <dd>{{ metric(lane, 'evidence_count') }}</dd>
          </div>
        </dl>
        <p class="audit-note">审计：{{ auditLabel(lane) }}。引用检查仅做机械核对，不代表结论正确。</p>
        <p v-if="laneMetrics(lane).failure_diagnosis" class="audit-note">当前诊断：{{ laneMetrics(lane).failure_diagnosis.message }}</p>
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

    <div v-if="completed && comparison.valid_for_review && !reveal" class="review">
      <h4>揭盲前评分</h4>
      <div class="score-grid">
        <div />
        <strong>正确性</strong><strong>完整性</strong><strong>证据充分</strong><strong>幻觉控制</strong>
        <template v-for="lane in laneNames" :key="'scores-' + lane">
          <strong>{{ lane === 'left' ? '左侧' : '右侧' }}</strong>
          <el-rate v-model="scores[lane].correctness" />
          <el-rate v-model="scores[lane].completeness" />
          <el-rate v-model="scores[lane].evidence" />
          <el-rate v-model="scores[lane].hallucination_control" />
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
import { historyConditions } from './history.js'
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
const comparison = ref(null)
const reveal = ref(null)
const running = ref(false)
const reviewing = ref(false)
const auditVisible = ref(false)
const auditText = ref('')
const preferredLane = ref('tie')
const notes = ref('')
const scores = reactive({
  left: { correctness: 3, completeness: 3, evidence: 3, hallucination_control: 3 },
  right: { correctness: 3, completeness: 3, evidence: 3, hallucination_control: 3 },
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
const reportModeLabel = (lane) => ({ tools_none: '保留定义，禁用新工具', history_no_tools: '无工具定义，保留有效历史' }[laneMetrics(lane).execution_policy?.final_report_mode] || '历史未记录')
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
  for (const lane of laneNames) Object.assign(scores[lane], { correctness: 3, completeness: 3, evidence: 3, hallucination_control: 3 })
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
    reveal.value = result.reveal
    if (result.review) {
      preferredLane.value = result.review.preferred_lane
      notes.value = result.review.notes || ''
      for (const lane of laneNames) Object.assign(scores[lane], result.review.scores[lane])
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
    const result = await createExperimentComparison(projectId, { question: text, max_steps: 4 })
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
  reviewing.value = true
  const ticket = generation
  try {
    const result = await reviewExperimentComparison(comparison.value.id, {
      preferred_lane: preferredLane.value,
      left: { ...scores.left },
      right: { ...scores.right },
      notes: notes.value,
    })
    if (ticket !== generation) return
    reveal.value = result.reveal
    comparison.value.reviewed = true
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
.score-grid { display: grid; grid-template-columns: 80px repeat(4, minmax(130px, 1fr)); align-items: center; gap: 9px; overflow-x: auto; }
.review-actions { margin-top: 14px; }
.review-actions .el-input { flex: 1; }
@media (max-width: 1000px) {
  .lanes { grid-template-columns: 1fr; }
  .metrics { grid-template-columns: repeat(3, 1fr); }
  .review-actions { align-items: stretch; flex-direction: column; }
}
</style>
