<template>
  <el-card class="detail-card">
    <template #header>
      <div class="card-header">
        <span class="panel-title">🔍 当前文件的包含关系 <small>单击符号定位</small></span>
        <el-tooltip v-if="node" :content="node.path || node.name" placement="top" :show-after="350">
          <div class="file-path">
            <span v-if="pathPrefix" class="path-prefix">{{ pathPrefix }}/</span>
            <strong class="path-name">{{ node.name }}</strong>
          </div>
        </el-tooltip>
      </div>
    </template>
    <template v-if="node">
      <div v-if="node.is_dir" class="empty-tip">当前选择的是目录：<strong>{{ node.name }}</strong>，请选择代码文件。</div>
      <el-tree v-else-if="hierarchy.length" :data="hierarchy" :props="treeProps" default-expand-all :expand-on-click-node="false" class="arch-tree" @node-click="locateSymbol">
        <template #default="{ node: treeNode, data }">
          <span class="symbol-node" :class="{ locatable: data.type !== 'category' }" :title="treeNode.label">
            <span class="symbol-main">
              <el-tag size="small" :type="tagType(data.type)">{{ data.type }}</el-tag>
              <span class="symbol-name">{{ treeNode.label }}</span>
            </span>
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
import { computed } from 'vue'

const props = defineProps({ node: { type: Object, default: null }, hierarchy: { type: Array, default: () => [] } })
const emit = defineEmits(['locate'])
const treeProps = { label: 'name', children: 'children' }
const tagType = (type) => ({ class: 'success', category: 'info', method: 'warning', variable: 'primary', property: 'danger', constant: 'info' }[type] || 'info')
const locateSymbol = (symbol) => {
  if (symbol?.type === 'category' || !symbol?.fqn || !props.node?.path) return
  emit('locate', { file: props.node.path, name: symbol.name, fqn: symbol.fqn, line: symbol.line })
}
const pathPrefix = computed(() => {
  const path = String(props.node?.path || '').replace(/\\/g, '/')
  const parts = path.split('/').filter(Boolean)
  if (parts.length <= 1) return ''
  return parts.slice(0, -1).join('/')
})
</script>

<style scoped>
.detail-card { min-width: 0; min-height: 250px; }
.card-header { display: flex; flex-direction: column; align-items: stretch; gap: 7px; min-width: 0; }
.panel-title { font-weight: 600; line-height: 20px; white-space: nowrap; }
.panel-title small { margin-left: 5px; color: #909399; font-size: 10px; font-weight: 400; }
.file-path {
  display: flex;
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  padding: 4px 7px;
  overflow: hidden;
  border: 1px solid #d9ecff;
  border-radius: 5px;
  background: #ecf5ff;
  color: #337ecc;
  font-size: 11px;
  line-height: 17px;
  white-space: nowrap;
}
.path-prefix { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.path-name { flex: none; }
.symbol-node {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  min-width: 0;
  gap: 10px;
  padding-right: 8px;
  overflow: hidden;
}
.symbol-main { display: inline-flex; align-items: center; min-width: 0; gap: 7px; }
.symbol-main .el-tag, .line { flex: none; }
.symbol-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.symbol-node.locatable { cursor: pointer; }
.symbol-node.locatable:hover .symbol-name { color: #409eff; }
.line { color: #909399; font-size: 12px; }
.arch-tree { background: #fcfcfc; border-radius: 4px; padding: 5px; }
:deep(.arch-tree .el-tree-node__content) { overflow: hidden; }
.empty-tip { color: #909399; text-align: center; padding: 40px 0; }
</style>
