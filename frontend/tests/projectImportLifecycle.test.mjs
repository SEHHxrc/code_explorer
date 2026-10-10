import assert from 'node:assert/strict'
import test from 'node:test'
import { setTimeout as delay } from 'node:timers/promises'
import { createRenderer } from 'vue'
import { apiClient } from '../src/services/httpClient.js'
import { useProjectAnalysis } from '../src/features/project-insight/composables/useProjectAnalysis.js'

const mountAnalysis = () => {
  let analysis
  const renderer = createRenderer({
    createComment: () => ({}), insert: () => {}, remove: () => {}, parentNode: () => null,
    nextSibling: () => null,
  })
  const app = renderer.createApp({
    setup: () => { analysis = useProjectAnalysis(); return () => null },
  })
  app.mount({})
  return { analysis, unmount: () => app.unmount() }
}

const response = (config, data) => ({ config, status: 200, headers: {}, data: { code: 200, data } })
const projectResult = { project_id: 'project-1', file_tree: [], dependency_graph: { nodes: [], edges: [] } }

test('ZIP import binds progress ID, shows upload and server counts, and releases polling', async () => {
  const originalAdapter = apiClient.defaults.adapter
  const { analysis, unmount } = mountAnalysis()
  let resolvePost
  let postConfig
  let lastPollSignal
  let polls = 0
  apiClient.defaults.adapter = async (config) => {
    if (config.url.endsWith('/analysis-progress')) return response(config, { request_id: 'request-1' })
    if (config.method === 'get') {
      polls++
      lastPollSignal = config.signal
      return response(config, polls === 1
        ? { status: 'pending', elapsed_seconds: 1 }
        : { status: 'running', stage: 'dependency', stage_label: '解析源码', total_files: 20, processed_files: 7 })
    }
    postConfig = config
    assert.equal(config.data.get('request_id'), 'request-1')
    config.onUploadProgress({ loaded: 50, total: 100 })
    return new Promise((resolve) => { resolvePost = () => resolve(response(config, projectResult)) })
  }
  try {
    const pending = analysis.analyzeZip(new File(['demo'], 'demo.zip'))
    await delay(30)
    assert.equal(analysis.importing.value, true)
    assert.equal(analysis.importProgress.value.upload_percent, 50)
    await delay(1100)
    assert.equal(analysis.importProgress.value.processed_files, 7)
    resolvePost()
    assert.equal((await pending).projectId, 'project-1')
    assert.equal(analysis.status.value, 'ready')
    assert.equal(lastPollSignal.aborted, true)
    assert.equal(postConfig.signal.aborted, false)
  } finally {
    unmount()
    apiClient.defaults.adapter = originalAdapter
  }
})

test('unmount stops polling and a late analysis response cannot restore cleared state', async () => {
  const originalAdapter = apiClient.defaults.adapter
  const { analysis, unmount } = mountAnalysis()
  let resolvePost
  let pollSignal
  let postSignal
  apiClient.defaults.adapter = async (config) => {
    if (config.url.endsWith('/analysis-progress')) return response(config, { request_id: 'request-2' })
    if (config.method === 'get') {
      pollSignal = config.signal
      return response(config, { status: 'running', stage: 'dataflow', stage_label: '污点传播' })
    }
    postSignal = config.signal
    return new Promise((resolve) => { resolvePost = () => resolve(response(config, projectResult)) })
  }
  try {
    const pending = analysis.analyzeGit('https://example.test/repo').catch((error) => error)
    await delay(30)
    unmount()
    assert.equal(pollSignal.aborted, true)
    assert.equal(postSignal.aborted, true)
    resolvePost()
    await pending
    assert.equal(analysis.currentProjectId.value, '')
  } finally { apiClient.defaults.adapter = originalAdapter }
})

test('failed import shows the public error, stops polling and permits a fresh retry', async () => {
  const originalAdapter = apiClient.defaults.adapter
  const { analysis, unmount } = mountAnalysis()
  let requests = 0
  let pollSignal
  apiClient.defaults.adapter = async (config) => {
    if (config.url.endsWith('/analysis-progress')) return response(config, { request_id: `request-${++requests}` })
    if (config.method === 'get') {
      pollSignal = config.signal
      return response(config, { status: 'running', stage: 'preparing', stage_label: '准备源码' })
    }
    if (requests === 1) {
      await delay(5)
      throw Object.assign(new Error('Request failed'), { response: { data: { detail: '压缩包无法安全解压' } } })
    }
    return response(config, projectResult)
  }
  try {
    await assert.rejects(analysis.analyzeGit('https://example.test/repo'))
    assert.equal(analysis.status.value, 'error')
    assert.equal(analysis.importProgress.value.message, '压缩包无法安全解压')
    assert.equal(pollSignal.aborted, true)
    assert.equal((await analysis.analyzeGit('https://example.test/repo')).projectId, 'project-1')
    assert.equal(analysis.status.value, 'ready')
    assert.equal(requests, 2)
  } finally {
    unmount()
    apiClient.defaults.adapter = originalAdapter
  }
})
