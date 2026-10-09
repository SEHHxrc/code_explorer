# 静态安全证据盲态对照实验页面

`ExperimentComparison.vue` 比较“静态安全证据 + 原始文件”与“仅原始文件”的安全分析答案。创建后隐藏组别标签，展示左右答案和明确标注的字符 Token 估算。只有两组都成功才显示评分；失败/取消不能算作有效效果对比。提交评分后揭盲。新 `security_evidence` 与旧 `graph` 历史分别显示，不能混合统计。

## 入口与依赖

组件接收当前 `projectId`，通过 `services/experimentApi.js` 调用创建与评审接口，并消费比较 SSE 快照。它依赖后端 `experiments` 模块保证两组使用相同模型和步骤预算。

> **临时对照组边界：** 重复试验确认静态安全证据更优并结束对照实验后，应按 `../../../../docs/EXPERIMENT_PROTOCOL.md` 清理对照分支，而不是保留为长期产品模式。
