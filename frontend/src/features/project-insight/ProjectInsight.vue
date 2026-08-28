<template>
  <div class="insight-container" :class="{ onboarding: !analysis.hasProject.value }">
    <ProjectImportPanel
      v-if="!analysis.hasProject.value"
      :importing="analysis.importing.value"
      :deleting="false"
      :has-project="false"
      @analyze-git="handleGit"
      @analyze-zip="handleZip"
    />

    <div v-else class="project-shell">
      <ProjectWorkspaceHeader
        :manifest="analysis.projectManifest.value"
        :model-status="analysis.modelStatus.value"
        :graph="analysis.dependencyGraph.value"
        :deleting="analysis.deleting.value"
        @delete="handleReset"
      />

      <div class="workspace-layout">
        <ProjectWorkspaceNav :active="activeWorkspace" @select="activeWorkspace = $event" />
        <main class="workspace-content">
          <KeepAlive :max="5">
            <ProjectOverviewPanel
              v-if="activeWorkspace === 'overview'"
              class="workspace-view overview-workspace"
              :manifest="analysis.projectManifest.value"
              :overview="analysis.projectOverview.value"
              :model-status="analysis.modelStatus.value"
              :loading="analysis.overviewLoading.value"
              @generate="handleOverview"
            />
            <ProjectExploreWorkspace
              v-else-if="activeWorkspace === 'explore'"
              class="workspace-view explore-view"
              :file-tree="analysis.fileTree.value"
              :dependency-graph="analysis.dependencyGraph.value"
            />
            <AgentWorkspace
              v-else-if="activeWorkspace === 'agent'"
              class="workspace-view scroll-workspace"
              :project-id="analysis.currentProjectId.value"
              :model-status="analysis.modelStatus.value"
            />
            <ExperimentComparison
              v-else-if="activeWorkspace === 'experiment'"
              class="workspace-view scroll-workspace"
              :project-id="analysis.currentProjectId.value"
              :model-status="analysis.modelStatus.value"
            />
            <ExecutionWorkspace
              v-else
              class="workspace-view scroll-workspace"
              :project-id="analysis.currentProjectId.value"
            />
          </KeepAlive>
        </main>
      </div>
    </div>
  </div>
</template>

<script setup>
import { defineAsyncComponent, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { apiErrorMessage } from '../../services/httpClient.js'
import ProjectImportPanel from './components/ProjectImportPanel.vue'
import ProjectOverviewPanel from './components/ProjectOverviewPanel.vue'
import ProjectWorkspaceHeader from './components/ProjectWorkspaceHeader.vue'
import ProjectWorkspaceNav from './components/ProjectWorkspaceNav.vue'
import { useProjectAnalysis } from './composables/useProjectAnalysis.js'

const ProjectExploreWorkspace = defineAsyncComponent(() => import('./views/ProjectExploreWorkspace.vue'))
const AgentWorkspace = defineAsyncComponent(() => import('../../components/AgentWorkspace.vue'))
const ExperimentComparison = defineAsyncComponent(() => import('../experiment-comparison/ExperimentComparison.vue'))
const ExecutionWorkspace = defineAsyncComponent(() => import('../execution/ExecutionWorkspace.vue'))

const analysis = useProjectAnalysis()
const activeWorkspace = ref('overview')

const handleGit = async (url) => {
  try {
    const result = await analysis.analyzeGit(url)
    if (result) {
      activeWorkspace.value = 'overview'
      ElMessage.success(
        '项目加载成功，已安全过滤 '
        + String(result.sanitizeReport.filtered_out_files || 0)
        + ' 项内容。',
      )
    }
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, 'Git 项目分析失败'))
  }
}

const handleZip = async (file) => {
  try {
    const result = await analysis.analyzeZip(file)
    if (result) {
      activeWorkspace.value = 'overview'
      ElMessage.success(
        '本地项目加载成功，已安全过滤 '
        + String(result.sanitizeReport.filtered_out_files || 0)
        + ' 项内容。',
      )
    }
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
    activeWorkspace.value = 'overview'
    ElMessage.info('服务器项目资源与本地视图已清理。')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '项目清理失败，请重试'))
  }
}

onMounted(analysis.loadModelStatus)
</script>

<style scoped>
.insight-container {
  width: 100%;
  height: 100vh;
  padding: 14px;
  box-sizing: border-box;
  overflow: hidden;
}
.insight-container.onboarding {
  max-width: 1120px;
  height: auto;
  min-height: 100vh;
  margin: 0 auto;
  overflow: visible;
}
.project-shell { display: flex; flex-direction: column; gap: 12px; height: 100%; min-height: 0; }
.workspace-layout { display: flex; flex: 1; gap: 12px; min-height: 0; }
.workspace-content { flex: 1; min-width: 0; min-height: 0; overflow: hidden; }
.workspace-view { height: 100%; min-height: 0; box-sizing: border-box; }
.scroll-workspace { overflow: auto; }
.overview-workspace { overflow: hidden; }
.overview-workspace :deep(.el-card__body) {
  max-height: calc(100% - 58px);
  overflow: auto;
  box-sizing: border-box;
}
@media (max-width: 900px) {
  .insight-container { height: auto; min-height: 100vh; overflow: visible; }
  .workspace-layout { flex-direction: column; }
  .workspace-content { min-height: 620px; }
}
</style>
