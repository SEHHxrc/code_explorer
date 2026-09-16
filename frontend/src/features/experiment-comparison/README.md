# 依赖图盲态对照实验页面

`ExperimentComparison.vue` 用于比较同一问题在“有依赖图”和“临时无图对照组”下的回答。创建比较后只显示随机化的左/右答案与指标；用户提交正确性、完整性、证据、幻觉控制评分和偏好后才揭示组别。

## 入口与依赖

组件接收当前 `projectId`，通过 `services/experimentApi.js` 调用创建与评审接口，并消费比较 SSE 快照。它依赖后端 `experiments` 模块保证两组使用相同模型和步骤预算。

> **临时对照组边界：** 如果实验确认有图方案更优，应按 `../../../../docs/EXPERIMENT_PROTOCOL.md` 删除本功能及 `services/experimentApi.js`，而不是把无图模式继续保留为产品选项。
