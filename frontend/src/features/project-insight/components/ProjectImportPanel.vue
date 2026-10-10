<template>
  <el-card class="import-card">
    <template #header>
      <div class="card-header">
        <span>🚀 开源项目辅助理解工具 - 导入控制台</span>
        <el-button size="small" plain @click="$emit('manage-data')">后端数据管理</el-button>
      </div>
    </template>
    <el-form :inline="true">
      <el-form-item label="Git 仓库链接">
        <el-input v-model="repoUrl" :disabled="hasProject || importing" placeholder="https://github.com/xxx/xxx" style="width: 300px" clearable />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="importing" :disabled="hasProject" @click="submitGit">分析 Git 项目</el-button>
        <el-button v-if="hasProject" type="danger" plain :loading="deleting" @click="$emit('reset')">清空当前项目</el-button>
      </el-form-item>
    </el-form>
    <el-divider>或者本地上传</el-divider>
    <el-upload
      ref="uploadRef"
      drag
      action="#"
      :auto-upload="false"
      :disabled="hasProject || importing"
      :on-change="selectFile"
      :limit="1"
      accept=".zip,application/zip"
    >
      <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
      <div class="el-upload__text">将项目压缩包拖到此处，或 <em>点击上传 (.zip)</em></div>
    </el-upload>
    <section v-if="progress" class="analysis-progress" aria-live="polite" :aria-busy="importing">
      <div class="progress-heading">
        <strong>{{ progress.stage_label }}</strong>
        <span>已用时 {{ Math.round(progress.elapsed_seconds || 0) }} 秒</span>
      </div>
      <p v-if="progress.detail">{{ progress.detail }}</p>
      <template v-if="progress.stage === 'uploading' && progress.upload_percent != null">
        <el-progress :percentage="progress.upload_percent" :stroke-width="10" />
        <p>文件上传进度；上传结束后继续执行服务器静态分析。</p>
      </template>
      <template v-else-if="filePercent != null && progress.status !== 'failed'">
        <el-progress :percentage="filePercent" :stroke-width="10" />
        <p>文件解析尝试：{{ progress.processed_files }} / {{ progress.total_files }}。包含失败或跳过的尝试，不代表全部分析完成。</p>
      </template>
      <template v-else-if="importing">
        <el-progress :percentage="100" :indeterminate="true" :show-text="false" :stroke-width="10" />
        <p>本阶段没有可靠的总量计数，完成后自动进入下一阶段。</p>
      </template>
      <p v-if="progress.message" class="progress-error">{{ progress.message }}</p>
      <p v-if="progress.polling_warning" class="progress-warning">{{ progress.polling_warning }}</p>
    </section>
  </el-card>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import { fileProgressPercent } from '../utils/importProgress.js'

const props = defineProps({ importing: Boolean, deleting: Boolean, hasProject: Boolean, progress: Object })
const filePercent = computed(() => fileProgressPercent(props.progress))
const emit = defineEmits(['analyze-git', 'analyze-zip', 'reset', 'manage-data'])
const repoUrl = ref('')
const uploadRef = ref()

const submitGit = () => {
  const value = repoUrl.value.trim()
  if (!value) return ElMessage.warning('请输入有效的 Git 仓库链接！')
  emit('analyze-git', value)
}
const selectFile = (uploadFile) => {
  const file = uploadFile?.raw
  uploadRef.value?.clearFiles()
  if (!file) return
  if (!file.name.toLowerCase().endsWith('.zip')) return ElMessage.warning('请选择 ZIP 项目压缩包')
  emit('analyze-zip', file)
}

/** Reset native upload state after project removal so another ZIP can be selected immediately. */
watch(() => props.hasProject, (hasProject) => {
  if (hasProject) return
  repoUrl.value = ''
  uploadRef.value?.clearFiles()
})
</script>

<style scoped>
.import-card { margin-bottom: 20px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.analysis-progress { margin-top: 20px; padding: 16px; background: var(--el-fill-color-light); border-radius: 8px; }
.progress-heading { display: flex; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.analysis-progress p { margin: 10px 0 0; color: var(--el-text-color-secondary); font-size: 13px; overflow-wrap: anywhere; }
.analysis-progress .progress-error { color: var(--el-color-danger); }
.analysis-progress .progress-warning { color: var(--el-color-warning); }
</style>
