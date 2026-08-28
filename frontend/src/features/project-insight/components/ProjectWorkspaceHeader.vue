<template>
  <header class="project-header">
    <div class="identity">
      <div class="project-mark"><el-icon><FolderOpened /></el-icon></div>
      <div class="project-copy">
        <div class="project-name">{{ manifest?.project_name || '已分析项目' }}</div>
        <div class="project-meta">
          <span>{{ languages }}</span>
          <span>{{ graphCount }}</span>
          <span>{{ modelLabel }}</span>
        </div>
      </div>
    </div>
    <div class="actions">
      <el-tag size="small" type="success" effect="plain">分析已完成</el-tag>
      <el-popconfirm
        title="确定删除当前项目及其分析、实验和执行记录吗？"
        width="260"
        confirm-button-text="删除"
        cancel-button-text="取消"
        @confirm="$emit('delete')"
      >
        <template #reference>
          <el-button type="danger" plain size="small" :loading="deleting">删除项目</el-button>
        </template>
      </el-popconfirm>
    </div>
  </header>
</template>

<script setup>
import { computed } from 'vue'
import { FolderOpened } from '@element-plus/icons-vue'

const props = defineProps({
  manifest: { type: Object, default: null },
  modelStatus: { type: Object, default: () => ({ configured: false }) },
  graph: { type: Object, default: () => ({ nodes: [], edges: [] }) },
  deleting: Boolean,
})
defineEmits(['delete'])

const languages = computed(() => props.manifest?.languages?.slice(0, 4).join(' · ') || '语言未识别')
const graphCount = computed(() => (
  String(props.graph?.nodes?.length || 0) + ' 节点 · ' + String(props.graph?.edges?.length || 0) + ' 关系'
))
const modelLabel = computed(() => (
  props.modelStatus.configured
    ? String(props.modelStatus.provider) + ' / ' + String(props.modelStatus.model)
    : '模型未配置'
))
</script>

<style scoped>
.project-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 68px;
  padding: 10px 16px;
  box-sizing: border-box;
  border: 1px solid #e4e7ed;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 2px 10px rgba(31, 45, 61, .04);
}
.identity, .actions, .project-meta { display: flex; align-items: center; }
.identity { min-width: 0; gap: 12px; }
.project-mark {
  display: grid;
  flex: 0 0 38px;
  width: 38px;
  height: 38px;
  place-items: center;
  border-radius: 9px;
  background: #ecf5ff;
  color: #2563eb;
  font-size: 20px;
}
.project-copy { min-width: 0; }
.project-name { overflow: hidden; color: #303133; font-size: 16px; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
.project-meta { flex-wrap: wrap; gap: 5px 14px; margin-top: 4px; color: #909399; font-size: 11px; }
.actions { gap: 10px; }
@media (max-width: 700px) {
  .project-meta span:nth-child(2), .actions .el-tag { display: none; }
}
</style>
