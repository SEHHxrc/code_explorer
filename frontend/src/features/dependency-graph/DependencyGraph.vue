<template>
  <div class="graph-shell">
    <GraphControls
      :search-text="searchText"
      :search-match-count="searchMatches.length"
      :presets="graphData.pathFamilies ? [] : VIEW_PRESETS"
      :path-mode="Boolean(graphData.pathFamilies)"
      :view-preset="viewPreset"
      :node-levels="NODE_LEVELS"
      :edge-relations="EDGE_RELATIONS"
      :active-levels="activeLevels"
      :active-relations="activeRelations"
      :level-counts="summary.levelCounts"
      :relation-counts="summary.relationCounts"
      :min-degree="minDegree"
      :degree-slider-max="degreeSliderMax"
      :hidden-count="manuallyHidden.size"
      :ready="ready"
      :layout-running="layout.running.value"
      @update:search-text="onSearchInput"
      @focus-search="focusSearch"
      @preset="applyPreset"
      @toggle-level="toggleLevel"
      @toggle-all-levels="toggleAllLevels"
      @toggle-relation="toggleRelation"
      @toggle-all-relations="toggleAllRelations"
      @degree="setDegree"
      @restore-hidden="restoreHiddenNodes"
      @restart-layout="layout.restart"
      @toggle-layout="layout.toggle"
    />

    <div class="canvas-wrap">
      <div ref="containerRef" class="sigma-canvas" title="按住节点可拖拽并固定位置" />
      <div v-if="!ready" class="overlay-tip">暂无图谱数据 —— 请先分析一个项目</div>
      <div v-else-if="summary.visibleNodeCount === 0" class="overlay-tip">当前筛选条件下没有可显示的节点</div>

      <transition name="fade">
        <div v-if="layout.running.value" class="layout-badge"><span class="dot" />力导向布局计算中…</div>
      </transition>

      <div v-if="sigma.hovered.value" class="hover-chip" :style="hoverChipStyle">
        <span class="chip-dot" :style="{ background:sigma.hovered.value.color }" />
        <span class="chip-name">{{ sigma.hovered.value.label }}</span>
        <span class="chip-kind">{{ sigma.hovered.value.kind }}</span>
        <span v-if="sigma.hovered.value.pinned" class="chip-kind">· 已固定</span>
      </div>

      <div class="zoom-controls">
        <el-tooltip content="放大" placement="left"><button class="zbtn" @click="sigma.zoom(1 / 1.4)">＋</button></el-tooltip>
        <el-tooltip content="缩小" placement="left"><button class="zbtn" @click="sigma.zoom(1.4)">－</button></el-tooltip>
        <el-tooltip content="适配窗口" placement="left"><button class="zbtn" @click="resetCamera">⤢</button></el-tooltip>
        <el-tooltip v-if="selectedNode || selectedEdge" content="取消选中" placement="left">
          <button class="zbtn active" @click="clearSelection">✕</button>
        </el-tooltip>
      </div>

      <div class="legend">
        <div class="legend-title">{{ graphData.pathFamilies ? '颜色：路径族 · 箭头：调用方向' : '节点类型 · 箭头：关系方向' }}</div>
        <div v-for="item in visibleLegend" :key="item.id || item.key" class="legend-item" :title="item.title || item.label">
          <span class="swatch" :style="{ background:item.color }" />{{ item.label }}
        </div>
        <div v-if="graphData.hasSharedFamilies" class="legend-item" title="同一节点或边属于多个路径族；选择单条路线后使用所属族颜色">
          <span class="swatch" :style="{ background:SHARED_PATH_COLOR }" />跨族共享
        </div>
        <div v-if="graphData.possibleCount" class="legend-item" title="动态派发或启发式候选目标；实线也不代表值传播已闭合或运行时必然执行">
          <span style="width: 14px; border-top: 2px dashed currentColor" />候选目标（{{ graphData.possibleCount }}）
        </div>
        <div v-if="graphData.operationCount" class="legend-item" title="仅有敏感操作事实，未建立 Source 到该操作的传播路线；不属于路径族">
          <span class="swatch" :style="{ background:UNPAIRED_OPERATION_COLOR }" />未配对操作（非路径族）
        </div>
        <div v-if="graphData.structureCount" class="legend-item" title="已有调用结构，不代表值传播已建立">
          <span class="swatch" style="background:#94a3b8" />灰细箭头：仅结构关系
        </div>
        <div v-if="graphData.boundaryCount" class="legend-more">{{ graphData.boundaryCount }} 处传播边界，可点节点查看</div>
        <div v-if="remainingFamilies" class="legend-more">另 {{ remainingFamilies }} 族，可选路查看</div>
      </div>

      <div class="stat-bar">
        <span>{{ summary.visibleNodeCount }} / {{ summary.nodeCount }} 节点</span><span class="sep">·</span>
        <span>{{ summary.visibleEdgeCount }} / {{ summary.edgeCount }} 关系</span><span class="sep">·</span>
        <span>可拖拽节点</span>
      </div>

      <GraphInspector
        :node="selectedNode"
        :edge="selectedEdge"
        :outgoing="outgoing"
        :incoming="incoming"
        :focus-mode="focusMode"
        @clear="clearSelection"
        @toggle-focus="toggleFocusMode"
        @hide-node="hideSelectedNode"
        @select-node="selectNode"
      />
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import GraphControls from './components/GraphControls.vue'
import GraphInspector from './components/GraphInspector.vue'
import { useGraphLayout } from './composables/useGraphLayout.js'
import { useGraphModel, VIEW_PRESETS } from './composables/useGraphModel.js'
import { resolveSymbolNodeId } from './domain/graphModel.js'
import { useSigmaRenderer } from './composables/useSigmaRenderer.js'
import { observeGraphSize } from './composables/observeGraphSize.js'
import { EDGE_RELATIONS, NODE_LEVELS, SHARED_PATH_COLOR, UNPAIRED_OPERATION_COLOR } from './graphStyle.js'

const props = defineProps({ graphData: { type: Object, default: () => ({ nodes: [], edges: [] }) } })
const emit = defineEmits(['select', 'select-edge'])

const containerRef = ref(null)
const ready = ref(false)
const searchText = ref('')
const searchMatches = ref([])
const selectedNode = ref(null)
const selectedEdge = ref(null)
const outgoing = ref([])
const incoming = ref([])
const focusMode = ref(false)

const renderState = {
  selected: null,
  selectedEdge: null,
  edgeEndpoints: new Set(),
  neighbors: new Set(),
  matches: new Set(),
  layoutActive: false,
  visibleEdges: 0,
}

const model = useGraphModel()
const {
  graph, displayGraph, summary, activeLevels, activeRelations, manuallyHidden,
  minDegree, viewPreset,
} = model

let layout
const sigma = useSigmaRenderer({
  containerRef,
  fullGraph: graph,
  displayGraph,
  renderState,
  onNode: (id) => selectNode(id),
  onEdge: (id) => selectEdge(id),
  onStage: () => clearSelection(),
  onDragStart: () => layout?.stop(),
})
layout = useGraphLayout({
  fullGraph: graph,
  displayGraph,
  renderer: sigma.renderer,
  renderState,
  onSettled: () => resetCamera(),
})

const degreeSliderMax = computed(() => Math.max(1, Math.min(summary.maxDegree, 30)))
const visibleLegend = computed(() => props.graphData.pathFamilies?.slice(0, 6)
  || NODE_LEVELS.filter((level) => (summary.levelCounts[level.key] || 0) > 0))
const remainingFamilies = computed(() => Math.max(0, (props.graphData.pathFamilies?.length || 0) - 6))
const hoverChipStyle = computed(() => ({ left: `${sigma.hoverPos.x}px`, top: `${sigma.hoverPos.y}px` }))

const resetRenderSelection = () => {
  renderState.selected = null
  renderState.selectedEdge = null
  renderState.edgeEndpoints = new Set()
  renderState.neighbors = new Set()
  selectedNode.value = null
  selectedEdge.value = null
  outgoing.value = []
  incoming.value = []
}

const refreshRelations = () => {
  renderState.neighbors = new Set()
  if (!renderState.selected || !graph.value?.hasNode(renderState.selected)) return
  graph.value.forEachNeighbor(renderState.selected, (id) => {
    if (!graph.value.getNodeAttribute(id, 'hidden')) renderState.neighbors.add(id)
  })
  const relations = model.relationsForNode(renderState.selected)
  outgoing.value = relations.outgoing
  incoming.value = relations.incoming
}

const applyVisibility = () => {
  model.applyVisibility({ selectedId: renderState.selected, focusMode: focusMode.value })
  renderState.visibleEdges = summary.visibleEdgeCount
  refreshRelations()
  sigma.setGraph()
}

const relayoutVisibleGraph = () => {
  layout.stop()
  applyVisibility()
  layout.start()
}

const selectNode = (id) => {
  const view = model.node(id)
  if (!view) return
  renderState.selected = id
  renderState.selectedEdge = null
  renderState.edgeEndpoints = new Set()
  renderState.matches = new Set()
  selectedNode.value = view
  selectedEdge.value = null
  searchMatches.value = []
  refreshRelations()
  if (focusMode.value) relayoutVisibleGraph()
  else sigma.refresh()
  sigma.focusNode(id)
  emit('select', { id, label: view.label, kind: view.kind, file: view.file, line: view.line })
}

const selectEdge = (id) => {
  if (!graph.value?.hasEdge(id) || graph.value.getEdgeAttribute(id, 'hidden')) return
  const view = model.edge(id)
  if (!view) return
  renderState.selected = null
  renderState.selectedEdge = id
  renderState.edgeEndpoints = new Set([view.source.id, view.target.id])
  renderState.matches = new Set()
  selectedNode.value = null
  selectedEdge.value = view
  outgoing.value = []
  incoming.value = []
  searchMatches.value = []
  sigma.focusEdge(view.source.id, view.target.id)
  sigma.refresh()
  emit('select-edge', view)
}

const clearSelection = () => {
  const restoreFocus = focusMode.value
  focusMode.value = false
  resetRenderSelection()
  if (restoreFocus) relayoutVisibleGraph()
  else sigma.refresh()
}

const applyPreset = (key) => {
  focusMode.value = false
  model.configurePreset(key)
  relayoutVisibleGraph()
}
const setCustom = () => { viewPreset.value = 'custom'; relayoutVisibleGraph() }
const toggleLevel = (key) => { activeLevels.has(key) ? activeLevels.delete(key) : activeLevels.add(key); setCustom() }
const toggleAllLevels = () => {
  if (activeLevels.size === NODE_LEVELS.length) activeLevels.clear()
  else NODE_LEVELS.forEach((item) => activeLevels.add(item.key))
  setCustom()
}
const toggleRelation = (key) => { activeRelations.has(key) ? activeRelations.delete(key) : activeRelations.add(key); setCustom() }
const toggleAllRelations = () => {
  if (activeRelations.size === EDGE_RELATIONS.length) activeRelations.clear()
  else EDGE_RELATIONS.forEach((item) => activeRelations.add(item.key))
  setCustom()
}
const setDegree = (value) => { minDegree.value = Number(value) || 0; setCustom() }
const toggleFocusMode = () => {
  if (!renderState.selected) return
  focusMode.value = !focusMode.value
  relayoutVisibleGraph()
}
const hideSelectedNode = () => {
  if (!renderState.selected) return
  model.hideNode(renderState.selected)
  focusMode.value = false
  resetRenderSelection()
  relayoutVisibleGraph()
}
const restoreHiddenNodes = () => { model.restoreHidden(); relayoutVisibleGraph() }

let searchTimer = null
const onSearchInput = (value) => {
  searchText.value = value || ''
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    const hits = model.search(searchText.value)
    renderState.matches = new Set(hits)
    resetRenderSelection()
    renderState.matches = new Set(hits)
    searchMatches.value = hits
    sigma.refresh()
  }, 180)
}
const focusSearch = () => { if (searchMatches.value.length) selectNode(searchMatches.value[0]) }
const resetCamera = () => sigma.resetCamera(summary.visibleNodeCount)

const revealSymbol = async (target) => {
  const id = resolveSymbolNodeId(props.graphData?.nodes || [], target)
  if (!id || !graph.value?.hasNode(id)) return false

  layout.stop()
  focusMode.value = false
  searchText.value = ''
  searchMatches.value = []
  renderState.matches = new Set()
  resetRenderSelection()
  model.restoreHidden()
  model.configurePreset('all')
  applyVisibility()
  await nextTick()
  selectNode(id)
  return true
}

defineExpose({ revealSymbol })

const destroyAll = () => {
  layout.destroy()
  sigma.destroy()
  model.destroy()
}

let buildGeneration = 0
let active = true
const rebuild = async () => {
  const generation = ++buildGeneration
  destroyAll()
  resetRenderSelection()
  searchText.value = ''
  searchMatches.value = []
  renderState.matches = new Set()
  focusMode.value = false
  if (!(props.graphData?.nodes || []).length) {
    ready.value = false
    Object.assign(summary, { nodeCount: 0, edgeCount: 0, visibleNodeCount: 0, visibleEdgeCount: 0 })
    return
  }
  model.build(props.graphData)
  applyVisibility()
  ready.value = true
  await nextTick()
  if (generation !== buildGeneration || !active || !containerRef.value) return
  sigma.create()
  if (sigma.renderer.value) layout.start()
}

let stopObservingSize = () => {}
const observeContainer = () => {
  stopObservingSize()
  stopObservingSize = observeGraphSize(containerRef.value, sigma.resizeViewport)
}
watch(() => props.graphData, rebuild)
let resumeLayoutOnActivate = false
onMounted(() => {
  rebuild()
  observeContainer()
})
onDeactivated(() => {
  active = false
  stopObservingSize()
  resumeLayoutOnActivate = layout.running.value
  layout.destroy()
  sigma.destroy({ preserveCamera: true })
})
onActivated(async () => {
  active = true
  await nextTick()
  if (!active) return
  observeContainer()
  if (ready.value && !sigma.renderer.value) {
    sigma.create()
  }
  sigma.resizeViewport()
  if (resumeLayoutOnActivate) {
    resumeLayoutOnActivate = false
    layout.start()
  }
})
onUnmounted(() => {
  active = false
  ++buildGeneration
  stopObservingSize()
  clearTimeout(searchTimer)
  destroyAll()
})
</script>

<style scoped>
.graph-shell { display:flex; flex-direction:column; width:100%; height:100%; min-width:0; min-height:0; overflow:hidden; background:#fff; }
.canvas-wrap { position:relative; flex:1; min-width:0; min-height:0; overflow:hidden; }
.sigma-canvas { position:absolute; inset:0; overflow:hidden; overscroll-behavior:none; touch-action:none; cursor:grab; background:radial-gradient(circle at 50% 42%,#fff 0%,#f8fafc 62%,#f2f5f9 100%); }
.sigma-canvas :deep(canvas) { display:block; }
.sigma-canvas:active { cursor:grabbing; }
.overlay-tip { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; color:#909399; font-size:13px; pointer-events:none; }
.layout-badge { position:absolute; top:12px; left:50%; transform:translateX(-50%); display:flex; align-items:center; gap:8px; padding:5px 12px; font-size:12px; color:#606266; background:rgba(255,255,255,.94); border:1px solid #e4e7ed; border-radius:999px; box-shadow:0 2px 8px rgba(31,45,61,.06); }
.layout-badge .dot { width:7px; height:7px; border-radius:50%; background:#0891b2; animation:pulse 1.1s ease-in-out infinite; }
@keyframes pulse { 0%,100% { opacity:.3 } 50% { opacity:1 } }
.hover-chip { position:absolute; transform:translate(-50%,-100%); display:flex; align-items:center; gap:6px; padding:4px 9px; font-size:12px; background:rgba(255,255,255,.97); border:1px solid #dcdfe6; border-radius:6px; box-shadow:0 2px 10px rgba(31,45,61,.1); color:#303133; pointer-events:none; white-space:nowrap; z-index:6; }
.chip-dot { width:8px; height:8px; border-radius:50%; }.chip-name { font-family:ui-monospace,Consolas,monospace; }.chip-kind { color:#909399; font-size:11px; }
.zoom-controls { position:absolute; right:12px; bottom:12px; display:flex; flex-direction:column; gap:6px; }
.zbtn { width:32px; height:32px; display:flex; align-items:center; justify-content:center; background:rgba(255,255,255,.96); border:1px solid #dcdfe6; border-radius:6px; color:#606266; font-size:15px; cursor:pointer; box-shadow:0 1px 4px rgba(31,45,61,.06); }
.zbtn:hover { background:#ecf5ff; border-color:#a0cfff; color:#409eff; }.zbtn.active { border-color:#b88ef5; color:#7c4dff; }
.legend { position:absolute; left:12px; bottom:12px; display:flex; flex-wrap:wrap; gap:4px 12px; max-width:46%; padding:8px 10px; background:rgba(255,255,255,.92); border:1px solid #ebeef5; border-radius:6px; }
.legend-item { display:flex; align-items:center; gap:5px; font-size:11px; color:#606266; }.swatch { width:10px; height:10px; border-radius:50%; flex-shrink:0; }
.legend-title { width:100%; font-size:11px; color:#475569; margin-bottom:2px; }
.legend-more { font-size:11px; color:#909399; }
.stat-bar { position:absolute; top:12px; left:12px; display:flex; gap:6px; padding:4px 10px; font-size:11px; color:#909399; background:rgba(255,255,255,.92); border:1px solid #ebeef5; border-radius:999px; }.stat-bar .sep { color:#c0c4cc; }
.fade-enter-active,.fade-leave-active { transition:opacity .25s; }.fade-enter-from,.fade-leave-to { opacity:0; }
</style>
