/** 只投影已记录条件，不用当前设置补填历史；缺失值保持未知。 */
export const historyConditions = (lane) => ({
  prompt_sha256: lane?.prompt_snapshot?.instructions_sha256 ?? null,
  report_version: lane?.prompt_snapshot?.report_version ?? lane?.metrics?.answer_completeness?.report_version ?? null,
  contract_hash: lane?.metrics?.contract_hash ?? null,
  files_sha256: lane?.prompt_snapshot?.files_sha256 ?? null,
  static_artifact_sha256: lane?.prompt_snapshot?.static_artifact_sha256 ?? null,
  tools_sha256: lane?.prompt_snapshot?.tools_sha256 ?? null,
  requested_model: lane?.run?.model ?? null,
  returned_models: lane?.metrics?.actual_models ?? null,
  execution_policy: lane?.metrics?.execution_policy ?? null,
  budget: lane?.metrics?.budget_configurations ?? null,
})

/** 只比较共同提示词哈希；相同提示词不代表模型、预算、源码等实验条件相同。 */
export const promptComparison = (experiments) => {
  const hashes = experiments.map((item) => Object.values(item.lanes || {})
    .map((lane) => lane.prompt_snapshot?.instructions_sha256).filter(Boolean))
  if (hashes.some((items) => items.length !== 2 || items[0] !== items[1])) return '提示词快照缺失或组间不一致，无法确认仅提示词不同。'
  return hashes[0]?.[0] === hashes[1]?.[0]
    ? '共同提示词哈希相同；其他运行条件仍需核对。'
    : '共同提示词哈希不同；不代表效果差异一定由提示词导致，仍需核对其他条件并重复试验。'
}
