<template>
  <div class="path-detail">
    <div class="path-heading">
      <el-tag size="small" :type="route.kind === 'propagation_path' ? 'primary' : 'info'">{{ pathKindLabel(route.kind) }}</el-tag>
      <span>{{ route.category }} · {{ unpaired ? `规则等级 ${route.severity}（非漏洞等级）` : `置信度 ${route.confidence}` }}</span>
      <span v-if="route.truncated || route.projection_missing || route.path_status === 'incomplete'" class="warning">分析或显示不完整</span>
    </div>
    <el-collapse>
      <el-collapse-item name="details" :title="unpaired ? '查看敏感操作定位（未建立传播路线）' : `查看路径定位与传播步骤（${route.step_count}）`">
        <div class="detail-scroll">
          <div v-if="route.source" class="endpoint"><b>Source</b> {{ route.source.name }} · {{ route.source.trust_class }}<br>{{ pathLocation(route.source.location) }}</div>
          <div v-else class="warning">没有已保存的 Source 配对；不代表不存在输入来源，也不代表该操作构成漏洞。</div>
          <div class="endpoint"><b>Sink</b> {{ route.sink.name }}<br>{{ pathLocation(route.sink.location) }}</div>
          <div v-if="unpaired" class="explanation">操作 ID：{{ route.id }}<br>规则：{{ route.rule_id }}；显示缺失 {{ route.projection_missing }}。灰色细箭头仅表示已有调用结构，未证明值传播；此条不是路径族成员。</div>
          <div v-else class="explanation">
            <div>路线 ID：{{ route.id }}<br>路径族 ID：{{ route.path_group_id }}</div>
            {{ route.kind === 'propagation_path' ? '静态值传播推断，不等于可实际利用。' : '仅有调用或共现线索，未证明输入到达敏感参数。' }}
            未解析边界 {{ route.unresolved_count }}；显示缺失 {{ route.projection_missing }}。
            路径状态：{{ route.path_status }}。
            <template v-if="route.condition_statuses.length">条件核验：{{ route.condition_statuses.join(' / ') }}（模型内结果，不是项目安全结论）。</template>
          </div>
          <div v-if="Object.keys(route.call_connection_counts || {}).length" class="explanation">
            调用目标依据：<span v-for="(count, status) in route.call_connection_counts" :key="status">{{ connectionStatusLabel(status) }} {{ count }}；</span>
            保守绑定不等于虚连接；此计数不证明值传播或实际执行。
          </div>
          <div v-if="loading">正在读取已保存步骤…</div>
          <div v-else-if="error" class="warning">{{ error }} <el-button link @click="$emit('retry')">重试详情</el-button></div>
          <template v-else-if="detail">
            <div v-for="item in detail.call_connections || []" :key="item.call_edge_id" class="explanation">
              <b>{{ connectionStatusLabel(item.status) }}</b> · {{ pathLocation(item.location) }}<br>
              {{ item.speculative_target ? '候选目标（虚线）' : '已记录调用关系（实线）' }}<br>
              {{ item.source }} → {{ item.target }}<br>
              解析方式 {{ item.resolution_method }}；目标 {{ item.target_certainty }}；置信度 {{ item.confidence }}<template v-if="item.truncated">；解析达到上限</template>。
            </div>
            <div v-for="item in detail.boundaries || []" :key="item.id" class="warning">
              <b>传播边界</b> · {{ pathLocation(item.location) }}<br>{{ item.reasons.map(boundaryReasonLabel).join('；') }}
            </div>
            <ol>
              <li v-for="(step, index) in detail.steps" :key="index">
                <b>{{ stepLabels[step.kind] || step.kind }}</b> · {{ pathLocation(step.location) }} · {{ step.certainty }}
                <div>{{ step.symbol }}</div>
                <div v-if="step.uses.length || step.defines.length">使用 [{{ step.uses.join(', ') }}] → 定义 [{{ step.defines.join(', ') }}]</div>
              </li>
            </ol>
            <div v-for="(items, kind) in { guard: detail.guards, sanitizer: detail.sanitizers }" :key="kind">
              <template v-if="items.length">
                <b>{{ kind === 'guard' ? 'Guard 条件观察' : 'Sanitizer 调用观察' }}（不代表防护有效）</b>
                <div v-for="(item, index) in items" :key="index">{{ item.name }} · {{ pathLocation(item.location) }}</div>
              </template>
            </div>
            <div v-if="detail.value_boundary_count">包含 {{ detail.value_boundary_count }} 个公共值边界；画布仅保留已记录调用边，值边界不能伪装成额外调用。</div>
            <div v-if="detail.unavailable_locations" class="warning">{{ detail.unavailable_locations }} 个步骤没有可公开的源码位置。</div>
          </template>
        </div>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { pathKindLabel, pathLocation, boundaryReasonLabel, connectionStatusLabel } from '../domain/securityPaths.js'
const props = defineProps({ route: { type: Object, required: true }, detail: Object, loading: Boolean, error: String })
const unpaired = computed(() => props.route.kind === 'unpaired_operation')
defineEmits(['retry'])
const stepLabels = { source: '输入', sink: '敏感操作', assignment: '赋值', call: '调用', return: '返回', sanitizer: '净化调用观察' }
</script>

<style scoped>
.path-detail { padding: 4px 12px 0; border-bottom: 1px solid #ebeef5; }
.path-heading { display: flex; gap: 8px; flex-wrap: wrap; font-size: 12px; color: #606266; }
.detail-scroll { max-height: 180px; overflow: auto; overflow-wrap: anywhere; font-size: 12px; line-height: 1.6; }
.endpoint, .explanation, li { margin-bottom: 7px; }
.warning { color: #b36b00; }
ol { padding-left: 22px; }
</style>
