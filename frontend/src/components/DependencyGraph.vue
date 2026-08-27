<template>
  <FeatureDependencyGraph
    :graph-data="props.graphData"
    @select="emit('select', $event)"
    @select-edge="emit('select-edge', $event)"
  />
</template>

<script setup>
import FeatureDependencyGraph from '../features/dependency-graph/DependencyGraph.vue'

const props = defineProps({
  graphData: { type: Object, default: () => ({ nodes: [], edges: [] }) },
})
const emit = defineEmits(['select', 'select-edge'])
</script>
