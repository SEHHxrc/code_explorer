# Code Explorer 前端

Vue 3 + Vite 前端，负责项目导入与分析状态、Sigma.js 依赖图、项目概览和智能体事件流展示。

实验失败页与历史实验共用 `features/experiment-comparison/ReportConflictDiagnostic.vue`。仅台账状态冲突可显式申请“询问台账冲突原因”；组件通过 `services/experimentApi.js` 的 POST 启动独立诊断，GET 轮询或恢复已有状态。切换实验/离开页面取消本地请求和轮询，不取消服务端已认领任务，也不重复调用模型。结果、用量和耗时单独显示，不改变主报告、评分或盲态分组；诊断引用沿用原提交，不表示重新验证源码。

安全路径画布从同一份静态产物投影：路径族用颜色区分，已记录调用为实线，动态派发或
启发式候选目标为虚线；`may` 本身不决定线型，没有候选目标时显示传播断点。
节点详情和图例中的路径族悬停提示使用起点与终点函数名，ID 仅用于内部身份与选路。
`curvePrograms.js` 复用锁定版本的 Sigma 曲线几何与拾取，
仅增加虚线遮罩；升级 `@sigma/edge-curve` 时需验证 shader、箭头、点击、高亮及尺寸调整。

```powershell
npm install
npm run dev
```

生产构建使用 `npm run build`。主要组件和浏览器端接口见 [项目架构文档](../docs/ARCHITECTURE.md)。
