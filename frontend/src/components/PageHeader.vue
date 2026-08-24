<template>
  <header class="fx-page-header">
    <div class="fx-page-header__text">
      <h1 class="page-title">{{ title }}</h1>
      <p v-if="subtitle" class="page-subtitle">{{ subtitle }}</p>
    </div>
    <div v-if="$slots.actions" class="fx-page-header__actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<script setup lang="ts">
/**
 * The one page header. Title and actions share a line while they both fit and
 * wrap onto separate lines when they do not — which is why the text block sets
 * `min-width: 0` and the actions never shrink: without both, a long subtitle
 * pushes the buttons out of the row and they end up sitting on top of it.
 *
 * The heading is a real `h1` so the document outline matches what the page
 * looks like, rather than being a styled div.
 */
defineProps<{ title: string; subtitle?: string }>()
</script>

<style scoped>
.page-title {
  margin: 0;
}

.page-subtitle {
  margin: var(--fx-space-1) 0 0;
}
</style>
