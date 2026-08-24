<template>
  <q-page class="page-shell">
    <PageHeader
      title="Typhoon analog forecast"
      subtitle="Find the historical typhoons whose tracks most closely match a query track, then vote on the CWA landfall-track category"
    >
      <template #actions>
        <span class="fx-tag">statistical model</span>
        <q-btn no-caps flat dense icon="help_outline" label="Method" @click="methodDialog = true" />
      </template>
    </PageHeader>

    <div class="row q-col-gutter-md">
      <!-- controls -->
      <div class="col-12 col-md-4">
        <SectionCard
          title="Query track"
          subtitle="Click the map to add points, or replay a historical typhoon"
          class="q-mb-md"
        >
          <div class="q-gutter-sm">
            <q-btn-toggle
              v-model="mode"
              spread
              no-caps
              unelevated
              toggle-color="primary"
              :options="[
                { label: 'Draw', value: 'draw' },
                { label: 'Historical', value: 'historical' },
              ]"
            />

            <template v-if="mode === 'draw'">
              <q-list dense bordered class="rounded-borders" style="max-height: 200px; overflow: auto">
                <q-item v-for="(point, index) in track" :key="index" dense>
                  <q-item-section>
                    <span class="mono">
                      {{ index + 1 }}. {{ point.latitude.toFixed(2) }}°N, {{ point.longitude.toFixed(2) }}°E
                    </span>
                  </q-item-section>
                  <q-item-section side>
                    <q-btn flat dense round icon="close" @click="track.splice(index, 1)" />
                  </q-item-section>
                </q-item>
                <q-item v-if="!track.length">
                  <q-item-section class="text-caption" style="opacity: 0.6">
                    No points yet — click the map, or load a sample track.
                  </q-item-section>
                </q-item>
              </q-list>
              <div class="row q-gutter-sm">
                <q-btn no-caps flat dense icon="auto_fix_high" label="Sample" @click="loadSample" />
                <q-btn no-caps flat dense icon="clear" label="Clear" @click="track = []" />
              </div>
            </template>

            <template v-else>
              <q-select
                v-model="selectedTyphoon"
                :options="typhoonOptions"
                label="Historical typhoon"
                dense
                outlined
                use-input
                emit-value
                map-options
                input-debounce="200"
                @filter="filterTyphoons"
              />
            </template>
          </div>
        </SectionCard>

        <SectionCard
          title="Parameters"
          subtitle="How the search is scored"
          class="q-mb-md"
        >
          <div class="q-gutter-md">
            <q-select
              v-model="params.method"
              :options="methodOptions"
              label="Similarity method"
              dense
              outlined
              emit-value
              map-options
            />
            <div>
              <div class="text-caption">Analogs (k): {{ params.k }}</div>
              <q-slider v-model="params.k" :min="1" :max="12" :step="1" label markers />
            </div>
            <div>
              <div class="text-caption">
                Coastline buffer: {{ params.buffer_km }} km
                <q-icon name="info" size="14px">
                  <q-tooltip>
                    Every method computes only inside this band around Taiwan's coastline.
                  </q-tooltip>
                </q-icon>
              </div>
              <q-slider v-model="params.buffer_km" :min="100" :max="1200" :step="50" label />
            </div>
            <q-toggle v-model="params.use_rainfall" label="Include event-rainfall ranking" dense />
            <q-select
              v-if="params.use_rainfall"
              v-model="params.rainfall_region"
              :options="regionOptions"
              label="Rainfall region"
              dense
              outlined
              emit-value
              map-options
            />
            <q-input
              v-if="params.use_rainfall"
              v-model.number="params.expected_rainfall"
              label="Expected rainfall (mm)"
              type="number"
              dense
              outlined
            />
            <q-btn no-caps
              color="primary"
              unelevated
              class="full-width"
              icon="cyclone"
              label="Find analogs"
              :loading="running"
              :disable="!canRun"
              @click="predict"
            />
          </div>
        </SectionCard>

        <SectionCard
          v-if="prediction"
          title="Predicted track category"
          :subtitle="`${prediction.analogs.length} analogs voted, weighted by track distance`"
        >
            <div class="row items-baseline q-gutter-sm">
              <span class="fx-figure fx-figure--hero">{{ prediction.predicted_category ?? '—' }}</span>
              <span class="text-subtitle2">{{ (prediction.confidence * 100).toFixed(1) }}% confidence</span>
            </div>
            <div class="text-caption q-mt-xs" style="opacity: 0.75">
              {{ categoryLabel(prediction.predicted_category) }}
            </div>
            <q-separator class="q-my-sm" />
            <div class="text-caption q-mb-xs" style="opacity: 0.7">Vote distribution</div>
            <div v-for="(weight, category) in prediction.category_votes" :key="category" class="q-mb-xs">
              <div class="row items-center justify-between text-caption">
                <span>Category {{ category }}</span>
                <span class="mono">{{ (weight * 100).toFixed(1) }}%</span>
              </div>
              <q-linear-progress :value="weight" size="6px" rounded color="primary" />
            </div>
        </SectionCard>
      </div>

      <!-- map + analogs -->
      <div class="col-12 col-md-8">
        <SectionCard title="Tracks" :subtitle="mapSubtitle" class="q-mb-md">
            <TyphoonMap
              :query-track="mapQueryTrack"
              :analogs="prediction?.analogs ?? []"
              :coastline="geometry.coastline"
              :buffer="geometry.buffer"
              :highlighted="highlighted"
              :editable="mode === 'draw'"
              @add-point="addPoint"
            />
        </SectionCard>

        <SectionCard
          v-if="prediction"
          title="Closest historical analogs"
          :subtitle="`Mean path offset in km inside the ${prediction.buffer_km} km buffer — an absolute measure, not a normalised score`"
          flush
        >
            <q-list separator dense class="fx-list">
              <q-item
                v-for="analog in prediction.analogs"
                :key="analog.typhoon_id"
                clickable
                @mouseenter="highlighted = analog.typhoon_id"
                @mouseleave="highlighted = null"
              >
                <q-item-section>
                  <q-item-label>
                    {{ analog.name_zh }} {{ analog.name_en }}
                    <span class="text-caption" style="opacity: 0.65">({{ analog.year }})</span>
                  </q-item-label>
                  <q-item-label caption>
                    Category {{ analog.category }} · {{ analog.category_label }}
                  </q-item-label>
                </q-item-section>
                <q-item-section side>
                  <div class="text-right">
                    <div class="mono">{{ analog.offset_km }} km</div>
                    <div class="text-caption" style="opacity: 0.65">
                      similarity {{ (analog.score * 100).toFixed(0) }}%
                    </div>
                  </div>
                </q-item-section>
              </q-item>
            </q-list>
        </SectionCard>

        <SectionCard
          v-if="rainfallRows.length"
          title="Event rainfall across the analogs"
          subtitle="Observed totals for the matched historical typhoons, per region"
          flush
        >
            <DataTable :rows="rainfallRows" row-key="region" :rows-per-page="10" />
        </SectionCard>
      </div>
    </div>

    <q-dialog v-model="methodDialog">
      <q-card style="min-width: 560px">
        <q-card-section class="fx-dialog__title">How the analog search works</q-card-section>
        <q-card-section>
          <div class="flow q-mb-md">
            <span class="flow__node">Track</span>
            <span class="flow__arrow">→</span>
            <span class="flow__node">Clip to buffer</span>
            <span class="flow__arrow">→</span>
            <span class="flow__node">Rank</span>
            <span class="flow__arrow">→</span>
            <span class="flow__node">RRF fusion</span>
            <span class="flow__arrow">→</span>
            <span class="flow__node">Weighted vote</span>
          </div>
          <q-list dense separator>
            <q-item v-for="method in methods" :key="method.key">
              <q-item-section>
                <q-item-label class="mono">{{ method.key }}</q-item-label>
                <q-item-label caption>{{ method.description }}</q-item-label>
              </q-item-section>
            </q-item>
          </q-list>
          <div class="text-caption q-mt-md" style="opacity: 0.72">
            Coastline measures the symmetric mean nearest-point (Chamfer) distance between two tracks in
            kilometres, so the top matches are the ones that visually hug the query path. Coastline-RRF
            fuses that ranking (weight 0.80) with a weighted-KNN feature ranking (0.20) and, optionally,
            an event-rainfall ranking, using reciprocal rank fusion.
          </div>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn no-caps flat label="Close" v-close-popup />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup lang="ts">
import { useQuasar } from 'quasar'
import { computed, onMounted, ref, watch } from 'vue'

import { typhoon as typhoonApi } from '@/api'
import DataTable from '@/components/DataTable.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import TyphoonMap from '@/components/TyphoonMap.vue'
import type { TrackCoord, TyphoonPrediction, TyphoonSummary } from '@/types'

const $q = useQuasar()

interface TrackPoint {
  latitude: number
  longitude: number
  wind_kt?: number | null
  pressure_mb?: number | null
}

/** A westward track passing just north of Taiwan — a category 1/2 shape. */
const SAMPLE_TRACK: TrackPoint[] = [
  { latitude: 22.0, longitude: 132.0, wind_kt: 45, pressure_mb: 990 },
  { latitude: 23.2, longitude: 128.5, wind_kt: 65, pressure_mb: 970 },
  { latitude: 24.4, longitude: 125.0, wind_kt: 80, pressure_mb: 955 },
  { latitude: 25.2, longitude: 122.0, wind_kt: 85, pressure_mb: 950 },
  { latitude: 25.6, longitude: 119.0, wind_kt: 70, pressure_mb: 965 },
]

const mode = ref<'draw' | 'historical'>('draw')
const track = ref<TrackPoint[]>([])
const selectedTyphoon = ref<string | null>(null)
const historicalTrack = ref<TrackCoord[]>([])
const typhoons = ref<TyphoonSummary[]>([])
const filteredTyphoons = ref<TyphoonSummary[]>([])
const prediction = ref<TyphoonPrediction | null>(null)
const highlighted = ref<string | null>(null)
const running = ref(false)
const methodDialog = ref(false)

const methods = ref<{ key: string; description: string }[]>([])
const regions = ref<{ code: string; label: string }[]>([])
const categories = ref<Record<string, string>>({})
const geometry = ref<{ coastline: TrackCoord[]; buffer: TrackCoord[] }>({ coastline: [], buffer: [] })

const params = ref({
  method: 'coastline_rrf',
  k: 5,
  buffer_km: 500,
  use_rainfall: false,
  rainfall_region: 'tn',
  expected_rainfall: null as number | null,
})

const methodOptions = computed(() => methods.value.map((m) => ({ label: m.key, value: m.key })))
const regionOptions = computed(() => regions.value.map((r) => ({ label: r.label, value: r.code })))
const typhoonOptions = computed(() =>
  filteredTyphoons.value.map((t) => ({
    label: `${t.year} ${t.name_zh || ''} ${t.name_en} · cat ${t.category}`,
    value: t.typhoon_id,
  })),
)

const mapQueryTrack = computed<TrackCoord[]>(() => {
  if (prediction.value) return prediction.value.query.track
  if (mode.value === 'historical') return historicalTrack.value
  return track.value.map((p) => ({ lat: p.latitude, lon: p.longitude }))
})

/** Name what is currently drawn on the map, so the card is never unlabelled. */
const mapSubtitle = computed(() => {
  const points = mapQueryTrack.value.length
  if (!points) return 'Click inside the buffer to start drawing a track'
  const analogs = prediction.value?.analogs.length ?? 0
  const base = `Query track of ${points} points`
  return analogs ? `${base} against ${analogs} historical analogs` : base
})

const canRun = computed(() =>
  mode.value === 'draw' ? track.value.length >= 2 : Boolean(selectedTyphoon.value),
)

const rainfallRows = computed(() => {
  const stations = prediction.value?.rainfall?.stations ?? {}
  return Object.values(stations).map((station) => ({
    region: station.label,
    mean: station.mean,
    median: station.median,
    min: station.min,
    max: station.max,
    analogs: station.count,
  }))
})

function categoryLabel(category: string | null) {
  return category ? categories.value[category] ?? '' : 'No category predicted'
}

function addPoint(point: { latitude: number; longitude: number }) {
  prediction.value = null
  track.value.push(point)
}

function loadSample() {
  prediction.value = null
  track.value = SAMPLE_TRACK.map((p) => ({ ...p }))
}

function filterTyphoons(needle: string, update: (fn: () => void) => void) {
  update(() => {
    const term = needle.toLowerCase()
    filteredTyphoons.value = term
      ? typhoons.value.filter(
          (t) =>
            t.name_en.toLowerCase().includes(term) ||
            (t.name_zh ?? '').includes(needle) ||
            t.typhoon_id.includes(term),
        )
      : typhoons.value.slice(0, 200)
  })
}

async function loadGeometry() {
  try {
    const outline = await typhoonApi.coastline(params.value.buffer_km)
    geometry.value = { coastline: outline.coastline, buffer: outline.buffer }
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

async function predict() {
  running.value = true
  try {
    const body: Record<string, unknown> = {
      method: params.value.method,
      k: params.value.k,
      buffer_km: params.value.buffer_km,
      use_rainfall: params.value.use_rainfall,
      rainfall_region: params.value.rainfall_region,
      expected_rainfall: params.value.expected_rainfall,
    }
    if (mode.value === 'historical') body.typhoon_id = selectedTyphoon.value
    else body.track = track.value

    prediction.value = await typhoonApi.predict(body)
    geometry.value = {
      coastline: prediction.value.geometry.coastline,
      buffer: prediction.value.geometry.buffer,
    }
    $q.notify({
      type: 'positive',
      message: `Category ${prediction.value.predicted_category} · ${prediction.value.analogs.length} analogs`,
      caption: `execution ${prediction.value.execution_id}`,
    })
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    running.value = false
  }
}

watch(
  () => params.value.buffer_km,
  () => {
    prediction.value = null
    loadGeometry()
  },
)

watch(selectedTyphoon, async (id) => {
  prediction.value = null
  if (!id) {
    historicalTrack.value = []
    return
  }
  try {
    const record = await typhoonApi.track(id, params.value.buffer_km)
    historicalTrack.value = record.track
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
})

onMounted(async () => {
  try {
    const [methodInfo, categoryInfo, catalogue] = await Promise.all([
      typhoonApi.methods(),
      typhoonApi.categories(),
      typhoonApi.list({ limit: 400 }),
    ])
    methods.value = methodInfo.methods
    regions.value = methodInfo.rainfall_regions
    params.value.method = methodInfo.default
    categories.value = Object.fromEntries(
      categoryInfo.categories.map((c) => [c.category, c.description]),
    )
    typhoons.value = catalogue.typhoons
    filteredTyphoons.value = catalogue.typhoons.slice(0, 200)
    await loadGeometry()
    loadSample()
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
})
</script>
