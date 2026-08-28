<template>
  <aside class="workspace-nav" aria-label="项目功能导航">
    <div v-for="group in groups" :key="group.label" class="nav-group">
      <div class="group-label">{{ group.label }}</div>
      <button
        v-for="item in group.items"
        :key="item.key"
        type="button"
        class="nav-item"
        :class="{ active: active === item.key }"
        @click="$emit('select', item.key)"
      >
        <el-icon><component :is="item.icon" /></el-icon>
        <span>{{ item.label }}</span>
        <small v-if="item.badge">{{ item.badge }}</small>
      </button>
    </div>
    <div class="nav-foot">
      <span class="status-dot" />
      <span>项目上下文已加载</span>
    </div>
  </aside>
</template>

<script setup>
import {
  ChatDotRound,
  Connection,
  DataAnalysis,
  Opportunity,
  Lock,
} from '@element-plus/icons-vue'

defineProps({
  active: { type: String, required: true },
})
defineEmits(['select'])

const groups = [
  {
    label: '理解项目',
    items: [
      { key: 'overview', label: '项目概览', icon: DataAnalysis },
      { key: 'explore', label: '代码与图谱', icon: Connection },
      { key: 'agent', label: '智能体分析', icon: ChatDotRound },
    ],
  },
  {
    label: '验证与运行',
    items: [
      { key: 'experiment', label: '依赖图盲测', icon: Opportunity, badge: 'A/B' },
      { key: 'execution', label: '执行与安全', icon: Lock },
    ],
  },
]
</script>

<style scoped>
.workspace-nav {
  display: flex;
  flex: 0 0 196px;
  flex-direction: column;
  gap: 20px;
  height: 100%;
  padding: 14px 10px;
  box-sizing: border-box;
  border: 1px solid #e4e7ed;
  border-radius: 10px;
  background: #fff;
}
.nav-group { display: flex; flex-direction: column; gap: 5px; }
.group-label {
  padding: 0 10px 5px;
  color: #a8abb2;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .08em;
}
.nav-item {
  display: grid;
  grid-template-columns: 20px 1fr auto;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 40px;
  padding: 8px 10px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: #606266;
  text-align: left;
  cursor: pointer;
}
.nav-item:hover { background: #f5f7fa; color: #303133; }
.nav-item.active { background: #ecf5ff; color: #2563eb; font-weight: 600; }
.nav-item small {
  padding: 1px 5px;
  border-radius: 999px;
  background: #f0f2f5;
  color: #909399;
  font-size: 9px;
}
.nav-foot {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: auto;
  padding: 10px;
  border-top: 1px solid #ebeef5;
  color: #909399;
  font-size: 11px;
}
.status-dot { width: 7px; height: 7px; border-radius: 50%; background: #67c23a; }
@media (max-width: 900px) {
  .workspace-nav {
    flex: none;
    flex-direction: row;
    width: 100%;
    height: auto;
    overflow-x: auto;
    padding: 8px;
  }
  .nav-group { flex-direction: row; }
  .group-label, .nav-foot { display: none; }
  .nav-item { display: flex; width: max-content; min-height: 36px; white-space: nowrap; }
}
</style>
