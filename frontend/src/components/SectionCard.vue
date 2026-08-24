<template>
  <q-card flat class="flux-card fx-section">
    <header
      v-if="title || $slots.actions"
      class="fx-card__header"
      :class="{ 'fx-card__header--plain': plain }"
    >
      <q-icon v-if="icon" :name="icon" size="20px" class="fx-card__icon" />
      <div class="fx-card__heading">
        <div class="fx-card__title">{{ title }}</div>
        <div v-if="subtitle" class="fx-card__subtitle">{{ subtitle }}</div>
      </div>
      <div v-if="$slots.actions" class="fx-card__actions">
        <slot name="actions" />
      </div>
    </header>
    <div class="fx-card__body" :class="bodyClass">
      <slot />
    </div>
    <footer v-if="$slots.footer" class="fx-card__footer">
      <slot name="footer" />
    </footer>
  </q-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'

/**
 * Every card in the app is this card. One border, one radius, one padding
 * rhythm, and a header whose title, subtitle and actions land on the same
 * baseline no matter what the page puts inside them.
 */
const props = withDefaults(
  defineProps<{
    title?: string
    subtitle?: string
    /** A quiet glyph beside the title, for pages that mix section kinds. */
    icon?: string
    /** Remove the body padding, for a flush table or list. */
    flush?: boolean
    /** Tighter vertical padding, for dense bodies. */
    tight?: boolean
    /** Drop the rule under the header. */
    plain?: boolean
  }>(),
  { flush: false, tight: false, plain: false },
)

const bodyClass = computed(() => ({
  'fx-card__body--flush': props.flush,
  'fx-card__body--tight': props.tight,
}))
</script>

<style scoped>
.fx-section {
  display: flex;
  flex-direction: column;
}

/*
 * A card fills its column only when it is the whole column. Quasar rows stretch
 * every column to the tallest one, so an unconditional `height: 100%` made a
 * six-row card in a stacked column swell to the height of the entire page and
 * push everything below it off the screen. `:only-child` keeps the equal-height
 * behaviour where cards sit side by side, and drops it where they stack.
 */
.fx-section:only-child {
  height: 100%;
}

.fx-card__heading {
  min-width: 0;
}

.fx-card__icon {
  opacity: 0.45;
  margin-top: 1px;
  flex: 0 0 auto;
}

.fx-card__body {
  flex: 1 1 auto;
  min-width: 0;
}

.fx-card__footer {
  display: flex;
  align-items: center;
  gap: var(--fx-space-2);
  border-top: 1px solid var(--fx-border);
  padding: var(--fx-space-2) var(--fx-space-3);
}
</style>
