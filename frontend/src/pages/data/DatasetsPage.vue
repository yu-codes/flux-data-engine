<template>
  <q-page class="page-shell">
    <PageHeader
      title="Datasets"
      subtitle="Normalised, versioned tables. Every version is immutable and stored as Parquet."
    >
      <template #actions>
        <q-btn no-caps flat dense icon="upload_file" label="Upload" @click="uploadDialog = true" />
        <q-btn no-caps color="primary" unelevated icon="add" label="From source" @click="dialog = true" />
      </template>
    </PageHeader>

    <SectionCard title="Datasets" :subtitle="listSubtitle" flush>
      <template #actions>
        <SearchField v-model="term" placeholder="Search datasets" @update:model-value="load" />
        <q-btn-toggle
          v-model="include"
          :options="[
            { label: 'Curated', value: 'curated' },
            { label: 'All', value: 'all' },
          ]"
          no-caps
          dense
          unelevated
          toggle-color="primary"
          @update:model-value="load"
        >
          <q-tooltip>
            Every pipeline step materialises a dataset. Curated hides those working
            tables and keeps what was ingested, uploaded or produced as a deliverable.
          </q-tooltip>
        </q-btn-toggle>
      </template>
      <AsyncSection
        :pending="loading && !datasets.length"
        :error="loadError"
        title="Could not load datasets"
        :rows="4"
        :on-retry="load"
      >
      <q-list separator class="fx-list">
        <q-item
          v-for="dataset in datasets"
          :key="dataset.id"
          clickable
          :to="{ name: 'dataset-detail', params: { id: dataset.id } }"
        >
          <q-item-section avatar>
            <q-avatar square size="32px" :color="originColour(dataset.origin)" text-color="white" icon="table_chart" />
          </q-item-section>
          <q-item-section>
            <q-item-label>{{ dataset.name }}</q-item-label>
            <q-item-label caption>
              <span class="chip-id">{{ dataset.id }}</span>
              <span v-if="dataset.description"> · {{ dataset.description }}</span>
            </q-item-label>
          </q-item-section>
          <q-item-section side>
            <div class="row items-center q-gutter-xs">
              <span v-if="dataset.tags.length" class="fx-tag">{{ dataset.tags.join(', ') }}</span>
              <span class="fx-tag">{{ dataset.origin }}</span>
              <q-btn flat dense round icon="delete" color="negative" @click.prevent.stop="remove(dataset)" />
            </div>
          </q-item-section>
        </q-item>
      </q-list>
      <EmptyState
        v-if="!datasets.length && !loading"
        message="No datasets yet"
        hint="Ingest one from a source, or upload a file"
        icon="table_chart"
      />
      </AsyncSection>
    </SectionCard>

    <q-dialog v-model="dialog">
      <q-card style="min-width: 460px">
        <q-card-section class="fx-dialog__title">Ingest from a source</q-card-section>
        <q-card-section class="q-gutter-md">
          <q-select
            v-model="form.source_id"
            :options="sourceOptions"
            label="Source"
            dense
            outlined
            emit-value
            map-options
          />
          <q-input v-model="form.name" label="Dataset name" dense outlined />
          <q-input v-model="form.description" label="Description" dense outlined />
        </q-card-section>
        <q-card-actions align="right">
          <q-btn no-caps flat label="Cancel" v-close-popup />
          <q-btn no-caps unelevated color="primary" label="Ingest" :loading="saving" @click="create" />
        </q-card-actions>
      </q-card>
    </q-dialog>

    <q-dialog v-model="uploadDialog">
      <q-card style="min-width: 440px">
        <q-card-section class="fx-dialog__title">Upload a file</q-card-section>
        <q-card-section class="q-gutter-md">
          <q-input v-model="uploadForm.name" label="Dataset name" dense outlined />
          <q-file
            v-model="uploadForm.file"
            label="CSV, Excel, JSON, NDJSON or Parquet"
            dense
            outlined
            accept=".csv,.tsv,.xlsx,.xls,.json,.ndjson,.jsonl,.parquet"
          />
        </q-card-section>
        <q-card-actions align="right">
          <q-btn no-caps flat label="Cancel" v-close-popup />
          <q-btn no-caps unelevated color="primary" label="Upload" :loading="saving" @click="upload" />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup lang="ts">
import { useQuasar } from 'quasar'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { data as dataApi } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import SearchField from '@/components/SearchField.vue'
import AsyncSection from '@/components/AsyncSection.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import type { Dataset, Source } from '@/types'

const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const datasets = ref<Dataset[]>([])
const term = ref('')
//  Datasets can outgrow one response once pipelines run regularly, so the
//  search goes to the server rather than filtering what happens to be loaded.
const include = ref<'curated' | 'all'>('curated')

const listSubtitle = computed(() => {
  const count = `${datasets.value.length} dataset${datasets.value.length === 1 ? '' : 's'}`
  if (term.value.trim()) return `${count} matching`
  return include.value === 'all' ? `${count}, including pipeline working tables` : count
})
const sources = ref<Source[]>([])
const loading = ref(false)
const loadError = ref<string | null>(null)
const saving = ref(false)
const dialog = ref(false)
const uploadDialog = ref(false)

const form = ref({ source_id: '', name: '', description: '' })
const uploadForm = ref<{ name: string; file: File | null }>({ name: '', file: null })

const sourceOptions = computed(() =>
  sources.value.map((source) => ({ label: `${source.name} (${source.type})`, value: source.id })),
)

function originColour(origin: string) {
  return { source: 'primary', execution: 'accent', upload: 'secondary', builtin: 'positive' }[origin] ?? 'grey'
}

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const params = new URLSearchParams({ include: include.value })
    if (term.value.trim()) params.set('search', term.value.trim())
    const [list, sourceList] = await Promise.all([
      dataApi.listDatasets(`?${params}`),
      dataApi.listSources(),
    ])
    datasets.value = list
    sources.value = sourceList
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
    await dataApi.createDataset(form.value)
    $q.notify({ type: 'positive', message: 'Dataset ingested' })
    dialog.value = false
    form.value = { source_id: '', name: '', description: '' }
    await load()
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    saving.value = false
  }
}

async function upload() {
  if (!uploadForm.value.file || !uploadForm.value.name) {
    $q.notify({ type: 'warning', message: 'A name and a file are both required' })
    return
  }
  saving.value = true
  try {
    const body = new FormData()
    body.append('file', uploadForm.value.file)
    body.append('name', uploadForm.value.name)
    await dataApi.uploadDataset(body)
    $q.notify({ type: 'positive', message: 'File uploaded and registered' })
    uploadDialog.value = false
    uploadForm.value = { name: '', file: null }
    await load()
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    saving.value = false
  }
}

function remove(dataset: Dataset) {
  $q.dialog({
    title: 'Delete dataset',
    message: `Delete "${dataset.name}" and all of its versions?`,
    cancel: true,
  }).onOk(async () => {
    try {
      await dataApi.deleteDataset(dataset.id)
      await load()
    } catch (error) {
      $q.notify({ type: 'negative', message: (error as Error).message })
    }
  })
}

/**
 * Arriving from a source opens the ingest dialog already pointed at it.
 *
 * Registering a source and then having to remember its name on another page is
 * the seam between two screens showing through; the workflow is one step.
 */
async function openFromQuery() {
  const sourceId = route.query.from as string | undefined
  if (!sourceId) return
  form.value = { source_id: sourceId, name: '', description: '' }
  const source = sources.value.find((s) => s.id === sourceId)
  if (source) form.value.name = source.name
  dialog.value = true
  await router.replace({ query: {} })
}

onMounted(async () => {
  await load()
  await openFromQuery()
})
</script>
