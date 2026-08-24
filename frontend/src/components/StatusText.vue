<template>
  <span class="fx-status" :class="`fx-status--${tone}`">
    <span class="fx-status__dot" />
    <span>{{ label ?? status ?? '—' }}</span>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

/**
 * A dot and a word rather than a coloured pill. Status appears on nearly every
 * row in this app; pills at that density turn a list into confetti and stop
 * carrying meaning.
 */
const props = defineProps<{ status: string | null | undefined; label?: string }>()

const KNOWN = new Set([
  'succeeded',
  'failed',
  'running',
  'pending',
  'cancelled',
  'active',
  'paused',
  'published',
  'draft',
])

const tone = computed(() => (KNOWN.has(props.status ?? '') ? props.status : 'neutral'))
</script>
