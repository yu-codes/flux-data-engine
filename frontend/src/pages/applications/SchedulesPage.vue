<template>
  <q-page class="page-shell">
    <PageHeader
      title="Schedules"
      subtitle="Recurring executions. The worker fires them; each run is an ordinary Execution."
    >
      <template #actions>
        <q-btn no-caps flat dense icon="refresh" :loading="loading" label="Refresh" @click="load" />
        <q-btn no-caps color="primary" unelevated icon="add" label="New schedule" @click="openCreate" />
      </template>
    </PageHeader>

    <q-banner v-if="info && !info.scheduler_enabled" dense class="flux-card q-mb-md">
      <template #avatar><q-icon name="warning" color="warning" /></template>
      The scheduler loop is switched off in this deployment
      (<span class="mono">FLUX_SCHEDULER_ENABLED=false</span>), so schedules only run when
      fired by hand.
    </q-banner>

    <SectionCard title="Schedules"
                 :subtitle="`${schedules.length} defined`" flush>
      <q-list separator class="fx-list">
        <q-item v-for="schedule in schedules" :key="schedule.id">
          <q-item-section avatar>
            <q-icon
              :name="schedule.status === 'active' ? 'schedule' : 'pause_circle'"
              :color="schedule.status === 'active' ? 'primary' : 'grey'"
            />
          </q-item-section>
          <q-item-section>
            <q-item-label>{{ schedule.name }}</q-item-label>
            <q-item-label caption>
              {{ targetName(schedule) }} · {{ schedule.kind }} ·
              <span class="mono">{{ trigger(schedule) }}</span>
            </q-item-label>
            <q-item-label caption>
              <span v-if="schedule.next_run_at">next {{ formatTime(schedule.next_run_at) }}</span>
              <span v-if="schedule.run_count"> · {{ schedule.run_count }} runs</span>
              <span v-if="schedule.failure_count"> · {{ schedule.failure_count }} failed</span>
            </q-item-label>
            <q-item-label v-if="schedule.last_error" caption class="text-negative">
              {{ schedule.last_error }}
            </q-item-label>
          </q-item-section>
          <q-item-section side>
            <div class="row items-center q-gutter-xs">
              <StatusText v-if="schedule.last_status" :status="schedule.last_status" />
              <q-btn
                v-if="schedule.last_execution_id"
                flat
                dense
                icon="play_circle"
                :to="{ name: 'execution-detail', params: { id: schedule.last_execution_id } }"
              >
                <q-tooltip>Last execution</q-tooltip>
              </q-btn>
              <q-btn flat dense icon="bolt" @click="runNow(schedule)">
                <q-tooltip>Run now</q-tooltip>
              </q-btn>
              <q-btn
                flat
                dense
                :icon="schedule.status === 'active' ? 'pause' : 'play_arrow'"
                @click="toggle(schedule)"
              >
                <q-tooltip>{{ schedule.status === 'active' ? 'Pause' : 'Resume' }}</q-tooltip>
              </q-btn>
              <q-btn flat dense round icon="delete" color="negative" @click="remove(schedule)" />
            </div>
          </q-item-section>
        </q-item>
      </q-list>
      <EmptyState
        v-if="!schedules.length && !loading"
        message="No schedules"
        hint="Pick a model and a cadence; the worker does the rest"
        icon="schedule"
      />
    </SectionCard>

    <q-dialog v-model="dialog">
      <q-card style="min-width: 520px">
        <q-card-section class="fx-dialog__title">New schedule</q-card-section>
        <q-card-section class="q-gutter-md">
          <q-input v-model="form.name" label="Name" dense outlined autofocus />
          <q-select
            v-model="form.target_type"
            :options="targetTypeOptions"
            label="What to run"
            dense
            outlined
            emit-value
            map-options
            @update:model-value="syncTarget"
          />
          <q-select
            v-model="form.target_id"
            :options="targetOptions"
            :label="form.target_type === 'pipeline' ? 'Pipeline' : 'Model'"
            dense
            outlined
            emit-value
            map-options
            @update:model-value="syncKinds"
          />
          <!--
            A pipeline run has one verb, so the kind picker would be a control
            with one option in it.
          -->
          <q-select
            v-if="form.target_type === 'model'"
            v-model="form.kind"
            :options="kindOptions"
            label="Execution kind"
            dense
            outlined
          />
          <q-select
            v-model="form.dataset_id"
            :options="datasetOptions"
            label="Input dataset (optional)"
            dense
            outlined
            clearable
            emit-value
            map-options
          />

          <q-btn-toggle
            v-model="form.triggerType"
            spread
            no-caps
            unelevated
            toggle-color="primary"
            :options="[
              { label: 'Every N seconds', value: 'interval' },
              { label: 'Cron', value: 'cron' },
            ]"
          />
          <q-input
            v-if="form.triggerType === 'interval'"
            v-model.number="form.interval_seconds"
            type="number"
            label="Interval (seconds)"
            dense
            outlined
            :hint="`minimum ${minInterval}`"
          />
          <q-input
            v-else
            v-model="form.cron"
            label="Cron expression"
            dense
            outlined
            hint="minute hour day month weekday, e.g. 0 3 * * *"
          />

          <div class="row items-center q-gutter-sm">
            <q-btn no-caps flat dense icon="visibility" label="Preview" @click="preview" />
            <span v-if="nextRuns.length" class="text-caption mono">
              {{ nextRuns.map((r) => formatTime(r)).join('  ·  ') }}
            </span>
          </div>

          <q-input v-model="form.description" label="Description" dense outlined />
        </q-card-section>
        <q-card-actions align="right">
          <q-btn no-caps flat label="Cancel" v-close-popup />
          <q-btn no-caps unelevated color="primary" label="Create" :loading="saving" @click="create" />
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
  models as modelsApi,
  pipelines as pipelinesApi,
  platform,
  schedules as schedulesApi,
} from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusText from '@/components/StatusText.vue'
import SectionCard from '@/components/SectionCard.vue'
import type { Dataset, ModelDefinition, Pipeline, PlatformInfo, Schedule } from '@/types'

const $q = useQuasar()

const schedules = ref<Schedule[]>([])
const modelList = ref<ModelDefinition[]>([])
const pipelineList = ref<Pipeline[]>([])
const datasets = ref<Dataset[]>([])
const info = ref<PlatformInfo | null>(null)
const minInterval = ref(30)
const nextRuns = ref<string[]>([])
const loading = ref(false)
const saving = ref(false)
const dialog = ref(false)

const form = ref({
  name: '',
  target_type: 'model' as 'model' | 'pipeline',
  target_id: '',
  kind: 'prediction',
  dataset_id: null as string | null,
  triggerType: 'interval' as 'interval' | 'cron',
  interval_seconds: 3600,
  cron: '0 3 * * *',
  description: '',
})

//  A picker offers the library. Step models belong to their pipeline and
//  choosing one here would attach a stage of somebody else's chain.
const modelOptions = computed(() =>
  modelList.value.map((m) => ({ label: m.name, value: m.id })),
)
const pipelineOptions = computed(() =>
  pipelineList.value.map((p) => ({ label: p.name, value: p.id })),
)
const targetTypeOptions = [
  { label: 'A model', value: 'model' },
  { label: 'A pipeline', value: 'pipeline' },
]
const targetOptions = computed(() =>
  form.value.target_type === 'pipeline' ? pipelineOptions.value : modelOptions.value,
)

function syncTarget() {
  form.value.target_id = targetOptions.value[0]?.value ?? ''
  form.value.kind = form.value.target_type === 'pipeline' ? 'transformation' : 'prediction'
  syncKinds()
}

/** What this schedule fires, by name rather than by id. */
function targetName(schedule: Schedule): string {
  const list = schedule.target_type === 'pipeline' ? pipelineList.value : modelList.value
  const found = list.find((item) => item.id === schedule.target_id)
  return found ? found.name : schedule.target_id
}
const datasetOptions = computed(() => datasets.value.map((d) => ({ label: d.name, value: d.id })))
const kindOptions = computed(() => {
  const model = modelList.value.find((m) => m.id === form.value.target_id)
  return (model?.metadata.supported_kinds as string[]) ?? ['prediction']
})

function syncKinds() {
  form.value.kind = kindOptions.value[0] ?? 'prediction'
}

function trigger(schedule: Schedule) {
  return schedule.cron ? `cron ${schedule.cron}` : `every ${schedule.interval_seconds}s`
}

function formatTime(value: string) {
  return new Date(value).toLocaleString()
}

async function load() {
  loading.value = true
  try {
    const [list, allModels, allPipelines, datasetList, statuses, platformInfo] =
      await Promise.all([
        schedulesApi.list(),
        modelsApi.all(),
        pipelinesApi.list(),
        dataApi.listDatasets(),
        schedulesApi.statuses(),
        platform.info(),
      ])
    schedules.value = list
    modelList.value = allModels
    pipelineList.value = allPipelines
    datasets.value = datasetList
    minInterval.value = statuses.min_interval_seconds
    info.value = platformInfo
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    loading.value = false
  }
}

function openCreate() {
  form.value = {
    name: '',
    target_type: 'model',
    target_id: modelList.value[0]?.id ?? '',
    kind: 'prediction',
    dataset_id: null,
    triggerType: 'interval',
    interval_seconds: 3600,
    cron: '0 3 * * *',
    description: '',
  }
  syncKinds()
  nextRuns.value = []
  dialog.value = true
}

async function preview() {
  try {
    const body =
      form.value.triggerType === 'cron'
        ? { cron: form.value.cron, count: 3 }
        : { interval_seconds: form.value.interval_seconds, count: 3 }
    nextRuns.value = (await schedulesApi.preview(body)).next_runs
  } catch (error) {
    nextRuns.value = []
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

async function create() {
  saving.value = true
  try {
    await schedulesApi.create({
      name: form.value.name,
      target_id: form.value.target_id,
      target_type: form.value.target_type,
      kind: form.value.kind,
      dataset_id: form.value.dataset_id,
      description: form.value.description,
      ...(form.value.triggerType === 'cron'
        ? { cron: form.value.cron }
        : { interval_seconds: form.value.interval_seconds }),
    })
    dialog.value = false
    await load()
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    saving.value = false
  }
}

async function runNow(schedule: Schedule) {
  try {
    const fired = await schedulesApi.runNow(schedule.id)
    $q.notify({
      type: fired.last_status === 'failed' ? 'negative' : 'positive',
      message: `Fired: ${fired.last_status}`,
      caption: fired.last_error ?? undefined,
    })
    await load()
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

async function toggle(schedule: Schedule) {
  if (schedule.status === 'active') await schedulesApi.pause(schedule.id)
  else await schedulesApi.resume(schedule.id)
  await load()
}

function remove(schedule: Schedule) {
  $q.dialog({ title: 'Delete schedule', message: `Delete "${schedule.name}"?`, cancel: true }).onOk(
    async () => {
      await schedulesApi.remove(schedule.id)
      await load()
    },
  )
}

onMounted(load)
</script>
