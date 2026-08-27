import { computed, onBeforeUnmount, ref } from 'vue'
import {
  analyzeGitProject,
  analyzeZipProject,
  deleteProject,
  generateProjectOverview,
  getModelStatus,
} from '../../../services/projectApi.js'

const emptyGraph = () => ({ schema_version: '1.0', nodes: [], edges: [], warnings: [] })
const emptyOverview = () => ({ content: '', source: 'static' })

const normalizeAnalysis = (data) => {
  if (!data?.project_id || !Array.isArray(data.file_tree) || !Array.isArray(data.dependency_graph?.nodes)
      || !Array.isArray(data.dependency_graph?.edges)) {
    throw new Error('项目分析响应缺少必要字段')
  }
  return {
    projectId: data.project_id,
    fileTree: data.file_tree,
    dependencyGraph: data.dependency_graph,
    manifest: data.project_manifest || null,
    overview: data.project_overview || emptyOverview(),
    sanitizeReport: data.sanitize_report || {},
  }
}

/** Own the project-analysis request lifecycle and atomically replace successful project state. */
export const useProjectAnalysis = () => {
  const status = ref('idle')
  const currentProjectId = ref('')
  const fileTree = ref([])
  const dependencyGraph = ref(emptyGraph())
  const projectManifest = ref(null)
  const projectOverview = ref(emptyOverview())
  const sanitizeReport = ref({})
  const modelStatus = ref({ configured: false, provider: null, model: null })
  let activeController = null
  let requestSequence = 0

  const hasProject = computed(() => Boolean(currentProjectId.value))
  const importing = computed(() => status.value === 'importing')
  const overviewLoading = computed(() => status.value === 'generating_overview')
  const deleting = computed(() => status.value === 'deleting')

  const replaceProject = (next) => {
    currentProjectId.value = next.projectId
    fileTree.value = next.fileTree
    dependencyGraph.value = next.dependencyGraph
    projectManifest.value = next.manifest
    projectOverview.value = next.overview
    sanitizeReport.value = next.sanitizeReport
  }

  const analyze = async (request) => {
    if (hasProject.value) throw new Error('请先清空当前项目，再导入新的项目')
    activeController?.abort()
    const controller = new AbortController()
    activeController = controller
    const sequence = ++requestSequence
    status.value = 'importing'
    try {
      const data = await request(controller.signal)
      const next = normalizeAnalysis(data)
      if (sequence !== requestSequence) return null
      replaceProject(next)
      status.value = 'ready'
      return next
    } catch (error) {
      if (sequence === requestSequence) status.value = hasProject.value ? 'ready' : 'error'
      throw error
    } finally {
      if (activeController === controller) activeController = null
    }
  }

  const analyzeGit = (repoUrl) => analyze((signal) => analyzeGitProject(repoUrl, { signal }))
  const analyzeZip = (file) => analyze((signal) => analyzeZipProject(file, { signal }))

  const loadModelStatus = async () => {
    try {
      modelStatus.value = await getModelStatus()
    } catch (_) {
      modelStatus.value = { configured: false, provider: null, model: null }
    }
  }

  const refreshOverview = async () => {
    if (!currentProjectId.value) return null
    status.value = 'generating_overview'
    try {
      projectOverview.value = await generateProjectOverview(currentProjectId.value, {
        use_model: true,
        language: 'zh-CN',
      })
      return projectOverview.value
    } finally {
      status.value = 'ready'
    }
  }

  const clearLocal = () => {
    requestSequence += 1
    activeController?.abort()
    activeController = null
    currentProjectId.value = ''
    fileTree.value = []
    dependencyGraph.value = emptyGraph()
    projectManifest.value = null
    projectOverview.value = emptyOverview()
    sanitizeReport.value = {}
    status.value = 'idle'
  }

  const removeCurrentProject = async () => {
    if (!currentProjectId.value) return null
    status.value = 'deleting'
    try {
      const result = await deleteProject(currentProjectId.value)
      clearLocal()
      return result
    } catch (error) {
      status.value = 'ready'
      throw error
    }
  }

  onBeforeUnmount(() => activeController?.abort())
  return {
    status, currentProjectId, fileTree, dependencyGraph, projectManifest,
    projectOverview, sanitizeReport, modelStatus, hasProject, importing,
    overviewLoading, deleting, analyzeGit, analyzeZip, loadModelStatus,
    refreshOverview, removeCurrentProject, clearLocal,
  }
}