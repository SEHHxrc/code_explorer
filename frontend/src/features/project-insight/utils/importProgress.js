/** 串行轮询进度；独立控制器保证停止轮询不会中断正在返回分析结果的 POST。 */
export const pollImportProgress = ({ requestId, getProgress, onProgress, onError, interval = 1000 }) => {
  const controller = new AbortController()
  let timer = null

  const poll = async () => {
    try {
      const progress = await getProgress(requestId, { signal: controller.signal })
      if (controller.signal.aborted) return
      onProgress(progress)
      if (['completed', 'failed'].includes(progress.status)) return
    } catch (error) {
      if (controller.signal.aborted) return
      onError(error)
    }
    if (!controller.signal.aborted) timer = setTimeout(poll, interval)
  }
  void poll()

  return () => {
    controller.abort()
    clearTimeout(timer)
  }
}

/** 百分比只表示当前文件阶段，不代表全部分析；未知总量时返回 null。 */
export const fileProgressPercent = (progress) => {
  if (!progress?.total_files) return null
  return Math.min(100, Math.max(0, Math.round(progress.processed_files / progress.total_files * 100)))
}
