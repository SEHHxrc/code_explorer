<template>
  <section v-if="review" class="saved-review" aria-label="已保存的盲评结果">
    <h4>已保存的盲评结果 · {{ reviewPreference(review.preferred_lane) }}</h4>
    <p>分数为人工评价，不代表系统已验证结论正确。每项满分 5 分；缺失历史分数不补填。</p>
    <table>
      <thead><tr><th>答案</th><th v-for="label in dimensions" :key="label">{{ label }}</th></tr></thead>
      <tbody><tr v-for="lane in ['left', 'right']" :key="lane">
        <th>{{ lane === 'left' ? '左侧' : '右侧' }}</th>
        <td v-for="(_, key) in dimensions" :key="key">{{ review.scores?.[lane]?.[key] ?? '未记录' }}</td>
      </tr></tbody>
    </table>
    <p class="notes">评审备注：{{ review.notes || '未填写' }}</p>
  </section>
</template>

<script setup>
import { reviewPreference } from './history.js'
defineProps({ review: { type: Object, default: null } })
const dimensions = { correctness: '正确性', completeness: '完整性', evidence: '证据充分', hallucination_control: '幻觉控制' }
</script>

<style scoped>
.saved-review { margin: 14px 0; padding: 12px; border: 1px solid #dcdfe6; border-radius: 6px; }
table { width: 100%; border-collapse: collapse; text-align: center; }
th, td { padding: 8px 4px; border-bottom: 1px solid #ebeef5; }
p { font-size: 12px; color: #606266; }
.notes { white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
