<template>
  <q-page class="page-shell">
    <PageHeader
      title="Reports"
      subtitle="Composed narratives over results. Sections reference live data, so a report is never stale."
    >
      <template #actions>
        <q-btn no-caps color="primary" unelevated icon="add" label="New report" @click="openCreate" />
      </template>
    </PageHeader>

    <div class="row q-col-gutter-md">
      <div class="col-12 col-md-3">
        <SectionCard title="Reports"
                     :subtitle="`${reports.length} documents`" flush>
          <q-list separator class="fx-list">
            <q-item
              v-for="report in reports"
              :key="report.id"
              clickable
              :active="report.id === activeId"
              @click="select(report.id)"
            >
              <q-item-section>
                <q-item-label>{{ report.name }}</q-item-label>
                <q-item-label caption>{{ report.sections.length }} sections</q-item-label>
              </q-item-section>
            </q-item>
          </q-list>
          <EmptyState
            v-if="!reports.length && !loading"
            message="No reports"
            hint="Pull a model, an execution and its rows into one document"
            icon="description"
          />
        </SectionCard>
      </div>

      <div class="col-12 col-md-9">
        <template v-if="rendered">
          <SectionCard
            :title="rendered.name"
            :subtitle="rendered.description"
            class="q-mb-md"
          >
            <template #actions>
                <q-btn-dropdown no-caps flat dense icon="download" label="Export">
                  <q-list dense>
                    <q-item
                      v-for="format in formats"
                      :key="format"
                      clickable
                      v-close-popup
                      @click="download(format)"
                    >
                      <q-item-section>{{ format }}</q-item-section>
                    </q-item>
                  </q-list>
                </q-btn-dropdown>
                <q-btn flat dense icon="refresh" @click="select(activeId)">
                  <q-tooltip>Re-render</q-tooltip>
                </q-btn>
                <q-btn flat dense round icon="delete" color="negative" @click="remove" />
            </template>
            <div class="fx-meta">
              Generated {{ new Date(rendered.generated_at).toLocaleString() }} ·
              {{ rendered.sections.length }} sections, each re-read from live data
            </div>
          </SectionCard>

          <SectionCard
            v-for="(section, index) in rendered.sections"
            :key="index"
            :title="section.title || section.kind"
            :subtitle="sectionSubtitle(section)"
            :icon="sectionIcon(section.kind)"
            class="q-mb-md"
          >
              <q-banner v-if="section.error" dense class="bg-red-1 text-negative">
                {{ section.error }}
              </q-banner>

              <div v-else-if="section.kind === 'text'" class="report-prose">
                <p v-for="(para, i) in paragraphs(section.body)" :key="i">{{ para }}</p>
              </div>

              <DataTable
                v-else-if="section.kind === 'table'"
                :rows="section.rows ?? []"
                :fields="section.columns ?? []"
                :rows-per-page="10"
              />

              <ChartView
                v-else-if="section.kind === 'chart' && charts[section.visualization_id]"
                :chart="charts[section.visualization_id]"
                :height="240"
              />

              <q-markup-table v-else flat dense class="scroll-x">
                <tbody>
                  <tr v-for="(value, key) in scalarFields(section)" :key="key">
                    <td class="text-caption" style="width: 34%">{{ key }}</td>
                    <td class="mono">{{ formatValue(value) }}</td>
                  </tr>
                </tbody>
              </q-markup-table>
          </SectionCard>
        </template>
        <EmptyState v-else message="Select a report" icon="description" />
      </div>
    </div>

    <q-dialog v-model="dialog">
      <q-card style="min-width: 640px">
        <q-card-section class="fx-dialog__title">New report</q-card-section>
        <q-card-section class="q-gutter-md">
          <q-input v-model="form.name" label="Name" dense outlined autofocus />
          <q-input v-model="form.description" label="Description" dense outlined />

          <div class="text-caption text-uppercase text-weight-medium" style="opacity: 0.7">
            Sections
          </div>
          <q-card v-for="(section, index) in form.sections" :key="index" flat bordered>
            <q-card-section class="row q-col-gutter-sm items-start">
              <div class="col-12 col-md-3">
                <q-select
                  v-model="section.kind"
                  :options="kinds"
                  label="Kind"
                  dense
                  outlined
                  @update:model-value="section.reference = null"
                />
              </div>
              <div class="col-12 col-md-3">
                <q-input v-model="section.title" label="Title" dense outlined />
              </div>
              <div class="col-11 col-md-5">
                <q-input
                  v-if="section.kind === 'text'"
                  v-model="section.body"
                  label="Body"
                  type="textarea"
                  rows="2"
                  dense
                  outlined
                />
                <q-select
                  v-else
                  v-model="section.reference"
                  :options="referenceOptions(section.kind)"
                  :label="referenceLabel(section.kind)"
                  dense
                  outlined
                  emit-value
                  map-options
                />
              </div>
              <div class="col-1 text-right">
                <q-btn flat dense round icon="close" @click="form.sections.splice(index, 1)" />
              </div>
            </q-card-section>
          </q-card>
          <q-btn no-caps flat dense icon="add" label="Add section" @click="addSection" />
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

import { analysis, executions as executionsApi, models as modelsApi, reports as reportsApi, results as resultsApi } from '@/api'
import ChartView from '@/components/ChartView.vue'
import DataTable from '@/components/DataTable.vue'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import { useUrlSelection } from '@/composables/useUrlSelection'
import type {
  ChartData,
  Execution,
  ModelDefinition,
  RenderedReport,
  Report,
  ResultRecord,
  Visualization,
} from '@/types'

const $q = useQuasar()

const reports = ref<Report[]>([])
const rendered = ref<RenderedReport | null>(null)
const charts = ref<Record<string, ChartData>>({})
//  Selection lives in the URL so a view can be linked, reloaded and shared.
const { selected: activeId, settle } = useUrlSelection()
const kinds = ref<string[]>([])
const formats = ref<string[]>(['markdown', 'html', 'json'])
const loading = ref(false)
const saving = ref(false)
const dialog = ref(false)

const executionList = ref<Execution[]>([])
const resultList = ref<ResultRecord[]>([])
const modelList = ref<ModelDefinition[]>([])
const visualizationList = ref<Visualization[]>([])

interface SectionForm {
  kind: string
  title: string
  body: string
  reference: string | null
}

const form = ref<{ name: string; description: string; sections: SectionForm[] }>({
  name: '',
  description: '',
  sections: [],
})

const ICONS: Record<string, string> = {
  text: 'notes',
  metrics: 'speed',
  table: 'table_rows',
  chart: 'insights',
  execution: 'play_circle',
  result: 'output',
  model: 'category',
}

function sectionIcon(kind: string) {
  return ICONS[kind] ?? 'article'
}

/** Say what a section is and how much of it there is, so the title can be prose. */
function sectionSubtitle(section: Record<string, any>) {
  if (section.error) return 'could not be rendered'
  if (section.kind === 'table') {
    const rows = section.rows?.length ?? 0
    const columns = section.columns?.length ?? 0
    return `table · ${rows} rows × ${columns} columns`
  }
  if (section.kind === 'chart') return 'chart · rendered from a saved visualization'
  if (section.kind === 'text') return 'note'
  if (section.kind === 'metrics') return 'metrics · read from the execution'
  return String(section.kind)
}

function paragraphs(body: string | undefined) {
  return (body ?? '').split('\n\n').filter(Boolean)
}

function scalarFields(section: Record<string, any>) {
  const skip = new Set(['kind', 'title', 'options', 'rows', 'columns', 'payload', 'versions'])
  return Object.fromEntries(Object.entries(section).filter(([key]) => !skip.has(key)))
}

function formatValue(value: unknown) {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function referenceLabel(kind: string) {
  return (
    { metrics: 'Execution', execution: 'Execution', result: 'Result', table: 'Result', chart: 'Visualization', model: 'Model' }[
      kind
    ] ?? 'Reference'
  )
}

function referenceOptions(kind: string) {
  if (kind === 'model') return modelList.value.map((m) => ({ label: m.name, value: m.id }))
  if (kind === 'chart') return visualizationList.value.map((v) => ({ label: v.name, value: v.id }))
  if (kind === 'result' || kind === 'table')
    return resultList.value.map((r) => ({
      label: `${r.kind} · ${r.id.slice(0, 14)} · ${new Date(r.created_at).toLocaleDateString()}`,
      value: r.id,
    }))
  return executionList.value.map((e) => ({
    label: `${e.kind} · ${e.id.slice(0, 14)} · ${e.status}`,
    value: e.id,
  }))
}

/** Map the single "reference" field back onto the section's typed id. */
function toPayload(section: SectionForm) {
  const base = { kind: section.kind, title: section.title, body: section.body }
  if (section.kind === 'text') return base
  const key =
    { metrics: 'execution_id', execution: 'execution_id', result: 'result_id', table: 'result_id', chart: 'visualization_id', model: 'model_id' }[
      section.kind
    ] ?? 'result_id'
  return { ...base, [key]: section.reference }
}

function addSection() {
  form.value.sections.push({ kind: 'text', title: '', body: '', reference: null })
}

function openCreate() {
  form.value = { name: '', description: '', sections: [] }
  addSection()
  dialog.value = true
}

async function load() {
  loading.value = true
  try {
    const [reportList, sectionInfo, execs, res, allModels, vizzes] = await Promise.all([
      reportsApi.list(),
      reportsApi.sectionKinds(),
      executionsApi.list('?limit=50&status=succeeded'),
      resultsApi.list('?limit=50'),
      modelsApi.list(),
      analysis.listVisualizations(),
    ])
    reports.value = reportList
    kinds.value = sectionInfo.kinds
    formats.value = sectionInfo.formats
    executionList.value = execs
    resultList.value = res
    modelList.value = allModels
    visualizationList.value = vizzes
    if (reportList.length) await select(settle(reportList.map((r) => r.id)))
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    loading.value = false
  }
}

async function select(id: string) {
  activeId.value = id
  try {
    rendered.value = await reportsApi.render(id)
    //  Chart sections reference a visualization; render each one once.
    for (const section of rendered.value.sections) {
      const vizId = section.visualization_id as string | undefined
      if (vizId && !charts.value[vizId]) {
        try {
          charts.value[vizId] = await analysis.renderVisualization(vizId)
        } catch {
          /* a chart that will not render is reported by its own section */
        }
      }
    }
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

async function create() {
  saving.value = true
  try {
    const created = await reportsApi.create({
      name: form.value.name,
      description: form.value.description,
      sections: form.value.sections.map(toPayload),
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

async function download(format: string) {
  if (!activeId.value) return
  try {
    const { text } = await reportsApi.exportAs(activeId.value, format)
    const suffix = { markdown: 'md', html: 'html', json: 'json' }[format] ?? 'txt'
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${rendered.value?.name ?? 'report'}.${suffix}`
    anchor.click()
    URL.revokeObjectURL(url)
    $q.notify({ type: 'positive', message: `Exported as ${format}` })
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

function remove() {
  const target = reports.value.find((r) => r.id === activeId.value)
  if (!target) return
  $q.dialog({ title: 'Delete report', message: `Delete "${target.name}"?`, cancel: true }).onOk(
    async () => {
      await reportsApi.remove(target.id)
      activeId.value = ''
      rendered.value = null
      await load()
    },
  )
}

onMounted(load)
</script>

<style scoped>
.report-prose p {
  margin: 0 0 0.7rem;
  line-height: 1.55;
}
</style>
