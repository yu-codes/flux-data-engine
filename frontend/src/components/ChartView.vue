<template>
  <figure ref="host" class="chart" @mouseleave="hover = null">
    <figcaption v-if="chart.name || chart.subtitle" class="chart__caption">
      <span v-if="chart.name" class="chart__name">{{ chart.name }}</span>
      <span v-if="chart.subtitle" class="chart__subtitle">{{ chart.subtitle }}</span>
    </figcaption>

    <div v-if="chart.error" class="chart__state chart__state--error">
      <q-icon name="error_outline" size="20px" />
      <span>{{ chart.error }}</span>
    </div>
    <div v-else-if="!hasData" class="chart__state">
      <q-icon name="show_chart" size="20px" />
      <span>No values to plot for this selection.</span>
    </div>

    <svg
      v-else
      :viewBox="`0 0 ${W} ${H}`"
      class="chart__svg"
      role="img"
      :aria-label="ariaLabel"
      @mousemove="onMove"
    >
      <title>{{ ariaLabel }}</title>
      <desc>{{ ariaDescription }}</desc>

      <template v-if="isHeatmap">
        <!-- one cell per (category, band); colour carries the value -->
        <g>
          <template v-for="(row, ri) in chart.series" :key="`hm-${ri}`">
            <rect
              v-for="(value, ci) in row.data"
              :key="`hc-${ri}-${ci}`"
              :x="cellX(ci)"
              :y="cellY(ri)"
              :width="cellWidth"
              :height="cellHeight"
              :fill="heatColour(value)"
              :stroke="hover === ci ? 'var(--fx-ink-strong, #222)' : 'none'"
              :stroke-width="hover === ci ? 1 : 0"
            />
          </template>
        </g>
        <!-- values, when the cells are big enough to hold them -->
        <g v-if="showCellLabels" class="chart__cell-labels">
          <template v-for="(row, ri) in chart.series" :key="`hl-${ri}`">
            <text
              v-for="(value, ci) in row.data"
              v-show="value !== null && value !== 0"
              :key="`ht-${ri}-${ci}`"
              :x="cellX(ci) + cellWidth / 2"
              :y="cellY(ri) + cellHeight / 2 + 3.5"
              text-anchor="middle"
              :fill="heatInk(value)"
            >
              {{ formatTick(value ?? 0) }}
            </text>
          </template>
        </g>
        <!-- band names down the side -->
        <g class="chart__ticks">
          <text
            v-for="(row, ri) in chart.series"
            :key="`hb-${ri}`"
            :x="pad.left - 8"
            :y="cellY(ri) + cellHeight / 2 + 4"
            text-anchor="end"
          >
            {{ truncate(row.name) }}
          </text>
        </g>
        <text
          v-if="chart.band_title"
          class="chart__axis-title"
          :transform="`translate(12, ${(pad.top + H - pad.bottom) / 2}) rotate(-90)`"
          text-anchor="middle"
        >
          {{ chart.band_title }}
        </text>
        <text
          v-if="chart.x_title"
          class="chart__axis-title"
          :x="pad.left + plotWidth / 2"
          :y="H - 6"
          text-anchor="middle"
        >
          {{ chart.x_title }}
        </text>
      </template>

      <template v-else-if="!isPie">
        <!-- horizontal gridlines and the value axis -->
        <g class="chart__grid">
          <line
            v-for="tick in yTicks"
            :key="`g-${tick}`"
            :x1="pad.left"
            :x2="W - pad.right"
            :y1="yScale(tick)"
            :y2="yScale(tick)"
            :class="{ 'chart__grid--zero': tick === 0 }"
          />
        </g>
        <g class="chart__ticks">
          <text
            v-for="tick in yTicks"
            :key="`yt-${tick}`"
            :x="pad.left - 10"
            :y="yScale(tick) + 4"
            text-anchor="end"
          >
            {{ formatTick(tick) }}
          </text>
        </g>

        <!-- axis titles -->
        <text
          v-if="yTitle"
          class="chart__axis-title"
          :transform="`translate(14, ${(pad.top + H - pad.bottom) / 2}) rotate(-90)`"
          text-anchor="middle"
        >
          {{ yTitle }}
        </text>
        <text
          v-if="chart.x_title"
          class="chart__axis-title"
          :x="pad.left + plotWidth / 2"
          :y="H - 6"
          text-anchor="middle"
        >
          {{ chart.x_title }}
        </text>

        <!-- hovered column highlight -->
        <rect
          v-if="hover !== null"
          class="chart__hover-band"
          :x="bandX(hover)"
          :y="pad.top"
          :width="bandWidth"
          :height="H - pad.top - pad.bottom"
        />
      </template>

      <!-- ---------------- series ---------------- -->
      <template v-if="chart.chart_type === 'line' || chart.chart_type === 'area'">
        <g v-for="(s, si) in chart.series" :key="`l-${si}`">
          <path
            v-if="chart.chart_type === 'area'"
            :d="areaPath(s.data)"
            :fill="colour(si)"
            fill-opacity="0.14"
          />
          <path
            :d="linePath(s.data)"
            fill="none"
            :stroke="colour(si)"
            stroke-width="2"
            stroke-linejoin="round"
            stroke-linecap="round"
          />
          <circle
            v-for="(point, pi) in s.data"
            v-show="point !== null && (showAllPoints || hover === pi)"
            :key="`p-${si}-${pi}`"
            :cx="xPoint(pi)"
            :cy="yScale(point ?? 0)"
            :r="hover === pi ? 4 : 2.6"
            :fill="colour(si)"
            class="chart__point"
          />
        </g>
      </template>

      <template v-else-if="chart.chart_type === 'scatter'">
        <g v-for="(s, si) in chart.series" :key="`s-${si}`">
          <circle
            v-for="(point, pi) in s.data"
            v-show="point !== null"
            :key="`sc-${si}-${pi}`"
            :cx="xPoint(pi)"
            :cy="yScale(point ?? 0)"
            :r="hover === pi ? 5 : 3.4"
            :fill="colour(si)"
            fill-opacity="0.8"
          />
        </g>
      </template>

      <template v-else-if="isPie">
        <g :transform="`translate(${W / 2}, ${pieCentreY})`">
          <path
            v-for="(slice, i) in pieSlices"
            :key="`pie-${i}`"
            :d="slice.path"
            :fill="colour(i)"
            class="chart__slice"
          />
          <text
            v-for="(slice, i) in pieSlices"
            :key="`pl-${i}`"
            :x="slice.labelX"
            :y="slice.labelY"
            text-anchor="middle"
            class="chart__slice-label"
          >
            {{ slice.label }}
          </text>
        </g>
      </template>

      <template v-else-if="isBox">
        <g v-for="(box, bi) in boxes" :key="`box-${bi}`">
          <!-- whisker from the lower to the upper fence -->
          <line
            class="chart__whisker"
            :x1="box.centre"
            :x2="box.centre"
            :y1="box.low"
            :y2="box.high"
          />
          <line
            class="chart__whisker"
            :x1="box.centre - box.width / 4"
            :x2="box.centre + box.width / 4"
            :y1="box.low"
            :y2="box.low"
          />
          <line
            class="chart__whisker"
            :x1="box.centre - box.width / 4"
            :x2="box.centre + box.width / 4"
            :y1="box.high"
            :y2="box.high"
          />
          <rect
            :x="box.centre - box.width / 2"
            :y="box.top"
            :width="box.width"
            :height="box.height"
            :fill="colour(0)"
            :fill-opacity="hover === null || hover === bi ? 0.28 : 0.14"
            :stroke="colour(0)"
            rx="2"
          />
          <line
            class="chart__median"
            :x1="box.centre - box.width / 2"
            :x2="box.centre + box.width / 2"
            :y1="box.median"
            :y2="box.median"
            :stroke="colour(0)"
          />
        </g>
        <!-- points the whiskers exclude, drawn rather than hidden -->
        <g v-if="chart.outliers">
          <circle
            v-for="(point, oi) in outlierPoints"
            :key="`out-${oi}`"
            :cx="point.x"
            :cy="point.y"
            r="2.4"
            :fill="colour(0)"
            fill-opacity="0.5"
          />
        </g>
      </template>

      <template v-else-if="isStacked">
        <g v-for="(s, si) in chart.series" :key="`st-${si}`">
          <rect
            v-for="(point, pi) in s.data"
            v-show="point !== null && point !== 0"
            :key="`sb-${si}-${pi}`"
            :x="xPoint(pi) - stackWidth / 2"
            :y="stackTop(pi, si)"
            :width="stackWidth"
            :height="stackHeight(pi, si)"
            :fill="colour(si)"
            :fill-opacity="hover === null || hover === pi ? 1 : 0.45"
          />
        </g>
      </template>

      <template v-else>
        <g v-for="(s, si) in chart.series" :key="`b-${si}`">
          <rect
            v-for="(point, pi) in s.data"
            v-show="point !== null"
            :key="`bar-${si}-${pi}`"
            :x="barX(pi, si)"
            :y="Math.min(yScale(point ?? 0), yScale(0))"
            :width="barWidth"
            :height="Math.max(1, Math.abs(yScale(point ?? 0) - yScale(0)))"
            :fill="colour(si)"
            :fill-opacity="hover === null || hover === pi ? 1 : 0.45"
            rx="2"
          />
        </g>
        <!-- value labels, when there is room for them -->
        <g v-if="showValueLabels" class="chart__value-labels">
          <text
            v-for="(point, pi) in chart.series[0].data"
            v-show="point !== null"
            :key="`vl-${pi}`"
            :x="xPoint(pi)"
            :y="yScale(point ?? 0) - 5"
            text-anchor="middle"
          >
            {{ formatTick(point ?? 0) }}
          </text>
        </g>
      </template>

      <!-- category axis -->
      <template v-if="!isPie && !isHeatmap">
        <line
          class="chart__axis"
          :x1="pad.left"
          :x2="W - pad.right"
          :y1="yScale(0)"
          :y2="yScale(0)"
        />
        <g class="chart__ticks">
          <text
            v-for="label in xLabels"
            :key="`xlt-${label.index}`"
            :x="xPoint(label.index)"
            :y="H - pad.bottom + 15"
            :text-anchor="rotateLabels ? 'end' : 'middle'"
            :transform="
              rotateLabels
                ? `rotate(-38, ${xPoint(label.index)}, ${H - pad.bottom + 15})`
                : undefined
            "
          >
            {{ label.text }}
          </text>
        </g>
      </template>

      <g v-if="isHeatmap" class="chart__ticks">
        <text
          v-for="label in xLabels"
          :key="`hx-${label.index}`"
          :x="cellX(label.index) + cellWidth / 2"
          :y="H - pad.bottom + 15"
          text-anchor="middle"
        >
          {{ label.text }}
        </text>
      </g>
    </svg>

    <!-- colour scale, when colour is the measure -->
    <div v-if="isHeatmap && hasData" class="chart__scale">
      <span class="chart__scale-end">{{ formatValue(minValue) }}</span>
      <span class="chart__scale-ramp" />
      <span class="chart__scale-end">{{ formatValue(maxValue) }}</span>
    </div>

    <!-- legend: only when it disambiguates something -->
    <div v-if="hasData && !isHeatmap && chart.series.length > 1" class="chart__legend">
      <span v-for="(s, si) in chart.series" :key="`lg-${si}`" class="chart__legend-item">
        <span class="chart__swatch" :style="{ background: colour(si) }" />
        {{ s.name }}
      </span>
    </div>

    <!-- readout: what the pointer is over, or the summary when it is not -->
    <div v-if="hasData" class="chart__readout" :class="{ 'chart__readout--live': hover !== null }">
      <template v-if="hover !== null">
        <span class="chart__readout-key">{{ categoryLabel(hover) }}</span>
        <template v-if="isBox">
          <span v-for="(s, si) in chart.series" :key="`bx-${si}`" class="chart__readout-value">
            <span class="chart__readout-name">{{ s.name }}</span>
            {{ formatValue(s.data[hover]) }}
          </span>
          <span v-if="chart.group_sizes" class="chart__readout-value">
            <span class="chart__readout-name">n</span>
            {{ chart.group_sizes[hover] }}
          </span>
        </template>
        <template v-else>
          <span v-for="(s, si) in chart.series" :key="`rv-${si}`" class="chart__readout-value">
            <span class="chart__swatch" :style="{ background: colour(si) }" />
            <span v-if="chart.series.length > 1" class="chart__readout-name">{{ s.name }}</span>
            {{ formatValue(s.data[hover]) }}
          </span>
        </template>
      </template>
      <template v-else>
        <span>{{ summaryLine }}</span>
      </template>
    </div>
  </figure>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import type { ChartData } from '@/types'

const props = withDefaults(
  defineProps<{ chart: ChartData; height?: number; compact?: boolean }>(),
  { height: 260, compact: false },
)

/**
 * Charts are plain SVG rather than a charting library: the shapes needed here
 * are simple, and it keeps the bundle free of a large dependency.
 *
 * Everything a reader needs to interpret the numbers is drawn — value axis with
 * ticks, both axis titles, gridlines, a zero line, category labels, a legend
 * when there is more than one series, and a readout that follows the pointer.
 */
const W = 720
const H = computed(() => props.height)

const PALETTE = [
  '#2f6f8f', '#c1662f', '#3f7d58', '#8a5fa8',
  '#c08b2e', '#b3453b', '#4d7c8a', '#7a6f5d',
]

const hover = ref<number | null>(null)

const chart = computed(() => props.chart)
const isPie = computed(() => chart.value.chart_type === 'pie')
const isBox = computed(() => chart.value.chart_type === 'box')
const isHeatmap = computed(() => chart.value.chart_type === 'heatmap')
const isStacked = computed(() => chart.value.chart_type === 'stacked_bar')
const isHistogram = computed(() => chart.value.chart_type === 'histogram')

const values = computed(() =>
  chart.value.series.flatMap((s) =>
    s.data.filter((v): v is number => v !== null && Number.isFinite(v)),
  ),
)
const hasData = computed(() => chart.value.series.length > 0 && values.value.length > 0)

/** A stacked bar is as tall as its column total, not as its tallest segment. */
const stackTotals = computed(() =>
  chart.value.categories.map((_, index) =>
    chart.value.series.reduce((sum, s) => sum + (s.data[index] ?? 0), 0),
  ),
)

const maxValue = computed(() =>
  isStacked.value
    ? Math.max(0, ...stackTotals.value)
    : Math.max(0, ...values.value, ...outlierValues.value),
)
const minValue = computed(() =>
  isStacked.value ? 0 : Math.min(0, ...values.value, ...outlierValues.value),
)

const outlierValues = computed(() =>
  isBox.value ? (chart.value.outliers ?? []).map((o) => o.value) : [],
)
const pointCount = computed(() => Math.max(1, chart.value.categories.length))

const yTitle = computed(() =>
  chart.value.unit && chart.value.y_title
    ? `${chart.value.y_title} (${chart.value.unit})`
    : chart.value.y_title || chart.value.unit,
)

//  Long category labels get rotated, and the bottom margin grows to fit them.
/**
 * Label width in units of one Latin character.
 *
 * A CJK glyph is full-width — it occupies roughly two Latin characters — so
 * counting `String.length` under-reads a Chinese label by half. That is why
 * ten category labels that "fit" arithmetically still overlapped on screen.
 */
function textUnits(value: unknown) {
  const text = String(value ?? '')
  let units = 0
  for (const ch of text) {
    units += /[ᄀ-ᅟ⺀-꓏가-힣豈-﫿︰-﹏＀-｠￠-￦]/.test(ch)
      ? 2
      : 1
  }
  return units
}

const longestLabel = computed(() =>
  Math.max(0, ...chart.value.categories.map((c) => textUnits(c))),
)
const rotateLabels = computed(
  () => !isPie.value && longestLabel.value > 6 && pointCount.value > 6,
)

//  The left margin is sized from the widest tick label, so the value axis never
//  collides with its own numbers.
const widestBand = computed(() =>
  Math.max(0, ...chart.value.series.map((s) => Math.min(14, s.name.length))),
)

const pad = computed(() => ({
  top: 14,
  right: 18,
  bottom: (rotateLabels.value ? 46 : 30) + (chart.value.x_title ? 16 : 0),
  left: isHeatmap.value
    ? Math.max(56, 14 + widestBand.value * 7) + (chart.value.band_title ? 16 : 0)
    : Math.max(46, 16 + widestTick.value * 7) + (yTitle.value ? 16 : 0),
}))

const plotWidth = computed(() => W - pad.value.left - pad.value.right)

const widestTick = computed(() =>
  Math.max(...yTicks.value.map((tick) => formatTick(tick).length), 2),
)

function colour(index: number) {
  return PALETTE[index % PALETTE.length]
}

function yScale(value: number) {
  const span = maxValue.value - minValue.value || 1
  const usable = H.value - pad.value.top - pad.value.bottom
  return H.value - pad.value.bottom - ((value - minValue.value) / span) * usable
}

const isBar = computed(() => !['line', 'area', 'scatter', 'pie'].includes(chart.value.chart_type))

//  Histogram bars touch: the x axis is continuous, and a gap would imply that
//  values between the buckets do not exist.
const barGap = computed(() => (isHistogram.value ? 0.98 : 0.68))

function xPoint(index: number) {
  const usable = plotWidth.value
  if (pointCount.value === 1) return pad.value.left + usable / 2
  if (isBar.value) {
    const step = usable / pointCount.value
    return pad.value.left + step * index + step / 2
  }
  return pad.value.left + (usable / (pointCount.value - 1)) * index
}

const bandWidth = computed(() => plotWidth.value / pointCount.value)

function bandX(index: number) {
  return pad.value.left + bandWidth.value * index
}

const barWidth = computed(() => {
  const slot = plotWidth.value / pointCount.value
  return Math.max(2, (slot * barGap.value) / Math.max(1, chart.value.series.length))
})

const stackWidth = computed(() =>
  Math.max(2, (plotWidth.value / pointCount.value) * 0.68),
)

function stackBase(pointIndex: number, seriesIndex: number) {
  let base = 0
  for (let i = 0; i < seriesIndex; i += 1) {
    base += chart.value.series[i].data[pointIndex] ?? 0
  }
  return base
}

function stackTop(pointIndex: number, seriesIndex: number) {
  const base = stackBase(pointIndex, seriesIndex)
  return yScale(base + (chart.value.series[seriesIndex].data[pointIndex] ?? 0))
}

function stackHeight(pointIndex: number, seriesIndex: number) {
  const base = stackBase(pointIndex, seriesIndex)
  const value = chart.value.series[seriesIndex].data[pointIndex] ?? 0
  return Math.max(0, yScale(base) - yScale(base + value))
}

// ---------------------------------------------------------------- box plot
/** Pre-compute each box, so the template stays a description of the shape. */
const boxes = computed(() => {
  if (!isBox.value) return []
  const named = (name: string) => chart.value.series.find((s) => s.name === name)
  const low = named('min')
  const q1 = named('q1')
  const median = named('median')
  const q3 = named('q3')
  const high = named('max')
  if (!low || !q1 || !median || !q3 || !high) return []
  const width = Math.max(6, (plotWidth.value / pointCount.value) * 0.5)
  return chart.value.categories.map((_, index) => {
    const top = yScale(q3.data[index] ?? 0)
    return {
      centre: xPoint(index),
      width,
      low: yScale(low.data[index] ?? 0),
      high: yScale(high.data[index] ?? 0),
      top,
      height: Math.max(1, yScale(q1.data[index] ?? 0) - top),
      median: yScale(median.data[index] ?? 0),
    }
  })
})

const outlierPoints = computed(() => {
  if (!isBox.value) return []
  const index = new Map(chart.value.categories.map((c, i) => [String(c), i]))
  return (chart.value.outliers ?? [])
    .map((outlier) => {
      const position = index.get(String(outlier.category))
      return position === undefined
        ? null
        : { x: xPoint(position), y: yScale(outlier.value) }
    })
    .filter((point): point is { x: number; y: number } => point !== null)
})

// ----------------------------------------------------------------- heatmap
const cellWidth = computed(() => plotWidth.value / Math.max(1, pointCount.value))
const cellHeight = computed(
  () => (H.value - pad.value.top - pad.value.bottom) / Math.max(1, chart.value.series.length),
)

function cellX(index: number) {
  return pad.value.left + cellWidth.value * index
}

function cellY(index: number) {
  return pad.value.top + cellHeight.value * index
}

const showCellLabels = computed(
  () => cellWidth.value >= 34 && cellHeight.value >= 20 && !props.compact,
)

/** A single-hue ramp: darker means more, which needs no legend to guess at. */
function heatColour(value: number | null) {
  if (value === null || value === undefined) return 'var(--fx-heat-empty, #f2f2f0)'
  const span = maxValue.value - minValue.value || 1
  const t = Math.max(0, Math.min(1, (value - minValue.value) / span))
  return `color-mix(in srgb, ${PALETTE[0]} ${(8 + t * 88).toFixed(0)}%, transparent)`
}

function heatInk(value: number | null) {
  const span = maxValue.value - minValue.value || 1
  const t = ((value ?? 0) - minValue.value) / span
  return t > 0.55 ? '#fff' : 'currentColor'
}

function barX(pointIndex: number, seriesIndex: number) {
  const groupWidth = barWidth.value * chart.value.series.length
  return xPoint(pointIndex) - groupWidth / 2 + barWidth.value * seriesIndex
}

//  Only label bars when the labels will not overlap each other.
//  Value labels are the point of a small bar chart — they are what makes it
//  readable without a hover. They only come off when the bars are too close
//  together to keep the numbers apart, which is a question of count, not of
//  whether the chart happens to be in a tile.
const showValueLabels = computed(
  () =>
    isBar.value &&
    !isStacked.value &&
    !isBox.value &&
    !isHeatmap.value &&
    chart.value.value_labels &&
    chart.value.series.length === 1 &&
    pointCount.value <= (props.compact ? 12 : 14),
)

const showAllPoints = computed(() => pointCount.value <= 40)

function linePath(data: (number | null)[]) {
  let path = ''
  let pen = 'M'
  data.forEach((value, index) => {
    if (value === null) {
      pen = 'M'
      return
    }
    path += `${pen}${xPoint(index).toFixed(1)},${yScale(value).toFixed(1)} `
    pen = 'L'
  })
  return path.trim()
}

function areaPath(data: (number | null)[]) {
  const points = data
    .map((value, index) => ({ value, index }))
    .filter((p): p is { value: number; index: number } => p.value !== null)
  if (!points.length) return ''
  const top = points
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${xPoint(p.index)},${yScale(p.value)}`)
    .join(' ')
  const first = points[0]
  const last = points[points.length - 1]
  return `${top} L${xPoint(last.index)},${yScale(0)} L${xPoint(first.index)},${yScale(0)} Z`
}

/** "Nice" ticks: 1, 2 or 5 times a power of ten, so the axis reads cleanly. */
const yTicks = computed(() => {
  const span = maxValue.value - minValue.value
  if (span === 0) return [0, maxValue.value || 1]
  const rough = span / 4
  const magnitude = 10 ** Math.floor(Math.log10(rough))
  const step = [1, 2, 5, 10].map((m) => m * magnitude).find((s) => s >= rough) ?? magnitude * 10
  const start = Math.floor(minValue.value / step) * step
  const ticks: number[] = []
  for (let value = start; value <= maxValue.value + step * 0.5; value += step) {
    ticks.push(Number(value.toFixed(10)))
  }
  return ticks
})

function formatTick(value: number) {
  const absolute = Math.abs(value)
  if (absolute >= 1_000_000) return `${trim(value / 1_000_000)}M`
  if (absolute >= 1000) return `${trim(value / 1000)}k`
  if (Number.isInteger(value)) return String(value)
  return trim(value)
}

function trim(value: number) {
  return String(Number(value.toFixed(absDigits(value))))
}

function absDigits(value: number) {
  const absolute = Math.abs(value)
  if (absolute >= 100) return 0
  if (absolute >= 10) return 1
  return 2
}

function formatValue(value: number | null | undefined) {
  if (value === null || value === undefined) return '—'
  const formatted = Number.isInteger(value) ? value.toLocaleString() : trim(value)
  return chart.value.unit ? `${formatted} ${chart.value.unit}` : formatted
}

function categoryLabel(index: number) {
  const raw = chart.value.categories[index]
  return raw === null || raw === undefined || raw === '' ? '(blank)' : String(raw)
}

//  Thin the category labels so they never collide.
const xLabels = computed(() => {
  const total = chart.value.categories.length
  const cap = labelCap.value
  //  A rotated label still needs horizontal room: its diagonal footprint is
  //  roughly its length times cos(38°), which is what the 0.62 stands for.
  const drawn = Math.min(longestLabel.value, cap)
  const perLabel = rotateLabels.value
    ? Math.max(16, drawn * 5.6 * 0.62)
    : Math.max(28, drawn * 5.6 + 10)
  const room = Math.max(1, Math.floor(plotWidth.value / perLabel))
  const stride = Math.max(1, Math.ceil(total / room))
  return chart.value.categories
    .map((category, index) => ({ index, text: truncate(category, cap) }))
    .filter((_, index) => index % stride === 0)
})

//  A tile on a dashboard has less room than a chart on its own page, so it
//  shortens its labels rather than letting them run into each other.
const labelCap = computed(() => (props.compact ? 10 : 14))

function truncate(value: string | number | null, cap = 14) {
  const text = value === null || value === undefined ? '' : String(value)
  if (textUnits(text) <= cap) return text
  let out = ''
  let units = 0
  for (const ch of text) {
    const next = units + textUnits(ch)
    if (next > cap - 1) break
    out += ch
    units = next
  }
  return `${out}…`
}

const pieCentreY = computed(() => H.value / 2)

const pieSlices = computed(() => {
  const series = chart.value.series[0]
  if (!series) return []
  const total = series.data.reduce<number>((sum, v) => sum + (v ?? 0), 0) || 1
  const radius = Math.min(W, H.value) / 2 - 52
  let angle = -Math.PI / 2
  return series.data.map((value, index) => {
    const sweep = ((value ?? 0) / total) * Math.PI * 2
    const end = angle + sweep
    const large = sweep > Math.PI ? 1 : 0
    const path = [
      'M0,0',
      `L${(radius * Math.cos(angle)).toFixed(1)},${(radius * Math.sin(angle)).toFixed(1)}`,
      `A${radius},${radius} 0 ${large} 1 ` +
        `${(radius * Math.cos(end)).toFixed(1)},${(radius * Math.sin(end)).toFixed(1)}`,
      'Z',
    ].join(' ')
    const mid = angle + sweep / 2
    const share = Math.round(((value ?? 0) / total) * 100)
    const slice = {
      path,
      label: sweep > 0.25 ? `${truncate(chart.value.categories[index] ?? '')} ${share}%` : '',
      labelX: Math.cos(mid) * (radius + 26),
      labelY: Math.sin(mid) * (radius + 26),
    }
    angle = end
    return slice
  })
})

/** Without a pointer, say what the chart covers rather than showing nothing. */
const summaryLine = computed(() => {
  const distribution = chart.value.distribution
  if (distribution) {
    return (
      `${distribution.counted.toLocaleString()} values in ${distribution.bins} buckets · ` +
      `median ${formatValue(distribution.median)} · mean ${formatValue(distribution.mean)} · ` +
      `range ${formatValue(distribution.min)} – ${formatValue(distribution.max)}`
    )
  }
  const parts: string[] = [`${pointCount.value} categories`]
  if (isBox.value && chart.value.outliers) {
    parts.push(`${chart.value.outliers.length} outliers`)
  }
  if (chart.value.row_count) parts.push(`${chart.value.row_count.toLocaleString()} rows`)
  if (isBox.value) return parts.join(' · ')
  if (values.value.length) {
    const total = values.value.reduce((sum, v) => sum + v, 0)
    parts.push(
      chart.value.aggregation === 'count'
        ? `${formatValue(total)} total`
        : `range ${formatValue(Math.min(...values.value))} – ${formatValue(Math.max(...values.value))}`,
    )
  }
  return parts.join(' · ')
})

const ariaLabel = computed(
  () => chart.value.name ?? `${chart.value.y_title || 'values'} by ${chart.value.x_title || 'category'}`,
)

const ariaDescription = computed(
  () =>
    `${chart.value.chart_type} chart. ${summaryLine.value}. ` +
    chart.value.series
      .map((s) => `${s.name}: ${s.data.filter((v) => v !== null).length} values`)
      .join('; '),
)

function onMove(event: MouseEvent) {
  if (isPie.value || !hasData.value) return
  //  Heatmap columns are the same width as the bands they are drawn in.
  const svg = event.currentTarget as SVGSVGElement
  const rect = svg.getBoundingClientRect()
  const x = ((event.clientX - rect.left) / rect.width) * W
  if (x < pad.value.left || x > W - pad.value.right) {
    hover.value = null
    return
  }
  const index = Math.floor((x - pad.value.left) / bandWidth.value)
  hover.value = Math.max(0, Math.min(pointCount.value - 1, index))
}
</script>

<style scoped>
.chart {
  margin: 0;
  width: 100%;
}

.chart__whisker {
  stroke: currentColor;
  stroke-opacity: 0.45;
  stroke-width: 1;
}

.chart__median {
  stroke-width: 2;
}

.chart__cell-labels text {
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}

.chart__scale {
  display: flex;
  align-items: center;
  gap: var(--fx-space-2);
  margin-top: var(--fx-space-2);
  font-size: var(--fx-text-xs);
  opacity: var(--fx-ink-muted);
}

.chart__scale-ramp {
  flex: 1 1 auto;
  height: 8px;
  border-radius: 4px;
  background: linear-gradient(
    to right,
    color-mix(in srgb, #2f6f8f 8%, transparent),
    color-mix(in srgb, #2f6f8f 96%, transparent)
  );
}

.chart__scale-end {
  font-family: var(--fx-mono);
}

.chart__readout-name {
  opacity: 0.6;
  margin-right: 4px;
}

.chart__caption {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: var(--fx-space-2);
}

.chart__name {
  font-size: var(--fx-text-base);
  font-weight: 600;
}

.chart__subtitle {
  font-size: var(--fx-text-xs);
  opacity: var(--fx-ink-muted);
}

.chart__svg {
  width: 100%;
  height: auto;
  display: block;
}

.chart__state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--fx-space-2);
  min-height: 120px;
  font-size: var(--fx-text-sm);
  opacity: var(--fx-ink-muted);
}

.chart__state--error {
  color: #b3453b;
  opacity: 1;
}

.chart__grid line {
  stroke: currentColor;
  stroke-opacity: 0.1;
  stroke-width: 1;
}

.chart__grid line.chart__grid--zero {
  stroke-opacity: 0.26;
}

.chart__axis {
  stroke: currentColor;
  stroke-opacity: 0.32;
  stroke-width: 1;
}

.chart__ticks text {
  fill: currentColor;
  fill-opacity: 0.6;
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}

.chart__axis-title {
  fill: currentColor;
  fill-opacity: 0.62;
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.01em;
}

.chart__value-labels text {
  fill: currentColor;
  fill-opacity: 0.7;
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}

.chart__hover-band {
  fill: currentColor;
  fill-opacity: 0.05;
}

.chart__point {
  transition: r 90ms ease-out;
}

.chart__slice {
  stroke: var(--q-dark-page, #fff);
  stroke-width: 1;
}

.chart__slice-label {
  fill: currentColor;
  fill-opacity: 0.72;
  font-size: 10px;
}

.chart__legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fx-space-4);
  margin-top: var(--fx-space-3);
}

.chart__legend-item {
  display: inline-flex;
  align-items: center;
  gap: var(--fx-space-2);
  font-size: var(--fx-text-xs);
  opacity: var(--fx-ink);
}

.chart__swatch {
  width: 9px;
  height: 9px;
  border-radius: 2px;
  display: inline-block;
  flex-shrink: 0;
}

.chart__readout {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--fx-space-3);
  margin-top: var(--fx-space-2);
  padding-top: var(--fx-space-2);
  border-top: 1px solid var(--fx-border);
  font-size: var(--fx-text-xs);
  opacity: var(--fx-ink-muted);
  min-height: 26px;
}

.chart__readout--live {
  opacity: var(--fx-ink-strong);
}

.chart__readout-key {
  font-weight: 600;
}

.chart__readout-value {
  display: inline-flex;
  align-items: center;
  gap: var(--fx-space-2);
  font-variant-numeric: tabular-nums;
}
</style>
