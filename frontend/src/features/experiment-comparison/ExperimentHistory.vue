<template>
  <el-collapse class="history">
    <el-collapse-item :title="`历史实验（${page.total}）`" name="history">
      <div class="history-actions">
        <span>保留全部试次；切换结果不取消后台任务。历史左右顺序独立随机，未揭盲时不能按左右身份跨试次比较。</span>
        <el-button size="small" :loading="loading" @click="loadPage">刷新</el-button>
      </div>
      <el-table :data="page.items" height="260" size="small" row-key="id" v-loading="loading">
        <el-table-column label="时间" width="175"><template #default="{ row }">{{ timestamp(row.created_at) }}</template></el-table-column>
        <el-table-column prop="question_preview" label="问题" min-width="200" show-overflow-tooltip />
        <el-table-column label="左 / 右状态" width="150"><template #default="{ row }">{{ status(row.lanes.left?.status) }} / {{ status(row.lanes.right?.status) }}</template></el-table-column>
        <el-table-column label="盲评偏好" width="110"><template #default="{ row }">{{ reviewPreference(row.preferred_lane) }}</template></el-table-column>
        <el-table-column label="操作" width="90"><template #default="{ row }"><el-button link type="primary" @click="$emit('open', row.id)">打开</el-button></template></el-table-column>
      </el-table>
      <div class="history-actions">
        <el-pagination small layout="prev, pager, next, total" :page-size="20" :total="page.total"
          :current-page="pageNumber" @current-change="changePage" />
        <el-select v-model="selected" multiple :multiple-limit="2" placeholder="选择本页两次实验" class="history-select">
          <el-option v-for="item in page.items" :key="item.id" :value="item.id" :label="`${timestamp(item.created_at)} ${item.question_preview}`" />
        </el-select>
        <el-button size="small" :disabled="selected.length !== 2" :loading="comparing" @click="compare">并排比较</el-button>
      </div>
    </el-collapse-item>
  </el-collapse>
  <el-dialog v-model="visible" title="历史实验：提示词、条件与原始报告" width="92%" @closed="details = []">
    <el-alert :title="promptComparison(details)" type="info" :closable="false" />
    <p>报告为原文，不自动合并根因或验证结论；失败试次也保留成本。完整传输审计仍需管理员权限。</p>
    <div class="history-comparison">
      <section v-for="item in details" :key="item.id">
        <h4>{{ timestamp(item.created_at) }} · {{ item.protocol }} · {{ item.id }}</h4>
        <p>{{ item.question }}</p>
        <el-alert v-if="item.quality_warnings?.length" :title="item.quality_warnings.join(' ')" type="warning" :closable="false" />
        <section v-for="(lane, name) in item.lanes" :key="name">
          <h4>{{ name === 'left' ? '左侧' : '右侧' }} · {{ status(lane.run?.status) }}{{ item.reveal ? ` · ${item.reveal[name]}` : ' · 未揭盲' }}</h4>
          <p>耗时 {{ lane.metrics.duration_ms ?? '未记录' }} ms；供应商总 Token {{ lane.metrics.actual_total_tokens ?? '未记录' }}（{{ lane.metrics.usage_status }}）；工具 {{ lane.metrics.tool_calls }}；引用警告 {{ lane.metrics.citation_check?.warnings?.length ?? '未记录' }}；有效配对 {{ item.valid_for_review ? '是（非结论正确性证明）' : '否' }}</p>
          <details><summary>当时的共同提示词与运行条件</summary>
            <pre>{{ lane.prompt_snapshot?.instructions || '提示词未记录或审计已过期，不能用当前版本重建。' }}</pre>
            <pre>{{ JSON.stringify(historyConditions(lane), null, 2) }}</pre>
          </details>
          <pre class="history-answer">{{ lane.run?.answer || lane.run?.error || '尚无回答' }}</pre>
        </section>
        <BlindReviewResult :review="item.review" />
      </section>
    </div>
  </el-dialog>
</template>

<script setup>
import { onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { apiErrorMessage } from '../../services/httpClient.js'
import { listExperimentHistory, readExperimentHistory } from '../../services/experimentApi.js'
import { historyConditions, promptComparison, reviewPreference } from './history.js'
import BlindReviewResult from './BlindReviewResult.vue'

const props = defineProps({ projectId: { type: String, default: '' }, refreshKey: { type: Number, default: 0 } })
defineEmits(['open'])
const page = ref({ items: [], total: 0 })
const pageNumber = ref(1)
const loading = ref(false)
const selected = ref([])
const comparing = ref(false)
const visible = ref(false)
const details = ref([])
let generation = 0
let comparisonGeneration = 0
const status = (value) => ({ queued: '排队', running: '运行', completed: '完成', failed: '失败', cancelled: '取消' }[value] || '缺失')
const timestamp = (value) => value ? new Date(/[Zz]|[+-]\d\d:\d\d$/.test(value) ? value : value + 'Z').toLocaleString() : '时间未记录'

const loadPage = async () => {
  const ticket = ++generation
  if (!props.projectId) return
  loading.value = true
  try {
    const result = await listExperimentHistory(props.projectId, (pageNumber.value - 1) * 20)
    if (ticket === generation) page.value = result
  } catch (error) {
    if (ticket === generation) ElMessage.error(apiErrorMessage(error, '读取历史实验失败'))
  } finally {
    if (ticket === generation) loading.value = false
  }
}
const changePage = (number) => { pageNumber.value = number; selected.value = []; loadPage() }
const compare = async () => {
  if (selected.value.length !== 2) return
  const projectId = props.projectId
  const ticket = generation
  const comparisonTicket = ++comparisonGeneration
  comparing.value = true
  try {
    const result = await Promise.all(selected.value.map(readExperimentHistory))
    if (ticket === generation && projectId === props.projectId) { details.value = result; visible.value = true }
  } catch (error) {
    if (ticket === generation) ElMessage.error(apiErrorMessage(error, '读取历史对比失败'))
  } finally {
    if (comparisonTicket === comparisonGeneration) comparing.value = false
  }
}
watch(() => props.projectId, () => {
  generation++
  comparisonGeneration++
  page.value = { items: [], total: 0 }
  pageNumber.value = 1
  selected.value = []; details.value = []; visible.value = false; loading.value = false; comparing.value = false
  loadPage()
}, { immediate: true })
watch(() => props.refreshKey, loadPage)
onUnmounted(() => { generation++; comparisonGeneration++ })
</script>

<style scoped>
.history { margin: 12px 0; }
.history-actions { display: flex; align-items: center; gap: 12px; margin: 10px 0; flex-wrap: wrap; font-size: 12px; }
.history-actions > span { flex: 1; }
.history-select { width: 300px; }
.history-comparison { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
.history-comparison > section { min-width: 0; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 300px; overflow: auto; background: #f5f7fa; padding: 10px; }
.history-answer { max-height: 420px; }
@media (max-width: 1000px) { .history-comparison { grid-template-columns: 1fr; } }
</style>
