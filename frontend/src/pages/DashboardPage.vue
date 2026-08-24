<template>
  <q-page class="page-shell">
    <PageHeader title="Dashboard" subtitle="What this platform currently holds, and what it last did">
      <template #actions>
        <q-btn flat dense no-caps icon="refresh" label="Refresh" :loading="loading" @click="load" />
      </template>
    </PageHeader>

    <!-- the abstraction, stated once -->
    <SectionCard
      class="q-mb-md"
      title="Data → Model → Execution → Result → Application"
      subtitle="A Model is any versioned, executable computational unit — formula, rule, statistical,
                simulation, optimisation or machine learning. Training is optional."
    >
      <div class="row q-col-gutter-md">
        <div v-for="stage in stages" :key="stage.label" class="col-6 col-md-4 col-lg">
          <router-link :to="{ name: stage.route }" class="stage">
            <q-icon :name="stage.icon" size="20px" class="stage__icon" />
            <div class="stage__count num">{{ counts[stage.key] ?? 0 }}</div>
            <div class="stage__label">{{ stage.label }}</div>
          </router-link>
        </div>
      </div>
    </SectionCard>

    <div class="row q-col-gutter-md">
      <!-- model mix -->
      <div class="col-12 col-md-5">
        <SectionCard
          title="Models by category"
          subtitle="Machine learning is one category among several"
        >
          <ChartView v-if="typeChart" :chart="typeChart" :height="240" />
          <EmptyState v-else message="No models yet" icon="category" />
        </SectionCard>
      </div>

      <!-- recent activity -->
      <div class="col-12 col-md-7">
        <SectionCard
          title="Recent executions"
          subtitle="Training, prediction, simulation, calculation, evaluation — all one abstraction"
          flush
        >
          <template #actions>
            <q-btn flat dense no-caps label="All executions" :to="{ name: 'executions' }" />
          </template>

          <q-list v-if="overview?.recent_executions.length" separator class="fx-list">
            <q-item
              v-for="execution in overview.recent_executions"
              :key="execution.id"
              clickable
              :to="{ name: 'execution-detail', params: { id: execution.id } }"
            >
              <q-item-section>
                <q-item-label class="truncate">{{ ranWhat(execution) }}</q-item-label>
                <q-item-label caption class="fx-meta">
                  {{ execution.kind }}
                  <span class="fx-meta__sep">·</span>{{ relativeTime(execution.created_at) }}
                  <template v-if="execution.duration_seconds !== null">
                    <span class="fx-meta__sep">·</span>{{ execution.duration_seconds }}s
                  </template>
                </q-item-label>
              </q-item-section>
              <q-item-section side>
                <StatusText :status="execution.status" />
              </q-item-section>
            </q-item>
          </q-list>
          <EmptyState
            v-else
            message="Nothing has run yet"
            hint="Open the Model library and run one"
            icon="play_circle"
          />
        </SectionCard>
      </div>
    </div>

    <!-- the worked example -->
    <SectionCard
      v-if="example.length"
      class="q-mt-md"
      title="Worked example: Taiwan typhoons"
      subtitle="The whole platform exercised once on the real CWA record — analysis, validation, scheduling and a report"
      flush
    >
      <div class="example">
        <router-link
          v-for="step in example"
          :key="step.label"
          :to="step.to"
          class="example__step"
        >
          <q-icon :name="step.icon" size="20px" class="example__icon" />
          <div class="example__label">{{ step.label }}</div>
          <div class="example__detail">{{ step.detail }}</div>
        </router-link>
      </div>
    </SectionCard>
  </q-page>
</template>

<script setup lang="ts">
import { useQuasar } from 'quasar'
import { computed, onMounted, ref } from 'vue'

import { models as modelsApi, pipelines as pipelinesApi, platform, reports as reportsApi, schedules as schedulesApi } from '@/api'
import ChartView from '@/components/ChartView.vue'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import StatusText from '@/components/StatusText.vue'
import type {
  ChartData,
  ModelDefinition,
  Overview,
  Pipeline,
  Report,
  Schedule,
} from '@/types'

const $q = useQuasar()
const loading = ref(false)
const overview = ref<Overview | null>(null)
const modelIndex = ref<Record<string, ModelDefinition>>({})
const pipelines = ref<Pipeline[]>([])
const reports = ref<Report[]>([])
const schedules = ref<Schedule[]>([])

const stages = [
  { key: 'sources', label: 'Sources', icon: 'cable', route: 'sources' },
  { key: 'datasets', label: 'Datasets', icon: 'table_chart', route: 'datasets' },
  { key: 'models', label: 'Models', icon: 'category', route: 'models' },
  { key: 'executions', label: 'Executions', icon: 'play_circle', route: 'executions' },
  { key: 'results', label: 'Results', icon: 'output', route: 'results' },
  { key: 'applications', label: 'Applications', icon: 'apps', route: 'applications' },
]

const counts = computed(() => overview.value?.counts ?? {})

const typeChart = computed<ChartData | null>(() => {
  const entries = Object.entries(overview.value?.models_by_type ?? {}).filter(([, n]) => n > 0)
  if (!entries.length) return null
  return {
    chart_type: 'bar',
    categories: entries.map(([type]) => type.replace(/_/g, ' ')),
    series: [{ name: 'models', data: entries.map(([, count]) => count) }],
    x_title: 'Model category',
    y_title: 'Models',
    unit: 'models',
    value_labels: true,
    aggregation: 'count',
    row_count: entries.reduce((sum, [, n]) => sum + n, 0),
  }
})

/** Deep links into the seeded typhoon example, when it is present. */
const example = computed(() => {
  const steps: { label: string; detail: string; icon: string; to: Record<string, unknown> }[] = []
  const pipeline = pipelines.value.find((p) => p.name === 'Typhoon climatology')
  if (pipeline) {
    steps.push({
      label: 'Clean the catalogue',
      detail: `${pipeline.steps.length}-step pipeline · ${pipeline.last_run_status ?? 'not run'}`,
      icon: 'account_tree',
      to: { name: 'pipelines' },
    })
  }
  steps.push({
    label: 'Read the climatology',
    detail: 'Four charts over the cleaned record',
    icon: 'dashboard',
    to: { name: 'dashboards' },
  })
  const validation = modelList.value.filter((m) => m.provider === 'typhoon-backtest')
  if (validation.length) {
    steps.push({
      label: 'Validate the model',
      detail: `${validation.length} backtests, leave-one-out`,
      icon: 'rule',
      to: { name: 'evaluation' },
    })
  }
  const schedule = schedules.value.find((s) => s.name.includes('typhoon'))
  if (schedule) {
    steps.push({
      label: 'Keep it current',
      detail: `cron ${schedule.cron} · ${schedule.status}`,
      icon: 'schedule',
      to: { name: 'schedules' },
    })
  }
  if (reports.value.length) {
    steps.push({
      label: 'Read the write-up',
      detail: reports.value[0].name,
      icon: 'description',
      to: { name: 'reports' },
    })
  }
  steps.push({
    label: 'Forecast a track',
    detail: 'Draw a path, find its analogs',
    icon: 'cyclone',
    to: { name: 'typhoon' },
  })
  return steps
})

const modelList = computed(() => Object.values(modelIndex.value))

function modelName(id: string) {
  return modelIndex.value[id]?.name ?? id
}

/** What a run ran: a model, a pipeline, or a definition made on the spot.
 *
 *  Takes the two fields it needs rather than a whole Execution: the overview
 *  sends a summary of a run, not the run itself.
 */
function ranWhat(execution: { target_id: string | null; target_type: string }) {
  const id = execution.target_id
  if (!id) return 'inline definition'
  if (execution.target_type === 'pipeline') {
    return pipelines.value.find((p) => p.id === id)?.name ?? id
  }
  return modelName(id)
}

function relativeTime(value: string) {
  const seconds = (Date.now() - new Date(value).getTime()) / 1000
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`
  return new Date(value).toLocaleDateString()
}

async function load() {
  loading.value = true
  try {
    const [summary, allModels, pipelineList, reportList, scheduleList] = await Promise.all([
      platform.overview(),
      modelsApi.all(),
      pipelinesApi.list().catch(() => []),
      reportsApi.list().catch(() => []),
      schedulesApi.list().catch(() => []),
    ])
    overview.value = summary
    modelIndex.value = Object.fromEntries(allModels.map((m) => [m.id, m]))
    pipelines.value = pipelineList
    reports.value = reportList
    schedules.value = scheduleList
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.stage {
  display: block;
  padding: var(--fx-space-3) var(--fx-space-4);
  border: 1px solid var(--fx-border);
  border-radius: var(--fx-radius-sm);
  text-decoration: none;
  color: inherit;
  transition: border-color 120ms ease, background 120ms ease;
}

.stage:hover {
  border-color: var(--fx-border-strong);
  background: var(--fx-surface-muted);
}

.stage__icon {
  opacity: var(--fx-ink-faint);
}

.stage__count {
  font-size: var(--fx-text-2xl);
  font-weight: 600;
  line-height: 1.15;
  margin-top: var(--fx-space-1);
}

.stage__label {
  font-size: var(--fx-text-xs);
  opacity: var(--fx-ink-muted);
}

.example {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
}

.example__step {
  padding: var(--fx-space-4) var(--fx-space-5);
  border-right: 1px solid var(--fx-border);
  border-bottom: 1px solid var(--fx-border);
  text-decoration: none;
  color: inherit;
  transition: background 120ms ease;
}

.example__step:hover {
  background: var(--fx-surface-muted);
}

.example__icon {
  opacity: var(--fx-ink-faint);
}

.example__label {
  font-size: var(--fx-text-sm);
  font-weight: 600;
  margin-top: var(--fx-space-2);
}

.example__detail {
  font-size: var(--fx-text-xs);
  opacity: var(--fx-ink-muted);
  margin-top: 2px;
}
</style>
