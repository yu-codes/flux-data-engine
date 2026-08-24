<template>
  <div class="empty-state">
    <q-icon :name="icon" size="32px" class="q-mb-sm" />
    <div>{{ message }}</div>
    <div v-if="hint" class="text-caption q-mt-xs">{{ hint }}</div>
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ message: string; hint?: string; icon?: string }>(), {
  icon: 'inbox',
})
</script>
