<template>
  <pre class="mono json-block">{{ pretty }}</pre>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ value: unknown }>()

const pretty = computed(() => {
  try {
    return JSON.stringify(props.value, null, 2)
  } catch {
    return String(props.value)
  }
})
</script>

<style scoped>
.json-block {
  margin: 0;
  padding: 10px 12px;
  border-radius: 6px;
  background: rgba(128, 145, 160, 0.12);
  max-height: 420px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
