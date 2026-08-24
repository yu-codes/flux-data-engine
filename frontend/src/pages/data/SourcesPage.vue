<template>
  <q-page class="page-shell">
    <PageHeader
      title="Sources"
      subtitle="Where external data comes from. Every format is normalised into a Dataset."
    >
      <template #actions>
        <q-btn no-caps color="primary" icon="add" label="New source" unelevated @click="dialog = true" />
      </template>
    </PageHeader>

    <SectionCard title="Registered sources" :subtitle="`${summary} connections`" flush>
      <template #actions>
        <SearchField v-model="term" placeholder="Search sources" />
      </template>
      <AsyncSection
        :pending="loading && !sources.length"
        :error="loadError"
        title="Could not load sources"
        :rows="3"
        :on-retry="load"
      >
      <q-list separator class="fx-list">
        <q-item v-for="source in filtered" :key="source.id">
          <q-item-section avatar>
            <q-avatar square size="32px" color="primary" text-color="white" :icon="iconFor(source.type)" />
          </q-item-section>
          <q-item-section>
            <q-item-label>{{ source.name }}</q-item-label>
            <q-item-label caption>
              <span class="chip-id">{{ source.id }}</span>
              <span v-if="source.description"> · {{ source.description }}</span>
            </q-item-label>
          </q-item-section>
          <q-item-section side>
            <div class="row items-center q-gutter-xs">
              <span class="fx-tag fx-tag--code">{{ source.type }}</span>
              <q-btn flat dense round icon="visibility" @click="preview(source.id)">
                <q-tooltip>Preview the rows</q-tooltip>
              </q-btn>
              <q-btn
                flat
                dense
                round
                icon="table_chart"
                color="primary"
                :to="{ name: 'datasets', query: { from: source.id } }"
              >
                <q-tooltip>Ingest into a dataset</q-tooltip>
              </q-btn>
              <q-btn flat dense round icon="delete" color="negative" @click="remove(source)">
                <q-tooltip>Delete</q-tooltip>
              </q-btn>
            </div>
          </q-item-section>
        </q-item>
      </q-list>
      <EmptyState
        v-if="!filtered.length && !loading"
        :message="isFiltering ? 'No source matches that' : 'No sources registered'"
        :hint="
          isFiltering
            ? 'Try a shorter term, or clear the search'
            : 'Register a CSV, Excel, JSON, Parquet, database or REST endpoint'
        "
        icon="cable"
      />
      </AsyncSection>
    </SectionCard>

    <!-- create -->
    <q-dialog v-model="dialog">
      <q-card style="min-width: 480px">
        <q-card-section class="fx-dialog__title">Register a source</q-card-section>
        <q-card-section class="q-gutter-md">
          <q-input v-model="form.name" label="Name" dense outlined autofocus />
          <q-select v-model="form.type" :options="sourceTypes" label="Type" dense outlined emit-value map-options />
          <q-input
            v-model="connectionText"
            label="Connection (JSON)"
            type="textarea"
            rows="5"
            dense
            outlined
            :hint="connectionHint"
            class="mono"
          />
          <q-input v-model="form.description" label="Description" dense outlined />
        </q-card-section>
        <q-card-actions align="right">
          <q-btn no-caps flat label="Cancel" v-close-popup />
          <q-btn no-caps unelevated color="primary" label="Create" :loading="saving" @click="create" />
        </q-card-actions>
      </q-card>
    </q-dialog>

    <!-- preview -->
    <q-dialog v-model="previewDialog" full-width>
      <q-card>
        <q-card-section class="row items-center justify-between">
          <div class="fx-dialog__title q-pa-none">Source preview</div>
          <q-btn flat dense round icon="close" v-close-popup />
        </q-card-section>
        <q-card-section>
          <DataTable v-if="previewData" :rows="previewData.rows" :fields="previewData.columns" />
        </q-card-section>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup lang="ts">
import { useQuasar } from 'quasar'
import { computed, onMounted, ref } from 'vue'

import { data as dataApi } from '@/api'
import DataTable from '@/components/DataTable.vue'
import EmptyState from '@/components/EmptyState.vue'
import SearchField from '@/components/SearchField.vue'
import { useListFilter } from '@/composables/useListFilter'
import AsyncSection from '@/components/AsyncSection.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import type { Preview, Source } from '@/types'

const $q = useQuasar()
const sources = ref<Source[]>([])
const { term, filtered, summary, isFiltering } = useListFilter(sources, (s) => [
  s.name,
  s.type,
  s.description,
])
const sourceTypes = ref<string[]>([])
const loading = ref(false)
const loadError = ref<string | null>(null)
const saving = ref(false)
const dialog = ref(false)
const previewDialog = ref(false)
const previewData = ref<Preview | null>(null)

const form = ref({ name: '', type: 'csv', description: '' })
const connectionText = ref('{\n  "path": "samples/sales.csv"\n}')

const HINTS: Record<string, string> = {
  csv: '{"path": "samples/sales.csv", "delimiter": ","}',
  excel: '{"path": "samples/book.xlsx", "sheet": 0}',
  json: '{"path": "file.json", "records_path": "data.items"}',
  ndjson: '{"path": "events.ndjson"}',
  parquet: '{"path": "table.parquet"}',
  database: '{"url": "postgresql+psycopg://...", "table": "sales"}',
  rest_api: '{"url": "https://api.example.com/items", "records_path": "data"}',
  inline: '{"rows": [{"a": 1}]}',
}

const connectionHint = computed(() => HINTS[form.value.type] ?? 'JSON object describing the connection')

const ICONS: Record<string, string> = {
  csv: 'description',
  excel: 'grid_on',
  json: 'data_object',
  ndjson: 'data_object',
  parquet: 'storage',
  database: 'database',
  rest_api: 'api',
  object_storage: 'cloud',
  inline: 'edit_note',
}

function iconFor(type: string) {
  return ICONS[type] ?? 'cable'
}

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const [list, types] = await Promise.all([dataApi.listSources(), dataApi.sourceTypes()])
    sources.value = list
    sourceTypes.value = types.types
  } catch (error) {
    //  Keep the reason on the page; a toast is gone in four seconds.
    loadError.value = (error as Error).message
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    loading.value = false
  }
}

async function create() {
  saving.value = true
  try {
    const connection = JSON.parse(connectionText.value || '{}')
    await dataApi.createSource({ ...form.value, connection })
    $q.notify({ type: 'positive', message: `Source "${form.value.name}" registered` })
    dialog.value = false
    form.value = { name: '', type: 'csv', description: '' }
    await load()
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    saving.value = false
  }
}

async function preview(id: string) {
  try {
    previewData.value = await dataApi.previewSource(id)
    previewDialog.value = true
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

function remove(source: Source) {
  $q.dialog({
    title: 'Delete source',
    message: `Delete "${source.name}"? Datasets already ingested from it are kept.`,
    cancel: true,
  }).onOk(async () => {
    try {
      await dataApi.deleteSource(source.id)
      await load()
    } catch (error) {
      $q.notify({ type: 'negative', message: (error as Error).message })
    }
  })
}

onMounted(load)
</script>
