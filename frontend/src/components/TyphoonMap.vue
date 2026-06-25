<template>
  <div class="map-wrap">
    <svg
      ref="svgEl"
      class="map-svg"
      :viewBox="`0 0 ${W} ${H}`"
      @wheel.prevent="onWheel"
      @mousedown="onDown"
      @mousemove="onMove"
      @mouseup="onUp"
      @mouseleave="onUp"
    >
      <rect :width="W" :height="H" class="sea" />
      <g :transform="`translate(${pan.x} ${pan.y}) scale(${zoom})`">
        <!-- 海岸線外擴緩衝區 -->
        <polygon v-if="bufferPts" :points="bufferPts" class="buffer" :stroke-width="1.5 / zoom" />
        <!-- 台灣本島 -->
        <polygon v-if="coastPts" :points="coastPts" class="land" :stroke-width="1 / zoom" />

        <!-- 相似颱風路徑：範圍外淡、範圍內（實際參與計算）醒目 -->
        <g v-for="(s, i) in similars" :key="s.typhoon_id">
          <polyline
            :points="trackPts(s.track)"
            class="sim-track"
            :stroke="color(i)"
            :stroke-width="(1.6 - i * 0.1) / zoom"
            :opacity="0.28"
          />
          <polyline
            v-for="(seg, si) in inRangeSegments(s.track)" :key="si"
            :points="trackPts(seg)"
            class="sim-track"
            :stroke="color(i)"
            :stroke-width="(2.8 - i * 0.15) / zoom"
            :opacity="0.92 - i * 0.06"
          />
          <circle
            v-for="(p, j) in s.track" :key="j"
            :cx="proj(p).x" :cy="proj(p).y" :r="(p.in_range ? 1.9 : 1.1) / zoom"
            :fill="color(i)" :opacity="p.in_range ? (0.85 - i * 0.05) : 0.25"
          />
        </g>

        <!-- 查詢路徑（最上層、醒目）：範圍外淡、範圍內粗 -->
        <polyline v-if="queryTrack.length" :points="trackPts(queryTrack)" class="query-track" :stroke-width="2 / zoom" :opacity="0.3" />
        <polyline
          v-for="(seg, si) in inRangeSegments(queryTrack)" :key="'qseg' + si"
          :points="trackPts(seg)" class="query-track" :stroke-width="4.5 / zoom"
        />
        <circle
          v-for="(p, j) in queryTrack" :key="'q' + j"
          :cx="proj(p).x" :cy="proj(p).y" :r="(p.in_range ? 2.8 : 1.6) / zoom"
          class="query-pt" :opacity="p.in_range ? 1 : 0.35"
        />
      </g>
    </svg>

    <!-- 控制與圖例 -->
    <div class="map-controls">
      <button @click="zoomBy(1.25)" title="放大">＋</button>
      <button @click="zoomBy(0.8)" title="縮小">－</button>
      <button @click="reset" title="重設視野">⟳</button>
    </div>
    <div class="map-legend">
      <div class="lg-row"><span class="lg-line query"></span> 查詢路徑</div>
      <div class="lg-row" v-for="(s, i) in similars" :key="s.typhoon_id">
        <span class="lg-line" :style="{ background: color(i) }"></span>
        {{ i + 1 }}. {{ s.name_zh }} ({{ s.year }})
        <span class="lg-meta">{{ distanceUnit === 'km' ? Math.round(s.distance) + ' km' : (s.score * 100).toFixed(0) + '%' }}</span>
      </div>
      <div class="lg-row sub"><span class="lg-line buffer"></span> 海岸線外擴 {{ bufferKm }} km</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'

const props = defineProps({
  coastline: { type: Array, default: () => [] },   // [{lat,lon}]
  buffer: { type: Array, default: () => [] },
  queryTrack: { type: Array, default: () => [] },
  similars: { type: Array, default: () => [] },     // [{typhoon_id,name_zh,year,track,score,distance}]
  bufferKm: { type: Number, default: 500 },
  distanceUnit: { type: String, default: 'score' },
})

const W = 800
const H = 600
const PAD = 30
const PALETTE = ['#2563eb', '#16a34a', '#9333ea', '#ea580c', '#0891b2', '#db2777', '#65a30d', '#7c3aed']

const svgEl = ref(null)
const zoom = ref(1)
const pan = ref({ x: 0, y: 0 })
const drag = ref(null)

function color(i) { return PALETTE[i % PALETTE.length] }

// --- 等距投影：用所有幾何的範圍計算 fit 變換，回傳 (point)->{x,y} ---
const projFn = computed(() => {
  const all = [...props.coastline, ...props.buffer, ...props.queryTrack]
  props.similars.forEach((s) => (s.track || []).forEach((p) => all.push(p)))
  if (!all.length) return () => ({ x: W / 2, y: H / 2 })

  const lat0 = all.reduce((a, p) => a + p.lat, 0) / all.length
  const cos0 = Math.cos((lat0 * Math.PI) / 180)
  const wx = (p) => p.lon * cos0      // 經度（cos 修正）
  const wy = (p) => -p.lat            // 北上為螢幕上方
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
  all.forEach((p) => {
    const x = wx(p), y = wy(p)
    if (x < minX) minX = x
    if (x > maxX) maxX = x
    if (y < minY) minY = y
    if (y > maxY) maxY = y
  })
  const spanX = maxX - minX || 1
  const spanY = maxY - minY || 1
  const scale = Math.min((W - 2 * PAD) / spanX, (H - 2 * PAD) / spanY)
  const offX = (W - scale * spanX) / 2
  const offY = (H - scale * spanY) / 2
  return (p) => ({ x: offX + (wx(p) - minX) * scale, y: offY + (wy(p) - minY) * scale })
})

function proj(p) { return projFn.value(p) }
function trackPts(track) {
  return (track || []).map((p) => { const q = proj(p); return `${q.x},${q.y}` }).join(' ')
}
// 取出落在計算範圍內的連續路徑段（in_range 的相鄰點），供醒目繪製
function inRangeSegments(track) {
  const segs = []
  let cur = []
  for (const p of track || []) {
    if (p.in_range) {
      cur.push(p)
    } else {
      if (cur.length >= 2) segs.push(cur)
      cur = []
    }
  }
  if (cur.length >= 2) segs.push(cur)
  return segs
}
const coastPts = computed(() => (props.coastline.length ? trackPts(props.coastline) : ''))
const bufferPts = computed(() => (props.buffer.length ? trackPts(props.buffer) : ''))

// --- 縮放 / 平移 ---
function zoomAt(factor, cx, cy) {
  const newZoom = Math.max(0.5, Math.min(20, zoom.value * factor))
  const k = newZoom / zoom.value
  pan.value = { x: cx - (cx - pan.value.x) * k, y: cy - (cy - pan.value.y) * k }
  zoom.value = newZoom
}
function svgPoint(evt) {
  const rect = svgEl.value.getBoundingClientRect()
  return {
    x: ((evt.clientX - rect.left) / rect.width) * W,
    y: ((evt.clientY - rect.top) / rect.height) * H,
  }
}
function onWheel(e) { const p = svgPoint(e); zoomAt(e.deltaY < 0 ? 1.15 : 0.87, p.x, p.y) }
function zoomBy(f) { zoomAt(f, W / 2, H / 2) }
function onDown(e) { drag.value = { ...svgPoint(e), px: pan.value.x, py: pan.value.y } }
function onMove(e) {
  if (!drag.value) return
  const p = svgPoint(e)
  pan.value = { x: drag.value.px + (p.x - drag.value.x), y: drag.value.py + (p.y - drag.value.y) }
}
function onUp() { drag.value = null }
function reset() { zoom.value = 1; pan.value = { x: 0, y: 0 } }

watch(() => [props.coastline, props.queryTrack, props.similars], reset)
</script>

<style scoped>
.map-wrap { position: relative; width: 100%; border-radius: 10px; overflow: hidden; border: 1px solid var(--border, #e2e8f0); }
.map-svg { display: block; width: 100%; height: auto; background: #eaf2fb; cursor: grab; }
.map-svg:active { cursor: grabbing; }
.sea { fill: #eaf2fb; }
.buffer { fill: rgba(56, 126, 184, 0.10); stroke: #387eb8; stroke-dasharray: 6 4; }
.land { fill: #d9e8c8; stroke: #6b8e4e; }
.sim-track { fill: none; stroke-linejoin: round; stroke-linecap: round; }
.query-track { fill: none; stroke: #dc2626; stroke-linejoin: round; stroke-linecap: round; }
.query-pt { fill: #dc2626; }
.map-controls { position: absolute; top: 10px; right: 10px; display: flex; flex-direction: column; gap: 4px; }
.map-controls button {
  width: 30px; height: 30px; border: 1px solid #cbd5e1; background: #fff; border-radius: 6px;
  font-size: 1rem; cursor: pointer; line-height: 1; color: #334155;
}
.map-controls button:hover { background: #f1f5f9; }
.map-legend {
  position: absolute; left: 10px; bottom: 10px; background: rgba(255, 255, 255, 0.92);
  border: 1px solid #e2e8f0; border-radius: 8px; padding: 8px 10px; font-size: 0.76rem;
  max-width: 62%; line-height: 1.5;
}
.lg-row { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
.lg-row.sub { margin-top: 4px; color: var(--text-secondary, #64748b); }
.lg-line { display: inline-block; width: 18px; height: 3px; border-radius: 2px; background: #999; flex: none; }
.lg-line.query { background: #dc2626; height: 4px; }
.lg-line.buffer { background: transparent; border-top: 2px dashed #387eb8; height: 0; }
.lg-meta { margin-left: auto; padding-left: 8px; color: var(--text-secondary, #64748b); font-variant-numeric: tabular-nums; }
</style>
