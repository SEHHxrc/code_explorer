import { reactive, ref, shallowRef } from 'vue'
import Sigma from 'sigma'
import EdgeCurveProgram from '@sigma/edge-curve'
import { fadeColor } from '../graphStyle.js'

/** Own Sigma, reducers, camera operations, hover state, and pointer interactions. */
export const useSigmaRenderer = ({
  containerRef,
  fullGraph,
  displayGraph,
  renderState,
  onNode,
  onEdge,
  onStage,
  onDragStart,
}) => {
  const renderer = shallowRef(null)
  const hovered = ref(null)
  const hoverPos = reactive({ x: 0, y: 0 })
  const draggingNode = ref(null)
  let dragMoved = false
  let suppressClick = false

  const nodeReducer = (id, data) => {
    if (data.hidden) return { ...data, hidden: true }
    const result = { ...data }
    const { selected, selectedEdge, edgeEndpoints, neighbors, matches } = renderState
    if (matches.size) {
      if (matches.has(id)) return { ...result, color: '#0891b2', zIndex: 3, highlighted: true }
      return { ...result, color: fadeColor(data.baseColor, 0.82), zIndex: 0 }
    }
    if (selectedEdge) {
      if (edgeEndpoints.has(id)) return { ...result, zIndex: 3, highlighted: true }
      return { ...result, color: fadeColor(data.baseColor, 0.85), zIndex: 0, label: '' }
    }
    if (selected) {
      if (id === selected) return { ...result, zIndex: 3, highlighted: true }
      if (neighbors.has(id)) return { ...result, zIndex: 2 }
      return { ...result, color: fadeColor(data.baseColor, 0.85), zIndex: 0, label: '' }
    }
    return result
  }

  const edgeReducer = (id, data) => {
    if (data.hidden || (renderState.layoutActive && renderState.visibleEdges > 500)) {
      return { ...data, hidden: true }
    }
    const result = { ...data, color: data.baseColor }
    if (renderState.selectedEdge) {
      return id === renderState.selectedEdge
        ? { ...result, size: Math.max(2.4, data.baseWidth * 3), zIndex: 3 }
        : { ...result, hidden: true }
    }
    if (renderState.selected && fullGraph.value) {
      const [source, target] = fullGraph.value.extremities(id)
      return source === renderState.selected || target === renderState.selected
        ? { ...result, size: data.baseWidth * 2.4, zIndex: 2 }
        : { ...result, hidden: true }
    }
    if (renderState.matches.size) result.color = fadeColor(data.baseColor, 0.75)
    return result
  }

  const create = () => {
    if (!containerRef.value || !displayGraph.value) return
    destroy()
    renderer.value = new Sigma(displayGraph.value, containerRef.value, {
      renderLabels: true,
      labelFont: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
      labelSize: 11,
      labelWeight: '500',
      labelColor: { color: '#3b4352' },
      labelRenderedSizeThreshold: 6,
      labelDensity: 0.12,
      labelGridCellSize: 68,
      defaultNodeColor: '#909399',
      defaultEdgeColor: '#dfe3e9',
      enableEdgeEvents: true,
      defaultEdgeType: 'curved',
      edgeProgramClasses: { curved: EdgeCurveProgram },
      minCameraRatio: 0.02,
      maxCameraRatio: 20,
      hideEdgesOnMove: true,
      zIndex: true,
      nodeReducer,
      edgeReducer,
      defaultDrawNodeHover: (context, data) => {
        context.beginPath()
        context.arc(data.x, data.y, (data.size || 4) + 4, 0, Math.PI * 2)
        context.strokeStyle = data.color || '#7c4dff'
        context.lineWidth = 2
        context.globalAlpha = 0.5
        context.stroke()
        context.globalAlpha = 1
      },
    })
    const sigma = renderer.value
    sigma.on('enterNode', ({ node }) => {
      const attrs = fullGraph.value.getNodeAttributes(node)
      hovered.value = { label: attrs.label, kind: attrs.kind, color: attrs.baseColor, pinned: attrs.pinned }
      const display = sigma.getNodeDisplayData(node)
      if (display) {
        const viewport = sigma.framedGraphToViewport(display)
        hoverPos.x = viewport.x
        hoverPos.y = viewport.y - 16
      }
      containerRef.value.style.cursor = 'pointer'
    })
    sigma.on('leaveNode', () => {
      hovered.value = null
      if (containerRef.value && !draggingNode.value) containerRef.value.style.cursor = 'grab'
    })
    sigma.on('downNode', ({ node, event }) => {
      onDragStart?.()
      draggingNode.value = node
      dragMoved = false
      suppressClick = false
      fullGraph.value.setNodeAttribute(node, 'pinned', true)
      displayGraph.value?.setNodeAttribute(node, 'pinned', true)
      if (hovered.value) hovered.value = { ...hovered.value, pinned: true }
      if (!sigma.getCustomBBox()) {
        const bbox = sigma.getBBox()
        const padX = Math.max((bbox.x[1] - bbox.x[0]) * 0.12, 1)
        const padY = Math.max((bbox.y[1] - bbox.y[0]) * 0.12, 1)
        sigma.setCustomBBox({
          x: [bbox.x[0] - padX, bbox.x[1] + padX],
          y: [bbox.y[0] - padY, bbox.y[1] + padY],
        })
      }
      event.preventSigmaDefault()
      containerRef.value.style.cursor = 'grabbing'
    })
    const captor = sigma.getMouseCaptor()
    captor.on('mousemovebody', (event) => {
      const node = draggingNode.value
      if (!node || !fullGraph.value?.hasNode(node)) return
      const position = sigma.viewportToGraph({ x: event.x, y: event.y })
      fullGraph.value.mergeNodeAttributes(node, { ...position, pinned: true })
      displayGraph.value?.mergeNodeAttributes(node, { ...position, pinned: true })
      dragMoved = true
      suppressClick = true
      event.preventSigmaDefault()
      event.original?.preventDefault()
      sigma.refresh()
    })
    captor.on('mouseup', () => {
      if (!draggingNode.value) return
      draggingNode.value = null
      if (containerRef.value) containerRef.value.style.cursor = 'grab'
      setTimeout(() => { suppressClick = false }, 0)
    })
    sigma.on('clickNode', ({ node }) => {
      if (!suppressClick && !dragMoved) onNode?.(node)
      dragMoved = false
    })
    sigma.on('clickEdge', ({ edge }) => { if (!suppressClick) onEdge?.(edge) })
    sigma.on('clickStage', () => { if (!suppressClick) onStage?.() })
  }

  const setGraph = () => {
    if (renderer.value && displayGraph.value) renderer.value.setGraph(displayGraph.value)
  }
  const refresh = () => renderer.value?.refresh()
  const zoom = (factor) => {
    const camera = renderer.value?.getCamera()
    if (camera) camera.animate({ ratio: camera.ratio * factor }, { duration: 200 })
  }
  const resetCamera = (visibleCount = 0) => {
    renderer.value?.getCamera().animate(
      { x: 0.5, y: 0.5, ratio: visibleCount > 120 ? 0.78 : 0.92, angle: 0 },
      { duration: 380 },
    )
  }
  const focusNode = (id) => {
    const display = renderer.value?.getNodeDisplayData(id)
    if (!display) return
    const camera = renderer.value.getCamera()
    camera.animate({ x: display.x, y: display.y, ratio: Math.min(camera.ratio, 0.45) }, { duration: 420 })
  }
  const focusEdge = (sourceId, targetId) => {
    const source = renderer.value?.getNodeDisplayData(sourceId)
    const target = renderer.value?.getNodeDisplayData(targetId)
    if (!source || !target) return
    const camera = renderer.value.getCamera()
    camera.animate({
      x: (source.x + target.x) / 2,
      y: (source.y + target.y) / 2,
      ratio: Math.min(camera.ratio, 0.65),
    }, { duration: 420 })
  }
  const destroy = () => {
    renderer.value?.kill()
    renderer.value = null
    hovered.value = null
    draggingNode.value = null
  }

  return {
    renderer, hovered, hoverPos,
    create, destroy, setGraph, refresh, zoom, resetCamera, focusNode, focusEdge,
  }
}
