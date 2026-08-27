<template>
  <div class="insight-container">
    <ProjectImportPanel
      :importing="analysis.importing.value"
      :deleting="analysis.deleting.value"
      :has-project="analysis.hasProject.value"
      @analyze-git="handleGit"
      @analyze-zip="handleZip"
      @reset="handleReset"
    />

    <div v-if="analysis.hasProject.value" class="main-content-layout">
      <ProjectFileTree :nodes="analysis.fileTree.value" @select="selectedNode = $event" />
      <div class="right-panel">
        <SymbolOutline :node="selectedNode" :hierarchy="fileHierarchy" />
        <el-card class="network-card" body-style="padding: 0">
          <template #header>
            <div class="card-header">
              <span>🕸️ 全局依赖图谱</span>
              <el-tag size="small" type="info">{{ graphCounts }}</el-tag>
            </div>
          </template>
          <DependencyGraph v-if="analysis.dependencyGraph.value.nodes.length" :graph-data="analysis.dependencyGraph.value" />
          <el-empty v-else description="当前项目没有可显示的依赖节点" :image-size="72" />
        </el-card>
        <ProjectOverviewPanel
          :manifest="analysis.projectManifest.value"
          :overview="analysis.projectOverview.value"
          :model-status="analysis.modelStatus.value"
          :loading="analysis.overviewLoading.value"
          @generate="handleOverview"
        />
        <AgentWorkspace
          v-if="analysis.currentProjectId.value"
          :project-id="analysis.currentProjectId.value"
          :model-status="analysis.modelStatus.value"
        />
        <ExperimentComparison
          v-if="analysis.currentProjectId.value"
          :project-id="analysis.currentProjectId.value"
          :model-status="analysis.modelStatus.value"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { apiErrorMessage } from '../../services/httpClient.js'
import ProjectFileTree from './components/ProjectFileTree.vue'
import ProjectImportPanel from './components/ProjectImportPanel.vue'
import ProjectOverviewPanel from './components/ProjectOverviewPanel.vue'
import SymbolOutline from './components/SymbolOutline.vue'
import { useProjectAnalysis } from './composables/useProjectAnalysis.js'
import { buildSymbolTree } from './domain/symbolTree.js'

const DependencyGraph = defineAsyncComponent(() => import('../dependency-graph/DependencyGraph.vue'))
const AgentWorkspace = defineAsyncComponent(() => import('../../components/AgentWorkspace.vue'))
const ExperimentComparison = defineAsyncComponent(() => import('../experiment-comparison/ExperimentComparison.vue'))
const analysis = useProjectAnalysis()
const selectedNode = ref(null)
const fileHierarchy = computed(() => (
  selectedNode.value && !selectedNode.value.is_dir
    ? buildSymbolTree(selectedNode.value.symbols || [])
    : []
))
const graphCounts = computed(() => (
  `${analysis.dependencyGraph.value.nodes.length} 节点 · ${analysis.dependencyGraph.value.edges.length} 关系`
))

const handleGit = async (url) => {
  try {
    const result = await analysis.analyzeGit(url)
    if (result) ElMessage.success(`项目加载成功，已安全过滤 ${result.sanitizeReport.filtered_out_files || 0} 项内容。`)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, 'Git 项目分析失败'))
  }
}
const handleZip = async (file) => {
  try {
    const result = await analysis.analyzeZip(file)
    if (result) ElMessage.success(`本地项目加载成功，已安全过滤 ${result.sanitizeReport.filtered_out_files || 0} 项内容。`)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, 'ZIP 项目分析失败'))
  }
}
const handleOverview = async () => {
  try {
    const overview = await analysis.refreshOverview()
    ElMessage[overview?.source === 'model' ? 'success' : 'info'](
      overview?.source === 'model' ? 'AI 增强项目概览生成完成' : '已生成确定性项目概览',
    )
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '项目概览生成失败'))
  }
}
const handleReset = async () => {
  try {
    await analysis.removeCurrentProject()
    selectedNode.value = null
    ElMessage.info('服务器项目资源与本地视图已清理。')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '项目清理失败，请重试'))
  }
}

onMounted(analysis.loadModelStatus)
</script>

<style scoped>
.insight-container { padding: 20px; max-width: 1400px; margin: 0 auto; }
.main-content-layout { display: flex; gap: 20px; }
.right-panel { flex: 1; display: flex; flex-direction: column; gap: 20px; min-width: 0; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.network-card :deep(.el-card__body) { padding: 0; }
</style>

