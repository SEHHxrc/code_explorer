<!-- TEMPORARY CONTROL GROUP / 临时对照组：本盲测 UI 包含无图对照展示，实验结束可整体删除。 -->
<template>
  <el-card class="experiment-card">
    <template #header>
      <div class="header">
        <div>
          <span class="title">🧪 依赖图效果盲测</span>
          <span class="subtitle">同一问题、同一模型、同一步骤预算</span>
        </div>
        <el-tag v-if="comparison" :type="completed ? 'success' : 'primary'" size="small">
          {{ completed ? '等待盲评' : '两组运行中' }}
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

    <div class="setup">
      <el-input
        v-model="question"
        type="textarea"
        :rows="3"
        maxlength="8000"
        show-word-limit
        placeholder="输入用于比较的问题，例如：这个项目的 FastAPI 入口与请求链路是什么？"
        :disabled="running"
      />
      <div class="setup-actions">
        <span>展示顺序和执行顺序都会随机化；评分前不显示实验组身份。</span>
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
          {{ laneRun(lane)?.answer || laneRun(lane)?.error || (completed ? '没有返回答案' : '正在分析项目证据…') }}
        </div>
        <dl class="metrics">
          <div><dt>耗时</dt><dd>{{ metric(lane, 'duration_ms', ' ms') }}</dd></div>
          <div><dt>输入 Token</dt><dd>≈ {{ metric(lane, 'estimated_input_tokens') }}</dd></div>
          <div><dt>输出 Token</dt><dd>≈ {{ metric(lane, 'estimated_output_tokens') }}</dd></div>
          <div><dt>工具调用</dt><dd>{{ metric(lane, 'tool_calls') }}</dd></div>
          <div><dt>证据数量</dt><dd>{{ metric(lane, 'evidence_count') }}</dd></div>
        </dl>
        <el-tag v-if="reveal" class="reveal" effect="dark" :type="reveal[lane] === 'graph' ? 'success' : 'warning'">
          {{ strategyLabel(reveal[lane]) }}
        </el-tag>
      </section>
    </div>

    <div v-if="completed && !reveal" class="review">
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
import { ElMessage } from 'element-plus'
import { apiErrorMessage } from '../../services/httpClient.js'
import {
  createExperimentComparison,
  reviewExperimentComparison,
  streamExperimentComparison,
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
const preferredLane = ref('tie')
const notes = ref('')
const scores = reactive({
  left: { correctness: 3, completeness: 3, evidence: 3, hallucination_control: 3 },
  right: { correctness: 3, completeness: 3, evidence: 3, hallucination_control: 3 },
})
let controller = null

const completed = computed(() => comparison.value?.status === 'completed')
const laneRun = (lane) => comparison.value?.lanes?.[lane]?.run
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
 * baseline 标签只为实验揭盲展示。确认图增强胜出后，随比较 UI 一起删除。
 */
const strategyLabel = (strategy) => (
  strategy === 'graph' ? '图增强组' : '临时无图对照组'
)

const reset = () => {
  controller?.abort()
  controller = null
  comparison.value = null
  reveal.value = null
  running.value = false
}
watch(() => props.projectId, reset)

const start = async () => {
  const text = question.value.trim()
  if (!text || running.value) return
  reset()
  running.value = true
  controller = new AbortController()
  try {
    comparison.value = await createExperimentComparison(props.projectId, { question: text, max_steps: 4 })
    await streamExperimentComparison(comparison.value.events_url, (snapshot) => {
      comparison.value = snapshot
    }, controller.signal)
  } catch (error) {
    if (error.name !== 'AbortError') ElMessage.error(apiErrorMessage(error, '无法启动配对实验'))
  } finally {
    running.value = false
    controller = null
  }
}

const submitReview = async () => {
  if (!comparison.value || reviewing.value) return
  reviewing.value = true
  try {
    const result = await reviewExperimentComparison(comparison.value.id, {
      preferred_lane: preferredLane.value,
      left: { ...scores.left },
      right: { ...scores.right },
      notes: notes.value,
    })
    reveal.value = result.reveal
    ElMessage.success('评分已保存，实验组身份已揭示。')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '保存盲评失败'))
  } finally {
    reviewing.value = false
  }
}

onUnmounted(() => controller?.abort())
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
