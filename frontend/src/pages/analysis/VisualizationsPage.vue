<template>
  <q-page class="page-shell">
    <PageHeader
      title="Visualizations"
      subtitle="Saved charts bound to a dataset version, including versions produced by executions"
    >
      <template #actions>
        <SearchField v-model="term" placeholder="Search charts" />
        <q-btn flat dense no-caps icon="travel_explore" label="Build one" :to="{ name: 'explore' }" />
      </template>
    </PageHeader>

    <div v-if="filtered.length" class="row q-col-gutter-md">
      <div v-for="viz in filtered" :key="viz.id" class="col-12 col-lg-6">
        <SectionCard :title="viz.name" :subtitle="viz.description">
          <template #actions>
            <q-btn flat dense round icon="travel_explore" :to="exploreLink(viz)">
              <q-tooltip>Open in Explore</q-tooltip>
            </q-btn>
            <q-btn flat dense round icon="delete" color="negative" @click="remove(viz)">
              <q-tooltip>Delete</q-tooltip>
            </q-btn>
          </template>

          <ChartView v-if="charts[viz.id]" :chart="charts[viz.id]" :height="280" />
          <div v-else class="chart-skeleton">
            <q-skeleton height="200px" />
          </div>
        </SectionCard>
      </div>
    </div>

    <SectionCard v-else-if="!loading">
      <EmptyState
        :message="isFiltering ? 'No chart matches that' : 'No visualizations saved'"
        :hint="
          isFiltering
            ? 'Try a shorter term, or clear the search'
            : 'Build one in Explore, label its axes, then save it'
        "
        icon="insights"
      />
    </SectionCard>
  </q-page>
</template>

<script setup lang="ts">
import { useQuasar } from 'quasar'
import { onMounted, ref } from 'vue'

import { analysis } from '@/api'
import ChartView from '@/components/ChartView.vue'
import EmptyState from '@/components/EmptyState.vue'
import SearchField from '@/components/SearchField.vue'
import { useListFilter } from '@/composables/useListFilter'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import type { ChartData, Visualization } from '@/types'

const $q = useQuasar()
const visualizations = ref<Visualization[]>([])
const { term, filtered, isFiltering } = useListFilter(visualizations, (v) => [
  v.name,
  v.description,
  v.spec.chart_type,
])
const charts = ref<Record<string, ChartData>>({})
const loading = ref(false)

function exploreLink(viz: Visualization) {
  return { name: 'explore', query: { version: viz.dataset_version_id ?? '' } }
}

async function load() {
  loading.value = true
  try {
    visualizations.value = await analysis.listVisualizations()
    await Promise.all(
      visualizations.value.map(async (viz) => {
        try {
          charts.value[viz.id] = { ...(await analysis.renderVisualization(viz.id)), name: undefined }
        } catch (error) {
          charts.value[viz.id] = {
            categories: [],
            series: [],
            chart_type: viz.spec.chart_type,
            error: (error as Error).message,
          }
        }
      }),
    )
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    loading.value = false
  }
}

function remove(viz: Visualization) {
  $q.dialog({ title: 'Delete visualization', message: `Delete "${viz.name}"?`, cancel: true }).onOk(
    async () => {
      await analysis.deleteVisualization(viz.id)
      await load()
    },
  )
}

onMounted(load)
</script>

<style scoped>
.chart-skeleton {
  padding: var(--fx-space-2) 0;
}
</style>
