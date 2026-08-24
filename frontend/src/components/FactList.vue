<template>
  <dl class="fx-facts">
    <template v-for="fact in visible" :key="fact.label">
      <dt class="fx-facts__label">{{ fact.label }}</dt>
      <dd class="fx-facts__value" :class="{ 'fx-facts__value--num': fact.numeric }">
        <slot :name="fact.label" :fact="fact">{{ fact.display }}</slot>
      </dd>
    </template>
  </dl>
</template>

<script setup lang="ts">
import { computed } from 'vue'

/**
 * Label/value pairs on a two-column grid, so the values line up down the page
 * regardless of how long the labels are.
 */
export interface Fact {
  label: string
  value: unknown
  numeric?: boolean
}

const props = withDefaults(defineProps<{ facts: Fact[]; hideEmpty?: boolean }>(), {
  hideEmpty: true,
})

const visible = computed(() =>
  props.facts
    .filter(
      (fact) =>
        !props.hideEmpty ||
        (fact.value !== null && fact.value !== undefined && fact.value !== ''),
    )
    .map((fact) => ({
      ...fact,
      numeric: fact.numeric ?? typeof fact.value === 'number',
      display: format(fact.value),
    })),
)

function format(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'number') {
    return Number.isInteger(value) ? value.toLocaleString() : String(Number(value.toFixed(4)))
  }
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
</script>
