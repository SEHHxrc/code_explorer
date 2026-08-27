<template>
  <el-card class="agent-card">
    <template #header>
      <div class="agent-header">
        <div>
          <span class="agent-title">🧠 项目智能体</span>
          <span class="agent-subtitle">只读分析模式</span>
        </div>
        <div class="agent-status">
          <el-tag size="small" :type="modelStatus.configured ? 'success' : 'info'">
            {{ modelStatus.configured ? `${modelStatus.provider} / ${modelStatus.model}` : '静态回退' }}
          </el-tag>
          <el-tag v-if="status !== 'idle'" size="small" :type="statusTagType">{{ statusLabel }}</el-tag>
        </div>
      </div>
    </template>

    <div class="agent-layout">
      <section class="conversation-panel">
        <div ref="conversationRef" class="conversation">
          <el-empty v-if="!question && !answer" description="询问项目架构、入口点、符号或调用关系" />
          <div v-if="question" class="message user-message">
            <div class="message-role">用户</div>
            <div>{{ question }}</div>
          </div>
          <div v-if="answer || running" class="message assistant-message">
            <div class="message-role">智能体</div>
            <div v-if="answer" class="answer-text">{{ answer }}</div>
            <div v-else class="thinking"><span class="pulse-dot" />正在分析项目证据…</div>
          </div>
          <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
        </div>

        <div class="prompt-box">
          <el-input
              v-model="draft"
              type="textarea"
              :rows="3"
              maxlength="8000"
              show-word-limit
              placeholder="例如：FastAPI 的入口在哪里？请求经过哪些模块？"
              :disabled="running || !projectId"
              @keydown.ctrl.enter.prevent="submit"
          />
          <div class="prompt-actions">
            <span class="prompt-hint">Ctrl + Enter 发送 · 模型只能调用只读工具</span>
            <el-button v-if="running" type="danger" plain @click="cancel">停止</el-button>
            <el-button v-else type="primary" :disabled="!draft.trim() || !projectId" @click="submit">分析</el-button>
          </div>
        </div>
      </section>

      <aside class="trace-panel">
        <el-tabs v-model="activeTab">
          <el-tab-pane label="运行步骤" name="steps">
            <el-timeline v-if="timeline.length" class="tool-timeline">
              <el-timeline-item
                  v-for="item in timeline"
                  :key="item.key"
                  :type="item.type"
                  :timestamp="item.timestamp"
              >
                <div class="timeline-title">{{ item.title }}</div>
                <pre v-if="item.detail" class="timeline-detail">{{ item.detail }}</pre>
              </el-timeline-item>
            </el-timeline>
            <el-empty v-else :image-size="60" description="尚无运行步骤" />
          </el-tab-pane>
          <el-tab-pane :label="`证据 (${evidence.length})`" name="evidence">
            <div v-if="evidence.length" class="evidence-list">
              <div v-for="(item, index) in evidence" :key="`${item.path}:${item.line}:${index}`" class="evidence-row">
                <code>{{ item.path }}<template v-if="item.line">:{{ item.line }}</template></code>
                <span v-if="item.symbol">{{ item.symbol }}</span>
                <small>{{ item.detail }}</small>
              </div>
            </div>
            <el-empty v-else :image-size="60" description="工具调用后将在这里显示证据" />
          </el-tab-pane>
        </el-tabs>
      </aside>
    </div>
  </el-card>
</template>

<script setup>
import { computed, nextTick, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { cancelAgentRun, createAgentRun, streamAgentEvents } from '../services/agentApi'

const props = defineProps({
  projectId: { type: String, default: '' },
  modelStatus: { type: Object, default: () => ({ configured: false }) },
})

const draft = ref('')
const question = ref('')
const answer = ref('')
const error = ref('')
const status = ref('idle')
const runId = ref('')
const timeline = ref([])
const evidence = ref([])
const activeTab = ref('steps')
const conversationRef = ref(null)
let controller = null

const running = computed(() => ['queued', 'running'].includes(status.value))
const statusLabel = computed(() => ({
  queued: '排队中', running: '分析中', completed: '已完成', failed: '失败', cancelled: '已取消',
}[status.value] || status.value))
const statusTagType = computed(() => ({
  completed: 'success', failed: 'danger', cancelled: 'warning', running: 'primary', queued: 'info',
}[status.value] || 'info'))

/** 将一个模型或工具步骤追加到时间线，并返回其稳定键。 */
const pushStep = (title, detail = '', type = 'primary', key = crypto.randomUUID()) => {
  timeline.value.push({ key, title, detail, type, timestamp: new Date().toLocaleTimeString() })
}

/** 等待 DOM 更新后将会话面板滚动到底部。 */
const scrollConversation = async () => {
  await nextTick()
  if (conversationRef.value) conversationRef.value.scrollTop = conversationRef.value.scrollHeight
}

/**
 * 将后端领域事件归并为运行状态、时间线、增量答案和证据。
 * @param {{type: string, payload?: object}} event 已解析的 SSE 事件。
 * @returns {void}
 */
const handleEvent = (event) => {
  const payload = event.payload || {}
  switch (event.type) {
    case 'run.started':
      status.value = 'running'
      pushStep('任务开始', `项目 ${payload.project_id || ''}`, 'primary', `event-${event.sequence}`)
      break
    case 'context.ready':
      pushStep('项目上下文已准备', `${payload.project_name || ''} · ${payload.characters || 0} 字符`, 'success', `event-${event.sequence}`)
      evidence.value = payload.evidence || []
      break
    case 'model.started':
      pushStep(`模型推理 · 第 ${payload.step} 步`, '', 'primary', `event-${event.sequence}`)
      break
    case 'tool.requested':
      pushStep(`调用工具：${payload.name}`, JSON.stringify(payload.arguments || {}, null, 2), 'warning', payload.call_id)
      break
    case 'tool.completed': {
      const item = timeline.value.find((row) => row.key === payload.call_id)
      if (item) {
        item.title = `工具完成：${payload.name}`
        item.type = 'success'
      }
      break
    }
    case 'tool.failed':
      pushStep(`工具失败：${payload.name}`, payload.error || '', 'danger', `event-${event.sequence}`)
      break
    case 'model.delta':
      answer.value += payload.delta || ''
      scrollConversation()
      break
    case 'run.completed':
      status.value = 'completed'
      answer.value = payload.answer || answer.value
      evidence.value = payload.evidence || evidence.value
      pushStep('分析完成', payload.model ? `${payload.provider} / ${payload.model}` : '确定性静态结果', 'success', `event-${event.sequence}`)
      break
    case 'run.failed':
      status.value = 'failed'
      error.value = payload.error || '智能体运行失败'
      break
    case 'run.cancelled':
      status.value = 'cancelled'
      pushStep('任务已取消', '', 'warning', `event-${event.sequence}`)
      break
  }
}

/** 校验当前问题、创建运行并消费事件流；结果写入响应式页面状态。 */
const submit = async () => {
  const text = draft.value.trim()
  if (!text || !props.projectId || running.value) return
  question.value = text
  draft.value = ''
  answer.value = ''
  error.value = ''
  evidence.value = []
  timeline.value = []
  status.value = 'queued'
  activeTab.value = 'steps'
  controller = new AbortController()
  try {
    const run = await createAgentRun(props.projectId, { question: text, use_model: true, max_steps: 4 })
    runId.value = run.id
    await streamAgentEvents(run.events_url, handleEvent, controller.signal)
  } catch (exc) {
    if (exc.name === 'AbortError') return
    status.value = 'failed'
    error.value = exc.response?.data?.detail || exc.message || '无法启动智能体任务'
    ElMessage.error(error.value)
  } finally {
    controller = null
  }
}

/** 中断浏览器事件连接并请求后端取消当前运行。 */
const cancel = async () => {
  if (!runId.value) return
  try {
    await cancelAgentRun(runId.value)
    status.value = 'cancelled'
    controller?.abort()
  } catch (exc) {
    ElMessage.error(exc.response?.data?.detail || '取消任务失败')
  }
}

onUnmounted(() => controller?.abort())
</script>

<style scoped>
.agent-card { min-height: 520px; }
.agent-header, .agent-status, .prompt-actions { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.agent-title { font-weight: 600; }
.agent-subtitle { margin-left: 10px; color: #909399; font-size: 12px; }
.agent-layout { display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(300px, .75fr); min-height: 430px; border: 1px solid #ebeef5; border-radius: 8px; overflow: hidden; }
.conversation-panel { display: flex; flex-direction: column; min-width: 0; background: #fafbfc; }
.conversation { flex: 1; max-height: 480px; overflow-y: auto; padding: 18px; }
.message { max-width: 88%; margin-bottom: 14px; padding: 12px 14px; border-radius: 8px; line-height: 1.65; }
.user-message { margin-left: auto; background: #ecf5ff; border: 1px solid #d9ecff; }
.assistant-message { background: #fff; border: 1px solid #ebeef5; }
.message-role { margin-bottom: 5px; color: #909399; font-size: 11px; }
.answer-text { white-space: pre-wrap; word-break: break-word; }
.thinking { display: flex; align-items: center; gap: 8px; color: #606266; }
.pulse-dot { width: 7px; height: 7px; border-radius: 50%; background: #409eff; animation: pulse 1s infinite alternate; }
@keyframes pulse { from { opacity: .25; } to { opacity: 1; } }
.prompt-box { padding: 14px; border-top: 1px solid #ebeef5; background: #fff; }
.prompt-actions { margin-top: 10px; }
.prompt-hint { color: #a8abb2; font-size: 11px; }
.trace-panel { padding: 0 14px; border-left: 1px solid #ebeef5; background: #fff; overflow: auto; }
.tool-timeline { padding: 8px 4px 0; }
.timeline-title { color: #303133; font-size: 13px; }
.timeline-detail { max-height: 120px; overflow: auto; margin: 6px 0 0; padding: 7px; background: #f5f7fa; border-radius: 4px; color: #606266; font-size: 10px; white-space: pre-wrap; }
.evidence-list { display: flex; flex-direction: column; gap: 8px; padding-bottom: 14px; }
.evidence-row { display: flex; flex-direction: column; gap: 3px; padding: 9px; border: 1px solid #ebeef5; border-radius: 6px; }
.evidence-row code { color: #2563eb; word-break: break-all; }
.evidence-row span { color: #606266; font-size: 12px; }
.evidence-row small { color: #a8abb2; }
@media (max-width: 1050px) { .agent-layout { grid-template-columns: 1fr; } .trace-panel { border-left: 0; border-top: 1px solid #ebeef5; } }
</style>
