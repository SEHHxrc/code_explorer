# 前端源码结构

前端使用 Vue 3 Composition API、Vite 和 Element Plus。项目工作台按功能懒加载，一次只显示一个主要页面；依赖图使用 Graphology 保存图数据、Sigma.js 渲染、ForceAtlas2 Web Worker 计算布局。

## 入口与依赖关系

| 文件或目录 | 作用 |
| --- | --- |
| `main.js` | 创建 Vue 应用、按需注册 Element Plus 组件并挂载 `App.vue`。 |
| `App.vue` | 页面根组件，通过兼容入口加载项目洞察工作台。 |
| `style.css` | 全局主题、布局和响应式样式。 |
| `components/` | 仍被外部引用的兼容入口，以及项目智能体工作区。 |
| `features/` | 按业务能力组织的项目、依赖图、实验和执行模块。 |
| `services/` | Axios HTTP 与 Fetch SSE 客户端。 |
| `assets/` | 静态图片资源，不包含业务逻辑。 |

```text
main.js → App.vue
  → features/project-insight/ProjectInsight.vue
      ├─ 项目概览
      ├─ 代码与依赖图
      ├─ components/AgentWorkspace.vue
      ├─ features/experiment-comparison/
      └─ features/execution/
          → services/* → FastAPI
```

当前原型刻意未引入 Vue Router：项目分析状态只保存在当前页面内，刷新后尚不能通过 URL 恢复。若未来增加项目分析读取接口，可再把工作区切换升级成可深链接路由。
