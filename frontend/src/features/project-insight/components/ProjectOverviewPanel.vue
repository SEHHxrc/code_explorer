<template>
  <el-card class="overview-card">
    <template #header>
      <div class="card-header">
        <span>🤖 项目架构与功能概览</span>
        <div class="actions">
          <el-tag size="small" :type="modelStatus.configured ? 'success' : 'info'">{{ modelLabel }}</el-tag>
          <el-button size="small" type="success" :loading="loading" @click="$emit('generate')">
            {{ modelStatus.configured ? '生成 AI 增强概览' : '刷新确定性概览' }}
          </el-button>
        </div>
      </div>
    </template>
    <div v-if="manifest">
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item label="主要语言">{{ manifest.languages?.join('、') || '未识别' }}</el-descriptions-item>
        <el-descriptions-item label="框架 / 工具">{{ manifest.frameworks?.join('、') || '未识别' }}</el-descriptions-item>
        <el-descriptions-item label="依赖图节点">{{ manifest.graph_summary?.node_count || 0 }}</el-descriptions-item>
        <el-descriptions-item label="依赖关系">{{ manifest.graph_summary?.edge_count || 0 }}</el-descriptions-item>
      </el-descriptions>
      <section class="entrypoints">
        <h4>程序入口点</h4>
        <el-table :data="manifest.entrypoints || []" border size="small" empty-text="未发现高置信度入口">
          <el-table-column prop="kind" label="类型" width="130" />
          <el-table-column prop="name" label="名称" width="180" />
          <el-table-column label="位置"><template #default="scope"><code>{{ scope.row.path }}{{ scope.row.line ? `:${scope.row.line}` : '' }}</code></template></el-table-column>
          <el-table-column prop="command" label="命令" />
        </el-table>
      </section>
      <el-alert v-if="overview.warning" :title="overview.warning" type="warning" :closable="false" show-icon />
      <div class="report">{{ overview.content }}</div>
    </div>
    <el-empty v-else description="项目完成静态分析后将在这里生成架构概览" />
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({ manifest: { type: Object, default: null }, overview: { type: Object, default: () => ({}) }, modelStatus: { type: Object, default: () => ({}) }, loading: Boolean })
defineEmits(['generate'])
const modelLabel = computed(() => props.modelStatus.configured ? `${props.modelStatus.provider} / ${props.modelStatus.model}` : '静态分析模式')
</script>

<style scoped>
.overview-card { min-height: 250px; }
.card-header, .actions { display: flex; justify-content: space-between; align-items: center; }
.actions { gap: 10px; }
.entrypoints { margin-top: 18px; }
.entrypoints h4 { margin: 0 0 10px; }
.report { margin-top: 18px; padding: 18px; border: 1px solid #ebeef5; border-radius: 6px; background: #fafafa; line-height: 1.7; white-space: pre-wrap; }
</style>