<template>
  <el-dialog
    :model-value="visible"
    title="后端项目数据管理"
    width="min(1040px, 94vw)"
    destroy-on-close
    @update:model-value="$emit('update:visible', $event)"
  >
    <div class="manager-toolbar">
      <div>
        <strong>{{ inventory.project_count || 0 }}</strong> 个项目
        <span>总占用 {{ formatBytes(inventory.total_bytes || 0) }}</span>
      </div>
      <el-button size="small" :loading="loading" @click="loadInventory">刷新</el-button>
    </div>

    <el-alert
      title="这里显示当前用户保存在后端的项目。恢复只重建前端视图；删除会同时清理源码、分析产物、Agent、实验和执行记录。"
      type="info"
      :closable="false"
      show-icon
    />

    <el-table
      v-loading="loading"
      :data="inventory.projects || []"
      class="project-table"
      max-height="520"
      empty-text="后端没有可管理的项目"
    >
      <el-table-column label="项目" min-width="190">
        <template #default="scope">
          <div class="project-name">
            <span>{{ scope.row.name }}</span>
            <el-tag v-if="scope.row.project_id === currentProjectId" size="small" type="success">当前</el-tag>
          </div>
          <code>{{ scope.row.project_id }}</code>
        </template>
      </el-table-column>
      <el-table-column label="来源" min-width="210" show-overflow-tooltip>
        <template #default="scope">{{ sourceLabel(scope.row.source) }}</template>
      </el-table-column>
      <el-table-column label="创建时间" width="170">
        <template #default="scope">{{ formatDate(scope.row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="存储占用" width="120">
        <template #default="scope">
          {{ formatBytes(scope.row.total_bytes) }}
          <el-tooltip v-if="!scope.row.size_complete" content="目录过大或部分文件不可读，当前数值是不完整统计">
            <span class="warning-mark">*</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="150">
        <template #default="scope">
          <div class="resource-status">
            <el-tag size="small" :type="scope.row.workspace_exists ? 'success' : 'danger'">源码</el-tag>
            <el-tag
              size="small"
              :type="scope.row.artifact_readable ? 'success' : 'danger'"
            >{{ scope.row.artifact_exists ? (scope.row.artifact_readable ? '产物' : '产物损坏') : '无产物' }}</el-tag>
            <el-tag v-if="scope.row.active_tasks" size="small" type="warning">任务中</el-tag>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="170" fixed="right">
        <template #default="scope">
          <el-button
            link
            type="primary"
            :disabled="!scope.row.workspace_exists || !scope.row.artifact_readable"
            :loading="restoringId === scope.row.project_id"
            @click="restore(scope.row.project_id)"
          >恢复</el-button>
          <el-button
            link
            type="danger"
            :disabled="scope.row.active_tasks"
            :loading="deletingId === scope.row.project_id"
            @click="remove(scope.row)"
          >删除</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiErrorMessage } from '../../../services/httpClient.js'
import {
  deleteProject,
  listStoredProjects,
  restoreStoredProject,
} from '../../../services/projectApi.js'

const props = defineProps({
  visible: Boolean,
  currentProjectId: { type: String, default: '' },
})
const emit = defineEmits(['update:visible', 'restore', 'deleted'])

const inventory = ref({ projects: [], project_count: 0, total_bytes: 0 })
const loading = ref(false)
const restoringId = ref('')
const deletingId = ref('')

/** 从服务端刷新当前用户全部项目及存储占用。 */
const loadInventory = async () => {
  loading.value = true
  try {
    inventory.value = await listStoredProjects()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '后端项目数据加载失败'))
  } finally {
    loading.value = false
  }
}

/** 加载指定项目分析快照并交给工作台恢复响应式状态。 */
const restore = async (projectId) => {
  restoringId.value = projectId
  try {
    const snapshot = await restoreStoredProject(projectId)
    emit('restore', snapshot)
    emit('update:visible', false)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '项目状态恢复失败'))
  } finally {
    restoringId.value = ''
  }
}

/** 经二次确认后调用完整项目生命周期删除，并刷新库存。 */
const remove = async (project) => {
  try {
    await ElMessageBox.confirm(
      `确定永久删除“${project.name}”及其源码、分析产物和关联记录吗？`,
      '删除后端项目数据',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch (_) {
    return
  }
  deletingId.value = project.project_id
  try {
    await deleteProject(project.project_id)
    emit('deleted', project.project_id)
    ElMessage.success('后端项目资源已完整删除')
    await loadInventory()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '项目删除失败'))
  } finally {
    deletingId.value = ''
  }
}

const formatBytes = (value) => {
  let size = Number(value || 0)
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size.toFixed(index ? 1 : 0)} ${units[index]}`
}
const formatDate = (value) => value ? new Date(value).toLocaleString() : '未知'
const sourceLabel = (value) => String(value || '').replace('local_upload://', '本地上传：')

watch(() => props.visible, (visible) => {
  if (visible) loadInventory()
})
</script>

<style scoped>
.manager-toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.manager-toolbar span { margin-left: 8px; color: #909399; font-size: 12px; }
.project-table { margin-top: 14px; }
.project-name { display: flex; align-items: center; gap: 7px; font-weight: 600; }
.project-table code { color: #909399; font-size: 10px; }
.resource-status { display: flex; flex-wrap: wrap; gap: 4px; }
.warning-mark { color: #e6a23c; font-weight: 700; cursor: help; }
</style>
