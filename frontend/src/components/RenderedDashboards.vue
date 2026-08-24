<template>
  <div>
    <SectionCard
      v-for="dashboard in dashboards"
      :key="dashboard.id"
      :title="dashboard.name"
      :subtitle="dashboard.description"
      class="q-mb-md"
    >
      <div class="rendered__grid">
        <div
          v-for="tile in dashboard.tiles"
          :key="tile.visualization_id"
          class="rendered__tile"
          :style="{ gridColumn: `span ${Math.min(tile.width ?? 6, 12)}` }"
        >
          <div class="rendered__tile-title">{{ tile.name }}</div>
          <ChartView v-if="tile.chart" :chart="tile.chart" />
          <EmptyState v-else message="This chart could not be rendered" icon="error_outline" />
        </div>
      </div>
    </SectionCard>

    <EmptyState
      v-if="!dashboards.length"
      message="Nothing to show yet"
      :hint="emptyHint"
      icon="dashboard"
    />
  </div>
</template>

<script setup lang="ts">
/**
 * An application's dashboards, drawn.
 *
 * Shared by the page a reader opens from a link and the page an owner opens
 * from the application list, for the same reason the backend renders both
 * through one function: what somebody shares should be what they saw. Two
 * copies of this markup would agree until the first change to either.
 */
import ChartView from '@/components/ChartView.vue'
import EmptyState from '@/components/EmptyState.vue'
import SectionCard from '@/components/SectionCard.vue'
import type { ChartData } from '@/types'

export interface RenderedTile {
  visualization_id: string
  name?: string
  width?: number
  chart?: ChartData | null
}

export interface RenderedBoard {
  id: string
  name: string
  description?: string
  tiles: RenderedTile[]
}

withDefaults(
  defineProps<{ dashboards: RenderedBoard[]; emptyHint?: string }>(),
  { emptyHint: 'This application has no dashboards in it.' },
)
</script>

<style scoped>
.rendered__grid {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  gap: var(--fx-space-4);
}

.rendered__tile {
  min-width: 0;
}

.rendered__tile-title {
  font-size: var(--fx-text-sm);
  font-weight: 600;
  margin-bottom: var(--fx-space-2);
}

/*
  Often opened on a phone from a chat message, where a twelve-column grid is
  one column.
*/
@media (max-width: 799px) {
  .rendered__tile {
    grid-column: span 12 !important;
  }
}
</style>
