# 项目工作台导航设计

## 调研摘要

代码智能产品通常不会把所有主要工具顺次堆放在一个纵向长页面中。

- [GitLab 项目设置](https://docs.gitlab.com/user/project/settings/) 使用常驻项目侧栏组织仓库、分析和安全能力。
- [SonarQube 项目总览](https://docs.sonarsource.com/sonarqube-server/10.8/user-guide/viewing-projects/project-overview) 以项目摘要为入口，并把代码、问题、度量和安全热点拆成项目级页面；摘要卡片可进入聚焦详情。
- [Sourcegraph 代码导航](https://sourcegraph.com/docs/code-navigation/features) 以代码浏览为中心，把 AI 探索或引用结果放到辅助面板，而不是不断向代码视图下方追加区块。
- Vue 官方建议对页面组件使用[懒加载](https://router.vuejs.org/guide/advanced/lazy-loading)以拆分代码；需要保存非活动组件状态时使用 [`KeepAlive`](https://vuejs.org/api/built-in-components)。

## 已采用的结构

项目导入后，Code Explorer 使用固定项目外壳：

- 紧凑的项目身份与生命周期页头；
- 分组左侧导航；
- 同一时间只显示一个工作区；
- 提供项目概览、代码与依赖图、项目智能体、依赖图实验、执行与安全工作区；
- 使用组件缓存，使会话、实验比较和任务输出在切换工作区后仍保留；
- 活动工作区内部滚动，不依赖整页纵向滚动。

导入表单是独立的新手引导状态，项目加载后即隐藏。在窄屏设备上，导航变为横向排列，并允许页面滚动作为响应式回退。

当前暂不引入 Vue Router。原型尚未提供浏览器刷新后重新获取完整分析视图的 API，因此路由 URL 会暗示系统支持实际不存在的深链接恢复能力。后续可在增加项目分析读取接口时，一并加入基于 Router 的 URL。
