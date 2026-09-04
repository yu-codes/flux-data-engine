<template>
  <figure class="cc">
    <figcaption class="cc__caption">
      <span class="cc__title">{{ title }}</span>
      <span v-if="subtitle" class="fx-meta">{{ subtitle }}</span>
    </figcaption>

    <div v-if="!plotted.length" class="cc__empty fx-meta">此期間沒有可繪製的讀值</div>

    <div v-else class="fx-scroll-x">
      <svg
        class="cc__svg"
        :viewBox="`0 0 ${WIDTH} ${HEIGHT}`"
        preserveAspectRatio="none"
        role="img"
        :aria-label="`${title}：${plotted.length} 天的日平均與門檻`"
        @mousemove="track"
        @mouseleave="hover = null"
      >
        <!-- the three bands, drawn as regions rather than as lines: a limit
             that moves with load is a band the reading sits inside -->
        <path v-if="bands.emergency" class="cc__band cc__band--emergency" :d="bands.emergency" />
        <path v-if="bands.critical" class="cc__band cc__band--critical" :d="bands.critical" />
        <path v-if="bands.warning" class="cc__band cc__band--warning" :d="bands.warning" />

        <g class="cc__grid">
          <line
            v-for="tick in ticks"
            :key="tick.value"
            :x1="PAD_L"
            :x2="WIDTH - PAD_R"
            :y1="tick.y"
            :y2="tick.y"
          />
        </g>

        <!-- what the physics predicted, against what arrived -->
        <path v-if="expectedPath" class="cc__expected" :d="expectedPath" />
        <path v-if="spreadPath" class="cc__spread" :d="spreadPath" />
        <path class="cc__line" :d="valuePath" />

        <g v-for="marker in markers" :key="`${marker.at}-${marker.kind}`" class="cc__event">
          <line :x1="marker.x" :x2="marker.x" :y1="PAD_T" :y2="HEIGHT - PAD_B"
                :class="`cc__event--${marker.tone}`" />
        </g>

        <g v-if="hover" class="cc__cursor">
          <line :x1="hover.x" :x2="hover.x" :y1="PAD_T" :y2="HEIGHT - PAD_B" />
          <circle :cx="hover.x" :cy="hover.y" r="3.5" />
        </g>

        <g class="cc__axis">
          <text v-for="tick in ticks" :key="`t-${tick.value}`" :x="PAD_L - 6" :y="tick.y + 3">
            {{ tick.label }}
          </text>
          <text :x="PAD_L" :y="HEIGHT - 6" class="cc__axis--x">{{ plotted[0].day }}</text>
          <text :x="WIDTH - PAD_R" :y="HEIGHT - 6" class="cc__axis--x cc__axis--end">
            {{ plotted[plotted.length - 1].day }}
          </text>
        </g>
      </svg>
    </div>

    <div class="cc__legend fx-meta">
      <span class="cc__key cc__key--line">日平均</span>
      <span class="cc__key cc__key--expected">應有值</span>
      <span class="cc__key cc__key--warning">警戒</span>
      <span class="cc__key cc__key--critical">嚴重</span>
      <span class="cc__key cc__key--emergency">緊急</span>
      <span v-if="markers.length" class="cc__key cc__key--event">維護／故障事件</span>
    </div>

    <div v-if="hover" class="cc__readout">
      <span class="cc__readout-day">{{ hover.point.day }}</span>
      <span>實測 <b>{{ fmt(hover.point.value) }}</b> {{ unit }}</span>
      <span>應有 {{ fmt(hover.point.expected) }}</span>
      <span v-if="hover.point.progress !== null">門檻進度 {{ fmt(hover.point.progress) }}%</span>
      <span v-if="hover.point.load_pct !== null">負載 {{ fmt(hover.point.load_pct) }}%</span>
      <span v-if="hover.point.ambient_c !== null">環境 {{ fmt(hover.point.ambient_c) }}°C</span>
    </div>
  </figure>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import type { MaintenanceSeriesPoint } from '@/types'

/**
 * One measurement against the limit that applies to it *today*.
 *
 * The reason this is not a `ChartView` is the limit. In this application the
 * warning, critical and emergency lines are offsets from what the response
 * model predicts for the load and the plant temperature of that day, so they
 * move — and a chart that draws them as three horizontal rules would be
 * drawing a different analysis from the one that produced the decision.
 *
 * Everything else follows from that. The bands are filled regions between the
 * moving limits, the expected value is drawn as its own line so the reader can
 * see the gap the whole assessment is about, and the daily minimum-to-maximum
 * spread is drawn behind both because a widening spread is itself a symptom.
 *
 * Colours come from the platform's tokens, so it reads correctly in both
 * themes without a palette of its own.
 */
const props = withDefaults(
  defineProps<{
    points: MaintenanceSeriesPoint[]
    events?: { at: string; kind: string; label: string }[]
    title: string
    subtitle?: string
    unit?: string
    /** Low-failing measurements are drawn with the bands below the line. */
    direction?: string
  }>(),
  { events: () => [], unit: '', direction: 'high', subtitle: '' },
)

const WIDTH = 960
const HEIGHT = 260
const PAD_L = 52
const PAD_R = 12
const PAD_T = 12
const PAD_B = 22

const hover = ref<{ x: number; y: number; point: MaintenanceSeriesPoint } | null>(null)

const plotted = computed(() => props.points.filter((point) => point.value !== null))

const scale = computed(() => {
  const values: number[] = []
  for (const point of plotted.value) {
    for (const candidate of [
      point.value,
      point.expected,
      point.high,
      point.low,
      point.warning,
      point.critical,
    ]) {
      if (candidate !== null && Number.isFinite(candidate)) values.push(candidate)
    }
  }
  if (!values.length) return { min: 0, max: 1 }
  let min = Math.min(...values)
  let max = Math.max(...values)
  //  A flat series would otherwise divide by zero and draw a line through the
  //  middle of nothing.
  if (max - min < 1e-9) {
    min -= 1
    max += 1
  }
  const margin = (max - min) * 0.12
  return { min: min - margin, max: max + margin }
})

function x(index: number): number {
  const count = Math.max(1, plotted.value.length - 1)
  return PAD_L + ((WIDTH - PAD_L - PAD_R) * index) / count
}

function y(value: number): number {
  const { min, max } = scale.value
  const ratio = (value - min) / (max - min)
  return HEIGHT - PAD_B - ratio * (HEIGHT - PAD_T - PAD_B)
}

function line(pick: (point: MaintenanceSeriesPoint) => number | null): string {
  const parts: string[] = []
  let open = false
  plotted.value.forEach((point, index) => {
    const value = pick(point)
    if (value === null || !Number.isFinite(value)) {
      open = false
      return
    }
    parts.push(`${open ? 'L' : 'M'}${x(index).toFixed(1)} ${y(value).toFixed(1)}`)
    open = true
  })
  return parts.join(' ')
}

const valuePath = computed(() => line((point) => point.value))
const expectedPath = computed(() => line((point) => point.expected))

/** The daily min-to-max envelope, closed back along itself. */
const spreadPath = computed(() => {
  const usable = plotted.value.filter((point) => point.high !== null && point.low !== null)
  if (usable.length < 2) return ''
  const top: string[] = []
  const bottom: string[] = []
  plotted.value.forEach((point, index) => {
    if (point.high === null || point.low === null) return
    top.push(`${top.length ? 'L' : 'M'}${x(index).toFixed(1)} ${y(point.high).toFixed(1)}`)
    bottom.unshift(`L${x(index).toFixed(1)} ${y(point.low).toFixed(1)}`)
  })
  return `${top.join(' ')} ${bottom.join(' ')} Z`
})

/**
 * A band between two moving limits, closed into a region. `outer` may be
 * absent — the emergency band runs to the edge of the plot, because beyond
 * emergency there is no further band to bound it.
 */
function band(
  inner: (point: MaintenanceSeriesPoint) => number | null,
  outer: ((point: MaintenanceSeriesPoint) => number | null) | null,
): string {
  const usable = plotted.value.filter((point) => inner(point) !== null)
  if (usable.length < 2) return ''
  const edge = props.direction === 'low' ? scale.value.min : scale.value.max
  const forward: string[] = []
  const back: string[] = []
  plotted.value.forEach((point, index) => {
    const low = inner(point)
    if (low === null) return
    const high = outer ? outer(point) : edge
    if (high === null) return
    forward.push(`${forward.length ? 'L' : 'M'}${x(index).toFixed(1)} ${y(low).toFixed(1)}`)
    back.unshift(`L${x(index).toFixed(1)} ${y(high).toFixed(1)}`)
  })
  return `${forward.join(' ')} ${back.join(' ')} Z`
}

const bands = computed(() => ({
  warning: band(
    (point) => point.warning,
    (point) => point.critical,
  ),
  critical: band(
    (point) => point.critical,
    (point) => point.emergency,
  ),
  emergency: band((point) => point.emergency, null),
}))

const ticks = computed(() => {
  const { min, max } = scale.value
  return [0, 0.25, 0.5, 0.75, 1].map((share) => {
    const value = min + (max - min) * share
    return { value, y: y(value), label: format(value) }
  })
})

const markers = computed(() => {
  const index = new Map(plotted.value.map((point, at) => [point.day, at]))
  return props.events
    .map((event) => {
      const at = index.get(event.at)
      if (at === undefined) return null
      return {
        ...event,
        x: x(at),
        tone: event.kind === 'failure' ? 'bad' : event.kind === 'corrective' ? 'warn' : 'ok',
      }
    })
    .filter((marker): marker is NonNullable<typeof marker> => marker !== null)
})

function track(event: MouseEvent) {
  const target = event.currentTarget as SVGSVGElement
  const box = target.getBoundingClientRect()
  if (!box.width || !plotted.value.length) return
  const ratio = (event.clientX - box.left) / box.width
  const at = Math.round(ratio * WIDTH)
  const count = Math.max(1, plotted.value.length - 1)
  const index = Math.min(
    plotted.value.length - 1,
    Math.max(0, Math.round(((at - PAD_L) / (WIDTH - PAD_L - PAD_R)) * count)),
  )
  const point = plotted.value[index]
  if (!point || point.value === null) return
  hover.value = { x: x(index), y: y(point.value), point }
}

function format(value: number): string {
  const size = Math.abs(value)
  if (size >= 1000) return value.toFixed(0)
  if (size >= 10) return value.toFixed(1)
  return value.toFixed(2)
}

function fmt(value: number | null): string {
  return value === null || value === undefined ? '—' : format(value)
}
</script>

<style scoped>
.cc {
  margin: 0;
  min-width: 0;
}

.cc__caption {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--fx-space-2);
  margin-bottom: var(--fx-space-2);
  min-width: 0;
}

.cc__title {
  font-size: var(--fx-text-base);
  font-weight: 600;
  overflow-wrap: anywhere;
}

.cc__empty {
  padding: var(--fx-space-5) 0;
}

.cc__svg {
  width: 100%;
  min-width: 520px;
  height: 260px;
  display: block;
}

.cc__grid line {
  stroke: currentColor;
  stroke-width: 1;
  opacity: 0.12;
  vector-effect: non-scaling-stroke;
}

.cc__band {
  stroke: none;
}

.cc__band--warning {
  fill: var(--fx-wait);
  opacity: 0.14;
}

.cc__band--critical {
  fill: var(--fx-bad);
  opacity: 0.14;
}

.cc__band--emergency {
  fill: var(--fx-bad);
  opacity: 0.24;
}

.cc__spread {
  fill: currentColor;
  opacity: 0.1;
  stroke: none;
}

.cc__line {
  fill: none;
  stroke: var(--fx-run);
  stroke-width: 1.75;
  vector-effect: non-scaling-stroke;
}

.cc__expected {
  fill: none;
  stroke: currentColor;
  opacity: 0.45;
  stroke-width: 1.25;
  stroke-dasharray: 5 4;
  vector-effect: non-scaling-stroke;
}

.cc__event line {
  stroke-width: 1.25;
  stroke-dasharray: 3 3;
  vector-effect: non-scaling-stroke;
}

.cc__event--bad {
  stroke: var(--fx-bad);
}

.cc__event--warn {
  stroke: var(--fx-wait);
}

.cc__event--ok {
  stroke: var(--fx-ok);
  opacity: 0.7;
}

.cc__cursor line {
  stroke: currentColor;
  opacity: 0.35;
  vector-effect: non-scaling-stroke;
}

.cc__cursor circle {
  fill: var(--fx-run);
}

.cc__axis text {
  font-size: 10px;
  fill: currentColor;
  opacity: var(--fx-ink-muted);
  text-anchor: end;
}

.cc__axis--x {
  text-anchor: start;
}

.cc__axis--end {
  text-anchor: end;
}

.cc__legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fx-space-3);
  margin-top: var(--fx-space-2);
}

.cc__key::before {
  content: '';
  display: inline-block;
  width: 12px;
  height: 3px;
  margin-right: var(--fx-space-1);
  vertical-align: middle;
  border-radius: 2px;
}

.cc__key--line::before {
  background: var(--fx-run);
}

.cc__key--expected::before {
  background: repeating-linear-gradient(90deg, currentColor 0 4px, transparent 4px 7px);
}

.cc__key--warning::before {
  background: var(--fx-wait);
  opacity: 0.5;
  height: 8px;
}

.cc__key--critical::before {
  background: var(--fx-bad);
  opacity: 0.45;
  height: 8px;
}

.cc__key--emergency::before {
  background: var(--fx-bad);
  opacity: 0.75;
  height: 8px;
}

.cc__key--event::before {
  background: var(--fx-bad);
  height: 10px;
  width: 2px;
}

.cc__readout {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fx-space-3);
  margin-top: var(--fx-space-2);
  font-size: var(--fx-text-xs);
  opacity: var(--fx-ink);
}

.cc__readout-day {
  font-family: var(--fx-mono);
}
</style>
