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
        <div v-if="node.security" class="detail-section">
          <div class="section-title">{{ node.security.pathCount ? `属于 ${node.security.pathCount} 条已保存路线（非漏洞计数）` : '尚无已保存的 Source→Sink 路线' }}</div>
          <el-tag v-for="role in node.security.roles" :key="role" size="small" effect="plain">{{ role }}</el-tag>
        </div>
        <div v-if="node.security?.operations?.length" class="detail-section">
          <div class="section-title">未配对敏感操作（{{ node.security.operations.length }}，非漏洞计数）</div>
          <div v-for="item in node.security.operations.slice(0, 20)" :key="item.id" class="operation-row">
            <b>{{ item.sink.name }}</b><br>{{ operationLocation(item.sink.location) }}
          </div>
          <div class="detail-empty">尚未建立输入传播链路。<template v-if="node.security.operations.length > 20">此处只展开前 20 项，其余可通过顶部选择器查看。</template></div>
        </div>
        <div v-if="node.security?.boundaries?.length" class="detail-section">
          <div class="section-title">传播在此未闭合（不代表安全或漏洞成立）</div>
          <div v-for="item in node.security.boundaries" :key="item.id" class="operation-row">
            {{ operationLocation(item.location) }}<br>{{ item.labels.join('；') }}
          </div>
        </div>
        <div class="detail-actions">
          <el-button size="small" :type="focusMode ? 'primary' : ''" @click="$emit('toggle-focus')">
            {{ focusMode ? '退出单跳聚焦' : '只看单跳邻域' }}
          </el-button>
          <el-button size="small" plain type="danger" @click="$emit('hide-node')">隐藏节点</el-button>
        </div>
        <div v-if="outgoing.length" class="detail-section">
          <div class="section-title">依赖出去 ({{ outgoing.length }})</div>
          <div v-for="item in outgoing.slice(0, 40)" :key="`o-${item.edgeId}`" class="detail-row" @click="$emit('select-node', item.id)">
            <span class="rel-tag" :style="{ color:item.color, borderColor:item.color }">{{ item.rel }}</span>
            <span class="row-name" :title="item.id">{{ item.label }}</span>
          </div>
        </div>
        <div v-if="incoming.length" class="detail-section">
          <div class="section-title">被谁依赖 ({{ incoming.length }})</div>
          <div v-for="item in incoming.slice(0, 40)" :key="`i-${item.edgeId}`" class="detail-row" @click="$emit('select-node', item.id)">
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
        <div v-if="edge.security?.structural" class="detail-empty">仅有调用结构，尚未证明输入值经此边到达敏感参数。</div>
        <div v-if="edge.security?.possible" class="detail-empty">虚线：包含动态派发或启发式解析的候选目标。可在路径详情核对解析方式和调用位置；不表示链路已闭合。</div>
        <div v-for="endpoint in [edge.source, edge.target]" :key="endpoint.id" class="detail-section">
          <div class="section-title">{{ endpoint.id === edge.source.id ? '起点' : '终点' }}</div>
          <div class="detail-row endpoint-row" @click="$emit('select-node', endpoint.id)">
            <span class="swatch" :style="{ background:endpoint.color }" />
            <span class="row-name" :title="endpoint.id">{{ endpoint.label }}</span>
            <span class="endpoint-kind">{{ endpoint.kind }}</span>
          </div>
        </div>
      </template>
      <div v-if="security?.families?.length" class="detail-section">
        <div class="section-title">路径族归属<template v-if="security.shared"> · 跨族共享</template></div>
        <div class="family-tags">
          <span v-for="family in security.families" :key="family.id" class="family-tag" :title="family.title || family.label">
            <span class="swatch" :style="{ background:family.color }" />{{ family.label }}
          </span>
        </div>
        <div v-if="edge" class="detail-empty">属于 {{ security.pathCount }} 条已保存路线（非漏洞计数）</div>
      </div>
    </div>
  </transition>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  node: { type: Object, default: null },
  edge: { type: Object, default: null },
  outgoing: { type: Array, default: () => [] },
  incoming: { type: Array, default: () => [] },
  focusMode: Boolean,
})
const security = computed(() => props.node?.security || props.edge?.security)
const operationLocation = (location) => location ? `${location.path}:${location.line}` : '位置不可用'
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
.operation-row { margin:5px 0; font-size:12px; color:#606266; overflow-wrap:anywhere; }
.family-tags { display:flex; flex-wrap:wrap; gap:6px 10px; }
.family-tag { display:inline-flex; align-items:center; gap:5px; font-size:11px; color:#475569; }
.swatch { width:10px; height:10px; border-radius:50%; flex-shrink:0; }
.swatch.line { width:14px; height:3px; border-radius:2px; }
.slide-enter-active,.slide-leave-active { transition:all .22s ease; }
.slide-enter-from,.slide-leave-to { opacity:0; transform:translateX(12px); }
</style>
