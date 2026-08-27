<template>
  <el-card class="tree-card">
    <template #header>
      <div class="card-header"><span>📁 项目组织结构</span><el-tag size="small" type="success">安全清洗完成</el-tag></div>
    </template>
    <el-input v-model="filterText" placeholder="输入关键字过滤文件..." class="filter-input" clearable />
    <el-tree
      ref="treeRef"
      class="filter-tree"
      :data="nodes"
      :props="treeProps"
      default-expand-all
      :filter-node-method="filterNode"
      @node-click="$emit('select', $event)"
    >
      <template #default="{ node, data }">
        <span class="tree-node">
          <el-icon :color="data.is_dir ? '#409EFF' : '#67C23A'"><component :is="data.is_dir ? Folder : Document" /></el-icon>
          <span :class="data.is_dir ? 'dir-text' : 'file-text'">{{ node.label }}</span>
        </span>
      </template>
    </el-tree>
  </el-card>
</template>

<script setup>
import { ref, watch } from 'vue'
import { Document, Folder } from '@element-plus/icons-vue'

defineProps({ nodes: { type: Array, default: () => [] } })
defineEmits(['select'])
const filterText = ref('')
const treeRef = ref()
const treeProps = { children: 'children', label: 'name' }
watch(filterText, (value) => treeRef.value?.filter(value))
const filterNode = (value, data) => !value || String(data?.name || '').toLowerCase().includes(value.toLowerCase())
</script>

<style scoped>
.tree-card { width: 350px; min-height: 600px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.filter-input { margin-bottom: 15px; }
.tree-node { display: inline-flex; gap: 6px; align-items: center; }
.file-text { color: #606266; }
.dir-text { color: #303133; font-weight: 600; }
</style>