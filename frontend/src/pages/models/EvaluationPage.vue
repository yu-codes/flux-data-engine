<template>
  <q-page class="page-shell">
    <PageHeader
      title="Evaluation"
      subtitle="Did a run meet its objective? Metric names stay open — accuracy, absolute error, objective value, scenario deviation."
    >
      <template #actions>
        <q-btn flat dense no-caps icon="refresh" label="Refresh" :loading="loading" @click="load" />
        <q-btn color="primary" unelevated no-caps icon="add" label="Record" @click="openCreate" />
      </template>
    </PageHeader>

    <!--
      Compare experiments on whatever they actually reported.

      This table used to name Accuracy, Correct and Total — the metrics one
      backtest happens to produce. A model measured by RMSE or by an objective
      value showed three empty columns and looked broken. The columns are now
      discovered from the runs.
    -->
    <SectionCard
      title="Compare experiments"
      :subtitle="comparisonSubtitle"
      class="q-mb-md"
    >
      <template #actions>
        <q-select
          v-model="chosenExperiments"
          :options="experimentOptions"
          label="Experiments"
          dense
          outlined
          multiple
          use-chips
          emit-value
          map-options
          class="evaluation__picker"
          @update:model-value="compare"
        />
        <q-select
          v-if="comparison && comparison.metric_names.length"
          v-model="chosenMetric"
          :options="comparison.metric_names"
          label="Rank by"
          dense
          outlined
          clearable
          class="evaluation__metric"
          @update:model-value="compare"
        />
        <q-toggle
          v-if="comparison"
          v-model="includeHistory"
          dense
          label="Every run"
          @update:model-value="compare"
        />
      </template>

      <div v-if="comparison && comparison.rows.length" class="fx-scroll-x">
        <table class="fx-table">
          <thead>
            <tr>
              <th class="fx-table__rank">#</th>
              <th class="compare__name">Trial</th>
              <th class="compare__name">Experiment</th>
              <th v-for="metric in comparison.metric_names" :key="metric" class="num">
                {{ metric.replace(/_/g, ' ') }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, index) in comparison.rows" :key="row.execution_id">
              <td class="fx-table__rank num">{{ index + 1 }}</td>
              <td class="wrap compare__name">
                <router-link
                  class="fx-link"
                  :to="{ name: 'execution-detail', params: { id: row.execution_id } }"
                >
                  {{ row.trial }}
                </router-link>
                <div class="fx-meta">{{ row.model }}</div>
              </td>
              <td class="wrap compare__name">{{ row.experiment }}</td>
              <td
                v-for="metric in comparison.metric_names"
                :key="metric"
                class="num"
                :class="{ 'fx-table__best': isBest(metric, row.metrics[metric]) }"
              >
                {{ format(row.metrics[metric]) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <EmptyState
        v-else-if="chosenExperiments.length"
        message="No finished runs in those experiments yet"
        hint="Run an experiment, then come back to compare"
        icon="rule"
      />
      <EmptyState
        v-else
        message="Choose experiments to compare"
        hint="Their metrics are read from the runs, whatever those metrics happen to be"
        icon="rule"
      />
    </SectionCard>

    <SectionCard title="Recorded evaluations" :subtitle="`${evaluations.length} on record`" flush>
      <q-list separator class="fx-list">
        <q-item v-for="evaluation in evaluations" :key="evaluation.id">
          <q-item-section>
            <q-item-label class="truncate">{{ modelName(evaluation.model_id) }}</q-item-label>
            <q-item-label caption class="fx-meta mono">{{ metricsLine(evaluation.metrics) }}</q-item-label>
            <q-item-label v-if="evaluation.notes" caption class="fx-meta">{{ evaluation.notes }}</q-item-label>
          </q-item-section>
          <q-item-section side class="evaluation__side">
            <StatusText
              :status="evaluation.passed === null ? 'neutral' : evaluation.passed ? 'succeeded' : 'failed'"
              :label="verdict(evaluation)"
            />
            <div class="fx-meta mono">{{ targetLine(evaluation.target) }}</div>
            <q-btn
              flat
              dense
              no-caps
              label="Execution"
              :to="{ name: 'execution-detail', params: { id: evaluation.execution_id } }"
            />
          </q-item-section>
        </q-item>
      </q-list>
      <EmptyState
        v-if="!evaluations.length && !loading"
        message="No evaluations recorded"
        hint="Run a backtest model, then score it against a target"
        icon="rule"
      />
    </SectionCard>

    <q-dialog v-model="dialog">
      <q-card style="min-width: 520px">
        <q-card-section class="fx-dialog__title">Record an evaluation</q-card-section>
        <q-card-section class="q-pt-none">
          <div class="fx-form">
            <q-select
              v-model="form.execution_id"
              :options="executionOptions"
              label="Execution"
              dense
              outlined
              emit-value
              map-options
              @update:model-value="prefillMetrics"
            />
            <q-input v-model="metricsText" label="Metrics (JSON)" type="textarea" rows="4" dense outlined class="mono" />
            <div class="row q-col-gutter-sm">
              <div class="col-5">
                <q-input v-model="target.metric" label="Target metric" dense outlined />
              </div>
              <div class="col-3">
                <q-select v-model="target.bound" :options="['min', 'max']" label="Bound" dense outlined />
              </div>
              <div class="col-4">
                <q-input v-model.number="target.value" type="number" label="Value" dense outlined />
              </div>
            </div>
            <q-input v-model="form.notes" label="Notes" dense outlined />
          </div>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn flat no-caps label="Cancel" v-close-popup />
          <q-btn unelevated no-caps color="primary" label="Save" @click="save" />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup lang="ts">
import { useQuasar } from 'quasar'
import { computed, onMounted, ref } from 'vue'

import { executions as executionsApi, models as modelsApi } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import StatusText from '@/components/StatusText.vue'
import type { Evaluation, Execution, ModelDefinition,
  Experiment,
  ExperimentComparison,
} from '@/types'

const $q = useQuasar()
const evaluations = ref<Evaluation[]>([])
const executionList = ref<Execution[]>([])
const modelList = ref<ModelDefinition[]>([])
const loading = ref(false)
const dialog = ref(false)
const form = ref({ execution_id: '', notes: '' })
const metricsText = ref('{}')
const target = ref<{ metric: string; bound: 'min' | 'max'; value: number }>({
  metric: 'accuracy',
  bound: 'min',
  value: 0.5,
})

const executionOptions = computed(() =>
  executionList.value.map((e) => ({
    label: `${modelName(e.model_id)} · ${e.kind} · ${e.id.slice(0, 12)}`,
    value: e.id,
  })),
)

const experiments = ref<Experiment[]>([])
const chosenExperiments = ref<string[]>([])
const chosenMetric = ref<string | null>(null)
const comparison = ref<ExperimentComparison | null>(null)
//  Off by default: a trial's latest run is where it stands, and repeated runs
//  would otherwise fill the table with near-identical rows.
const includeHistory = ref(false)

const experimentOptions = computed(() =>
  experiments.value.map((e) => ({
    label: `${e.name}${e.primary_metric ? ` · ${e.primary_metric}` : ''}`,
    value: e.id,
  })),
)

const comparisonSubtitle = computed(() => {
  const result = comparison.value
  if (!result || !result.rows.length) {
    return 'Pick the experiments whose trials you want side by side'
  }
  const ranked = result.ranked_by ? ` ranked by ${result.ranked_by}` : ''
  const across = result.experiments.length
  return `${result.rows.length} runs from ${across} experiment${across > 1 ? 's' : ''}${ranked}`
})

/**
 * Ask the server which metrics these experiments reported.
 *
 * Deliberately not computed in the browser from a fixed list: the point is that
 * the columns follow the data, so an optimiser reporting `best_objective` and a
 * backtest reporting `accuracy` can appear in the same table without either
 * being special-cased.
 */
async function compare() {
  if (!chosenExperiments.value.length) {
    comparison.value = null
    return
  }
  try {
    comparison.value = await modelsApi.compareExperiments(
      chosenExperiments.value,
      chosenMetric.value ?? undefined,
      includeHistory.value,
    )
    //  The server picks the ranking metric when none was asked for. Showing it
    //  keeps the control honest: the subtitle said "ranked by accuracy" while
    //  the select sat empty.
    chosenMetric.value = comparison.value.ranked_by
  } catch (error) {
    comparison.value = null
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

/** Highlight the leader per column, so a wide table is still readable. */
const bestByMetric = computed(() => {
  const best: Record<string, number> = {}
  for (const row of comparison.value?.rows ?? []) {
    for (const [metric, value] of Object.entries(row.metrics)) {
      if (typeof value === 'number' && (!(metric in best) || value > best[metric])) {
        best[metric] = value
      }
    }
  }
  return best
})

function isBest(metric: string, value: number | undefined) {
  return typeof value === 'number' && bestByMetric.value[metric] === value
}

function format(value: number | undefined) {
  if (typeof value !== 'number') return '—'
  return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(4)
}

function modelName(id: string | null) {
  return modelList.value.find((m) => m.id === id)?.name ?? id ?? 'unknown model'
}

/** Trim the shared prefix so the chart's category axis stays readable. */


function metricsLine(metrics: Record<string, unknown>) {
  const entries = Object.entries(metrics)
    .filter(([, value]) => typeof value !== 'object')
    .map(([key, value]) => `${key}=${value}`)
  return entries.length ? entries.join('  ') : 'no metrics'
}

function targetLine(targetValue: Record<string, unknown>) {
  if (!targetValue || !targetValue.metric) return ''
  const bounds: string[] = []
  if (targetValue.min !== undefined) bounds.push(`≥ ${targetValue.min}`)
  if (targetValue.max !== undefined) bounds.push(`≤ ${targetValue.max}`)
  return `${targetValue.metric} ${bounds.join(' ')}`
}

function verdict(evaluation: Evaluation) {
  if (evaluation.passed === null) return 'no target'
  return evaluation.passed ? 'meets target' : 'below target'
}

function prefillMetrics() {
  const execution = executionList.value.find((e) => e.id === form.value.execution_id)
  metricsText.value = JSON.stringify(execution?.metrics ?? {}, null, 2)
}

function openCreate() {
  form.value = { execution_id: '', notes: '' }
  metricsText.value = '{}'
  dialog.value = true
}

async function load() {
  loading.value = true
  try {
    const [evaluationList, runs, allModels, experimentList] = await Promise.all([
      modelsApi.listEvaluations(),
      executionsApi.list('?limit=100&status=succeeded'),
      modelsApi.all(),
      modelsApi.listExperiments(),
    ])
    evaluations.value = evaluationList
    executionList.value = runs
    modelList.value = allModels
    experiments.value = experimentList
    //  Arriving at a comparison page to find nothing compared is a dead end.
    //  Default to the experiments that have actually run - up to the four most
    //  recent, since past that the table stops being readable at a glance.
    if (!chosenExperiments.value.length) {
      chosenExperiments.value = experimentList
        .filter((e) => e.execution_ids.length)
        .slice(0, 4)
        .map((e) => e.id)
      if (chosenExperiments.value.length) await compare()
    }
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    loading.value = false
  }
}

async function save() {
  try {
    const execution = executionList.value.find((e) => e.id === form.value.execution_id)
    await modelsApi.createEvaluation({
      execution_id: form.value.execution_id,
      metrics: JSON.parse(metricsText.value || '{}'),
      target: { metric: target.value.metric, [target.value.bound]: target.value.value },
      model_id: execution?.model_id ?? null,
      notes: form.value.notes,
    })
    dialog.value = false
    await load()
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

onMounted(load)
</script>

<style scoped>
/*
  Chips grow with every experiment picked, and the header's actions do not
  shrink - four selections was enough to push the whole page sideways. Capped
  here so the chips wrap inside the field instead of widening it.
*/
/*
  The table scrolls sideways already, so squeezing the name columns buys
  nothing: at 768 "Coastline-RRF (k=5, 500km buffer)" was breaking into seven
  one-word lines while the numbers sat in comfortable columns beside it.
*/
.compare__name {
  min-width: 11rem;
}

.evaluation__picker {
  min-width: 14rem;
  max-width: 22rem;
  flex: 1 1 14rem;
}

.evaluation__metric {
  min-width: 9rem;
  max-width: 12rem;
}

.evaluation__side {
  align-items: flex-end;
  gap: var(--fx-space-1);
}
</style>
