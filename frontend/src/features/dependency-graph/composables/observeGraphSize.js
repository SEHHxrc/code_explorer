/**
 * 观察画布容器，把连续尺寸变化合并为一次尾部更新（120ms）。
 * 输入容器及更新回调，返回取消函数；重复尺寸、零尺寸和取消后的迟到通知不触发更新。
 * 不监听 window.resize：Sigma 已拥有该监听，避免再做一遍全图刷新。
 */
export const observeGraphSize = (element, onResize) => {
  if (!element || typeof ResizeObserver === 'undefined') return () => {}
  let timer = null
  let width = -1
  let height = -1
  let active = true
  const observer = new ResizeObserver((entries) => {
    if (!active) return
    const entry = entries.find((item) => item.target === element)
    if (!entry) return
    const nextWidth = Math.round(entry.contentRect.width)
    const nextHeight = Math.round(entry.contentRect.height)
    if (width === nextWidth && height === nextHeight) return
    width = nextWidth
    height = nextHeight
    clearTimeout(timer)
    timer = width > 0 && height > 0 ? setTimeout(() => { timer = null; onResize() }, 120) : null
  })
  observer.observe(element)
  return () => {
    active = false
    clearTimeout(timer)
    observer.disconnect()
  }
}
