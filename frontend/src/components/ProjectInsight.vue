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
          <el-input v-model="repoUrl" placeholder="https://github.com/xxx/xxx" style="width: 300px" clearable />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="loading" @click="handleAnalyzeGit">分析 Git 项目</el-button>
          <!-- 清空重置按钮 -->
          <el-button v-if="fileTree.length > 0" type="danger" plain @click="handleReset">清空当前项目</el-button>
        </el-form-item>
      </el-form>

      <el-divider>或者本地上传</el-divider>

      <el-upload
        class="upload-demo"
        drag
        action="#"
        :auto-upload="false"
        :http-request="handleCustomUpload"
        :on-change="handleFileChange"
        :limit="1"
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

        <!-- 宏观架构视图：点击左侧文件后显示对应的包含关系（类与函数） -->
        <el-card class="detail-card">
          <template #header>
            <div class="card-header">
              <span>🔍 宏观架构视图 (当前文件的包含关系)</span>
              <el-tag v-if="selectedNode" size="small" type="primary">{{ selectedNode.path }}</el-tag>
            </div>
          </template>

          <div v-if="selectedNode">
            <div v-if="!selectedNode.is_dir">
              <div v-if="fileHierarchy.length > 0">
                <p class="arch-title">📦 该文件包含的类与函数结构：</p>
                  <el-tree
                    :data="fileHierarchy"
                    :props="{ label: 'name', children: 'children' }"
                    default-expand-all
                    class="arch-tree"
                  >
                    <template #default="{ node, data }">
                      <span class="custom-tree-node" style="display: flex; justify-content: space-between; width: 100%;">
                        <div>
                          <el-tag size="small" :type="data.type === 'class' ? 'success' : 'warning'" style="margin-right: 8px;">
                            {{ data.type }}
                          </el-tag>
                          <span>{{ node.label }}</span>
                        </div>
                        <span style="color: #909399; font-size: 12px; margin-right: 15px;">Line {{ data.line }}</span>
                      </span>
                    </template>
                  </el-tree>
              </div>
              <div v-else class="empty-tip" style="padding: 10px 0;">
                该文件没有检测到类或函数定义（可能是纯配置或静态资源文件）。
              </div>
            </div>
            <div v-else class="empty-tip" style="padding: 10px 0;">
              您当前点击的是目录：<strong>{{ selectedNode.name }}</strong>，请点击具体的代码文件查看宏观架构。
            </div>
          </div>
          <div v-else class="empty-tip">
            请在左侧点击任意代码文件，查看其对应的宏观架构与包含关系
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
// 保存当前分析成功的项目 ID
const currentProjectId = ref('')

// Element Plus 树形控件字段映射
const defaultProps = {
  children: 'children',
  label: 'name',
}

// 监听过滤输入
watch(filterText, (val) => {
  treeRef.value?.filter(val)
})

const filterNode = (value, data) => {
  if (!value) return true
  return data.name.toLowerCase().includes(value.toLowerCase())
}

const getTagType = (type) => {
  switch (type) {
    case 'class': return 'success'
    case 'method': return 'warning'
    case 'function': return ''
    case 'variable': return 'primary'
    case 'property': return 'danger'
    case 'constant': return 'info'
    default: return 'info'
  }
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
      currentProjectId.value = res.data.data.project_id
      // 接收后端的多语言图谱数据
      console.log('后端返回的完整图谱数据:', res.data.data.dependency_graph)
      dependencyGraph.value = res.data.data.dependency_graph || { nodes: [], links: [] }
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

// 2. 处理本地 Zip 上传请求, 当文件被选择或拖入时触发
const handleFileChange = (uploadFile) => {
  if (uploadFile && uploadFile.raw) {
    handleCustomUpload(uploadFile)
  }
}

// 对应修改后的自定义上传函数
const handleCustomUpload = async (uploadItem) => {
  loading.value = true
  try {
    const formData = new FormData()
    // 兼容 el-upload 的 raw 文件对象
    const file = uploadItem.raw || uploadItem
    formData.append('file', file)

    const res = await axios.post('http://localhost:8000/api/projects/analyze', formData)
    if (res.data.code === 200) {
      fileTree.value = res.data.data.file_tree
      currentProjectId.value = res.data.data.project_id
      // 接收后端的多语言图谱数据
      console.log('后端返回的完整图谱数据:', res.data.data.dependency_graph)
      dependencyGraph.value = res.data.data.dependency_graph || { nodes: [], links: [] }
      ElMessage.success(`本地项目上传并清洗成功！`)
    } else {
      ElMessage.error(res.data.message)
    }
  } catch (error) {
    ElMessage.error('上传失败，请检查后端服务')
  } finally {
    loading.value = false
  }
}

// 3. 点击文件树节点
// 保存后端返回的完整依赖图
const dependencyGraph = ref({ nodes: [], links: [] })
// 当前选中文件内部包含的结构化层级数据（供右侧展示）
const fileHierarchy = ref([])
// 点击左侧文件树节点时触发
const handleNodeClick = (data) => {
  selectedNode.value = data
  fileHierarchy.value = []

  if (!data.is_dir && data.symbols) {
    // data.symbols 直接就是符号列表
    console.log('当前文件的 GitHub 风格符号:', data.symbols)

    const classesMap = {}
    const topLevelFunctions = []
    const topLevelConstants = []

    data.symbols.forEach(sym => {
      if (sym.kind === 'class') {
        classesMap[sym.fully_qualified_name] = {
          name: sym.name,
          type: 'class',
          line: sym.extent_utf16.start.line_number,
          children: []
        }
      }else if (sym.kind === 'constant') {
        // 检查这个 constant 是属于某个类（带点号），还是顶层全局常量
        if (sym.fully_qualified_name.includes('.')) {
          const parts = sym.fully_qualified_name.split('.')
          const className = parts[0]
          const attrName = parts.slice(1).join('.')

          if (classesMap[className]) {
            classesMap[className].children.push({
              name: attrName,
              type: 'property', // 识别为类属性
              line: sym.extent_utf16.start.line_number
            })
          }
        } else {
          topLevelConstants.push({
            name: sym.name,
            type: 'constant',
            line: sym.extent_utf16.start.line_number
          })
        }
      } else if (sym.kind === 'function') {
        // 判断是类方法还是函数
        if (sym.fully_qualified_name.includes('.')) {
          const className = sym.fully_qualified_name.split('.')[0]
          // 寻找对应的类
          const targetClassKey = Object.keys(classesMap).find(k => k === className || k.endsWith('.' + className))
          if (targetClassKey && classesMap[targetClassKey]) {
            classesMap[targetClassKey].children.push({
              name: sym.name,
              type: 'method',
              line: sym.extent_utf16.start.line_number
            })
          } else {
            topLevelFunctions.push({
              name: sym.name,
              type: 'function',
              line: sym.extent_utf16.start.line_number
            })
          }
        } else {
          topLevelFunctions.push({
            name: sym.name,
            type: 'function',
            line: sym.extent_utf16.start.line_number
          })
        }
      }
    })

    const resultTree = []
    if (topLevelConstants.length > 0) {
      resultTree.push({ name: 'Constants', type: 'category', children: topLevelConstants })
    }
    Object.values(classesMap).forEach(cls => resultTree.push(cls))
    if (topLevelFunctions.length > 0) {
      resultTree.push({ name: 'Functions', type: 'category', children: topLevelFunctions })
    }
    fileHierarchy.value = resultTree
  }
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

// 前后端同步清理项目
const handleReset = async () => {
  if (currentProjectId.value) {
    try {
      // 调用后端清理接口释放服务器资源
      await axios.delete(`http://localhost:8000/api/projects/clear/${currentProjectId.value}`)
    } catch (e) {
      console.error('Backend cleanup notification failed:', e)
    }
  }

  // 重置前端状态
  fileTree.value = []
  repoUrl.value = ''
  selectedNode.value = null
  pluginResults.value = []
  filterText.value = ''
  currentProjectId.value = ''

  ElMessage.info('已清空当前项目，服务器临时缓存和前端视图均已释放。')
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
.network-card {
  min-height: 200px;
}
.empty-network-area {
  color: #909399;
  text-align: center;
  padding: 30px;
  background-color: #fafafa;
  border: 1px dashed #dcdfe6;
  border-radius: 4px;
}
.arch-title {
  font-weight: bold;
  font-size: 14px;
  color: #303133;
  margin-bottom: 10px;
}
.arch-tree {
  background: #fcfcfc;
  border-radius: 4px;
  padding: 5px;
}
</style>