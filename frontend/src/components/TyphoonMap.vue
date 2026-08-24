<template>
  <div class="map-host">
    <svg
      :viewBox="`0 0 ${W} ${H}`"
      class="map-svg"
      role="img"
      aria-label="Typhoon tracks around Taiwan"
      @click="onClick"
      @mousemove="onMove"
    >
      <rect :width="W" :height="H" class="map-sea" />

      <!-- graticule -->
      <g class="map-grid">
        <line v-for="lon in gridLons" :key="`gl-${lon}`" :x1="px(lon)" :x2="px(lon)" y1="0" :y2="H" />
        <line v-for="lat in gridLats" :key="`ga-${lat}`" :y1="py(lat)" :y2="py(lat)" x1="0" :x2="W" />
        <text v-for="lon in gridLons" :key="`tl-${lon}`" :x="px(lon) + 3" :y="H - 5" class="map-tick">
          {{ lon }}°E
        </text>
        <text v-for="lat in gridLats" :key="`ta-${lat}`" x="4" :y="py(lat) - 3" class="map-tick">
          {{ lat }}°N
        </text>
      </g>

      <!-- computation window: the coastline buffer every method works inside -->
      <polygon v-if="bufferPath" :points="bufferPath" class="map-buffer" />
      <polygon v-if="coastlinePath" :points="coastlinePath" class="map-island" />

      <!-- analog tracks -->
      <g v-for="(analog, index) in analogs" :key="analog.typhoon_id">
        <polyline
          :points="trackPoints(analog.track)"
          class="map-analog"
          :stroke="analogColour(index)"
          :stroke-opacity="highlighted && highlighted !== analog.typhoon_id ? 0.18 : 0.85"
          :stroke-width="highlighted === analog.typhoon_id ? 3 : 1.8"
        />
        <circle
          v-for="(point, pi) in inRange(analog.track)"
          :key="`${analog.typhoon_id}-${pi}`"
          :cx="px(point.lon)"
          :cy="py(point.lat)"
          r="1.6"
          :fill="analogColour(index)"
          :fill-opacity="highlighted && highlighted !== analog.typhoon_id ? 0.2 : 0.8"
        />
      </g>

      <!-- query track -->
      <polyline v-if="queryTrack.length > 1" :points="trackPoints(queryTrack)" class="map-query" />
      <circle
        v-for="(point, index) in queryTrack"
        :key="`q-${index}`"
        :cx="px(point.lon)"
        :cy="py(point.lat)"
        :r="index === queryTrack.length - 1 ? 5 : 3.5"
        class="map-query-point"
      />
      <text
        v-for="(point, index) in queryTrack"
        :key="`qn-${index}`"
        :x="px(point.lon) + 7"
        :y="py(point.lat) - 6"
        class="map-query-label"
      >
        {{ index + 1 }}
      </text>

      <!-- cursor readout while drawing -->
      <text v-if="editable && cursor" :x="8" :y="18" class="map-cursor">
        {{ cursor.lat.toFixed(2) }}°N, {{ cursor.lon.toFixed(2) }}°E
      </text>
    </svg>

    <div class="map-legend row items-center q-gutter-md">
      <div class="row items-center q-gutter-xs">
        <span class="legend-line legend-line--query" />
        <span class="text-caption">Query track</span>
      </div>
      <div v-for="(analog, index) in analogs" :key="`lg-${analog.typhoon_id}`" class="row items-center q-gutter-xs">
        <span class="legend-line" :style="{ background: analogColour(index) }" />
        <span class="text-caption">
          {{ analog.name_en || analog.typhoon_id }} · {{ analog.offset_km }} km
        </span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import type { TrackCoord, TyphoonAnalog } from '@/types'

const props = withDefaults(
  defineProps<{
    queryTrack: TrackCoord[]
    analogs?: TyphoonAnalog[]
    coastline?: TrackCoord[]
    buffer?: TrackCoord[]
    highlighted?: string | null
    editable?: boolean
  }>(),
  { analogs: () => [], coastline: () => [], buffer: () => [], highlighted: null, editable: false },
)

const emit = defineEmits<{ (e: 'add-point', point: { latitude: number; longitude: number }): void }>()

/**
 * Equirectangular projection over a fixed western-Pacific window. It is not a
 * survey-grade projection, but at this scale it renders track geometry
 * faithfully and needs no tile server or mapping library.
 */
const W = 900
const H = 620
const LON_MIN = 108
const LON_MAX = 140
const LAT_MIN = 12
const LAT_MAX = 32

const gridLons = [110, 115, 120, 125, 130, 135, 140]
const gridLats = [15, 20, 25, 30]

const PALETTE = ['#c1662f', '#3f7d58', '#8a5fa8', '#c08b2e', '#4d7c8a', '#b3453b', '#7a6f5d']

const cursor = ref<{ lat: number; lon: number } | null>(null)

function px(lon: number) {
  return ((lon - LON_MIN) / (LON_MAX - LON_MIN)) * W
}

function py(lat: number) {
  return H - ((lat - LAT_MIN) / (LAT_MAX - LAT_MIN)) * H
}

function unproject(x: number, y: number) {
  return {
    lon: LON_MIN + (x / W) * (LON_MAX - LON_MIN),
    lat: LAT_MIN + ((H - y) / H) * (LAT_MAX - LAT_MIN),
  }
}

function trackPoints(track: TrackCoord[]) {
  return track.map((p) => `${px(p.lon).toFixed(1)},${py(p.lat).toFixed(1)}`).join(' ')
}

function inRange(track: TrackCoord[]) {
  return track.filter((p) => p.in_range !== false)
}

function analogColour(index: number) {
  return PALETTE[index % PALETTE.length]
}

const coastlinePath = computed(() => (props.coastline.length ? trackPoints(props.coastline) : ''))
const bufferPath = computed(() => (props.buffer.length ? trackPoints(props.buffer) : ''))

function toLocal(event: MouseEvent) {
  const svg = event.currentTarget as SVGSVGElement
  const rect = svg.getBoundingClientRect()
  return unproject(((event.clientX - rect.left) / rect.width) * W, ((event.clientY - rect.top) / rect.height) * H)
}

function onMove(event: MouseEvent) {
  if (!props.editable) return
  cursor.value = toLocal(event)
}

function onClick(event: MouseEvent) {
  if (!props.editable) return
  const point = toLocal(event)
  emit('add-point', { latitude: Number(point.lat.toFixed(3)), longitude: Number(point.lon.toFixed(3)) })
}
</script>

<style scoped>
.map-host {
  width: 100%;
}

.map-svg {
  width: 100%;
  height: auto;
  display: block;
  border-radius: 8px;
  border: 1px solid rgba(128, 145, 160, 0.25);
}

.map-sea {
  fill: rgba(47, 111, 143, 0.08);
}

.map-grid line {
  stroke: currentColor;
  stroke-opacity: 0.1;
  stroke-width: 1;
}

.map-tick {
  fill: currentColor;
  fill-opacity: 0.4;
  font-size: 9px;
}

.map-buffer {
  fill: rgba(47, 111, 143, 0.1);
  stroke: rgba(47, 111, 143, 0.55);
  stroke-dasharray: 6 4;
  stroke-width: 1.2;
}

.map-island {
  fill: rgba(63, 125, 88, 0.55);
  stroke: rgba(63, 125, 88, 0.9);
  stroke-width: 1;
}

.map-analog {
  fill: none;
  stroke-linejoin: round;
  stroke-linecap: round;
}

.map-query {
  fill: none;
  stroke: #d13b2f;
  stroke-width: 3;
  stroke-linejoin: round;
  stroke-linecap: round;
}

.map-query-point {
  fill: #d13b2f;
  stroke: #fff;
  stroke-width: 1.2;
}

.map-query-label {
  fill: currentColor;
  fill-opacity: 0.7;
  font-size: 10px;
}

.map-cursor {
  fill: currentColor;
  fill-opacity: 0.7;
  font-size: 11px;
}

.map-legend {
  margin-top: 8px;
  flex-wrap: wrap;
}

.legend-line {
  width: 16px;
  height: 3px;
  border-radius: 2px;
  display: inline-block;
}

.legend-line--query {
  background: #d13b2f;
}
</style>
