<template>
  <div class="insight-container">
    <!-- 顶部：项目导入控制台 -->
    <el-card class="box-card import-card">
      <template #header>
        <div class="card-header">
          <span>🚀 开源项目辅助理解工具 - 导入控制台</span>
        </div>
      </template>
      <el-form :inline="true" class="demo-form-inline">
        <el-form-item label="Git 仓库链接">
          <el-input v-model="repoUrl" placeholder="https://github.com/xxx/xxx" style="width: 300px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="loading" @click="handleAnalyzeGit">分析 Git 项目</el-button>
        </el-form-item>
      </el-form>

      <el-divider>或者本地上传</el-divider>

      <el-upload
          class="upload-demo"
          drag
          action="#"
          :http-request="handleCustomUpload"
          :limit="1"
          :show-file-list="false"
      >
        <el-icon class="el-icon--upload"><upload-filled /></el-icon>
        <div class="el-upload__text">
          将项目压缩包拖到此处，或 <em>点击上传 (.zip)</em>
        </div>
      </el-upload>
    </el-card>

    <!-- 主体内容区：左右分栏布局 -->
    <div v-if="fileTree.length > 0" class="main-content-layout">

      <!-- 左侧：可交互的项目组织结构（文件系统树） -->
      <el-card class="tree-card">
        <template #header>
          <div class="card-header">
            <span>📁 项目组织结构 (文件系统)</span>
            <el-tag size="small" type="success">安全清洗完成</el-tag>
          </div>
        </template>

        <el-input v-model="filterText" placeholder="输入关键字过滤文件..." style="margin-bottom: 15px;" clearable />

        <el-tree
            ref="treeRef"
            class="filter-tree"
            :data="fileTree"
            :props="defaultProps"
            default-expand-all
            :filter-node-method="filterNode"
            @node-click="handleNodeClick"
        >
          <template #default="{ node, data }">
            <span class="custom-tree-node">
              <el-icon :color="data.is_dir ? '#409EFF' : '#67C23A'" style="margin-right: 6px;">
                <component :is="data.is_dir ? 'Folder' : 'Document'" />
              </el-icon>
              <span :class="{'file-text': !data.is_dir, 'dir-text': data.is_dir}">{{ node.label }}</span>
            </span>
          </template>
        </el-tree>
      </el-card>

      <!-- 右侧：核心分析视图与插件扩展插槽 -->
      <div class="right-panel">

        <!-- 架构视图与当前选中详情 -->
        <el-card class="detail-card">
          <template #header>
            <div class="card-header">
              <span>🔍 模块与代码详情视图</span>
            </div>
          </template>
          <div v-if="selectedNode">
            <p><strong>当前选中路径：</strong> {{ selectedNode.path }}</p>
            <p><strong>类型：</strong> {{ selectedNode.is_dir ? '目录/模块' : '代码文件' }}</p>
            <el-alert title="后续这里将渲染：1. 模块架构总结；2. 函数级依赖关系图 (NetworkX)" type="info" :closable="false" style="margin-top: 15px;" />
          </div>
          <div v-else class="empty-tip">
            请在左侧点击任意文件或目录查看详情
          </div>
        </el-card>

        <!-- 插件扩展区域（例如：漏洞挖掘插件运行结果展示） -->
        <el-card class="plugin-card">
          <template #header>
            <div class="card-header">
              <span>🔌 插件运行结果区 (如: 漏洞挖掘与风险排序)</span>
              <el-tag size="small" type="warning">插槽已就绪</el-tag>
            </div>
          </template>

          <!-- 预留插件插槽：后续可以通过组件动态挂载不同插件的UI -->
          <div class="plugin-slot-container">
            <slot name="extension-plugins">
              <el-empty description="当前没有激活的插件。后续可加载漏洞挖掘插件查看风险函数排序。">
                <el-button type="success" plain @click="simulateVulnPlugin">模拟运行漏洞挖掘插件</el-button>
              </el-empty>
            </slot>

            <!-- 模拟插件运行后的结果展示（演示效果） -->
            <div v-if="pluginResults.length > 0" class="vuln-results">
              <el-table :data="pluginResults" style="width: 100%" border size="small">
                <el-table-column prop="level" label="风险等级" width="100">
                  <template #default="scope">
                    <el-tag :type="scope.row.level === 'High' ? 'danger' : 'warning'">{{ scope.row.level }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="function" label="疑似漏洞函数/代码点" />
                <el-table-column prop="score" label="可能性评分" width="100" sortable />
              </el-table>
            </div>
          </div>
        </el-card>

      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Folder, Document, UploadFilled } from '@element-plus/icons-vue'
import axios from 'axios'

// 状态定义
const repoUrl = ref('')
const loading = ref(false)
const fileTree = ref([])
const filterText = ref('')
const treeRef = ref()
const selectedNode = ref(null)
const pluginResults = ref([])

// Element Plus 树形控件字段映射
const defaultProps = {
  children: 'children',
  label: 'name',
}

// 监听过滤输入
watch(filterText, (val) => {
  treeRef.value!.filter(val)
})

const filterNode = (value, data) => {
  if (!value) return true
  return data.name.toLowerCase().includes(value.toLowerCase())
}

// 1. 处理 Git 分析请求
const handleAnalyzeGit = async () => {
  if (!repoUrl.value) {
    ElMessage.warning('请输入有效的 Git 仓库链接！')
    return
  }
  loading.value = true
  try {
    const formData = new FormData()
    formData.append('repo_url', repoUrl.value)

    const res = await axios.post('http://localhost:8000/api/projects/analyze', formData)
    if (res.data.code === 200) {
      fileTree.value = res.data.data.file_tree
      ElMessage.success(`项目加载成功！已安全清洗 ${res.data.data.sanitize_report.filtered_out_files} 个违规/噪音文件。`)
    } else {
      ElMessage.error(res.data.message)
    }
  } catch (error) {
    ElMessage.error('网络请求失败或后端服务异常')
  } finally {
    loading.value = false
  }
}

// 2. 处理本地 Zip 上传请求
const handleCustomUpload = async (options) => {
  loading.value = true
  try {
    const formData = new FormData()
    formData.append('file', options.file)

    const res = await axios.post('http://localhost:8000/api/projects/analyze', formData)
    if (res.data.code === 200) {
      fileTree.value = res.data.data.file_tree
      ElMessage.success(`本地项目上传并清洗成功！`)
    } else {
      ElMessage.error(res.data.message)
    }
  } catch (error) {
    ElMessage.error('上传失败')
  } finally {
    loading.value = false
  }
}

// 3. 点击文件树节点
const handleNodeClick = (data) => {
  selectedNode.value = data
}

// 4. 模拟插件运行（例如：漏洞挖掘插件）
const simulateVulnPlugin = () => {
  ElMessage.info('正在调用漏洞挖掘插件扫描代码树...')
  setTimeout(() => {
    pluginResults.value = [
      { level: 'High', function: 'eval_execute() in utils.py', score: 95 },
      { level: 'Medium', function: 'sql_query_concat() in db.py', score: 78 },
      { level: 'Low', function: 'unvalidated_redirect() in router.js', score: 45 },
    ]
    ElMessage.success('插件执行完毕，已按可能性从高到低排序！')
  }, 1000)
}
</script>

<style scoped>
.insight-container {
  padding: 20px;
  max-width: 1400px;
  margin: 0 auto;
}
.import-card {
  margin-bottom: 20px;
}
.main-content-layout {
  display: flex;
  gap: 20px;
}
.tree-card {
  width: 350px;
  min-height: 600px;
}
.right-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.detail-card, .plugin-card {
  min-height: 250px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.empty-tip {
  color: #909399;
  text-align: center;
  padding: 40px 0;
}
.file-text {
  color: #606266;
}
.dir-text {
  font-weight: bold;
  color: #303133;
}
.vuln-results {
  margin-top: 15px;
}
</style>