<template>
  <q-page class="page-shell">
    <PageHeader title="Dashboards" subtitle="Grouped visualizations, rendered from live dataset versions">
      <template #actions>
        <q-btn color="primary" unelevated no-caps icon="add" label="New dashboard" @click="openCreate" />
      </template>
    </PageHeader>

    <div class="row q-col-gutter-md">
      <div class="col-12 col-md-3">
        <SectionCard title="Dashboards" flush>
          <q-list separator class="fx-list">
            <q-item
              v-for="dashboard in dashboards"
              :key="dashboard.id"
              clickable
              :active="dashboard.id === activeId"
              @click="select(dashboard.id)"
            >
              <q-item-section>
                <q-item-label class="truncate">{{ dashboard.name }}</q-item-label>
                <q-item-label caption class="fx-meta">
                  {{ dashboard.tiles.length }} charts
                </q-item-label>
              </q-item-section>
            </q-item>
          </q-list>
          <EmptyState v-if="!dashboards.length && !loading" message="No dashboards" icon="dashboard" />
        </SectionCard>
      </div>

      <div class="col-12 col-md-9">
        <template v-if="rendered">
          <div class="dashboard__heading q-mb-md">
            <div>
              <div class="page-title">{{ rendered.name }}</div>
              <div class="page-subtitle">{{ rendered.description }}</div>
            </div>
            <div class="fx-tags">
              <q-btn
                no-caps
                unelevated
                color="primary"
                icon="add_chart"
                label="Add chart"
                class="fx-btn"
                :disable="!addableCharts.length"
                @click="addDialog = true"
              >
                <q-tooltip v-if="!addableCharts.length">
                  Every saved chart is already on this dashboard
                </q-tooltip>
              </q-btn>
              <q-btn flat dense round icon="refresh" :loading="refreshing" @click="select(activeId)">
                <q-tooltip>Re-render from live data</q-tooltip>
              </q-btn>
              <q-btn flat dense round icon="delete" color="negative" @click="remove">
                <q-tooltip>Delete dashboard</q-tooltip>
              </q-btn>
            </div>
          </div>

          <div class="row q-col-gutter-md">
            <div
              v-for="(tile, index) in rendered.tiles"
              :key="tile.visualization_id"
              :class="tile.width >= 12 ? 'col-12' : 'col-12 col-lg-6'"
            >
              <SectionCard :title="tile.chart.name ?? 'Chart'">
                <template #actions>
                  <q-btn
                    flat
                    dense
                    round
                    icon="arrow_back"
                    :disable="index === 0"
                    @click="moveTile(tile.visualization_id, -1)"
                  >
                    <q-tooltip>Move earlier</q-tooltip>
                  </q-btn>
                  <q-btn
                    flat
                    dense
                    round
                    icon="arrow_forward"
                    :disable="index === rendered.tiles.length - 1"
                    @click="moveTile(tile.visualization_id, 1)"
                  >
                    <q-tooltip>Move later</q-tooltip>
                  </q-btn>
                  <q-btn
                    flat
                    dense
                    round
                    :icon="tile.width >= 12 ? 'close_fullscreen' : 'open_in_full'"
                    @click="toggleWidth(tile)"
                  >
                    <q-tooltip>{{ tile.width >= 12 ? 'Half width' : 'Full width' }}</q-tooltip>
                  </q-btn>
                  <q-btn
                    flat
                    dense
                    round
                    icon="close"
                    @click="removeTile(tile.visualization_id, tile.chart.name)"
                  >
                    <q-tooltip>Remove from dashboard</q-tooltip>
                  </q-btn>
                </template>
                <ChartView
                  :chart="{ ...tile.chart, name: undefined }"
                  :height="tile.width >= 12 ? 340 : 280"
                  :compact="tile.width < 12"
                />
              </SectionCard>
            </div>
          </div>

          <SectionCard v-if="!rendered.tiles.length" class="q-mt-md">
            <EmptyState
              message="This dashboard has no charts yet"
              hint="Add one from the saved visualizations"
              icon="dashboard"
            />
          </SectionCard>
        </template>
        <SectionCard v-else>
          <EmptyState message="Select a dashboard" icon="dashboard" />
        </SectionCard>
      </div>
    </div>

    <q-dialog v-model="addDialog">
      <q-card style="min-width: 480px">
        <q-card-section class="fx-dialog__title">Add a chart</q-card-section>
        <q-card-section class="q-pt-none">
          <q-list separator class="fx-list">
            <q-item
              v-for="viz in addableCharts"
              :key="viz.id"
              clickable
              @click="addTile(viz.id)"
            >
              <q-item-section avatar>
                <q-icon :name="chartIcon(viz.spec.chart_type)" size="20px" />
              </q-item-section>
              <q-item-section>
                <q-item-label>{{ viz.name }}</q-item-label>
                <q-item-label caption class="fx-meta">
                  {{ viz.spec.chart_type.replace(/_/g, ' ') }}
                  <span v-if="viz.description"> · {{ viz.description }}</span>
                </q-item-label>
              </q-item-section>
            </q-item>
          </q-list>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn no-caps flat label="Done" v-close-popup />
        </q-card-actions>
      </q-card>
    </q-dialog>

    <q-dialog v-model="dialog">
      <q-card style="min-width: 460px">
        <q-card-section class="fx-dialog__title">New dashboard</q-card-section>
        <q-card-section class="q-pt-none">
          <div class="fx-form">
            <q-input v-model="form.name" label="Name" dense outlined autofocus />
            <q-input v-model="form.description" label="Description" dense outlined />
            <q-select
              v-model="form.visualizations"
              :options="vizOptions"
              label="Charts"
              dense
              outlined
              multiple
              use-chips
              emit-value
              map-options
            />
          </div>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn flat no-caps label="Cancel" v-close-popup />
          <q-btn unelevated no-caps color="primary" label="Create" @click="create" />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup lang="ts">
import { useQuasar } from 'quasar'
import { computed, onMounted, ref } from 'vue'

import { analysis } from '@/api'
import ChartView from '@/components/ChartView.vue'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import { useUrlSelection } from '@/composables/useUrlSelection'
import type { Dashboard, RenderedDashboard, Visualization } from '@/types'

const $q = useQuasar()
const dashboards = ref<Dashboard[]>([])
const visualizations = ref<Visualization[]>([])
const rendered = ref<RenderedDashboard | null>(null)
//  Selection lives in the URL so a view can be linked, reloaded and shared.
const { selected: activeId, settle } = useUrlSelection()
const loading = ref(false)
const refreshing = ref(false)
const dialog = ref(false)
const addDialog = ref(false)

const CHART_ICONS: Record<string, string> = {
  line: 'show_chart',
  area: 'area_chart',
  bar: 'bar_chart',
  stacked_bar: 'stacked_bar_chart',
  scatter: 'scatter_plot',
  pie: 'pie_chart',
  histogram: 'insert_chart',
  box: 'candlestick_chart',
  heatmap: 'grid_on',
}

function chartIcon(kind: string) {
  return CHART_ICONS[kind] ?? 'insights'
}

/** Only offer charts the dashboard does not already carry. */
const addableCharts = computed(() => {
  const present = new Set(rendered.value?.tiles.map((tile) => tile.visualization_id) ?? [])
  return visualizations.value.filter((viz) => !present.has(viz.id))
})
const form = ref<{ name: string; description: string; visualizations: string[] }>({
  name: '',
  description: '',
  visualizations: [],
})

const vizOptions = computed(() => visualizations.value.map((v) => ({ label: v.name, value: v.id })))

async function load() {
  loading.value = true
  try {
    const [dashboardList, vizList] = await Promise.all([
      analysis.listDashboards(),
      analysis.listVisualizations(),
    ])
    dashboards.value = dashboardList
    visualizations.value = vizList
    if (dashboardList.length) await select(settle(dashboardList.map((d) => d.id)))
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    loading.value = false
  }
}

async function select(id: string) {
  activeId.value = id
  refreshing.value = true
  try {
    rendered.value = await analysis.renderDashboard(id)
  } finally {
    refreshing.value = false
  }
}

/** Every edit re-renders: the tiles are live data, not a cached picture. */
async function applyEdit(action: Promise<unknown>, message: string) {
  try {
    await action
    await select(activeId.value)
    await load()
    $q.notify({ type: 'positive', message })
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

async function addTile(visualizationId: string) {
  addDialog.value = false
  await applyEdit(
    analysis.addTile(activeId.value, { visualization_id: visualizationId }),
    'Chart added',
  )
}

async function removeTile(visualizationId: string, name?: string) {
  await applyEdit(
    analysis.removeTile(activeId.value, visualizationId),
    `Removed ${name ?? 'chart'}`,
  )
}

async function moveTile(visualizationId: string, offset: number) {
  await applyEdit(
    analysis.updateTile(activeId.value, visualizationId, { move: offset }),
    'Reordered',
  )
}

async function toggleWidth(tile: { visualization_id: string; width: number }) {
  await applyEdit(
    analysis.updateTile(activeId.value, tile.visualization_id, {
      width: tile.width >= 12 ? 6 : 12,
    }),
    tile.width >= 12 ? 'Half width' : 'Full width',
  )
}

function openCreate() {
  form.value = { name: '', description: '', visualizations: [] }
  dialog.value = true
}

async function create() {
  try {
    await analysis.createDashboard({
      name: form.value.name,
      description: form.value.description,
      tiles: form.value.visualizations.map((id, index) => ({
        visualization_id: id,
        x: (index % 2) * 6,
        y: Math.floor(index / 2) * 4,
        width: 6,
        height: 4,
      })),
    })
    dialog.value = false
    await load()
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

function remove() {
  const target = dashboards.value.find((d) => d.id === activeId.value)
  if (!target) return
  $q.dialog({ title: 'Delete dashboard', message: `Delete "${target.name}"?`, cancel: true }).onOk(
    async () => {
      await analysis.deleteDashboard(target.id)
      activeId.value = ''
      rendered.value = null
      await load()
    },
  )
}

onMounted(load)
</script>

<style scoped>
.dashboard__heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--fx-space-4);
}
</style>
