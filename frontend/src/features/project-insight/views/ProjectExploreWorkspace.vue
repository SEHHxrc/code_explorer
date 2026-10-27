<template>
  <div class="explore-workspace">
    <ProjectFileTree :nodes="fileTree" @select="selectedNode = $event" />
    <el-card class="network-card" body-style="padding: 0">
      <template #header>
        <div class="card-header">
          <span>🕸️ 静态安全路径</span>
          <el-tag size="small" type="info">{{ graphCounts }}</el-tag>
        </div>
      </template>
      <div class="path-toolbar">
        <el-select-v2 v-model="paths.selectedId.value" :options="pathOptions" filterable :placeholder="pathOptions[0].label" aria-label="选择静态路径" />
        <el-button :loading="paths.loading.value" @click="paths.reload">刷新</el-button>
      </div>
      <div class="path-notice">
        {{ pathCounts }}。仅展示已保存事实，非全部可能路径或已确认漏洞；边沿用调用方向，不等同数据流方向。
        未配对操作以独立节点标记，不补造传播连线。
        <span v-if="hasLimit" class="warning">存在分析/显示上限、诊断或缺失项，请查看覆盖说明。</span>
        <el-tooltip placement="bottom" :content="coverageDescription"><el-button link size="small">覆盖说明</el-button></el-tooltip>
      </div>
      <SecurityPathDetails v-if="paths.selected.value" :route="paths.selected.value" :detail="paths.detail.value"
        :loading="paths.detailLoading.value" :error="paths.detailError.value" @retry="paths.loadDetail" />
      <div v-if="paths.loading.value" class="path-placeholder">正在读取静态分析结果…</div>
      <div v-else-if="paths.error.value" class="path-placeholder warning">{{ paths.error.value }}<el-button link @click="paths.reload">重试</el-button></div>
      <DependencyGraph v-else-if="paths.graphData.value.nodes.length" ref="graphRef" class="path-canvas" :graph-data="paths.graphData.value" />
      <el-empty v-else :description="emptyDescription" :image-size="72" />
    </el-card>
    <SymbolOutline :node="selectedNode" :hierarchy="fileHierarchy" @locate="locateSymbol" />
  </div>
</template>

<script setup>
import { computed, defineAsyncComponent, nextTick, ref, toRef, watch } from 'vue'
import { ElMessage } from 'element-plus'
import ProjectFileTree from '../components/ProjectFileTree.vue'
import SymbolOutline from '../components/SymbolOutline.vue'
import { buildSymbolTree } from '../domain/symbolTree.js'
import SecurityPathDetails from '../components/SecurityPathDetails.vue'
import { useSecurityPaths } from '../composables/useSecurityPaths.js'
import { pathFamilies, pathFamilyId, pathKindLabel, pathLocation } from '../domain/securityPaths.js'

const DependencyGraph = defineAsyncComponent(() => import('../../dependency-graph/DependencyGraph.vue'))
const props = defineProps({
  fileTree: { type: Array, default: () => [] },
  projectId: { type: String, required: true },
})
const paths = useSecurityPaths(toRef(props, 'projectId'))
const selectedNode = ref(null)
const graphRef = ref(null)
const locateSymbol = async (target) => {
  paths.selectedId.value = ''
  await nextTick()
  if (!await graphRef.value?.revealSymbol(target)) ElMessage.info('该符号不在已保存的安全路径或操作中，不展开无关调用图。')
}
watch(() => props.projectId, () => { selectedNode.value = null })
const fileHierarchy = computed(() => (
  selectedNode.value && !selectedNode.value.is_dir
    ? buildSymbolTree(selectedNode.value.symbols || [])
    : []
))
const graphCounts = computed(() => (
  String(paths.graphData.value.nodes.length)
  + ' 节点 · '
  + String(paths.graphData.value.edges.length)
  + ' 关系'
))
const pathOptions = computed(() => {
  const routes = paths.data.value?.paths || []
  const operations = paths.data.value?.operations || []
  const families = new Map(pathFamilies(routes).map((family) => [family.id, family]))
  return [
    { value: '', label: `全部已保存路线与操作（${routes.length} 条路线 · ${operations.length} 个未配对操作）` },
    ...routes.map((route, index) => ({ value: route.id,
      label: `${index + 1}. ${families.get(pathFamilyId(route)).label} · ${pathKindLabel(route.kind)} · ${route.source.name} → ${route.sink.name} · ${pathLocation(route.sink.location)} · ${route.step_count} 步` })),
    ...operations.map((item, index) => ({ value: item.id,
      label: `操作 ${index + 1}. ${item.sink.name} · ${pathLocation(item.sink.location)} · 未配对 Source` })),
  ]
})
const pathCounts = computed(() => {
  const counts = paths.data.value?.kind_counts || {}
  return `值传播 ${counts.propagation_path || 0} · 结构线索 ${counts.structural_lead || 0} · 操作线索 ${counts.operation_lead || 0}`
    + ` · 未配对操作 ${paths.data.value?.operations?.length || 0}`
})
const hasLimit = computed(() => paths.data.value && (
  !paths.data.value.completed || paths.data.value.analysis_truncated || paths.data.value.graph.summary.truncated
  || paths.data.value.unavailable_candidates || paths.data.value.unavailable_dataflows
  || paths.data.value.scan_failure_count || paths.data.value.parse_failure_count
  || paths.data.value.unsupported_languages.length || paths.data.value.paths.some((route) => route.projection_missing || route.path_status === 'incomplete')
  || paths.data.value.operations?.some((operation) => operation.projection_missing)
))
const coverageDescription = computed(() => {
  const data = paths.data.value
  return data ? `分析${data.completed ? '已完成（仍为有界分析）' : '未完成'}；尝试扫描 ${data.files_scanned}/${data.files_considered} 文件；`
    + `保存候选 ${data.candidate_count}，候选之外的已保存值流 ${data.additional_dataflow_count}；引用缺失候选/值流 ${data.unavailable_candidates}/${data.unavailable_dataflows}；`
    + `未配对 Sink ${data.unpaired_sink_count}（独立操作标记，不建立传播连线）；扫描诊断 ${data.scan_failure_count}，其中解析失败计数 ${data.parse_failure_count}；`
    + `不支持的语言：${data.unsupported_languages.join('、') || '未记录'}；同一函数内传播在详情中展开，不画自调用边。` : '尚未读取分析产物。'
})
const emptyDescription = computed(() => !paths.data.value?.available
  ? '该项目没有保存静态安全证据，请重新分析项目'
  : !paths.data.value.completed ? '静态安全分析未完成，暂无可显示路径'
    : '已保存事实中没有可显示路径或操作；零结果不证明项目安全')
</script>

<style scoped>
.explore-workspace {
  display: grid;
  grid-template-columns: minmax(230px, 280px) minmax(460px, 1fr) minmax(250px, 310px);
  gap: 12px;
  height: 100%;
  min-height: 0;
}
.network-card, .explore-workspace > :deep(.tree-card), .explore-workspace > :deep(.detail-card) { height: 100%; min-height: 0; }
.explore-workspace > :deep(.tree-card), .explore-workspace > :deep(.detail-card) { display: flex; flex-direction: column; }
.network-card { min-width: 0; overflow: hidden; display: flex; flex-direction: column; }
.network-card :deep(.el-card__body) { flex: 1; min-height: 0; overflow: hidden; display: flex; flex-direction: column; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.path-toolbar { display: flex; gap: 8px; padding: 10px 12px 4px; flex-shrink: 0; }
.path-toolbar :deep(.el-select-v2) { flex: 1; min-width: 0; }
.path-notice { padding: 4px 12px 8px; font-size: 12px; color: #909399; flex-shrink: 0; }
.path-canvas { flex: 1; min-height: 0; }
.path-placeholder { margin: auto; padding: 20px; color: #909399; }
.warning { color: #b36b00; }
.explore-workspace > :deep(.tree-card .el-card__body), .explore-workspace > :deep(.detail-card .el-card__body) {
  flex: 1;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  box-sizing: border-box;
  scrollbar-gutter: stable;
}
.explore-workspace > :deep(.detail-card .el-card__header) { min-width: 0; }
@media (max-width: 1180px) {
  .explore-workspace { grid-template-columns: minmax(220px, 270px) minmax(0, 1fr); }
  .explore-workspace > :deep(.detail-card) { display: none; }
}
@media (max-width: 760px) {
  .explore-workspace { grid-template-columns: 1fr; height: auto; }
  .explore-workspace > :deep(.tree-card), .network-card { height: 560px; }
}
</style>
