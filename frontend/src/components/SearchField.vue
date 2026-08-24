<template>
  <q-input
    :model-value="modelValue"
    :placeholder="placeholder"
    dense
    outlined
    clearable
    debounce="200"
    class="fx-search"
    @update:model-value="(value) => emit('update:modelValue', (value as string) ?? '')"
  >
    <template #prepend><q-icon name="search" /></template>
  </q-input>
</template>

<script setup lang="ts">
/**
 * The one search box. Every list that can grow past a screenful gets this and
 * nothing else, so finding something works the same way everywhere.
 *
 * Debounced rather than submit-on-enter: filtering a list is a continuous
 * narrowing, and making someone press a key to see the effect turns a glance
 * into a transaction.
 */
withDefaults(defineProps<{ modelValue: string; placeholder?: string }>(), {
  placeholder: 'Search',
})

const emit = defineEmits<{ 'update:modelValue': [string] }>()
</script>

<style scoped>
.fx-search {
  min-width: 200px;
}
</style>
