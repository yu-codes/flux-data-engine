<template>
  <q-page class="page-shell">
    <PageHeader
      title="Experiments"
      subtitle="Compare models, parameters or methods — an ML model and a formula can share one experiment"
    >
      <template #actions>
        <q-btn no-caps flat dense icon="refresh" label="Refresh" :loading="loading" @click="load" />
        <q-btn no-caps color="primary" unelevated icon="add" label="New experiment" @click="dialog = true" />
      </template>
    </PageHeader>

    <div class="row q-col-gutter-md">
      <div class="col-12 col-md-4 fx-pane">
        <SectionCard
          title="Experiments"
          :subtitle="`${experiments.length} defined`"
          flush
        >
          <q-list separator class="fx-list">
            <q-item
              v-for="experiment in experiments"
              :key="experiment.id"
              clickable
              :active="experiment.id === activeId"
              @click="select(experiment.id)"
            >
              <q-item-section avatar><q-icon name="science" size="20px" /></q-item-section>
              <q-item-section>
                <q-item-label>{{ experiment.name }}</q-item-label>
                <q-item-label caption>
                  {{ countLine(experiment) }}
                </q-item-label>
              </q-item-section>
              <q-item-section side>
                <q-btn flat dense round icon="delete" color="negative" @click.stop="remove(experiment)" />
              </q-item-section>
            </q-item>
          </q-list>
          <EmptyState
            v-if="!experiments.length && !loading"
            message="No experiments"
            hint="Group the models you want to compare, then attach executions"
            icon="science"
          />
        </SectionCard>
      </div>

      <div class="col-12 col-md-8">
        <template v-if="active">
          <SectionCard
            :title="active.name"
            :subtitle="active.objective || active.description || 'No stated objective'"
            class="q-mb-md"
          >
            <template #actions>
              <q-btn
                no-caps
                flat
                dense
                icon="fact_check"
                label="Check"
                :loading="checking"
                @click="runCheck"
              />
              <q-btn
                no-caps
                unelevated
                color="primary"
                icon="play_arrow"
                label="Run experiment"
                class="fx-btn"
                :loading="running"
                @click="run"
              />
            </template>
            <FactList :facts="facts" />
          </SectionCard>

          <!--
            The check, before anything runs. A comparison that fails halfway is
            worse than one that never started, because the partial results still
            look like an answer.
          -->
          <SectionCard
            v-if="check"
            class="q-mb-md"
            :title="check.runnable ? 'Ready to run' : 'Cannot run yet'"
            :subtitle="checkSubtitle"
            :icon="check.runnable ? 'check_circle' : 'error_outline'"
            tight
          >
            <template #actions>
              <q-btn flat dense round icon="close" @click="check = null">
                <q-tooltip>Dismiss</q-tooltip>
              </q-btn>
            </template>
            <ul v-if="check.errors.length || check.warnings.length" class="check">
              <li v-for="m in check.errors" :key="`e-${m}`" class="check__error">{{ m }}</li>
              <li v-for="m in check.warnings" :key="`w-${m}`" class="check__warning">{{ m }}</li>
            </ul>
            <table class="fx-table q-mt-sm">
              <thead>
                <tr>
                  <th>Trial</th>
                  <th>Model</th>
                  <th>Runs as</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="trial in check.trials" :key="trial.label + trial.model_id">
                  <td class="wrap">{{ trial.label }}</td>
                  <td class="wrap">{{ trial.model_name ?? trial.model_id }}</td>
                  <td>{{ (trial.kinds ?? []).join(', ') || '—' }}</td>
                  <td class="wrap">
                    <StatusText
                      :status="trial.runnable ? 'succeeded' : 'failed'"
                      :label="trial.runnable ? 'ready' : 'blocked'"
                    />
                    <ul v-if="trial.errors.length || trial.warnings.length" class="check">
                      <li v-for="m in trial.errors" :key="m" class="check__error">{{ m }}</li>
                      <li v-for="m in trial.warnings" :key="m" class="check__warning">{{ m }}</li>
                    </ul>
                  </td>
                </tr>
              </tbody>
            </table>
          </SectionCard>

          <SectionCard
            v-if="active.trials.length"
            title="Trials"
            :subtitle="`${active.trials.length} in this comparison`"
            class="q-mb-md"
            flush
          >
            <q-list separator class="fx-list">
              <q-item v-for="(trial, index) in active.trials" :key="index">
                <q-item-section avatar><div class="num builder__index">{{ index + 1 }}</div></q-item-section>
                <q-item-section>
                  <q-item-label>{{ trial.label || trialTarget(trial) }}</q-item-label>
                  <q-item-label caption class="fx-meta">
                    <template v-if="trial.label">{{ trialTarget(trial) }}<span class="fx-meta__sep">·</span></template>
                    <template v-if="trial.kind">{{ trial.kind }}</template>
                    <template v-else>as configured</template>
                    <template v-if="Object.keys(trial.parameters).length">
                      <span class="fx-meta__sep">·</span>{{ parameterSummary(trial.parameters) }}
                    </template>
                  </q-item-label>
                </q-item-section>
              </q-item>
            </q-list>
          </SectionCard>

          <SectionCard
            v-if="board"
            title="Leaderboard"
            :subtitle="
              board.primary_metric
                ? `Ranked by ${board.primary_metric}, newest run per trial`
                : 'No primary metric set — showing every recorded metric'
            "
            flush
          >
            <template #actions>
              <q-btn
                no-caps
                flat
                dense
                icon="rule"
                label="Evaluations"
                :to="{ name: 'evaluation' }"
              />
            </template>

            <div v-if="board.rows.length" class="fx-scroll-x">
              <table class="fx-table">
                <thead>
                  <tr>
                    <th class="fx-table__rank">#</th>
                    <th>Trial</th>
                    <th v-for="metric in board.metric_names" :key="metric" class="num">
                      {{ metric.replace(/_/g, ' ') }}
                    </th>
                    <th>Target</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, index) in board.rows" :key="`${row.trial}-${index}`">
                    <td class="fx-table__rank num">
                      {{ row.primary_value === null ? '—' : index + 1 }}
                    </td>
                    <td class="wrap fx-table__model">
                      <!--
                        A row leads to whatever it compared. A pipeline trial
                        has no model page to lead to, and linking to one with
                        an empty id was an error the router raised before the
                        page could finish rendering.
                      -->
                      <router-link class="fx-link" :to="rowTarget(row)">
                        {{ row.trial }}
                      </router-link>
                      <div class="fx-meta">
                        {{ row.model_name }}
                        <span class="fx-meta__sep">·</span>{{ row.provider }}
                      </div>
                    </td>
                    <td
                      v-for="metric in board.metric_names"
                      :key="metric"
                      class="num"
                      :class="{ 'fx-table__best': isBest(metric, row) }"
                    >
                      {{ formatMetric(row.metrics[metric]) }}
                    </td>
                    <td>
                      <StatusText
                        v-if="row.passed !== null"
                        :status="row.passed ? 'succeeded' : 'failed'"
                        :label="row.passed ? 'met' : 'missed'"
                      />
                      <span v-else class="fx-meta">
                        {{ row.execution_id ? 'measured, not scored' : 'no run yet' }}
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <EmptyState
              v-else
              message="No trials in this experiment"
              hint="Add trials when you create the experiment"
              icon="science"
            />
          </SectionCard>

          <SectionCard
            v-if="chart"
            title="Metric comparison"
            :subtitle="`${board?.primary_metric || 'metric'} by trial`"
            class="q-mt-md"
          >
            <ChartView :chart="chart" :height="260" />
          </SectionCard>
        </template>

        <SectionCard v-else title="Comparison">
          <EmptyState message="Select an experiment" icon="science" />
        </SectionCard>
      </div>
    </div>

    <q-dialog v-model="dialog" full-width>
      <q-card style="max-width: 900px; margin: 0 auto">
        <q-card-section class="fx-dialog__title">Specify a comparison</q-card-section>
        <q-card-section class="fx-dialog__subtitle">
          Choose what to compare and what to compare it on. Everything is validated
          before it runs.
        </q-card-section>
        <q-card-section class="q-pt-none">
          <ExperimentBuilder
            v-model="form"
            :models="modelList"
            :pipelines="pipelineList"
            :datasets="datasets"
            :dataset-columns="datasetColumns"
          />
        </q-card-section>
        <q-card-actions align="right">
          <q-btn no-caps flat label="Cancel" v-close-popup />
          <q-btn
            no-caps
            unelevated
            color="primary"
            label="Create"
            class="fx-btn"
            :loading="saving"
            @click="create"
          />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup lang="ts">
import { useQuasar } from 'quasar'
import { computed, onMounted, ref, watch } from 'vue'

import { data as dataApi, models as modelsApi, pipelines as pipelinesApi } from '@/api'
import ChartView from '@/components/ChartView.vue'
import ExperimentBuilder from '@/components/ExperimentBuilder.vue'
import EmptyState from '@/components/EmptyState.vue'
import FactList, { type Fact } from '@/components/FactList.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import StatusText from '@/components/StatusText.vue'
import { useUrlSelection } from '@/composables/useUrlSelection'
import type {
  ChartData,
  Dataset,
  Experiment,
  ExperimentCheck,
  ExperimentTrial,
  Leaderboard,
  LeaderboardRow,
  ModelDefinition,
  Pipeline,
} from '@/types'

const $q = useQuasar()
const experiments = ref<Experiment[]>([])
const modelList = ref<ModelDefinition[]>([])
//  A trial can be a pipeline, so the page has to know their names as well.
const pipelineList = ref<Pipeline[]>([])
const board = ref<Leaderboard | null>(null)
//  Selection lives in the URL so a view can be linked, reloaded and shared.
const { selected: activeId, settle } = useUrlSelection()
const loading = ref(false)
const dialog = ref(false)
interface TrialDraft {
  uid: number
  target_id: string
  target_type: 'model' | 'pipeline'
  label: string
  kind: string | null
  parameters: Record<string, unknown>
}

function blankForm() {
  return {
    name: '',
    objective: '',
    primary_metric: '',
    primary_direction: 'higher' as 'higher' | 'lower',
    dataset_version_id: null as string | null,
    trials: [] as TrialDraft[],
  }
}

const form = ref(blankForm())
const datasets = ref<Dataset[]>([])
const datasetColumns = ref<string[]>([])
const check = ref<ExperimentCheck | null>(null)
const checking = ref(false)
const running = ref(false)
const saving = ref(false)

const checkSubtitle = computed(() => {
  const report = check.value
  if (!report) return undefined
  const blocked = report.trials.filter((t) => !t.runnable).length
  if (report.runnable) return `${report.trials.length} trials ready`
  return `${blocked} of ${report.trials.length} trials cannot run`
})

/** Trials are the unit now, so the list says how many there are. */
function countLine(experiment: Experiment) {
  const trials = experiment.trials.length
  const runs = experiment.execution_ids.length
  const trialWord = trials === 1 ? 'trial' : 'trials'
  const runWord = runs === 1 ? 'run' : 'runs'
  return `${trials} ${trialWord} · ${runs} ${runWord}`
}

/** Where a leaderboard row leads: the model, or the pipeline it compared. */
function rowTarget(row: LeaderboardRow) {
  return row.target_type === 'pipeline'
    ? { name: 'pipelines', query: { id: row.target_id } }
    : { name: 'model-detail', params: { id: row.target_id || row.model_id } }
}

/** What a trial is comparing, named: a model or a pipeline. */
function trialTarget(trial: ExperimentTrial) {
  if (trial.target_type === 'pipeline') {
    return pipelineList.value.find((p) => p.id === trial.target_id)?.name ?? trial.target_id
  }
  return modelName(trial.target_id)
}

function modelName(id: string) {
  return modelList.value.find((m) => m.id === id)?.name ?? id
}

/** Parameters as one readable line, so a trial row stays a row. */
function parameterSummary(parameters: Record<string, unknown>) {
  return Object.entries(parameters)
    .slice(0, 3)
    .map(([key, value]) => `${key}=${typeof value === 'object' ? '…' : value}`)
    .join(', ')
}

async function loadDatasetColumns() {
  const versionId = form.value.dataset_version_id
  datasetColumns.value = []
  if (!versionId) return
  const dataset = datasets.value.find((d) => d.current_version_id === versionId)
  if (!dataset) return
  try {
    const detail = await dataApi.getDataset(dataset.id)
    datasetColumns.value = detail.schema_fields.map((field) => field.name)
  } catch {
    //  Suggestions only; a missing schema must not block building.
    datasetColumns.value = []
  }
}

watch(() => form.value.dataset_version_id, loadDatasetColumns)

async function runCheck() {
  if (!active.value) return
  checking.value = true
  try {
    check.value = await modelsApi.checkExperiment(active.value.id)
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    checking.value = false
  }
}

/**
 * Run the whole experiment.
 *
 * The check runs first and its report stays on screen, so a refusal explains
 * itself instead of arriving as a toast that disappears.
 */
async function run() {
  if (!active.value) return
  running.value = true
  try {
    check.value = await modelsApi.checkExperiment(active.value.id)
    if (!check.value.runnable) {
      $q.notify({ type: 'warning', message: 'Some trials cannot run yet — see the check' })
      return
    }
    await modelsApi.runExperiment(active.value.id)
    await load()
    await loadBoard()
    $q.notify({ type: 'positive', message: 'Experiment run — every trial submitted' })
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    running.value = false
  }
}
const active = computed(() => experiments.value.find((e) => e.id === activeId.value) ?? null)

const facts = computed<Fact[]>(() => {
  const experiment = active.value
  if (!experiment) return []
  const evaluated = board.value?.rows.filter((row) => row.evaluation_id).length ?? 0
  const measured = board.value?.rows.filter((row) => row.execution_id).length ?? 0
  const best = board.value?.rows.find((row) => row.primary_value !== null)
  const dataset = datasets.value.find(
    (d) => d.current_version_id === experiment.dataset_version_id,
  )
  return [
    { label: 'Trials', value: String(experiment.trials.length), numeric: true },
    { label: 'Dataset', value: dataset?.name ?? 'none — models take no input' },
    { label: 'Primary metric', value: experiment.primary_metric || 'not set' },
    { label: 'Runs so far', value: String(experiment.execution_ids.length), numeric: true },
    //  Measured and scored are different claims: a trial can have a result
    //  nobody has judged against a target, and reading "Evaluated 0 of 3"
    //  beside a full leaderboard made the page look broken.
    { label: 'Measured', value: `${measured} of ${experiment.trials.length}`, numeric: true },
    { label: 'Scored against a target', value: `${evaluated} of ${experiment.trials.length}`, numeric: true },
    { label: 'Leader', value: best ? best.trial : 'none yet' },
  ]
})

/** Only trials that actually carry the primary metric belong on the bar chart. */
const scoredRows = computed(() => board.value?.rows.filter((row) => row.primary_value !== null) ?? [])

const chart = computed<ChartData | null>(() => {
  if (!board.value || scoredRows.value.length < 1) return null
  const metric = board.value.primary_metric || 'value'
  return {
    chart_type: 'bar',
    categories: scoredRows.value.map((row) => row.trial),
    series: [{ name: metric, data: scoredRows.value.map((row) => row.primary_value) }],
    x_title: 'Trial',
    y_title: metric.replace(/_/g, ' '),
    value_labels: true,
    row_count: scoredRows.value.length,
  }
})

/** Highlight the best cell per metric so a wide table stays readable. */
const bestByMetric = computed(() => {
  const best: Record<string, number> = {}
  for (const metric of board.value?.metric_names ?? []) {
    for (const row of board.value?.rows ?? []) {
      const value = row.metrics[metric]
      if (typeof value === 'number' && (!(metric in best) || value > best[metric])) {
        best[metric] = value
      }
    }
  }
  return best
})

function isBest(metric: string, row: LeaderboardRow) {
  const value = row.metrics[metric]
  return typeof value === 'number' && bestByMetric.value[metric] === value
}

function formatMetric(value: unknown) {
  if (typeof value !== 'number') return value === undefined || value === null ? '—' : String(value)
  return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(3)
}

function select(id: string) {
  activeId.value = id
}

async function loadBoard() {
  if (!activeId.value) {
    board.value = null
    return
  }
  try {
    board.value = await modelsApi.leaderboard(activeId.value)
  } catch (error) {
    board.value = null
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

async function load() {
  loading.value = true
  try {
    const [experimentList, allModels, allPipelines, datasetList] = await Promise.all([
      modelsApi.listExperiments(),
      modelsApi.all(),
      //  Names, not content: a failure here must not empty the page.
      pipelinesApi.list().catch(() => []),
      dataApi.listDatasets('?include=all'),
    ])
    experiments.value = experimentList
    modelList.value = allModels
    pipelineList.value = allPipelines
    datasets.value = datasetList
    settle(experiments.value.map((e) => e.id))
    await loadBoard()
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    loading.value = false
  }
}

async function create() {
  if (!form.value.name.trim()) {
    $q.notify({ type: 'warning', message: 'The experiment needs a name' })
    return
  }
  saving.value = true
  try {
    const created = await modelsApi.createExperiment({
      name: form.value.name,
      objective: form.value.objective,
      primary_metric: form.value.primary_metric,
      dataset_version_id: form.value.dataset_version_id,
      trials: form.value.trials
        .filter((trial) => trial.target_id)
        .map((trial) => ({
          target_id: trial.target_id,
          target_type: trial.target_type,
          label: trial.label,
          kind: trial.kind,
          parameters: trial.parameters,
        })),
    })
    dialog.value = false
    form.value = blankForm()
    await load()
    activeId.value = created.id
    //  Check straight away: a comparison you cannot run is worth knowing about
    //  before you go looking for the Run button.
    await runCheck()
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    saving.value = false
  }
}

function remove(experiment: Experiment) {
  $q.dialog({
    title: 'Delete experiment',
    message: `Delete "${experiment.name}"? The models and their runs are kept.`,
    cancel: true,
  }).onOk(async () => {
    try {
      await modelsApi.deleteExperiment(experiment.id)
      if (activeId.value === experiment.id) activeId.value = ''
      await load()
    } catch (error) {
      $q.notify({ type: 'negative', message: (error as Error).message })
    }
  })
}

watch(activeId, loadBoard)
onMounted(load)
</script>

<style scoped>
.check {
  margin: var(--fx-space-1) 0 0;
  padding-left: var(--fx-space-4);
  display: grid;
  gap: var(--fx-space-1);
  font-size: var(--fx-text-xs);
}

.check__error {
  color: var(--fx-bad);
}

.check__warning {
  color: var(--fx-wait);
}

/* Wide enough that a provider-qualified model name stays on one line. */
.fx-table__model {
  min-width: 17rem;
}

.fx-table__rank {
  width: 40px;
  color: var(--fx-ink-soft);
}

.fx-table__best {
  font-weight: 600;
}
</style>
