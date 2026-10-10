import { computed, onBeforeUnmount, ref } from 'vue'
import {
  analyzeGitProject,
  analyzeZipProject,
  createAnalysisProgress,
  getAnalysisProgress,
  deleteProject,
  generateProjectOverview,
} from '../../../services/projectApi.js'
import { getModelStatus } from '../../../services/modelApi.js'
import { pollImportProgress } from '../utils/importProgress.js'
import { apiErrorMessage } from '../../../services/httpClient.js'

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
  const importProgress = ref(null)
  let activeController = null
  let stopProgressPolling = null
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
    if (importing.value) throw new Error('项目正在导入，请等待当前分析完成')
    activeController?.abort()
    const controller = new AbortController()
    activeController = controller
    const sequence = ++requestSequence
    status.value = 'importing'
    importProgress.value = { status: 'pending', stage: 'connecting', stage_label: '准备进度查询', elapsed_seconds: 0 }
    try {
      const { request_id: requestId } = await createAnalysisProgress({ signal: controller.signal })
      if (sequence !== requestSequence || controller.signal.aborted) return null
      importProgress.value = { ...importProgress.value, stage: 'uploading', stage_label: '提交项目，等待服务器准备源码' }
      stopProgressPolling = pollImportProgress({
        requestId,
        getProgress: getAnalysisProgress,
        onProgress: (progress) => {
          if (sequence !== requestSequence) return
          // multipart 上传完成前后端尚未进入路由，保留浏览器侧真实上传百分比。
          importProgress.value = progress.status === 'pending'
            ? { ...importProgress.value, elapsed_seconds: progress.elapsed_seconds, polling_warning: '' }
            : { ...progress, polling_warning: '' }
        },
        onError: () => {
          if (sequence === requestSequence) importProgress.value = {
            ...importProgress.value, polling_warning: '暂时无法读取进度，仍在等待分析结果；将自动重试。',
          }
        },
      })
      const data = await request({
        signal: controller.signal,
        requestId,
        onUploadProgress: ({ loaded, total }) => {
          if (sequence !== requestSequence || importProgress.value?.stage !== 'uploading') return
          importProgress.value = {
            ...importProgress.value,
            upload_percent: total ? Math.min(100, Math.round(loaded / total * 100)) : null,
            stage_label: total && loaded >= total ? '上传完成，等待服务器准备源码' : '正在上传项目压缩包',
          }
        },
      })
      const next = normalizeAnalysis(data)
      if (sequence !== requestSequence) return null
      replaceProject(next)
      status.value = 'ready'
      return next
    } catch (error) {
      if (sequence === requestSequence) {
        status.value = hasProject.value ? 'ready' : 'error'
        importProgress.value = {
          ...importProgress.value, status: 'failed', stage_label: '项目导入或分析失败',
          message: apiErrorMessage(error),
        }
      }
      throw error
    } finally {
      if (activeController === controller) {
        stopProgressPolling?.()
        stopProgressPolling = null
        activeController = null
      }
    }
  }

  const analyzeGit = (repoUrl) => analyze((options) => analyzeGitProject(repoUrl, options))
  const analyzeZip = (file) => analyze((options) => analyzeZipProject(file, options))

  const restoreProjectSnapshot = (data) => {
    const next = normalizeAnalysis(data)
    replaceProject(next)
    status.value = 'ready'
    return next
  }

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
    stopProgressPolling?.()
    stopProgressPolling = null
    activeController = null
    currentProjectId.value = ''
    fileTree.value = []
    dependencyGraph.value = emptyGraph()
    projectManifest.value = null
    projectOverview.value = emptyOverview()
    sanitizeReport.value = {}
    importProgress.value = null
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

  onBeforeUnmount(() => {
    requestSequence += 1
    activeController?.abort()
    stopProgressPolling?.()
  })
  return {
    status, currentProjectId, fileTree, dependencyGraph, projectManifest,
    projectOverview, sanitizeReport, modelStatus, importProgress, hasProject, importing,
    overviewLoading, deleting, analyzeGit, analyzeZip, restoreProjectSnapshot, loadModelStatus,
    refreshOverview, removeCurrentProject, clearLocal,
  }
}
