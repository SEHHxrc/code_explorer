<template>
  <transition name="slide">
    <div v-if="node || edge" class="detail-card">
      <template v-if="node">
        <div class="detail-head">
          <span class="swatch" :style="{ background: node.color }" />
          <span class="detail-name" :title="node.id">{{ node.label }}</span>
          <button class="close-btn" @click="$emit('clear')">✕</button>
        </div>
        <div class="detail-meta">
          <el-tag size="small" effect="dark">{{ node.kind }}</el-tag>
          <span v-if="node.file" class="detail-file" :title="node.file">
            {{ node.file }}<template v-if="node.line">:{{ node.line }}</template>
          </span>
        </div>
        <div class="detail-actions">
          <el-button size="small" :type="focusMode ? 'primary' : ''" @click="$emit('toggle-focus')">
            {{ focusMode ? '退出单跳聚焦' : '只看单跳邻域' }}
          </el-button>
          <el-button size="small" plain type="danger" @click="$emit('hide-node')">隐藏节点</el-button>
        </div>
        <div v-if="outgoing.length" class="detail-section">
          <div class="section-title">依赖出去 ({{ outgoing.length }})</div>
          <div v-for="item in outgoing.slice(0, 40)" :key="`o-${item.id}`" class="detail-row" @click="$emit('select-node', item.id)">
            <span class="rel-tag" :style="{ color:item.color, borderColor:item.color }">{{ item.rel }}</span>
            <span class="row-name" :title="item.id">{{ item.label }}</span>
          </div>
        </div>
        <div v-if="incoming.length" class="detail-section">
          <div class="section-title">被谁依赖 ({{ incoming.length }})</div>
          <div v-for="item in incoming.slice(0, 40)" :key="`i-${item.id}`" class="detail-row" @click="$emit('select-node', item.id)">
            <span class="rel-tag" :style="{ color:item.color, borderColor:item.color }">{{ item.rel }}</span>
            <span class="row-name" :title="item.id">{{ item.label }}</span>
          </div>
        </div>
        <div v-if="!outgoing.length && !incoming.length" class="detail-empty">该节点在当前筛选下没有可见的关系</div>
      </template>

      <template v-else-if="edge">
        <div class="detail-head">
          <span class="swatch line" :style="{ background:edge.color }" />
          <span class="detail-name" :title="edge.id">{{ edge.source.label }} → {{ edge.target.label }}</span>
          <button class="close-btn" @click="$emit('clear')">✕</button>
        </div>
        <div class="detail-meta">
          <span class="rel-tag" :style="{ color:edge.color, borderColor:edge.color }">{{ edge.relationLabel }}</span>
          <el-tag v-if="edge.dispatch" size="small" effect="plain">{{ edge.dispatch }} dispatch</el-tag>
        </div>
        <div v-for="endpoint in [edge.source, edge.target]" :key="endpoint.id" class="detail-section">
          <div class="section-title">{{ endpoint.id === edge.source.id ? '起点' : '终点' }}</div>
          <div class="detail-row endpoint-row" @click="$emit('select-node', endpoint.id)">
            <span class="swatch" :style="{ background:endpoint.color }" />
            <span class="row-name" :title="endpoint.id">{{ endpoint.label }}</span>
            <span class="endpoint-kind">{{ endpoint.kind }}</span>
          </div>
        </div>
      </template>
    </div>
  </transition>
</template>

<script setup>
defineProps({
  node: { type: Object, default: null },
  edge: { type: Object, default: null },
  outgoing: { type: Array, default: () => [] },
  incoming: { type: Array, default: () => [] },
  focusMode: Boolean,
})
defineEmits(['clear', 'toggle-focus', 'hide-node', 'select-node'])
</script>

<style scoped>
.detail-card { position:absolute; top:12px; right:12px; width:300px; max-height:calc(100% - 70px); overflow-y:auto; padding:12px; background:rgba(255,255,255,.98); border:1px solid #e4e7ed; border-radius:8px; box-shadow:0 4px 18px rgba(31,45,61,.1); backdrop-filter:blur(6px); z-index:5; }
.detail-head { display:flex; align-items:center; gap:7px; }
.detail-name { flex:1; font-family:ui-monospace,Consolas,monospace; font-size:13px; color:#303133; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.close-btn { background:none; border:none; color:#c0c4cc; cursor:pointer; font-size:13px; padding:0 2px; }
.close-btn:hover { color:#606266; }
.detail-meta { display:flex; align-items:center; gap:8px; margin:8px 0 4px; }
.detail-actions { display:flex; gap:6px; margin:10px 0 2px; }
.detail-file { font-size:11px; color:#909399; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.detail-section { margin-top:12px; }
.section-title { font-size:11px; color:#909399; letter-spacing:.3px; margin-bottom:5px; }
.detail-row { display:flex; align-items:center; gap:7px; padding:3px 4px; border-radius:4px; cursor:pointer; }
.detail-row:hover { background:#f5f7fa; }
.rel-tag { flex-shrink:0; font-size:10px; padding:0 5px; border:1px solid; border-radius:3px; filter:brightness(.82); }
.row-name { flex:1; font-family:ui-monospace,Consolas,monospace; font-size:12px; color:#606266; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.endpoint-row { padding:6px; }
.endpoint-kind { flex-shrink:0; color:#a8abb2; font-size:10px; }
.detail-empty { margin-top:10px; font-size:12px; color:#909399; }
.swatch { width:10px; height:10px; border-radius:50%; flex-shrink:0; }
.swatch.line { width:14px; height:3px; border-radius:2px; }
.slide-enter-active,.slide-leave-active { transition:all .22s ease; }
.slide-enter-from,.slide-leave-to { opacity:0; transform:translateX(12px); }
</style>
