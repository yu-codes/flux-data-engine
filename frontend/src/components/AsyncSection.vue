<template>
  <!-- loading: a shape, not a spinner, so the page does not jump when it fills -->
  <div v-if="pending" class="async__loading" role="status" aria-live="polite">
    <q-skeleton v-for="row in rows" :key="row" type="text" class="async__row" />
    <span class="sr-only">Loading…</span>
  </div>

  <!-- failed: say what broke and offer the way out -->
  <div v-else-if="error" class="async__error" role="alert">
    <q-icon name="error_outline" size="20px" />
    <div class="async__error-text">
      <div class="async__error-title">{{ title }}</div>
      <div class="fx-meta">{{ error }}</div>
    </div>
    <q-btn
      v-if="onRetry"
      no-caps
      flat
      dense
      icon="refresh"
      label="Try again"
      :loading="retrying"
      @click="retry"
    />
  </div>

  <slot v-else />
</template>

<script setup lang="ts">
import { ref } from 'vue'

/**
 * The three states a fetch can be in, told apart.
 *
 * Every page here used to report a failure with a toast and then render its
 * empty state. The toast disappears after a few seconds and what is left says
 * "No datasets yet" — so a broken backend and an empty platform look identical,
 * and the only way forward is to guess that reloading might help.
 *
 * An error is content, not a notification: it stays on the page, says what
 * failed, and carries the retry with it.
 */
const props = withDefaults(
  defineProps<{
    pending?: boolean
    error?: string | null
    /** Skeleton rows to reserve while loading. */
    rows?: number
    title?: string
    onRetry?: (() => void | Promise<void>) | null
  }>(),
  { pending: false, error: null, rows: 3, title: 'Could not load this', onRetry: null },
)

const retrying = ref(false)

async function retry() {
  if (!props.onRetry) return
  retrying.value = true
  try {
    await props.onRetry()
  } finally {
    retrying.value = false
  }
}
</script>

<style scoped>
.async__loading {
  padding: var(--fx-space-4) var(--fx-space-5);
  display: grid;
  gap: var(--fx-space-3);
}

.async__row {
  height: 18px;
}

.async__row:nth-child(2) {
  width: 82%;
}

.async__row:nth-child(3) {
  width: 64%;
}

.async__error {
  display: flex;
  align-items: flex-start;
  gap: var(--fx-space-3);
  padding: var(--fx-space-5);
  color: var(--fx-bad);
}

.async__error-text {
  flex: 1 1 auto;
  min-width: 0;
}

.async__error-title {
  font-size: var(--fx-text-sm);
  font-weight: 600;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
</style>
