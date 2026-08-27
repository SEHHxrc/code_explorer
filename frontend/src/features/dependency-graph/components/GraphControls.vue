<template>
  <div class="toolbar">
    <div class="toolbar-group search-group">
      <el-input
        :model-value="searchText"
        size="small"
        placeholder="搜索符号（回车定位）"
        clearable
        @update:model-value="$emit('update:searchText', $event)"
        @keyup.enter="$emit('focus-search')"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <span v-if="searchText" class="search-count">{{ searchMatchCount }} 个匹配</span>
    </div>

    <div class="toolbar-group action-group">
      <el-button-group class="preset-group">
        <el-button
          v-for="preset in presets"
          :key="preset.key"
          size="small"
          :type="viewPreset === preset.key ? 'primary' : ''"
          @click="$emit('preset', preset.key)"
        >{{ preset.label }}</el-button>
      </el-button-group>

      <el-popover placement="bottom" :width="260" trigger="click">
        <template #reference>
          <el-button size="small" plain>
            <el-icon><Filter /></el-icon>&nbsp;节点 ({{ activeLevels.size }}/{{ nodeLevels.length }})
          </el-button>
        </template>
        <div class="filter-panel">
          <div class="filter-head">
            <span>显示的节点类型</span>
            <el-link type="primary" :underline="false" @click="$emit('toggle-all-levels')">
              {{ activeLevels.size === nodeLevels.length ? '全不选' : '全选' }}
            </el-link>
          </div>
          <label v-for="level in nodeLevels" :key="level.key" class="filter-row">
            <el-checkbox
              :model-value="activeLevels.has(level.key)"
              @change="$emit('toggle-level', level.key)"
            />
            <span class="swatch" :style="{ background: level.color }" />
            <span class="filter-name">{{ level.label }}</span>
            <span class="filter-num">{{ levelCounts[level.key] || 0 }}</span>
          </label>
        </div>
      </el-popover>

      <el-popover placement="bottom" :width="250" trigger="click">
        <template #reference>
          <el-button size="small" plain>
            <el-icon><Share /></el-icon>&nbsp;关系 ({{ activeRelations.size }}/{{ edgeRelations.length }})
          </el-button>
        </template>
        <div class="filter-panel">
          <div class="filter-head">
            <span>显示的关系</span>
            <el-link type="primary" :underline="false" @click="$emit('toggle-all-relations')">
              {{ activeRelations.size === edgeRelations.length ? '全不选' : '全选' }}
            </el-link>
          </div>
          <label v-for="relation in edgeRelations" :key="relation.key" class="filter-row">
            <el-checkbox
              :model-value="activeRelations.has(relation.key)"
              @change="$emit('toggle-relation', relation.key)"
            />
            <span class="swatch line" :style="{ background: relation.color }" />
            <span class="filter-name">{{ relation.label }}</span>
            <span class="filter-num">{{ relationCounts[relation.key] || 0 }}</span>
          </label>
        </div>
      </el-popover>

      <el-popover placement="bottom" :width="270" trigger="click">
        <template #reference><el-button size="small" plain>重要度 ≥ {{ minDegree }}</el-button></template>
        <div class="importance-panel">
          <div class="filter-head"><span>最小连接数</span><span class="filter-num">保留文件与包节点</span></div>
          <el-slider
            :model-value="minDegree"
            :min="0"
            :max="degreeSliderMax"
            :step="1"
            show-input
            :show-input-controls="false"
            @change="$emit('degree', $event)"
          />
        </div>
      </el-popover>

      <el-button v-if="hiddenCount" size="small" plain @click="$emit('restore-hidden')">
        恢复隐藏 ({{ hiddenCount }})
      </el-button>
      <el-button size="small" plain :disabled="!ready" @click="$emit('restart-layout')">
        <el-icon><Refresh /></el-icon>&nbsp;重新布局
      </el-button>
      <el-button size="small" plain :disabled="!ready" @click="$emit('toggle-layout')">
        <el-icon><VideoPause v-if="layoutRunning" /><VideoPlay v-else /></el-icon>
        &nbsp;{{ layoutRunning ? '暂停' : '继续' }}
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { Search, Filter, Share, Refresh, VideoPlay, VideoPause } from '@element-plus/icons-vue'

defineProps({
  searchText: { type: String, default: '' },
  searchMatchCount: { type: Number, default: 0 },
  presets: { type: Array, required: true },
  viewPreset: { type: String, default: 'core' },
  nodeLevels: { type: Array, required: true },
  edgeRelations: { type: Array, required: true },
  activeLevels: { type: Object, required: true },
  activeRelations: { type: Object, required: true },
  levelCounts: { type: Object, default: () => ({}) },
  relationCounts: { type: Object, default: () => ({}) },
  minDegree: { type: Number, default: 0 },
  degreeSliderMax: { type: Number, default: 1 },
  hiddenCount: { type: Number, default: 0 },
  ready: Boolean,
  layoutRunning: Boolean,
})

defineEmits([
  'update:searchText', 'focus-search', 'preset', 'toggle-level', 'toggle-all-levels',
  'toggle-relation', 'toggle-all-relations', 'degree', 'restore-hidden',
  'restart-layout', 'toggle-layout',
])
</script>

<style scoped>
.toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:10px 14px; background:#fafbfc; border-bottom:1px solid #ebeef5; flex-wrap:wrap; }
.toolbar-group { display:flex; align-items:center; gap:8px; }
.action-group { flex-wrap:wrap; justify-content:flex-end; }
.preset-group { flex-shrink:0; }
.search-group { flex:1; max-width:340px; min-width:220px; }
.search-count { font-size:12px; color:#909399; white-space:nowrap; }
.filter-panel,.importance-panel { padding:2px; }
.filter-head { display:flex; justify-content:space-between; align-items:center; font-size:12px; color:#909399; margin-bottom:8px; }
.filter-row { display:flex; align-items:center; gap:7px; padding:3px 0; cursor:pointer; }
.filter-name { flex:1; font-size:13px; }
.filter-num { font-size:11px; color:#a8abb2; }
.swatch { width:10px; height:10px; border-radius:50%; flex-shrink:0; }
.swatch.line { width:14px; height:3px; border-radius:2px; }
</style>
