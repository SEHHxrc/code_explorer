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
            {{ modelStatus.configured ? `${modelStatus.provider} / ${selectedModel || modelStatus.model}` : '静态回退' }}
          </el-tag>
          <el-tag v-if="status !== 'idle'" size="small" :type="statusTagType">{{ statusLabel }}</el-tag>
        </div>
      </div>
    </template>

    <section class="history-controls">
      <div class="history-selector">
        <span class="control-label">历史分析</span>
        <el-select
          v-model="selectedHistoryId"
          :disabled="running || historyLoading"
          :loading="historyLoading"
          clearable
          filterable
          placeholder="选择已保存的 Agent 运行"
          @change="restoreHistory"
        >
          <el-option
            v-for="item in historyRuns"
            :key="item.id"
            :label="historyLabel(item)"
            :value="item.id"
          />
        </el-select>
        <el-tag size="small" type="info" effect="plain">{{ historyRuns.length }} 条</el-tag>
      </div>
      <el-button size="small" :disabled="running || !projectId" :loading="historyLoading" @click="loadHistory(false)">
        刷新历史
      </el-button>
    </section>

    <section class="model-controls">
      <div class="model-selector">
        <span class="control-label">运行模型</span>
        <el-select-v2
          v-model="selectedModel"
          :options="modelOptions"
          :disabled="running || !modelStatus.configured"
          filterable
          placeholder="选择智能体使用的模型"
        />
        <el-tag v-if="modelCatalog?.connected" size="small" type="info" effect="plain">
          当前凭据可见 {{ modelCatalog.models.length }} 个
        </el-tag>
        <el-tag v-if="modelStatus.max_input_tokens" size="small" type="info" effect="plain">
          应用输入预算 {{ Number(modelStatus.max_input_tokens).toLocaleString() }} tokens · 单次输出上限 {{ Number(modelStatus.max_output_tokens).toLocaleString() }} tokens
        </el-tag>
        <el-tag v-if="modelStatus.max_context_chars" size="small" type="warning" effect="plain">
          旧字符保护 {{ Number(modelStatus.max_context_chars).toLocaleString() }} 字符
        </el-tag>
        <el-tag v-if="modelStatus.configured" size="small" type="info" effect="plain">
          {{ modelStatus.context_window_tokens && (!selectedModel || selectedModel === modelStatus.model) ? `窗口配置 ${Number(modelStatus.context_window_tokens).toLocaleString()} tokens` : '平台窗口未知 · 本地输入为估算' }}
        </el-tag>
      </div>
      <div class="model-actions">
        <el-tooltip content="针对当前模型验证工具调用、结果回传和最终回答，最多两次生成请求，可能产生少量费用" placement="bottom">
          <span>
            <el-button
              size="small"
              :disabled="running || !modelStatus.configured || !selectedModel"
              :loading="modelProbing"
              @click="probeModel"
            >测试连接</el-button>
          </span>
        </el-tooltip>
        <el-button
          size="small"
          :disabled="running || !modelStatus.configured"
          :loading="modelsLoading"
          @click="loadModels"
        >查看可用模型</el-button>
      </div>
    </section>
    <el-alert
      v-if="modelStatus.configured && modelStatus.provider === 'compatible' && modelStatus.transport_secure === false"
      title="当前兼容模型端点使用 HTTP。远程平台建议使用 HTTPS，避免密钥和项目内容以明文传输。"
      type="warning"
      :closable="false"
      show-icon
    />
    <el-alert
      v-if="modelProbe"
      class="model-diagnostic"
      :title="modelProbe.message"
      :type="modelProbe.generation_available && modelProbe.agent_compatible !== false ? 'success' : 'error'"
      :description="probeDescription"
      :closable="false"
      show-icon
    />
    <el-alert
      v-if="modelCatalog && !modelCatalog.connected"
      class="model-diagnostic"
      :title="modelCatalog.message"
      type="error"
      :closable="false"
      show-icon
    />

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
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  cancelAgentRun,
  createAgentRun,
  getAgentRunSnapshot,
  listProjectAgentRuns,
  streamAgentEvents,
} from '../services/agentApi'
import { getAvailableModels, probeModelConnection } from '../services/modelApi.js'
import { apiErrorMessage } from '../services/httpClient.js'

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
const selectedModel = ref('')
const modelProbe = ref(null)
const modelCatalog = ref(null)
const modelProbing = ref(false)
const modelsLoading = ref(false)
const historyRuns = ref([])
const selectedHistoryId = ref('')
const historyLoading = ref(false)
let controller = null

const running = computed(() => ['queued', 'running'].includes(status.value))
const statusLabel = computed(() => ({
  queued: '排队中', running: '分析中', completed: '已完成', failed: '失败', cancelled: '已取消',
}[status.value] || status.value))
const statusTagType = computed(() => ({
  completed: 'success', failed: 'danger', cancelled: 'warning', running: 'primary', queued: 'info',
}[status.value] || 'info'))
const modelOptions = computed(() => {
  const models = new Set(modelCatalog.value?.models || [])
  if (props.modelStatus.model) models.add(props.modelStatus.model)
  if (selectedModel.value) models.add(selectedModel.value)
  return [...models].sort().map((model) => ({ label: model, value: model }))
})
const probeDescription = computed(() => {
  if (!modelProbe.value) return ''
  return [
    modelProbe.value.model ? `模型：${modelProbe.value.model}` : '',
    modelProbe.value.status_code ? `HTTP：${modelProbe.value.status_code}` : '',
    modelProbe.value.error_code ? `错误代码：${modelProbe.value.error_code}` : '',
    modelProbe.value.retry_after ? `建议等待：${modelProbe.value.retry_after} 秒` : '',
    modelProbe.value.request_id ? `请求 ID：${modelProbe.value.request_id}` : '',
  ].filter(Boolean).join(' · ')
})
const historyLabel = (item) => {
  const when = item.created_at ? new Date(item.created_at).toLocaleString() : '未知时间'
  const label = statusLabelFor(item.status)
  return `${when} · ${label} · ${item.question_preview || '未命名问题'}`
}
const statusLabelFor = (value) => ({
  queued: '排队中', running: '分析中', completed: '已完成', failed: '失败', cancelled: '已取消',
}[value] || value)

watch(
  () => props.modelStatus.model,
  (model) => {
    if (!selectedModel.value && model) selectedModel.value = model
  },
  { immediate: true },
)
watch(selectedModel, () => {
  modelProbe.value = null
})

/** 查询当前凭据可见的模型，并将目录提供给运行模型选择器。 */
const loadModels = async () => {
  modelsLoading.value = true
  try {
    modelCatalog.value = await getAvailableModels()
    ElMessage[modelCatalog.value.connected ? 'success' : 'warning'](modelCatalog.value.message)
  } catch (exc) {
    ElMessage.error(apiErrorMessage(exc, '可见模型查询失败'))
  } finally {
    modelsLoading.value = false
  }
}

/** 对当前选择的模型执行一次最小函数工具探测，验证 Agent 所需协议。 */
const probeModel = async () => {
  if (!selectedModel.value) return
  modelProbing.value = true
  try {
    modelProbe.value = await probeModelConnection(selectedModel.value)
    const compatible = modelProbe.value.generation_available && modelProbe.value.agent_compatible !== false
    ElMessage[compatible ? 'success' : 'warning'](modelProbe.value.message)
  } catch (exc) {
    ElMessage.error(apiErrorMessage(exc, '模型连通性测试失败'))
  } finally {
    modelProbing.value = false
  }
}

/** 将一个模型或工具步骤追加到时间线，并返回其稳定键。 */
const pushStep = (
  title,
  detail = '',
  type = 'primary',
  key = crypto.randomUUID(),
  timestamp = new Date().toLocaleTimeString(),
) => {
  timeline.value.push({ key, title, detail, type, timestamp })
}

/** 清空当前会话视图；不影响后端持久化历史。 */
const clearRunView = () => {
  controller?.abort()
  controller = null
  question.value = ''
  answer.value = ''
  error.value = ''
  status.value = 'idle'
  runId.value = ''
  timeline.value = []
  evidence.value = []
  activeTab.value = 'steps'
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
  const eventTime = event.created_at
    ? new Date(event.created_at).toLocaleTimeString()
    : new Date().toLocaleTimeString()
  switch (event.type) {
    case 'run.started':
      status.value = 'running'
      pushStep('任务开始', `项目 ${payload.project_id || ''}`, 'primary', `event-${event.sequence}`, eventTime)
      break
    case 'context.ready':
      pushStep('项目上下文已准备', `${payload.project_name || ''} · ${payload.characters || 0} 字符`, 'success', `event-${event.sequence}`, eventTime)
      evidence.value = payload.evidence || []
      break
    case 'model.started':
      pushStep(
        `模型推理 · 第 ${payload.step} 步`,
        payload.input_token_estimate
          ? `输入估算 ${Number(payload.input_token_estimate.tokens).toLocaleString()} tokens（${payload.input_token_estimate.method}）· ${payload.tool_count || 0} 个工具 · 输出上限 ${Number(payload.max_output_tokens || 0).toLocaleString()} tokens`
          : payload.request_chars
          ? `请求约 ${Number(payload.request_chars).toLocaleString()} 字符 · ${payload.tool_count || 0} 个工具 · 输出上限 ${Number(payload.max_output_tokens || 0).toLocaleString()} tokens`
          : '',
        'primary',
        `event-${event.sequence}`,
        eventTime,
      )
      break
    case 'tool.requested':
      pushStep(`调用工具：${payload.name}`, JSON.stringify(payload.arguments || {}, null, 2), 'warning', payload.call_id, eventTime)
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
      pushStep(`工具失败：${payload.name}`, payload.error || '', 'danger', `event-${event.sequence}`, eventTime)
      break
    case 'model.delta':
      answer.value += payload.delta || ''
      scrollConversation()
      break
    case 'run.completed':
      status.value = 'completed'
      answer.value = payload.answer || answer.value
      evidence.value = payload.evidence || evidence.value
      pushStep('分析完成', payload.model ? `${payload.provider} / ${payload.model}` : '确定性静态结果', 'success', `event-${event.sequence}`, eventTime)
      break
    case 'run.failed':
      status.value = 'failed'
      error.value = payload.error || '智能体运行失败'
      pushStep(
        '模型请求失败',
        [
          payload.status_code ? `HTTP：${payload.status_code}` : '',
          payload.error_code ? `错误代码：${payload.error_code}` : '',
          payload.retry_after ? `建议等待：${payload.retry_after} 秒` : '',
          payload.request_id ? `请求 ID：${payload.request_id}` : '',
        ].filter(Boolean).join('\n'),
        'danger',
        `event-${event.sequence}`,
        eventTime,
      )
      break
    case 'run.cancelled':
      status.value = 'cancelled'
      pushStep('任务已取消', '', 'warning', `event-${event.sequence}`, eventTime)
      break
  }
}

/** 读取一次持久化运行，并重建问题、答案、步骤与证据。 */
const restoreHistory = async (historyId) => {
  if (!historyId || running.value) return
  historyLoading.value = true
  try {
    const snapshot = await getAgentRunSnapshot(historyId)
    clearRunView()
    const run = snapshot.run || {}
    runId.value = run.id || historyId
    question.value = run.question || ''
    status.value = run.status || 'idle'
    answer.value = run.answer || ''
    error.value = run.error || ''
    for (const event of snapshot.events || []) handleEvent(event)
    answer.value = run.answer || answer.value
    evidence.value = snapshot.evidence?.length ? snapshot.evidence : evidence.value
    selectedHistoryId.value = historyId
    await scrollConversation()
  } catch (exc) {
    ElMessage.error(apiErrorMessage(exc, 'Agent 历史恢复失败'))
  } finally {
    historyLoading.value = false
  }
}

/** 加载当前项目的普通 Agent 历史；A/B 实验运行由实验模块独立管理。 */
const loadHistory = async (restoreLatest = false) => {
  if (!props.projectId || running.value) return
  historyLoading.value = true
  try {
    historyRuns.value = await listProjectAgentRuns(props.projectId)
    if (restoreLatest && historyRuns.value.length) {
      historyLoading.value = false
      await restoreHistory(historyRuns.value[0].id)
    }
  } catch (exc) {
    ElMessage.error(apiErrorMessage(exc, 'Agent 历史列表加载失败'))
  } finally {
    historyLoading.value = false
  }
}

watch(
  () => props.projectId,
  async (projectId) => {
    clearRunView()
    historyRuns.value = []
    selectedHistoryId.value = ''
    if (projectId) await loadHistory(true)
  },
  { immediate: true },
)

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
  selectedHistoryId.value = ''
  status.value = 'queued'
  activeTab.value = 'steps'
  controller = new AbortController()
  try {
    const run = await createAgentRun(props.projectId, {
      question: text,
      use_model: true,
      max_steps: 4,
      model: selectedModel.value || null,
    })
    runId.value = run.id
    await streamAgentEvents(run.events_url, handleEvent, controller.signal)
  } catch (exc) {
    if (exc.name === 'AbortError') return
    status.value = 'failed'
    error.value = exc.response?.data?.detail || exc.message || '无法启动智能体任务'
    ElMessage.error(error.value)
  } finally {
    controller = null
    if (runId.value) {
      await loadHistory(false)
      selectedHistoryId.value = runId.value
    }
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
.history-controls { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 10px; padding: 9px 12px; border: 1px solid #ebeef5; border-radius: 8px; background: #fff; }
.history-selector { display: flex; flex: 1; min-width: 0; align-items: center; gap: 10px; }
.history-selector .el-select { width: min(680px, 65vw); }
.model-controls { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 12px; padding: 10px 12px; border: 1px solid #e4e7ed; border-radius: 8px; background: #f8fafc; }
.model-selector, .model-actions { display: flex; align-items: center; gap: 10px; }
.model-selector { flex-wrap: wrap; }
.model-selector :deep(.el-select-v2) { width: min(360px, 34vw); }
.control-label { flex: none; color: #606266; font-size: 13px; }
.model-diagnostic { margin-bottom: 12px; }
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
@media (max-width: 1050px) { .agent-layout { grid-template-columns: 1fr; } .trace-panel { border-left: 0; border-top: 1px solid #ebeef5; } .model-controls, .history-controls { align-items: stretch; flex-direction: column; } .model-selector :deep(.el-select-v2), .history-selector .el-select { flex: 1; width: auto; } }
</style>
