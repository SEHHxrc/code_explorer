<template>
  <el-card class="import-card">
    <template #header><div class="card-header"><span>🚀 开源项目辅助理解工具 - 导入控制台</span></div></template>
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
  </el-card>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'

defineProps({ importing: Boolean, deleting: Boolean, hasProject: Boolean })
const emit = defineEmits(['analyze-git', 'analyze-zip', 'reset'])
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
</script>

<style scoped>
.import-card { margin-bottom: 20px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
</style>