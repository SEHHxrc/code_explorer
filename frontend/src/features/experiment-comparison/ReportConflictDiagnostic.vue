<template>
  <div v-if="available || record" class="report-diagnostic">
    <p>台账冲突解释为独立诊断意见，原实验状态、评分和统计保持原样；诊断的模型用量与耗时另计。</p>
    <template v-if="!record || record.status === 'not_requested'">
      <el-button size="small" :loading="requesting" :disabled="!modelConfigured" @click="start">询问台账冲突原因</el-button>
      <span>仅点击后发起一次模型请求；重复点击复用记录，不自动重跑实验。</span>
    </template>
    <p v-else-if="record.status === 'running'">正在诊断，最长约三分钟；可以离开页面，之后在历史实验中查看。</p>
    <template v-else-if="record.status === 'completed'">
      <b>{{ classificationLabel(record.explanation.classification) }}</b>
      <p>{{ record.explanation.summary }}</p>
      <div v-if="record.explanation.conflicting_fields.length">涉及字段：{{ record.explanation.conflicting_fields.join('、') }}</div>
      <ul><li v-for="item in record.explanation.scope_conditions" :key="item">{{ item }}</li></ul>
      <p v-if="record.explanation.suggested_change">建议：{{ record.explanation.suggested_change }}</p>
      <div v-for="(item, index) in record.explanation.evidence" :key="index">引用：{{ item.path }}:{{ item.start_line }}-{{ item.end_line }}（沿用原报告，未重新核验源码）</div>
      <p>解释不证明原判断正确，也不会自动放宽契约或把失败报告纳入评分。</p>
    </template>
    <p v-else>{{ record.error || '诊断未完成；原实验失败结果保持不变。' }}</p>
    <p v-if="record && record.status !== 'running' && record.status !== 'not_requested'">诊断请求 {{ record.model_requests ?? '未知' }} 次；供应商总 Token {{ record.usage?.total_tokens ?? '未完整报告' }}；耗时 {{ record.elapsed_ms ?? '未知' }} ms。</p>
    <p v-if="error" class="diagnostic-error">{{ error }}</p>
  </div>
</template>

<script setup>
import { onScopeDispose, ref, watch } from 'vue'
import { readReportDiagnostic, requestReportDiagnostic } from '../../services/experimentApi.js'
import { apiErrorMessage } from '../../services/httpClient.js'

const props = defineProps({ comparisonId: String, lane: String, available: Boolean, snapshot: Object, modelConfigured: Boolean })
const record = ref(null)
const requesting = ref(false)
const error = ref('')
let controller, timer, version = 0
const classificationLabel = (value) => ({ fields_conflict: '字段存在矛盾', scoped_coexistence: '可能适用于不同条件或范围',
  contract_limitation: '可能存在契约表达局限', insufficient_context: '诊断上下文不足', unclear: '暂时无法判断' }[value] || '诊断意见')
const stop = () => { version++; clearTimeout(timer); controller?.abort() }
const poll = async (current) => {
  if (current !== version) return
  controller = new AbortController()
  try {
    const value = await readReportDiagnostic(props.comparisonId, props.lane, controller.signal)
    if (current !== version) return
    record.value = value
    error.value = ''
  } catch (cause) {
    if (current !== version) return
    error.value = apiErrorMessage(cause, '诊断状态读取失败，将重试读取；不会重复调用模型')
  }
  if (current === version && record.value?.status === 'running') timer = setTimeout(() => poll(current), 2000)
}
const start = async () => {
  if (requesting.value || (record.value && record.value.status !== 'not_requested')) return
  stop()
  const current = version
  controller = new AbortController()
  requesting.value = true
  error.value = ''
  try {
    const value = await requestReportDiagnostic(props.comparisonId, props.lane, controller.signal)
    if (current !== version) return
    record.value = value
    if (value.status === 'running') timer = setTimeout(() => poll(current), 1000)
  } catch (cause) {
    if (current === version) error.value = apiErrorMessage(cause, '无法申请诊断')
  } finally {
    if (current === version) requesting.value = false
  }
}
watch(() => [props.comparisonId, props.lane], () => {
  stop(); record.value = props.snapshot || null; requesting.value = false; error.value = ''
  if (record.value?.status === 'running') poll(version)
}, { immediate: true })
watch(() => props.snapshot, (value) => {
  if (!value || requesting.value) return
  // SSE 的旧 running 快照不能覆盖已经读取到的终态诊断。
  if (value.id === record.value?.id && value.status === 'running' && record.value.status !== 'running') return
  stop()
  record.value = value
  if (value.status === 'running') poll(version)
})
onScopeDispose(stop)
</script>

<style scoped>
.report-diagnostic { margin: 10px 0; padding: 10px; border: 1px solid #e2e8f0; border-radius: 5px; font-size: 12px; overflow-wrap: anywhere; }
.report-diagnostic p { margin: 5px 0; }
.diagnostic-error { color: #b45309; }
</style>
