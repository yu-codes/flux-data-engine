<template>
  <q-page class="page-shell">
    <PageHeader
      title="Explore"
      subtitle="Profile, filter and chart any dataset version — however it arrived"
    />

    <!-- source selector -->
    <SectionCard class="q-mb-md" tight plain>
      <div class="row q-col-gutter-md items-end">
        <div class="col-12 col-md-5">
          <q-select
            v-model="datasetId"
            :options="datasetOptions"
            label="Dataset"
            dense
            outlined
            emit-value
            map-options
          />
        </div>
        <div class="col-12 col-md-4">
          <q-select
            v-model="versionId"
            :options="versionOptions"
            label="Version"
            dense
            outlined
            emit-value
            map-options
            :disable="!versionOptions.length"
          />
        </div>
        <div class="col-12 col-md-3">
          <q-btn
            color="primary"
            unelevated
            no-caps
            class="full-width"
            label="Load"
            :loading="loading"
            @click="load"
          />
        </div>
      </div>
    </SectionCard>

    <!-- headline numbers, once loaded -->
    <div v-if="profile" class="row q-col-gutter-md q-mb-md">
      <div v-for="stat in headline" :key="stat.label" class="col-6 col-md-3">
        <SectionCard tight plain>
          <div class="fx-figure">{{ stat.value }}</div>
          <div class="fx-figure__label">{{ stat.label }}</div>
        </SectionCard>
      </div>
    </div>

    <q-tabs v-model="tab" dense no-caps align="left" class="explore__tabs q-mb-md text-primary">
      <q-tab name="profile" label="Columns" />
      <q-tab name="rows" label="Rows" />
      <q-tab name="chart" label="Chart" />
    </q-tabs>

    <!-- ------------------------- columns ------------------------- -->
    <template v-if="tab === 'profile'">
      <SectionCard
        v-if="profile"
        title="Column profile"
        subtitle="What is actually in each column: how complete it is, how varied, and its range"
        flush
      >
        <q-markup-table flat class="scroll-x profile-table">
          <thead>
            <tr>
              <th class="text-left">Column</th>
              <th class="text-left">Type</th>
              <th class="text-left" style="width: 150px">Completeness</th>
              <th class="text-right">Distinct</th>
              <th class="text-right">Min</th>
              <th class="text-right">Median</th>
              <th class="text-right">Mean</th>
              <th class="text-right">Max</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="column in profile.columns" :key="column.name">
              <td class="text-weight-medium">{{ column.name }}</td>
              <td><span class="fx-tag fx-tag--code">{{ column.type }}</span></td>
              <td>
                <div class="completeness">
                  <div class="completeness__track">
                    <div
                      class="completeness__fill"
                      :class="{ 'completeness__fill--poor': column.null_ratio > 0.3 }"
                      :style="{ width: `${(1 - column.null_ratio) * 100}%` }"
                    />
                  </div>
                  <span class="completeness__text num">
                    {{ Math.round((1 - column.null_ratio) * 100) }}%
                  </span>
                </div>
              </td>
              <td class="text-right num">{{ column.distinct_count.toLocaleString() }}</td>
              <td class="text-right num">{{ number(column.min) }}</td>
              <td class="text-right num">{{ number(column.median) }}</td>
              <td class="text-right num">{{ number(column.mean) }}</td>
              <td class="text-right num">{{ number(column.max) }}</td>
            </tr>
          </tbody>
        </q-markup-table>
      </SectionCard>
      <SectionCard v-else>
        <EmptyState message="Pick a dataset to profile" icon="analytics" />
      </SectionCard>
    </template>

    <!-- -------------------------- rows --------------------------- -->
    <template v-else-if="tab === 'rows'">
      <SectionCard
        title="Rows"
        :subtitle="rowSubtitle"
        flush
      >
        <template #actions>
          <q-btn flat dense no-caps icon="add" label="Condition" @click="addFilter" />
          <q-btn
            flat
            dense
            no-caps
            icon="filter_alt_off"
            label="Clear"
            @click="clearFilter"
          />
          <!--
            Explore is where somebody works out what they want. Without this
            the only way to keep it was to open the pipeline builder and set
            the same conditions again from memory.
          -->
          <q-btn
            flat
            dense
            no-caps
            icon="account_tree"
            label="Save as pipeline"
            :disable="!canSaveAsPipeline"
            @click="openSaveAsPipeline"
          >
            <q-tooltip v-if="!canSaveAsPipeline">
              Add a filter or a sort first — there is nothing to keep yet
            </q-tooltip>
          </q-btn>
        </template>

        <div class="q-pa-md">
          <div
            v-for="(condition, index) in filters"
            :key="condition.uid"
            class="row q-col-gutter-sm items-start q-mb-sm"
          >
            <div class="col-auto filter__joiner fx-meta">
              {{ index === 0 ? 'where' : 'and' }}
            </div>
            <div class="col-12 col-md-3">
              <q-select
                v-model="condition.column"
                :options="columnNames"
                label="Column"
                dense
                outlined
                clearable
              />
            </div>
            <div class="col-6 col-md-3">
              <q-select
                v-model="condition.op"
                :options="OPERATORS"
                label="Condition"
                dense
                outlined
                emit-value
                map-options
              />
            </div>
            <div class="col-6 col-md-4">
              <q-input
                v-model="condition.value"
                label="Value"
                dense
                outlined
                :disable="VALUELESS.includes(condition.op)"
                :hint="valueHint(condition.op)"
                @keyup.enter="runQuery"
              />
            </div>
            <div class="col-auto">
              <q-btn flat dense round icon="close" @click="removeFilter(index)">
                <q-tooltip>Remove condition</q-tooltip>
              </q-btn>
            </div>
          </div>

          <div class="row q-col-gutter-sm items-end">
            <div class="col-12 col-md-4">
              <q-select
                v-model="sortBy"
                :options="columnNames"
                label="Sort by"
                dense
                outlined
                clearable
              />
            </div>
            <div class="col-6 col-md-2">
              <q-toggle v-model="sortDesc" label="Descending" dense :disable="!sortBy" />
            </div>
            <div class="col-6 col-md-2">
              <q-btn
                color="primary"
                unelevated
                no-caps
                class="full-width fx-btn"
                label="Apply"
                @click="runQuery"
              />
            </div>
          </div>
        </div>

        <DataTable v-if="rows.length" :rows="rows" :rows-per-page="25" />
        <EmptyState v-else message="No rows match" icon="table_rows" />
      </SectionCard>
    </template>

    <!-- -------------------------- chart -------------------------- -->
    <template v-else>
      <div class="row q-col-gutter-md">
        <div class="col-12 col-md-4">
          <SectionCard title="Chart" subtitle="Everything here appears on the rendered chart">
            <div class="fx-form">
              <q-select
                v-model="spec.chart_type"
                :options="chartTypeOptions"
                label="Type"
                dense
                outlined
                emit-value
                map-options
                :hint="chartHint"
              />
              <q-select
                v-if="!isDistributionOnly"
                v-model="spec.x"
                :options="columnNames"
                :label="xLabel"
                dense
                outlined
                clearable
              />
              <q-select
                v-model="spec.y"
                :options="columnNames"
                :label="yLabel"
                dense
                outlined
                :multiple="!singleMeasure"
                :use-chips="!singleMeasure"
                @update:model-value="normaliseY"
              />
              <q-select
                v-if="supportsSeries"
                v-model="spec.series"
                :options="columnNames"
                label="Split by (bands)"
                dense
                outlined
                clearable
                hint="one band per distinct value — required for a heatmap"
              />
              <q-select
                v-if="supportsAggregation"
                v-model="spec.aggregation"
                :options="aggregations"
                label="Aggregate"
                dense
                outlined
                hint="how repeated X values are combined"
              />
              <q-input
                v-if="spec.chart_type === 'histogram'"
                v-model.number="spec.bins"
                type="number"
                label="Buckets"
                dense
                outlined
                :min="2"
                :max="60"
                hint="how many equal-width buckets to cut the column into"
              />
              <q-input
                v-if="!isDistributionOnly"
                v-model="xOrderText"
                label="Category order"
                dense
                outlined
                hint="comma-separated; anything unlisted keeps its natural order"
              />
              <q-input
                v-if="supportsSeries && spec.series"
                v-model="seriesOrderText"
                label="Band order"
                dense
                outlined
                hint="comma-separated, for an ordinal scale like mild / moderate / severe"
              />

              <div class="fx-form__label">Labelling</div>
              <q-input v-model="spec.x_title" label="X axis title" dense outlined
                       :placeholder="spec.x ?? ''" />
              <q-input v-model="spec.y_title" label="Y axis title" dense outlined
                       :placeholder="defaultYTitle" />
              <q-input v-model="spec.unit" label="Unit" dense outlined placeholder="mm, m/s, count…" />
              <q-input v-model="spec.subtitle" label="Caption" dense outlined />
              <q-toggle
                v-if="supportsValueLabels"
                v-model="spec.value_labels"
                label="Show value labels"
                dense
              />

              <q-btn
                color="primary"
                unelevated
                no-caps
                label="Draw"
                class="fx-btn"
                @click="drawChart"
              />
            </div>
          </SectionCard>
        </div>

        <div class="col-12 col-md-8">
          <SectionCard title="Preview">
            <template #actions>
              <q-btn
                v-if="chart"
                flat
                dense
                no-caps
                icon="bookmark_add"
                label="Save"
                @click="saveDialog = true"
              />
            </template>
            <ChartView v-if="chart" :chart="chart" :height="340" />
            <EmptyState
              v-else
              message="Choose an X axis and at least one Y series"
              hint="Then press Draw"
              icon="insights"
            />
          </SectionCard>
        </div>
      </div>
    </template>

    <q-dialog v-model="saveDialog">
      <q-card style="min-width: 420px">
        <q-card-section class="fx-dialog__title">Save visualization</q-card-section>
        <q-card-section class="q-pt-none">
          <q-input v-model="saveName" label="Name" dense outlined autofocus />
        </q-card-section>
        <q-card-actions align="right">
          <q-btn flat no-caps label="Cancel" v-close-popup />
          <q-btn unelevated no-caps color="primary" label="Save" @click="saveVisualization" />
        </q-card-actions>
      </q-card>
    </q-dialog>

    <q-dialog v-model="savingPipeline">
      <q-card style="min-width: 460px">
        <q-card-section class="fx-dialog__title">Save as pipeline</q-card-section>
        <q-card-section class="fx-dialog__subtitle">
          The conditions and sort on this screen become steps you can re-run,
          schedule, or build on.
        </q-card-section>
        <q-card-section class="q-pt-none q-gutter-md">
          <q-input v-model="pipelineName" label="Name" dense outlined autofocus />
          <div class="fx-meta">{{ pipelinePreview }}</div>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn no-caps flat label="Cancel" v-close-popup />
          <q-btn
            no-caps
            unelevated
            color="primary"
            label="Save"
            :loading="savingNow"
            :disable="!pipelineName.trim()"
            @click="saveAsPipeline"
          />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup lang="ts">
import { useQuasar } from 'quasar'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { analysis, data as dataApi, pipelines as pipelinesApi } from '@/api'
import ChartView from '@/components/ChartView.vue'
import DataTable from '@/components/DataTable.vue'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import type { ChartData, ColumnProfile, Dataset, DatasetVersion } from '@/types'

const $q = useQuasar()
const route = useRoute()
const router = useRouter()

/**
 * The condition list is the one place a reader meets the query language, so it
 * spells the comparison out instead of showing the wire name. `value` is what
 * the API expects; `label` is what a person can act on without guessing.
 */
const OPERATORS = [
  { value: 'eq', label: 'is equal to (=)' },
  { value: 'ne', label: 'is not equal to (≠)' },
  { value: 'gt', label: 'is greater than (>)' },
  { value: 'gte', label: 'is greater than or equal to (≥)' },
  { value: 'lt', label: 'is less than (<)' },
  { value: 'lte', label: 'is less than or equal to (≤)' },
  { value: 'contains', label: 'contains the text' },
  { value: 'in', label: 'is one of (comma-separated)' },
  { value: 'is_null', label: 'is empty' },
  { value: 'not_null', label: 'is not empty' },
]

const VALUELESS = ['is_null', 'not_null']

const datasets = ref<Dataset[]>([])
const versions = ref<DatasetVersion[]>([])
const datasetId = ref('')
const versionId = ref('')
const tab = ref<'profile' | 'rows' | 'chart'>('profile')
const loading = ref(false)

const profile = ref<{ row_count: number; column_count: number; columns: ColumnProfile[] } | null>(null)
const rows = ref<Record<string, unknown>[]>([])
const total = ref(0)
const chart = ref<ChartData | null>(null)
const chartTypes = ref<string[]>(['bar', 'line', 'area', 'scatter', 'pie'])
const aggregations = ref<string[]>(['none', 'sum', 'mean', 'min', 'max', 'count'])

interface Condition {
  uid: number
  column: string | null
  op: string
  value: string
}

let nextConditionUid = 1

function blankCondition(): Condition {
  return { uid: nextConditionUid++, column: null, op: 'eq', value: '' }
}

const filters = ref<Condition[]>([blankCondition()])
const sortBy = ref<string | null>(null)
const sortDesc = ref(false)

function addFilter() {
  filters.value.push(blankCondition())
}

function removeFilter(index: number) {
  filters.value.splice(index, 1)
  if (!filters.value.length) filters.value.push(blankCondition())
}

const spec = ref({
  chart_type: 'bar',
  x: null as string | null,
  y: [] as string[],
  series: null as string | null,
  bins: 12,
  aggregation: 'sum',
  x_title: '',
  y_title: '',
  unit: '',
  subtitle: '',
  value_labels: true,
})

const saveDialog = ref(false)
const saveName = ref('')

const datasetOptions = computed(() =>
  datasets.value.map((dataset) => ({ label: dataset.name, value: dataset.id })),
)
const versionOptions = computed(() =>
  versions.value.map((version) => ({
    label: `v${version.version} · ${version.row_count.toLocaleString()} rows`,
    value: version.id,
  })),
)
const columnNames = computed(() => profile.value?.columns.map((column) => column.name) ?? [])

const headline = computed(() => {
  if (!profile.value) return []
  const columns = profile.value.columns
  const complete = columns.filter((c) => c.null_ratio === 0).length
  const numeric = columns.filter((c) => ['integer', 'float'].includes(c.type)).length
  return [
    { label: 'Rows', value: profile.value.row_count.toLocaleString() },
    { label: 'Columns', value: profile.value.column_count },
    { label: 'Numeric columns', value: numeric },
    { label: 'Columns with no gaps', value: `${complete} / ${columns.length}` },
  ]
})

const rowSubtitle = computed(() => {
  if (!total.value) return 'Filter and sort the version, then read it'
  const shown = `${rows.value.length.toLocaleString()} shown of ${total.value.toLocaleString()} matching`
  const count = activeFilters.value.length
  return count ? `${shown} · ${count} condition${count > 1 ? 's' : ''}` : shown
})

const defaultYTitle = computed(() => {
  if (!spec.value.y.length) return ''
  const columns = spec.value.y.join(', ')
  if (spec.value.chart_type === 'histogram') return 'rows'
  if (spec.value.chart_type === 'box' || spec.value.aggregation === 'none') return columns
  return `${spec.value.aggregation} of ${columns}`
})

/**
 * Chart types are not interchangeable: a histogram has no category axis, a box
 * plot does not aggregate, and a heatmap needs a second categorical column.
 * Saying so here keeps the form honest instead of offering inputs that are
 * silently discarded.
 */
const CHART_TYPES = [
  { value: 'bar', label: 'Bar', hint: 'a value per category' },
  { value: 'stacked_bar', label: 'Stacked bar', hint: 'composition within each category' },
  { value: 'line', label: 'Line', hint: 'a value across an ordered axis' },
  { value: 'area', label: 'Area', hint: 'a line with the space beneath it filled' },
  { value: 'scatter', label: 'Scatter', hint: 'one point per row, for a relationship' },
  { value: 'pie', label: 'Pie', hint: 'shares of one whole' },
  { value: 'histogram', label: 'Histogram', hint: 'the distribution of one numeric column' },
  { value: 'box', label: 'Box plot', hint: 'median and spread, per category' },
  { value: 'heatmap', label: 'Heatmap', hint: 'one measure across two categorical axes' },
]

const chartTypeOptions = computed(() =>
  CHART_TYPES.filter((type) => chartTypes.value.includes(type.value)).map((type) => ({
    label: type.label,
    value: type.value,
  })),
)

const chartHint = computed(
  () => CHART_TYPES.find((type) => type.value === spec.value.chart_type)?.hint ?? '',
)

/** A histogram and a box plot read one column; the rest can read several. */
const singleMeasure = computed(() =>
  ['histogram', 'box', 'heatmap', 'stacked_bar'].includes(spec.value.chart_type),
)
const isDistributionOnly = computed(() => spec.value.chart_type === 'histogram')
const supportsSeries = computed(() =>
  ['heatmap', 'stacked_bar', 'bar', 'line', 'area'].includes(spec.value.chart_type),
)
const supportsAggregation = computed(() => !['histogram', 'box'].includes(spec.value.chart_type))
const supportsValueLabels = computed(() =>
  ['bar', 'pie'].includes(spec.value.chart_type) && !spec.value.series,
)

const xLabel = computed(() =>
  spec.value.chart_type === 'box' || spec.value.chart_type === 'heatmap'
    ? 'X axis (groups)'
    : 'X axis (category)',
)

const yLabel = computed(() => {
  if (spec.value.chart_type === 'histogram') return 'Column to distribute'
  if (spec.value.chart_type === 'box') return 'Column to summarise'
  return singleMeasure.value ? 'Measure' : 'Y series (values)'
})

/**
 * A single-measure chart still posts `y` as a list, so switching type must not
 * leave three columns selected where the builder now shows one.
 */
function normaliseY(value: string | string[] | null) {
  const asList = value === null ? [] : Array.isArray(value) ? value : [value]
  spec.value.y = singleMeasure.value ? asList.slice(0, 1) : asList
}

const xOrderText = ref('')
const seriesOrderText = ref('')

function parseOrder(text: string) {
  return text
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean)
}

function number(value: number | undefined) {
  if (value === undefined || value === null) return '—'
  return Number.isInteger(value) ? value.toLocaleString() : Number(value.toFixed(3)).toLocaleString()
}

function clearFilter() {
  filters.value = [blankCondition()]
  sortBy.value = null
  runQuery()
}

async function loadDatasets() {
  datasets.value = await dataApi.listDatasets()
  const fromQuery = route.query.version as string | undefined
  if (fromQuery) {
    versionId.value = fromQuery
    const owner = datasets.value.find((d) => d.current_version_id === fromQuery)
    if (owner) datasetId.value = owner.id
    await load()
  } else if (datasets.value.length) {
    datasetId.value = datasets.value[0].id
  }
}

watch(datasetId, async (id) => {
  if (!id) return
  const detail = await dataApi.getDataset(id)
  versions.value = detail.versions
  versionId.value = detail.current_version_id ?? detail.versions[0]?.id ?? ''
})

async function load() {
  if (!versionId.value) return
  loading.value = true
  try {
    profile.value = await analysis.profile(versionId.value)
    //  Offer a sensible first chart rather than an empty builder.
    if (!spec.value.x) {
      const categorical = profile.value.columns.find(
        (c) => c.type === 'string' && c.distinct_count > 1 && c.distinct_count <= 30,
      )
      const numeric = profile.value.columns.find((c) => ['integer', 'float'].includes(c.type))
      spec.value.x = categorical?.name ?? profile.value.columns[0]?.name ?? null
      spec.value.y = numeric ? [numeric.name] : []
    }
    await runQuery()
    router.replace({ query: { version: versionId.value } })
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    loading.value = false
  }
}

//  Only what a pipeline step can express. `contains` has no `filter_rows`
//  equivalent, so offering to save it would promise a pipeline that filters
//  less than the screen does.
const UNSAVEABLE_OPS = ['contains']

const canSaveAsPipeline = computed(
  () =>
    Boolean(versionId.value) &&
    (sortBy.value !== null ||
      activeFilters.value.some((f) => !UNSAVEABLE_OPS.includes(String(f.op)))),
)

const savingPipeline = ref(false)
const savingNow = ref(false)
const pipelineName = ref('')

/** What the pipeline will contain, said before it is made. */
const pipelinePreview = computed(() => {
  const parts: string[] = []
  const usable = activeFilters.value.filter((f) => !UNSAVEABLE_OPS.includes(String(f.op)))
  if (usable.length) parts.push(`${usable.length} filter step(s)`)
  if (sortBy.value) parts.push(`sorted by ${sortBy.value}`)
  const dropped = activeFilters.value.length - usable.length
  const summary = parts.join(', ') || 'nothing yet'
  return dropped
    ? `${summary} — ${dropped} "contains" condition(s) cannot be a step and will be left out`
    : summary
})

function openSaveAsPipeline() {
  const dataset = datasets.value.find((d) => d.id === datasetId.value)
  pipelineName.value = dataset ? `${dataset.name} — filtered` : 'Explore result'
  savingPipeline.value = true
}

async function saveAsPipeline() {
  savingNow.value = true
  try {
    const created = await pipelinesApi.fromQuery({
      name: pipelineName.value.trim(),
      dataset_id: datasetId.value,
      description: 'Saved from Explore.',
      filters: activeFilters.value.filter(
        (f) => !UNSAVEABLE_OPS.includes(String(f.op)),
      ),
      sort_by: sortBy.value,
      sort_desc: sortDesc.value,
    })
    savingPipeline.value = false
    $q.notify({ type: 'positive', message: `Saved as "${created.name}"` })
    router.push('/pipelines')
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    savingNow.value = false
  }
}

async function runQuery() {
  if (!versionId.value) return
  try {
    const response = await analysis.query(versionId.value, {
      filters: activeFilters.value,
      sort_by: sortBy.value,
      sort_desc: sortDesc.value,
      limit: 200,
    })
    rows.value = response.rows
    total.value = response.total
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

/** Say what the Value box expects, since it changes with the condition. */
function valueHint(op: string) {
  if (VALUELESS.includes(op)) return 'not needed for this condition'
  if (op === 'in') return 'e.g. 1, 2, 3'
  if (op === 'contains') return 'matched anywhere, ignoring case'
  return 'a typed number matches a numeric or a text column'
}

function coerce(condition: Condition): unknown {
  if (condition.value === '') return null
  //  "is one of" takes a list, so the comma-separated box becomes an array.
  if (condition.op === 'in') {
    return condition.value
      .split(',')
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => (Number.isNaN(Number(part)) ? part : Number(part)))
  }
  const asNumber = Number(condition.value)
  return Number.isNaN(asNumber) ? condition.value : asNumber
}

/** Only conditions that name a column mean anything; the rest are half-typed. */
const activeFilters = computed(() =>
  filters.value
    .filter((condition) => condition.column && condition.op)
    .map((condition) => ({
      column: condition.column as string,
      op: condition.op,
      value: coerce(condition),
    })),
)

async function drawChart() {
  if (!versionId.value || !spec.value.y.length) {
    $q.notify({ type: 'warning', message: 'Pick at least one Y series' })
    return
  }
  try {
    chart.value = await analysis.series(versionId.value, {
      ...spec.value,
      x_order: parseOrder(xOrderText.value),
      series_order: parseOrder(seriesOrderText.value),
    })
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

async function saveVisualization() {
  try {
    await analysis.createVisualization({
      name: saveName.value || `${spec.value.y.join(', ')} by ${spec.value.x}`,
      dataset_version_id: versionId.value,
      spec: {
        ...spec.value,
        x_order: parseOrder(xOrderText.value),
        series_order: parseOrder(seriesOrderText.value),
      },
    })
    $q.notify({ type: 'positive', message: 'Saved to Visualizations' })
    saveDialog.value = false
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

onMounted(async () => {
  try {
    const options = await analysis.chartOptions()
    chartTypes.value = options.chart_types
    aggregations.value = options.aggregations
    await loadDatasets()
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
})
</script>

<style scoped>
.explore__tabs {
  border-bottom: 1px solid var(--fx-border);
}

/* "where" / "and" sits against the first field, so the stack reads as a sentence. */
.filter__joiner {
  width: 46px;
  padding-top: 12px;
  text-align: right;
}

.profile-table th {
  white-space: nowrap;
}

.completeness {
  display: flex;
  align-items: center;
  gap: var(--fx-space-2);
}

.completeness__track {
  flex: 1;
  height: 5px;
  border-radius: 3px;
  background: var(--fx-surface-inset);
  overflow: hidden;
  min-width: 60px;
}

.completeness__fill {
  height: 100%;
  background: #3f7d58;
  border-radius: 3px;
}

.completeness__fill--poor {
  background: #c08b2e;
}

.completeness__text {
  font-size: var(--fx-text-xs);
  opacity: var(--fx-ink-muted);
  min-width: 34px;
  text-align: right;
}
</style>
