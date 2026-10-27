import { computed, onScopeDispose, ref, shallowRef, watch } from 'vue'
import { getSecurityPaths } from '../../../services/projectApi.js'
import { apiErrorMessage } from '../../../services/httpClient.js'
import { securityGraph } from '../domain/securityPaths.js'

/** 页面只读状态；取消及序列校验共同阻止删除、换项目和快速选路后的迟到响应。 */
export const useSecurityPaths = (projectId) => {
  const data = shallowRef(null)
  const loading = ref(false)
  const error = ref('')
  const selectedId = ref('')
  const detail = shallowRef(null)
  const detailLoading = ref(false)
  const detailError = ref('')
  let overviewController
  let detailController
  let version = 0
  let detailVersion = 0

  const reload = async () => {
    const current = ++version
    overviewController?.abort()
    detailController?.abort()
    ++detailVersion
    selectedId.value = ''
    data.value = detail.value = null
    error.value = detailError.value = ''
    detailLoading.value = false
    loading.value = Boolean(projectId.value)
    if (!projectId.value) return
    const controller = overviewController = new AbortController()
    try {
      const result = await getSecurityPaths(projectId.value, { signal: controller.signal })
      if (current !== version || controller.signal.aborted) return
      if (!Array.isArray(result?.paths) || !Array.isArray(result?.graph?.nodes) || !Array.isArray(result?.graph?.edges)
        || (result.operations !== undefined && !Array.isArray(result.operations))) {
        throw new Error('静态路径响应格式不正确')
      }
      data.value = result
    } catch (cause) {
      if (current === version && !controller.signal.aborted) error.value = apiErrorMessage(cause, '静态路径读取失败')
    } finally {
      if (current === version) loading.value = false
    }
  }
  const loadDetail = async () => {
    const current = ++detailVersion
    detailController?.abort()
    detail.value = null
    detailError.value = ''
    detailLoading.value = Boolean(selectedId.value)
    if (!selectedId.value || !projectId.value) return
    const controller = detailController = new AbortController()
    try {
      const result = await getSecurityPaths(projectId.value, { candidateId: selectedId.value, signal: controller.signal })
      if (current === detailVersion && !controller.signal.aborted) detail.value = result
    } catch (cause) {
      if (current === detailVersion && !controller.signal.aborted) detailError.value = apiErrorMessage(cause, '路径详情读取失败')
    } finally {
      if (current === detailVersion) detailLoading.value = false
    }
  }
  watch(projectId, reload, { immediate: true })
  watch(selectedId, loadDetail)
  onScopeDispose(() => { ++version; ++detailVersion; overviewController?.abort(); detailController?.abort() })
  return {
    data, loading, error, selectedId, detail, detailLoading, detailError, reload, loadDetail,
    selected: computed(() => data.value?.paths.find((item) => item.id === selectedId.value)
      || data.value?.operations?.find((item) => item.id === selectedId.value)),
    graphData: computed(() => securityGraph(data.value, selectedId.value)),
  }
}
