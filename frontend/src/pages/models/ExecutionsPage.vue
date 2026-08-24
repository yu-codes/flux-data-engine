<template>
  <q-page class="page-shell">
    <PageHeader
      title="Executions"
      subtitle="Training, prediction, simulation, optimisation, calculation, evaluation and transformation — one abstraction"
    >
      <template #actions>
        <q-btn flat dense no-caps icon="refresh" label="Refresh" :loading="loading" @click="load" />
        <q-btn
          no-caps
          unelevated
          color="primary"
          icon="play_arrow"
          label="New execution"
          class="fx-btn"
          @click="runDialog = true"
        />
      </template>
    </PageHeader>

    <!--
      Why the queue can look empty: in inline mode an execution runs on submit,
      so it is already finished by the time this page lists it. Without saying
      so, an empty Pending filter reads as a fault rather than as the design.
    -->
    <p v-if="mode" class="fx-meta q-mb-md">
      Execution mode <span class="mono">{{ mode }}</span> —
      {{
        mode === 'inline'
          ? 'a submitted execution runs immediately, so pending and running are rarely populated.'
          : 'submitted executions are queued and picked up by a worker.'
      }}
    </p>

    <!-- kind breakdown, so the vocabulary is visible rather than asserted -->
    <div class="row q-col-gutter-sm q-mb-md">
      <div v-for="entry in kindCounts" :key="entry.kind" class="col-6 col-sm-4 col-md">
        <button
          class="kind"
          :class="{ 'kind--active': kind === entry.kind }"
          type="button"
          @click="toggleKind(entry.kind)"
        >
          <span class="kind__count num">{{ entry.count }}</span>
          <span class="kind__label">{{ entry.kind }}</span>
        </button>
      </div>
    </div>

    <SectionCard title="Recent runs" :subtitle="filterSummary" flush>
      <template #actions>
        <q-select
          v-model="status"
          :options="statuses"
          label="Status"
          dense
          outlined
          clearable
          style="min-width: 150px"
          @update:model-value="load"
        />
      </template>

      <q-list separator class="fx-list">
        <q-item
          v-for="execution in executionList"
          :key="execution.id"
          clickable
          :to="{ name: 'execution-detail', params: { id: execution.id } }"
        >
          <q-item-section avatar>
            <q-icon :name="kindIcon(execution.kind)" size="20px" class="run__icon" />
          </q-item-section>
          <q-item-section>
            <q-item-label class="truncate">
              {{ targetName(execution) }}
              <q-badge
                v-if="execution.target_type !== 'model'"
                outline
                class="q-ml-xs"
                :label="execution.target_type"
              />
            </q-item-label>
            <q-item-label caption class="fx-meta">
              {{ execution.kind }}
              <span class="fx-meta__sep">·</span>{{ formatTime(execution.created_at) }}
              <template v-if="execution.duration_seconds !== null">
                <span class="fx-meta__sep">·</span>{{ execution.duration_seconds }}s
              </template>
              <template v-if="headlineMetric(execution)">
                <span class="fx-meta__sep">·</span>{{ headlineMetric(execution) }}
              </template>
            </q-item-label>
          </q-item-section>
          <q-item-section side>
            <StatusText :status="execution.status" />
          </q-item-section>
        </q-item>
      </q-list>
      <EmptyState v-if="!executionList.length && !loading" message="No executions" icon="play_circle" />
    </SectionCard>
    <!--
      The page that lists runs could not start one: the only way in was to
      remember which model you wanted and find it in the library. Picking the
      model here and handing off to its own run dialog keeps one copy of the
      configuration UI while making the action discoverable from the list.
    -->
    <q-dialog v-model="runDialog">
      <q-card style="min-width: 460px">
        <q-card-section class="fx-dialog__title">New execution</q-card-section>
        <q-card-section class="fx-dialog__subtitle">
          Choose a model; its parameters and input are configured on the next screen.
        </q-card-section>
        <q-card-section class="q-pt-none">
          <q-select
            v-model="runModelId"
            :options="runOptions"
            label="Model"
            dense
            outlined
            emit-value
            map-options
            autofocus
            use-input
            input-debounce="150"
            @filter="filterRunOptions"
          />
        </q-card-section>
        <q-card-actions align="right">
          <q-btn no-caps flat label="Cancel" v-close-popup />
          <q-btn
            no-caps
            unelevated
            color="primary"
            label="Configure run"
            :disable="!runModelId"
            @click="goConfigureRun"
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
  executions as executionsApi,
  models as modelsApi,
  pipelines as pipelinesApi,
  platform,
} from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import StatusText from '@/components/StatusText.vue'
import { useRouter } from 'vue-router'

import type { Execution, ModelDefinition, Pipeline } from '@/types'

const $q = useQuasar()
const router = useRouter()
const executionList = ref<Execution[]>([])
const allExecutions = ref<Execution[]>([])
const modelIndex = ref<Record<string, ModelDefinition>>({})
//  A pipeline is a runnable, so a run in this list may be one; without its
//  name the row could only show an id.
const pipelineIndex = ref<Record<string, Pipeline>>({})
const kinds = ref<string[]>([])
const statuses = ref<string[]>([])
const kind = ref<string | null>(null)
const status = ref<string | null>(null)
const loading = ref(false)

const KIND_ICONS: Record<string, string> = {
  training: 'model_training',
  prediction: 'online_prediction',
  simulation: 'waves',
  optimization: 'tune',
  calculation: 'calculate',
  evaluation: 'rule',
  transformation: 'transform',
}

const kindCounts = computed(() =>
  kinds.value.map((value) => ({
    kind: value,
    count: allExecutions.value.filter((execution) => execution.kind === value).length,
  })),
)

const filterSummary = computed(() => {
  const parts: string[] = [`${executionList.value.length} shown`]
  if (kind.value) parts.push(`kind ${kind.value}`)
  if (status.value) parts.push(`status ${status.value}`)
  return parts.join(' · ')
})

function kindIcon(value: string) {
  return KIND_ICONS[value] ?? 'play_circle'
}

/** What this run ran, whatever kind of runnable it was. */
function targetName(execution: Execution) {
  const id = execution.target_id
  if (!id) return 'inline definition'
  const named =
    execution.target_type === 'pipeline'
      ? pipelineIndex.value[id]?.name
      : modelIndex.value[id]?.name
  return named ?? id
}

function formatTime(value: string) {
  return new Date(value).toLocaleString()
}

/** Surface the one number that matters for this kind of run, if there is one. */
function headlineMetric(execution: Execution) {
  const metrics = execution.metrics ?? {}
  for (const key of ['accuracy', 'r2', 'rows_processed', 'rows_out', 'analog_count', 'frames']) {
    const value = metrics[key]
    if (typeof value === 'number') {
      return `${key} ${Number.isInteger(value) ? value : value.toFixed(3)}`
    }
  }
  return ''
}

function toggleKind(value: string) {
  kind.value = kind.value === value ? null : value
  load()
}

const mode = ref('')
const runDialog = ref(false)
const runModelId = ref('')
const runFilter = ref('')

//  Only library models: a pipeline step is run by its pipeline, not on its own.
const runOptions = computed(() => {
  const needle = runFilter.value.toLowerCase()
  return Object.values(modelIndex.value)
    .filter((m) => !needle || m.name.toLowerCase().includes(needle))
    .map((m) => ({ label: m.name, value: m.id }))
})

function filterRunOptions(value: string, update: (fn: () => void) => void) {
  update(() => {
    runFilter.value = value
  })
}

function goConfigureRun() {
  runDialog.value = false
  void router.push({ name: 'model-detail', params: { id: runModelId.value }, query: { run: '1' } })
}

async function load() {
  loading.value = true
  try {
    const params = new URLSearchParams({ limit: '100' })
    if (kind.value) params.set('kind', kind.value)
    if (status.value) params.set('status', status.value)
    const [list, everything, allModels, allPipelines, info] = await Promise.all([
      executionsApi.list(`?${params}`),
      executionsApi.list('?limit=500'),
      modelsApi.all(),
      //  Context, not content: a failure here must not empty the page.
      pipelinesApi.list().catch(() => []),
      platform.info().catch(() => null),
    ])
    executionList.value = list
    allExecutions.value = everything
    modelIndex.value = Object.fromEntries(allModels.map((m) => [m.id, m]))
    pipelineIndex.value = Object.fromEntries(allPipelines.map((p) => [p.id, p]))
    mode.value = info?.execution_mode ?? ''
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  const catalogue = await executionsApi.kinds()
  kinds.value = catalogue.kinds
  statuses.value = catalogue.statuses
  await load()
})
</script>

<style scoped>
.kind {
  width: 100%;
  background: none;
  border: 1px solid var(--fx-border);
  border-radius: var(--fx-radius-sm);
  padding: var(--fx-space-2) var(--fx-space-3);
  cursor: pointer;
  color: inherit;
  font: inherit;
  text-align: left;
  transition: border-color 120ms ease, background 120ms ease;
}

.kind:hover {
  border-color: var(--fx-border-strong);
}

.kind--active {
  border-color: var(--fx-border-strong);
  background: var(--fx-surface-muted);
}

.kind__count {
  display: block;
  font-size: var(--fx-text-lg);
  font-weight: 600;
  line-height: 1.2;
}

.kind__label {
  display: block;
  font-size: var(--fx-text-xs);
  opacity: var(--fx-ink-muted);
}

.run__icon {
  opacity: var(--fx-ink-faint);
}
</style>
