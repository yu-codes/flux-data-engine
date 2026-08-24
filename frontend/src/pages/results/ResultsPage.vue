<template>
  <q-page class="page-shell">
    <PageHeader
      title="Results"
      subtitle="First-class outputs of every execution — a table, a scalar, a classification, a probability field, a report"
    >
      <template #actions>
        <q-select
          v-model="kind"
          :options="kinds"
          label="Kind"
          dense
          outlined
          clearable
          style="min-width: 160px"
          @update:model-value="load"
        />
      </template>
    </PageHeader>

    <div class="row q-col-gutter-md">
      <div class="col-12 col-md-5 fx-pane">
        <SectionCard title="Results"
                     :subtitle="`${records.length} recorded`" flush>
          <q-list separator class="fx-list">
            <q-item
              v-for="record in records"
              :key="record.id"
              clickable
              :active="record.id === activeId"
              @click="select(record.id)"
            >
              <q-item-section avatar>
                <q-icon :name="kindIcon(record.kind)" :color="record.is_materialised ? 'primary' : 'grey'" />
              </q-item-section>
              <q-item-section class="result-row">
                <q-item-label class="result-row__title">
                  {{ summaryLine(record.summary) || record.kind }}
                </q-item-label>
                <q-item-label caption>
                  {{ new Date(record.created_at).toLocaleString() }}
                  <span v-if="record.row_count !== null"> · {{ record.row_count }} rows</span>
                </q-item-label>
              </q-item-section>
              <q-item-section side>
                <span class="fx-tag">{{ record.kind }}</span>
              </q-item-section>
            </q-item>
          </q-list>
          <EmptyState v-if="!records.length && !loading" message="No results yet" icon="output" />
        </SectionCard>
      </div>

      <div class="col-12 col-md-7">
        <SectionCard
          v-if="active"
          :title="summaryLine(active.summary) || active.kind"
          :subtitle="detailSubtitle"
        >
          <template #actions>
              <q-btn no-caps
                flat
                dense
                icon="play_circle"
                label="Execution"
                :to="{ name: 'execution-detail', params: { id: active.execution_id } }"
              />
              <q-btn no-caps
                v-if="active.dataset_id"
                flat
                dense
                icon="table_chart"
                label="Dataset"
                :to="{ name: 'dataset-detail', params: { id: active.dataset_id } }"
              />
              <q-btn no-caps
                v-else-if="isTable"
                flat
                dense
                icon="save_alt"
                label="Materialise"
                @click="materialiseDialog = true"
              />
          </template>
          <FactList v-if="Object.keys(active.metrics).length" :facts="metricFacts" class="q-mb-md" />
          <DataTable v-if="tableRows.length" :rows="tableRows" :rows-per-page="20" />
          <JsonBlock v-else-if="payload !== null" :value="payload" />
          <EmptyState v-else message="No payload" icon="output" />
        </SectionCard>
        <EmptyState v-else message="Select a result" icon="output" />
      </div>
    </div>

    <q-dialog v-model="materialiseDialog">
      <q-card style="min-width: 420px">
        <q-card-section class="fx-dialog__title">Materialise as a dataset</q-card-section>
        <q-card-section>
          <q-input v-model="datasetName" label="Dataset name" dense outlined autofocus />
          <div class="text-caption q-mt-sm" style="opacity: 0.65">
            The rows become a versioned Dataset, chartable like any other.
          </div>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn no-caps flat label="Cancel" v-close-popup />
          <q-btn no-caps unelevated color="primary" label="Materialise" @click="materialise" />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup lang="ts">
import { useQuasar } from 'quasar'
import { computed, onMounted, ref } from 'vue'

import { results as resultsApi } from '@/api'
import DataTable from '@/components/DataTable.vue'
import EmptyState from '@/components/EmptyState.vue'
import FactList, { type Fact } from '@/components/FactList.vue'
import JsonBlock from '@/components/JsonBlock.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import { useUrlSelection } from '@/composables/useUrlSelection'
import type { ResultRecord } from '@/types'

const $q = useQuasar()
const records = ref<ResultRecord[]>([])
const kinds = ref<string[]>([])
const kind = ref<string | null>(null)
//  Selection lives in the URL so a view can be linked, reloaded and shared.
const { selected: activeId, settle } = useUrlSelection()
const payload = ref<unknown>(null)
const loading = ref(false)
const materialiseDialog = ref(false)
const datasetName = ref('')

const KIND_ICONS: Record<string, string> = {
  table: 'table_rows',
  dataset: 'table_chart',
  scalar: 'looks_one',
  classification: 'label',
  probability: 'blur_on',
  time_series: 'show_chart',
  report: 'description',
  object: 'data_object',
  artifact: 'inventory_2',
  array: 'view_list',
}

const active = computed(() => records.value.find((r) => r.id === activeId.value) ?? null)
const tableRows = computed(() => {
  const value = payload.value as { rows?: Record<string, unknown>[] } | null
  return value && typeof value === 'object' && Array.isArray(value.rows) ? value.rows : []
})
const isTable = computed(() => tableRows.value.length > 0)

/** Identify the selected result without repeating what the title already says. */
const detailSubtitle = computed(() => {
  const record = active.value
  if (!record) return undefined
  const parts = [record.kind, new Date(record.created_at).toLocaleString()]
  if (record.row_count !== null) parts.push(`${record.row_count.toLocaleString()} rows`)
  parts.push(record.is_materialised ? 'materialised' : 'in place')
  return parts.join(' · ')
})

const metricFacts = computed<Fact[]>(() =>
  Object.entries(active.value?.metrics ?? {}).map(([label, value]) => ({
    label,
    value,
    numeric: typeof value === 'number',
  })),
)

function kindIcon(value: string) {
  return KIND_ICONS[value] ?? 'output'
}

/**
 * A one-line description of what a result is.
 *
 * The summary is machine metadata: for a transform it holds the whole options
 * object, which as a title is both unreadable and unbounded — a serialised
 * column list pushed the page three thousand pixels wide. Scalars describe a
 * result; nested objects are named, not printed.
 */
function summaryLine(summary: Record<string, unknown>) {
  const parts: string[] = []
  for (const [key, value] of Object.entries(summary)) {
    if (value === null || value === undefined || value === '') continue
    if (typeof value === 'object') {
      const size = Array.isArray(value) ? value.length : Object.keys(value).length
      parts.push(`${key}: ${size} ${Array.isArray(value) ? 'items' : 'settings'}`)
    } else {
      const text = String(value)
      //  A sentence is prose, not a label. It belongs in the detail pane, and
      //  putting it in the title only pushes the identifying fields off-screen.
      if (text.length > 40) continue
      parts.push(`${key}: ${text}`)
    }
    if (parts.length === 3) break
  }
  return parts.join(' · ')
}

async function load() {
  loading.value = true
  try {
    records.value = await resultsApi.list(kind.value ? `?kind=${kind.value}&limit=100` : '?limit=100')
    if (records.value.length) await select(settle(records.value.map((r) => r.id)))
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    loading.value = false
  }
}

async function select(id: string) {
  activeId.value = id
  payload.value = null
  try {
    payload.value = (await resultsApi.payload(id)).payload
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

async function materialise() {
  if (!active.value) return
  try {
    await resultsApi.materialise(active.value.id, datasetName.value)
    $q.notify({ type: 'positive', message: 'Materialised as a dataset' })
    materialiseDialog.value = false
    await load()
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

onMounted(async () => {
  kinds.value = (await resultsApi.kinds()).kinds
  await load()
})
</script>

<style scoped>
/*
 * `min-width: 0` is what stops a long unbroken value from widening the column
 * it lives in; the clamp keeps a two-line ceiling so rows stay the same height.
 */
.result-row {
  min-width: 0;
}

.result-row__title {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
}
</style>
