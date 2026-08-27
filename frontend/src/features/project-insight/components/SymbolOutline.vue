<template>
  <el-card class="detail-card">
    <template #header>
      <div class="card-header"><span>🔍 当前文件的包含关系</span><el-tag v-if="node" size="small" type="primary">{{ node.path }}</el-tag></div>
    </template>
    <template v-if="node">
      <div v-if="node.is_dir" class="empty-tip">当前选择的是目录：<strong>{{ node.name }}</strong>，请选择代码文件。</div>
      <el-tree v-else-if="hierarchy.length" :data="hierarchy" :props="treeProps" default-expand-all class="arch-tree">
        <template #default="{ node: treeNode, data }">
          <span class="symbol-node">
            <span><el-tag size="small" :type="tagType(data.type)">{{ data.type }}</el-tag> {{ treeNode.label }}</span>
            <span v-if="data.line" class="line">Line {{ data.line }}</span>
          </span>
        </template>
      </el-tree>
      <div v-else class="empty-tip">该文件没有检测到类或函数定义。</div>
    </template>
    <div v-else class="empty-tip">请从左侧选择代码文件。</div>
  </el-card>
</template>

<script setup>
defineProps({ node: { type: Object, default: null }, hierarchy: { type: Array, default: () => [] } })
const treeProps = { label: 'name', children: 'children' }
const tagType = (type) => ({ class: 'success', category: 'info', method: 'warning', variable: 'primary', property: 'danger', constant: 'info' }[type] || 'info')
</script>

<style scoped>
.detail-card { min-height: 250px; }
.card-header, .symbol-node { display: flex; justify-content: space-between; align-items: center; }
.symbol-node { width: 100%; gap: 12px; }
.symbol-node .el-tag { margin-right: 8px; }
.line { color: #909399; font-size: 12px; margin-right: 15px; }
.arch-tree { background: #fcfcfc; border-radius: 4px; padding: 5px; }
.empty-tip { color: #909399; text-align: center; padding: 40px 0; }
</style>