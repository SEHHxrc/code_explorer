<template>
  <el-card class="execution-card">
    <template #header>
      <div class="header">
        <div>
          <span class="title">📦 隔离执行与安全检查</span>
          <span class="subtitle">独立 Docker Worker · 项目只读挂载 · 默认断网</span>
        </div>
        <el-tag :type="configuration.configured ? 'success' : 'info'" size="small">
          {{ configuration.configured ? '队列已配置' : '执行功能未配置' }}
        </el-tag>
      </div>
    </template>

    <el-alert
      title="Web 服务只负责入队。任务由独立 Worker 在无网络、非 root、资源受限的短生命周期容器中执行。"
      type="warning"
      :closable="false"
      show-icon
    />

    <div class="limits">
      <span>最长 {{ configuration.limits?.timeout_seconds || '—' }} 秒</span>
      <span>CPU {{ configuration.limits?.cpu || '—' }}</span>
      <span>内存 {{ configuration.limits?.memory_mb || '—' }} MB</span>
      <span>PID {{ configuration.limits?.pids || '—' }}</span>
      <span>输出 {{ outputLimit }}</span>
    </div>

    <el-form class="task-form" label-width="96px">
      <el-form-item label="任务类型">
        <el-radio-group v-model="kind" :disabled="running">
          <el-radio-button value="security_scan">安全扫描</el-radio-button>
          <el-radio-button value="command">受限命令</el-radio-button>
        </el-radio-group>
      </el-form-item>

      <template v-if="kind === 'security_scan'">
        <el-form-item label="扫描器">
          <el-select v-model="scanProfile" placeholder="选择服务端配置的扫描器" :disabled="running">
            <el-option v-for="profile in configuration.scan_profiles || []" :key="profile" :label="profile" :value="profile" />
          </el-select>
        </el-form-item>
      </template>

      <template v-else>
        <el-form-item label="容器镜像">
          <el-select v-model="image" placeholder="选择白名单镜像" filterable :disabled="running">
            <el-option v-for="item in configuration.allowed_images || []" :key="item" :label="item" :value="item" />
          </el-select>
        </el-form-item>
        <el-form-item label="参数数组">
          <el-input
            v-model="argvText"
            type="textarea"
            :rows="4"
            :disabled="running"
            placeholder='["python", "-m", "compileall", "-q", "."]'
          />
          <div class="field-help">必须使用 JSON 字符串数组；不会经过 Shell 展开、管道或重定向。</div>
        </el-form-item>
      </template>

      <el-form-item>
        <el-button type="primary" :loading="running" :disabled="!canSubmit" @click="submit">
          提交隔离任务
        </el-button>
        <el-button v-if="running" type="danger" plain @click="cancel">请求取消</el-button>
        <el-button :disabled="running" @click="refreshTasks">刷新历史</el-button>
      </el-form-item>
    </el-form>

    <div v-if="selectedTask" class="result-grid">
      <section class="console">
        <div class="section-title">
          <strong>任务输出</strong>
          <el-tag size="small" :type="statusTag(selectedTask.status)">{{ statusText(selectedTask.status) }}</el-tag>
        </div>
        <pre>{{ output || '尚无输出。排队任务需要单独启动 execution worker。' }}</pre>
        <el-alert v-if="selectedTask.error" :title="selectedTask.error" type="error" :closable="false" />
      </section>
      <section class="audit">
        <div class="section-title"><strong>审计事件</strong><span>{{ events.length }} 条</span></div>
        <el-timeline v-if="events.length">
          <el-timeline-item v-for="event in events" :key="event.sequence" :timestamp="'#' + event.sequence">
            {{ eventLabel(event.type) }}
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else :image-size="50" description="尚无事件" />
      </section>
    </div>

    <el-table v-if="tasks.length" :data="tasks" size="small" class="history" @row-click="selectHistory">
      <el-table-column prop="kind" label="类型" width="130" />
      <el-table-column prop="image" label="镜像" min-width="190" />
      <el-table-column label="状态" width="110">
        <template #default="{ row }"><el-tag size="small" :type="statusTag(row.status)">{{ statusText(row.status) }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="exit_code" label="退出码" width="80" />
      <el-table-column prop="created_at" label="创建时间" min-width="170" />
    </el-table>
  </el-card>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { apiErrorMessage } from '../../services/httpClient.js'
import {
  cancelExecutionTask,
  createExecutionTask,
  getExecutionConfiguration,
  getExecutionTask,
  listExecutionTasks,
  streamExecutionEvents,
} from '../../services/executionApi.js'
import { parseArgv } from './domain/parseArgv.js'

const props = defineProps({ projectId: { type: String, default: '' } })
const configuration = ref({ configured: false, allowed_images: [], scan_profiles: [], limits: {} })
const kind = ref('security_scan')
const scanProfile = ref('')
const image = ref('')
const argvText = ref('["python", "-m", "compileall", "-q", "."]')
const selectedTask = ref(null)
const tasks = ref([])
const events = ref([])
const output = ref('')
const running = computed(() => ['queued', 'running', 'cancel_requested'].includes(selectedTask.value?.status))
let controller = null

const canSubmit = computed(() => {
  if (!props.projectId || !configuration.value.configured || running.value) return false
  return kind.value === 'security_scan' ? Boolean(scanProfile.value) : Boolean(image.value && argvText.value.trim())
})
const outputLimit = computed(() => {
  const bytes = configuration.value.limits?.output_bytes
  return bytes ? Math.round(bytes / 1024) + ' KB' : '—'
})
const statusText = (status) => ({
  queued: '排队中', running: '运行中', cancel_requested: '取消中',
  completed: '已完成', failed: '失败', cancelled: '已取消', timed_out: '超时',
}[status] || status || '未知')
const statusTag = (status) => ({
  completed: 'success', failed: 'danger', timed_out: 'danger',
  cancelled: 'warning', cancel_requested: 'warning', running: 'primary',
}[status] || 'info')
const eventLabel = (type) => ({
  'task.queued': '任务已通过策略并入队',
  'task.started': 'Worker 已认领任务',
  'task.output': '收到容器输出',
  'task.cancel_requested': '已请求取消',
  'task.cancelled': '任务已取消',
  'task.completed': '容器执行完成',
  'task.failed': '任务执行失败',
  'task.timed_out': '任务超过时间限制',
}[type] || type)

const loadConfiguration = async () => {
  configuration.value = await getExecutionConfiguration()
  scanProfile.value = configuration.value.scan_profiles?.[0] || ''
  image.value = configuration.value.allowed_images?.[0] || ''
  if (!scanProfile.value && image.value) kind.value = 'command'
}
const refreshTasks = async () => {
  if (!props.projectId) return
  try {
    tasks.value = await listExecutionTasks(props.projectId)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '读取执行历史失败'))
  }
}
const handleEvent = (event) => {
  events.value.push(event)
  if (event.type === 'task.output') output.value += event.payload?.text || ''
  const terminal = event.type.replace('task.', '')
  if (['completed', 'failed', 'cancelled', 'timed_out'].includes(terminal) && selectedTask.value) {
    selectedTask.value.status = terminal
    selectedTask.value.exit_code = event.payload?.exit_code
    selectedTask.value.error = event.payload?.error
  } else if (event.type === 'task.started' && selectedTask.value) {
    selectedTask.value.status = 'running'
  } else if (event.type === 'task.cancel_requested' && selectedTask.value) {
    selectedTask.value.status = 'cancel_requested'
  }
}
const taskPayload = () => {
  const limits = configuration.value.limits || {}
  const common = {
    timeout_seconds: Math.min(120, limits.timeout_seconds || 120),
    cpu_limit: Math.min(1, limits.cpu || 1),
    memory_mb: Math.min(512, limits.memory_mb || 512),
    pids_limit: Math.min(128, limits.pids || 128),
  }
  return kind.value === 'security_scan'
    ? { ...common, kind: 'security_scan', scan_profile: scanProfile.value }
    : { ...common, kind: 'command', image: image.value, argv: parseArgv(argvText.value) }
}
const submit = async () => {
  controller?.abort()
  events.value = []
  output.value = ''
  controller = new AbortController()
  try {
    selectedTask.value = await createExecutionTask(props.projectId, taskPayload())
    await streamExecutionEvents(selectedTask.value.events_url, handleEvent, controller.signal)
    selectedTask.value = await getExecutionTask(selectedTask.value.id)
    await refreshTasks()
  } catch (error) {
    if (error.name !== 'AbortError') ElMessage.error(apiErrorMessage(error, '隔离任务提交失败'))
  } finally {
    controller = null
  }
}
const cancel = async () => {
  if (!selectedTask.value) return
  try {
    selectedTask.value = await cancelExecutionTask(selectedTask.value.id)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '取消任务失败'))
  }
}
const selectHistory = async (row) => {
  if (running.value) return
  selectedTask.value = row
  events.value = []
  output.value = ''
}

watch(() => props.projectId, () => {
  controller?.abort()
  selectedTask.value = null
  tasks.value = []
  events.value = []
  output.value = ''
  refreshTasks()
})
onMounted(async () => {
  try {
    await loadConfiguration()
    await refreshTasks()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '读取执行配置失败'))
  }
})
onUnmounted(() => controller?.abort())
</script>

<style scoped>
.execution-card { min-height: 320px; }
.header, .section-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.title { font-weight: 600; }
.subtitle { margin-left: 10px; color: #909399; font-size: 12px; }
.limits { display: flex; flex-wrap: wrap; gap: 8px 16px; margin: 12px 0; color: #606266; font-size: 12px; }
.task-form { max-width: 900px; }
.field-help { margin-top: 5px; color: #909399; font-size: 11px; }
.result-grid { display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(260px, .6fr); gap: 14px; margin-top: 14px; }
.console, .audit { min-width: 0; padding: 12px; border: 1px solid #dcdfe6; border-radius: 7px; }
.console pre { min-height: 160px; max-height: 420px; overflow: auto; padding: 12px; background: #111827; color: #d1fae5; border-radius: 5px; white-space: pre-wrap; word-break: break-word; }
.audit { max-height: 480px; overflow: auto; }
.section-title span { color: #909399; font-size: 11px; }
.history { margin-top: 16px; }
@media (max-width: 900px) { .result-grid { grid-template-columns: 1fr; } }
</style>
