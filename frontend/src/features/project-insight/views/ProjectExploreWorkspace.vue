<template>
  <div class="explore-workspace">
    <ProjectFileTree :nodes="fileTree" @select="selectedNode = $event" />
    <el-card class="network-card" body-style="padding: 0">
      <template #header>
        <div class="card-header">
          <span>🕸️ 全局依赖图谱</span>
          <el-tag size="small" type="info">{{ graphCounts }}</el-tag>
        </div>
      </template>
      <DependencyGraph v-if="dependencyGraph.nodes?.length" ref="graphRef" :graph-data="dependencyGraph" />
      <el-empty v-else description="当前项目没有可显示的依赖节点" :image-size="72" />
    </el-card>
    <SymbolOutline :node="selectedNode" :hierarchy="fileHierarchy" @locate="locateSymbol" />
  </div>
</template>

<script setup>
import { computed, defineAsyncComponent, ref } from 'vue'
import ProjectFileTree from '../components/ProjectFileTree.vue'
import SymbolOutline from '../components/SymbolOutline.vue'
import { buildSymbolTree } from '../domain/symbolTree.js'

const DependencyGraph = defineAsyncComponent(() => import('../../dependency-graph/DependencyGraph.vue'))
const props = defineProps({
  fileTree: { type: Array, default: () => [] },
  dependencyGraph: { type: Object, default: () => ({ nodes: [], edges: [] }) },
})
const selectedNode = ref(null)
const graphRef = ref(null)
const locateSymbol = (target) => graphRef.value?.revealSymbol(target)
const fileHierarchy = computed(() => (
  selectedNode.value && !selectedNode.value.is_dir
    ? buildSymbolTree(selectedNode.value.symbols || [])
    : []
))
const graphCounts = computed(() => (
  String(props.dependencyGraph.nodes?.length || 0)
  + ' 节点 · '
  + String(props.dependencyGraph.edges?.length || 0)
  + ' 关系'
))
</script>

<style scoped>
.explore-workspace {
  display: grid;
  grid-template-columns: minmax(230px, 280px) minmax(460px, 1fr) minmax(250px, 310px);
  gap: 12px;
  height: 100%;
  min-height: 0;
}
.network-card, :deep(.tree-card), :deep(.detail-card) { height: 100%; min-height: 0; }
:deep(.tree-card), :deep(.detail-card) { display: flex; flex-direction: column; }
.network-card { min-width: 0; overflow: hidden; }
.network-card :deep(.el-card__body) { height: calc(100% - 57px); min-height: 0; overflow: hidden; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
:deep(.tree-card .el-card__body), :deep(.detail-card .el-card__body) {
  flex: 1;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  box-sizing: border-box;
  scrollbar-gutter: stable;
}
:deep(.detail-card .el-card__header) { min-width: 0; }
@media (max-width: 1180px) {
  .explore-workspace { grid-template-columns: minmax(220px, 270px) minmax(0, 1fr); }
  :deep(.detail-card) { display: none; }
}
@media (max-width: 760px) {
  .explore-workspace { grid-template-columns: 1fr; height: auto; }
  :deep(.tree-card), .network-card { height: 560px; }
}
</style>
