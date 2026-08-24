<template>
  <q-page class="page-shell">
    <PageHeader
      title="Pipelines"
      subtitle="A graph of model executions. Each step's output dataset is the next step's input."
    >
      <template #actions>
        <q-btn no-caps color="primary" unelevated icon="add" label="New pipeline" @click="openCreate" />
      </template>
    </PageHeader>

    <div class="row q-col-gutter-md">
      <div class="col-12 col-md-4">
        <SectionCard title="Pipelines"
                     :subtitle="`${pipelines.length} defined`" flush>
          <q-list separator class="fx-list">
            <q-item
              v-for="pipeline in pipelines"
              :key="pipeline.id"
              clickable
              :active="pipeline.id === activeId"
              @click="select(pipeline.id)"
            >
              <q-item-section avatar>
                <q-icon name="account_tree" :color="statusColour(pipeline.last_run_status)" />
              </q-item-section>
              <q-item-section>
                <q-item-label>{{ pipeline.name }}</q-item-label>
                <q-item-label caption>
                  {{ pipeline.steps.length }} steps
                  <span v-if="pipeline.last_run_status"> · last run {{ pipeline.last_run_status }}</span>
                </q-item-label>
              </q-item-section>
            </q-item>
          </q-list>
          <EmptyState
            v-if="!pipelines.length && !loading"
            message="No pipelines"
            hint="Chain models so each one reads the previous one's output"
            icon="account_tree"
          />
        </SectionCard>
      </div>

      <div class="col-12 col-md-8">
        <template v-if="active">
          <SectionCard
            :title="active.name"
            :subtitle="active.description || `${active.steps.length} steps`"
            class="q-mb-md"
          >
            <template #actions>
              <q-btn
                no-caps
                color="primary"
                unelevated
                icon="play_arrow"
                label="Run pipeline"
                class="fx-btn"
                :loading="running"
                @click="run"
              />
              <!--
                A twelve-step pipeline over real data does not belong in an
                HTTP request. Running it as a job returns immediately and the
                page follows it, so the tab is usable while it works.
              -->
              <q-btn
                no-caps
                flat
                dense
                icon="schedule_send"
                label="Run in background"
                :disable="running || watching"
                @click="runInBackground"
              />
              <q-btn flat dense round icon="delete" color="negative" @click="remove">
                <q-tooltip>Delete pipeline</q-tooltip>
              </q-btn>
            </template>
            <div v-if="job" class="run-banner" :class="`run-banner--${job.status}`">
              <q-spinner v-if="watching" size="16px" />
              <span>
                Running in the background — {{ job.status }}
                <template v-if="job.attempts > 1">(attempt {{ job.attempts }})</template>
              </span>
              <span v-if="job.error" class="fx-meta">{{ job.error }}</span>
              <q-space />
              <q-btn
                v-if="watching"
                no-caps
                flat
                dense
                label="Cancel"
                @click="cancelJob"
              />
            </div>
            <PipelineGraph v-if="graph" :graph="graph" :step-runs="latestRun?.step_runs ?? []" />
          </SectionCard>

          <SectionCard v-if="latestRun" title="Latest run" :subtitle="runSubtitle" class="q-mb-md">
            <template #actions>
              <StatusText :status="latestRun.status" />
            </template>
              <q-banner v-if="latestRun.error" dense class="bg-red-1 text-negative q-mb-sm">
                {{ latestRun.error }}
              </q-banner>
              <q-list dense separator class="fx-list">
                <q-item v-for="step in orderedSteps" :key="step.step_name">
                  <q-item-section avatar>
                    <q-icon :name="stepIcon(step.status)" :color="statusColour(step.status)" />
                  </q-item-section>
                  <q-item-section>
                    <q-item-label>{{ step.step_name }}</q-item-label>
                    <q-item-label caption>
                      {{ ranWhat(step) }}
                      <span v-if="step.row_count !== null"> · {{ step.row_count }} rows</span>
                      <span v-if="step.duration_seconds !== null"> · {{ step.duration_seconds }}s</span>
                    </q-item-label>
                    <q-item-label v-if="step.error" caption class="text-negative">
                      {{ step.error }}
                    </q-item-label>
                  </q-item-section>
                  <q-item-section side>
                    <div class="row q-gutter-xs">
                      <!--
                        A nested step has a run rather than an execution: the
                        pipeline it delegated to is what there is to look at.
                      -->
                      <q-btn
                        v-if="step.pipeline_run_id"
                        flat
                        dense
                        no-caps
                        size="sm"
                        icon="account_tree"
                        label="Nested run"
                        @click="showRun(step.pipeline_run_id)"
                      />
                      <q-btn
                        v-if="step.execution_id"
                        flat
                        dense
                        icon="play_circle"
                        :to="{ name: 'execution-detail', params: { id: step.execution_id } }"
                      >
                        <q-tooltip>Execution</q-tooltip>
                      </q-btn>
                      <q-btn
                        v-if="step.dataset_id"
                        flat
                        dense
                        icon="table_chart"
                        :to="{ name: 'dataset-detail', params: { id: step.dataset_id } }"
                      >
                        <q-tooltip>Output dataset</q-tooltip>
                      </q-btn>
                    </div>
                  </q-item-section>
                </q-item>
              </q-list>
          </SectionCard>

          <SectionCard
            v-if="runs.length > 1"
            title="Run history"
            :subtitle="`${runs.length} runs recorded`"
            flush
          >
              <q-list dense separator class="fx-list">
                <q-item
                  v-for="entry in runs"
                  :key="entry.id"
                  clickable
                  :active="entry.id === latestRun?.id"
                  @click="latestRun = entry"
                >
                  <q-item-section>
                    <q-item-label>{{ new Date(entry.created_at).toLocaleString() }}</q-item-label>
                    <q-item-label caption>
                      {{ entry.step_runs.filter((s) => s.status === 'succeeded').length }} /
                      {{ entry.step_runs.length }} steps succeeded
                    </q-item-label>
                  </q-item-section>
                  <q-item-section side>
                    <StatusText :status="entry.status" />
                  </q-item-section>
                </q-item>
              </q-list>
          </SectionCard>
        </template>
        <EmptyState v-else message="Select a pipeline" icon="account_tree" />
      </div>
    </div>

    <!-- create -->
    <q-dialog v-model="dialog" full-width>
      <q-card style="max-width: 1000px; margin: 0 auto">
        <q-card-section class="fx-dialog__title">Build a pipeline</q-card-section>
        <q-card-section class="q-pt-none">
          <div class="row q-col-gutter-md">
            <div class="col-12 col-md-4">
              <q-input v-model="form.name" label="Name" dense outlined autofocus />
            </div>
            <div class="col-12 col-md-4">
              <q-select
                v-model="form.input_dataset_id"
                :options="datasetOptions"
                label="Input dataset"
                dense
                outlined
                emit-value
                map-options
                @update:model-value="loadInputColumns"
              />
            </div>
            <div class="col-12 col-md-4">
              <q-input v-model="form.description" label="Description" dense outlined />
            </div>
          </div>
        </q-card-section>

        <q-card-section class="q-pt-none">
          <div class="builder__heading">
            <div>
              <div class="fx-card__title">Steps</div>
              <div class="fx-card__subtitle">
                Each step reads one table and writes one table. Pick a transform and
                fill in its parameters, or point the step at a model or a pipeline you
                already have.
              </div>
            </div>
            <div class="fx-tags">
              <q-btn
                no-caps
                flat
                dense
                icon="add"
                label="Transform step"
                @click="addStep('transform')"
              />
              <q-btn
                no-caps
                flat
                dense
                icon="add"
                label="Model step"
                @click="addStep('model')"
              />
              <!--
                A shared preparation belongs in one pipeline that others run,
                not copied into each of them - after which fixing it means
                finding all the copies.
              -->
              <q-btn
                no-caps
                flat
                dense
                icon="add"
                label="Pipeline step"
                @click="addStep('pipeline')"
              />
            </div>
          </div>

          <div v-if="!form.steps.length" class="fx-meta q-py-md">
            No steps yet. A pipeline needs at least one.
          </div>

          <q-card
            v-for="(step, index) in form.steps"
            :key="step.uid"
            flat
            bordered
            class="builder__step q-mb-sm"
          >
            <q-card-section class="q-pb-none">
              <div class="row q-col-gutter-sm items-start">
                <div class="col-auto builder__index num">{{ index + 1 }}</div>
                <div class="col-12 col-md-3">
                  <q-input v-model="step.name" label="Step name" dense outlined />
                </div>
                <div class="col-12 col-md-4">
                  <q-select
                    v-if="step.mode === 'transform'"
                    v-model="step.transform"
                    :options="transformOptions"
                    label="Transform"
                    dense
                    outlined
                    emit-value
                    map-options
                    :hint="transformHint(step.transform)"
                    @update:model-value="() => resetOptions(step)"
                  />
                  <q-select
                    v-else-if="step.mode === 'pipeline'"
                    v-model="step.pipeline_id"
                    :options="nestableOptions"
                    label="Pipeline"
                    dense
                    outlined
                    emit-value
                    map-options
                    hint="another pipeline, run as this step"
                  />
                  <q-select
                    v-else
                    v-model="step.model_id"
                    :options="modelOptions"
                    label="Model"
                    dense
                    outlined
                    emit-value
                    map-options
                    hint="an existing model, run as-is"
                  />
                </div>
                <div class="col-10 col-md-4">
                  <q-select
                    v-model="step.input_from"
                    :options="upstreamOptions(index)"
                    label="Reads from"
                    dense
                    outlined
                    emit-value
                    map-options
                    clearable
                    hint="empty = the pipeline's input dataset"
                  />
                </div>
                <div class="col-2 col-md-auto text-right">
                  <q-btn flat dense round icon="arrow_upward" :disable="index === 0" @click="moveStep(index, -1)">
                    <q-tooltip>Move earlier</q-tooltip>
                  </q-btn>
                  <q-btn
                    flat
                    dense
                    round
                    icon="arrow_downward"
                    :disable="index === form.steps.length - 1"
                    @click="moveStep(index, 1)"
                  >
                    <q-tooltip>Move later</q-tooltip>
                  </q-btn>
                  <q-btn flat dense round icon="close" @click="form.steps.splice(index, 1)">
                    <q-tooltip>Remove step</q-tooltip>
                  </q-btn>
                </div>
              </div>
            </q-card-section>

            <q-card-section v-if="step.mode === 'transform' && step.transform">
              <ContractForm
                v-model="step.options"
                :fields="transformFields(step.transform)"
                :columns="inputColumns"
              />
            </q-card-section>
          </q-card>
        </q-card-section>

        <q-card-actions align="right">
          <q-btn no-caps flat label="Cancel" v-close-popup />
          <q-btn
            no-caps
            unelevated
            color="primary"
            label="Create pipeline"
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
import { computed, onMounted, ref } from 'vue'

import {
  data as dataApi,
  jobs as jobsApi,
  models as modelsApi,
  pipelines as pipelinesApi,
} from '@/api'
import { useJob } from '@/composables/useJob'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import StatusText from '@/components/StatusText.vue'
import ContractForm from '@/components/ContractForm.vue'
import PipelineGraph from '@/components/PipelineGraph.vue'
import { useUrlSelection } from '@/composables/useUrlSelection'
import type {
  Dataset,
  ModelDefinition,
  Pipeline,
  PipelineRun,
  StepRun,
  TransformSpec,
} from '@/types'

const $q = useQuasar()

const pipelines = ref<Pipeline[]>([])
const datasets = ref<Dataset[]>([])
const modelList = ref<ModelDefinition[]>([])
const runs = ref<PipelineRun[]>([])
const latestRun = ref<PipelineRun | null>(null)
const graph = ref<Awaited<ReturnType<typeof pipelinesApi.graph>> | null>(null)
//  Selection lives in the URL so a view can be linked, reloaded and shared.
const { selected: activeId, settle } = useUrlSelection()
const loading = ref(false)
const running = ref(false)
const { job, watching, watch } = useJob()
const saving = ref(false)
const dialog = ref(false)

interface StepForm {
  uid: number
  mode: 'transform' | 'model' | 'pipeline'
  name: string
  transform: string
  options: Record<string, unknown>
  model_id: string
  pipeline_id: string
  input_from: string | null
}

const form = ref<{ name: string; description: string; input_dataset_id: string; steps: StepForm[] }>(
  { name: '', description: '', input_dataset_id: '', steps: [] },
)

const transforms = ref<TransformSpec[]>([])
const inputColumns = ref<string[]>([])
let nextUid = 1

const transformOptions = computed(() =>
  transforms.value.map((t) => ({ label: t.name, value: t.key })),
)

function transformSpec(key: string) {
  return transforms.value.find((t) => t.key === key)
}

function transformHint(key: string) {
  return transformSpec(key)?.description ?? 'what this step does to the table'
}

function transformFields(key: string) {
  return transformSpec(key)?.parameters.fields ?? []
}

/** Changing the transform invalidates the parameters that belonged to it. */
function resetOptions(step: StepForm) {
  step.options = {}
  if (!step.name || step.name.startsWith('step ')) {
    step.name = transformSpec(step.transform)?.name.toLowerCase() ?? step.name
  }
}

function moveStep(index: number, offset: number) {
  const target = index + offset
  if (target < 0 || target >= form.value.steps.length) return
  const [moved] = form.value.steps.splice(index, 1)
  form.value.steps.splice(target, 0, moved)
}

/** Column names from the input dataset, so parameters can be picked not typed. */
async function loadInputColumns() {
  inputColumns.value = []
  const dataset = datasets.value.find((d) => d.id === form.value.input_dataset_id)
  if (!dataset) return
  try {
    const detail = await dataApi.getDataset(dataset.id)
    inputColumns.value = detail.schema_fields.map((field) => field.name)
  } catch {
    //  Suggestions are a convenience; a missing schema must not block building.
    inputColumns.value = []
  }
}

const active = computed(() => pipelines.value.find((p) => p.id === activeId.value) ?? null)
const orderedSteps = computed(() =>
  [...(latestRun.value?.step_runs ?? [])].sort((a, b) => a.order - b.order),
)

/** How the run went, in one line: when, how long, and how far it got. */
const runSubtitle = computed(() => {
  const run = latestRun.value
  if (!run) return undefined
  const done = run.step_runs.filter((step) => step.status === 'succeeded').length
  const when = new Date(run.created_at).toLocaleString()
  const took = run.duration_seconds !== null ? ` · ${run.duration_seconds}s` : ''
  return `${when}${took} · ${done} of ${run.step_runs.length} steps succeeded`
})
const datasetOptions = computed(() => datasets.value.map((d) => ({ label: d.name, value: d.id })))
//  A picker offers the library. Step models belong to their pipeline and
//  choosing one here would attach a stage of somebody else's chain.
const modelOptions = computed(() =>
  modelList.value
    .map((m) => ({ label: `${m.name} (${m.type.replace(/_/g, ' ')})`, value: m.id })),
)

//  Every pipeline except the one being edited: a pipeline that ran itself
//  would never end, and the server refuses it, but offering it is unkind.
const nestableOptions = computed(() =>
  pipelines.value
    .filter((p) => p.id !== activeId.value || !dialog.value)
    .map((p) => ({ label: `${p.name} (${p.steps.length} steps)`, value: p.id })),
)

/** Follow a nested step to the run it delegated to. */
async function showRun(runId: string) {
  try {
    const run = await pipelinesApi.run_detail(runId)
    await select(run.pipeline_id)
    //  The nested run, not merely the nested pipeline's latest: the step
    //  points at one particular run and that is the one to show.
    latestRun.value = runs.value.find((entry) => entry.id === run.id) ?? run
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

function upstreamOptions(index: number) {
  return form.value.steps
    .slice(0, index)
    .filter((step) => step.name)
    .map((step) => ({ label: step.name, value: step.name }))
}

function statusColour(status: string | null | undefined) {
  return (
    { succeeded: 'positive', failed: 'negative', running: 'info', pending: 'warning', cancelled: 'grey' }[
      status ?? ''
    ] ?? 'grey'
  )
}

function stepIcon(status: string) {
  return (
    { succeeded: 'check_circle', failed: 'error', cancelled: 'block', running: 'autorenew' }[status] ??
    'radio_button_unchecked'
  )
}

function modelName(id: string) {
  return modelList.value.find((m) => m.id === id)?.name ?? id
}

/** What a step ran: a nested pipeline, a library model, or its own transform. */
function ranWhat(step: StepRun) {
  if (step.pipeline_run_id) {
    const nested = active.value?.steps.find((s) => s.name === step.step_name)
    const name = pipelines.value.find((p) => p.id === nested?.pipeline_id)?.name
    return name ? `${name} · nested pipeline` : 'nested pipeline'
  }
  return step.model_id ? modelName(step.model_id) : 'inline transform'
}

function addStep(mode: 'transform' | 'model' | 'pipeline' = 'transform') {
  const previous = form.value.steps[form.value.steps.length - 1]
  form.value.steps.push({
    uid: nextUid++,
    mode,
    name: `step ${form.value.steps.length + 1}`,
    transform: '',
    options: {},
    model_id: '',
    pipeline_id: '',
    //  A new step reads the previous one by default: that is what a chain is,
    //  and branching is the deliberate choice, not the accident.
    input_from: previous?.name ?? null,
  })
}

function openCreate() {
  form.value = { name: '', description: '', input_dataset_id: datasets.value[0]?.id ?? '', steps: [] }
  addStep()
  void loadInputColumns()
  dialog.value = true
}

async function load() {
  loading.value = true
  try {
    const [pipelineList, datasetList, allModels, catalogue] = await Promise.all([
      pipelinesApi.list(),
      dataApi.listDatasets(),
      modelsApi.all(),
      modelsApi.transforms(),
    ])
    pipelines.value = pipelineList
    datasets.value = datasetList
    modelList.value = allModels
    transforms.value = catalogue.transforms
    if (pipelineList.length) await select(settle(pipelineList.map((p) => p.id)))
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    loading.value = false
  }
}

async function select(id: string) {
  activeId.value = id
  latestRun.value = null
  graph.value = null
  try {
    const [graphData, runList] = await Promise.all([
      pipelinesApi.graph(id),
      pipelinesApi.runs(id),
    ])
    graph.value = graphData
    runs.value = runList
    latestRun.value = runList[0] ?? null
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

/**
 * Submit the run and follow it.
 *
 * The page reloads its own state when the job finishes rather than polling:
 * the stream says when there is something new, which is both cheaper and the
 * difference between "appears to have done nothing" and "told you it was
 * working".
 */
async function runInBackground() {
  if (!active.value) return
  try {
    const submitted = await pipelinesApi.runInBackground(active.value.id)
    $q.notify({ type: 'info', message: 'Queued. This page will update when it finishes.' })
    await watch(submitted.job_id, async (finished) => {
      $q.notify({
        type: finished.status === 'succeeded' ? 'positive' : 'negative',
        message: `Pipeline ${finished.status}`,
        caption: finished.error ?? undefined,
      })
      await load()
    })
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

async function cancelJob() {
  if (!job.value) return
  try {
    await jobsApi.cancel(job.value.id)
    $q.notify({ type: 'info', message: 'Asked the run to stop' })
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

async function run() {
  if (!active.value) return
  running.value = true
  try {
    const result = await pipelinesApi.run(active.value.id)
    $q.notify({
      type: result.status === 'succeeded' ? 'positive' : 'negative',
      message: `Pipeline ${result.status}`,
      caption: result.error ?? undefined,
    })
    await load()
    await select(active.value.id)
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    running.value = false
  }
}

/**
 * Model names are unique platform-wide, so rebuilding a pipeline with the same
 * name would otherwise fail on the first step. Suffix rather than refuse: the
 * user asked for a pipeline, not for a lesson in naming.
 */
const claimedNames = new Set<string>()

function uniqueModelName(base: string) {
  const taken = new Set([...modelList.value.map((m) => m.name), ...claimedNames])
  let name = base
  let suffix = 2
  while (taken.has(name)) {
    name = `${base} (${suffix})`
    suffix += 1
  }
  claimedNames.add(name)
  return name
}

/**
 * Turn the builder's rows into the steps the API takes.
 *
 * A transform step describes itself - provider plus configuration - and stays
 * part of the pipeline. This used to create a ModelDefinition per step and
 * mark it `scope: 'step'` so the library would hide it again, which is a lot
 * of machinery for something nobody wanted to exist.
 */
function stepsForApi() {
  return form.value.steps.map((step) => {
    if (step.mode === 'transform') {
      if (!step.transform) throw new Error(`step "${step.name}" has no transform`)
      return {
        name: step.name,
        provider: 'python-transform',
        configuration: { transform: step.transform, options: step.options },
        description: transformSpec(step.transform)?.description ?? '',
        input_from: step.input_from || null,
      }
    }
    if (step.mode === 'pipeline') {
      if (!step.pipeline_id) throw new Error(`step "${step.name}" names no pipeline`)
      return {
        name: step.name,
        pipeline_id: step.pipeline_id,
        input_from: step.input_from || null,
      }
    }
    if (!step.model_id) throw new Error(`step "${step.name}" has no model`)
    return {
      name: step.name,
      model_id: step.model_id,
      input_from: step.input_from || null,
    }
  })
}

async function create() {
  if (!form.value.name.trim()) {
    $q.notify({ type: 'warning', message: 'The pipeline needs a name' })
    return
  }
  if (!form.value.steps.length) {
    $q.notify({ type: 'warning', message: 'Add at least one step' })
    return
  }
  saving.value = true
  try {
    const steps = stepsForApi()
    const created = await pipelinesApi.create({
      name: form.value.name,
      description: form.value.description,
      input_dataset_id: form.value.input_dataset_id,
      steps,
    })
    dialog.value = false
    await load()
    await select(created.id)
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    saving.value = false
  }
}

function remove() {
  if (!active.value) return
  const target = active.value
  $q.dialog({ title: 'Delete pipeline', message: `Delete "${target.name}"?`, cancel: true }).onOk(
    async () => {
      await pipelinesApi.remove(target.id)
      activeId.value = ''
      await load()
    },
  )
}

onMounted(load)
</script>

<style scoped>
/*
  A run happening elsewhere needs somewhere to say so. Inline with the graph
  rather than a toast, because a toast is gone by the time the run finishes.
*/
.run-banner {
  display: flex;
  align-items: center;
  gap: var(--fx-space-2);
  padding: var(--fx-space-2) var(--fx-space-4);
  border-bottom: 1px solid var(--fx-border);
  font-size: var(--fx-text-sm);
}

.run-banner--failed {
  color: var(--fx-danger);
}

.builder__heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--fx-space-4);
  margin-bottom: var(--fx-space-3);
}

.builder__step {
  border-color: var(--fx-border);
  border-radius: var(--fx-radius-sm);
}

.builder__index {
  width: 24px;
  padding-top: 10px;
  text-align: center;
  opacity: var(--fx-ink-faint);
  font-size: var(--fx-text-sm);
}
</style>
