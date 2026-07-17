<template>
  <div class="ppm">
    <div class="ppm-head">
      <div class="ppm-title">
        降水機率分布地圖
        <span class="ppm-sub">類比集合（Analog Ensemble）· 資料庫 {{ meta.n_database_hours || '—' }} 小時</span>
      </div>
      <div class="ppm-actions">
        <button class="mini" @click="reload" :disabled="loading" title="重新計算">
          <span v-if="loading" class="spinner-sm"></span>{{ loading ? '計算中' : '⟳ 重新計算' }}
        </button>
      </div>
    </div>

    <div v-if="error" class="ppm-error">{{ error }}</div>

    <template v-if="frames.length">
      <!-- 顯示模式 -->
      <div class="ppm-controls">
        <div class="seg">
          <button :class="{ on: metric === 'prob' }" @click="metric = 'prob'">超越機率 P(≥τ)</button>
          <button :class="{ on: metric === 'expected' }" @click="metric = 'expected'">期望降水 E[雨]</button>
        </div>
        <div class="seg" v-if="metric === 'prob'">
          <button
            v-for="t in thresholds" :key="t"
            :class="{ on: activeThreshold === t }"
            @click="activeThreshold = t"
          >≥{{ t }} mm/hr</button>
        </div>
      </div>

      <!-- 地圖 -->
      <div class="map-wrap">
        <svg
          ref="svgEl" class="map-svg" :viewBox="`0 0 ${W} ${H}`"
          @mousemove="onDrag" @mouseup="endDrag" @mouseleave="endDrag"
        >
          <defs>
            <filter id="ppm-blur" x="-10%" y="-10%" width="120%" height="120%">
              <feGaussianBlur stdDeviation="5.5" />
            </filter>
          </defs>
          <rect :width="W" :height="H" class="sea" />

          <!-- 降水機率/強度熱區（模糊營造連續場） -->
          <g filter="url(#ppm-blur)">
            <rect
              v-for="c in cells" :key="c.i"
              :x="c.x" :y="c.y" :width="cellW" :height="cellH"
              :fill="c.color" :opacity="c.opacity"
            />
          </g>

          <!-- 台灣海岸線 -->
          <polygon v-if="coastPts" :points="coastPts" class="land" />

          <!-- 查詢路徑 -->
          <polyline v-if="trackPts" :points="trackPts" class="qtrack" />

          <!-- 颱風中心標記（可拖曳） -->
          <g v-if="marker" :transform="`translate(${marker.x} ${marker.y})`"
             class="storm" @mousedown.stop="startDrag">
            <circle :r="13" class="storm-halo" />
            <circle :r="6" class="storm-core" />
            <path d="M0,-11 C6,-11 6,-3 0,-3 C-6,-3 -6,-11 0,-11 Z
                     M0,11 C-6,11 -6,3 0,3 C6,3 6,11 0,11 Z" class="storm-swirl" />
          </g>
        </svg>

        <!-- 色階圖例 -->
        <div class="legend">
          <div class="lg-title">{{ metric === 'prob' ? `P(雨 ≥ ${activeThreshold} mm/hr)` : '期望降水 (mm/hr)' }}</div>
          <div class="lg-bar" :style="{ background: legendGradient }"></div>
          <div class="lg-scale">
            <span>{{ metric === 'prob' ? '0%' : '0' }}</span>
            <span>{{ metric === 'prob' ? '50%' : (maxExpected/2).toFixed(0) }}</span>
            <span>{{ metric === 'prob' ? '100%' : maxExpected.toFixed(0) }}</span>
          </div>
        </div>

        <!-- 資訊 -->
        <div class="info">
          <div>颱風中心：{{ view.lat.toFixed(2) }}°N, {{ view.lon.toFixed(2) }}°E</div>
          <div v-if="view.wind_kt != null">近中心風速：{{ view.wind_kt }} kt</div>
          <div>類比樣本（有效）：{{ view.n_effective }}</div>
          <div v-if="dragMode" class="drag-hint">🖐 自訂位置（拖曳中）</div>
        </div>
      </div>

      <!-- 播放控制 -->
      <div class="ppm-player">
        <button class="play" @click="togglePlay" :disabled="dragMode">{{ playing ? '⏸' : '▶' }}</button>
        <input
          type="range" class="scrub" min="0" :max="frames.length - 1"
          v-model.number="frameIdx" :disabled="dragMode"
          @input="playing = false"
        />
        <span class="frame-lbl">{{ frameIdx + 1 }}/{{ frames.length }}</span>
        <button v-if="dragMode" class="mini" @click="exitDrag">↩ 回到路徑動畫</button>
      </div>
      <div class="ppm-note">
        沿颱風路徑逐格播放降水機率演變；亦可直接<strong>拖曳颱風中心</strong>至任意位置即時查詢。
        機率為「颱風位於相近位置時，歷史上該地降水達門檻的頻率」。
      </div>
    </template>

    <div v-else-if="!loading && !error" class="ppm-empty">
      執行預測後，將依颱風路徑產生降水機率分布地圖。
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import api from '../api'

const props = defineProps({
  track: { type: Array, default: () => [] }, // [{latitude/lat, longitude/lon, wind_kt}]
  steps: { type: Number, default: 20 },
  bandwidthKm: { type: Number, default: 150 },
})

const W = 760
const H = 620
const PAD = 24
// 視野限縮在「有降水資料的網格範圍」內（僅留極小邊距做視覺留白）
const MARGIN_DEG = 0.1

const svgEl = ref(null)
const loading = ref(false)
const error = ref('')
const gridLat = ref([])
const gridLon = ref([])
const cellDeg = ref(0.25)
const thresholds = ref([])
const frames = ref([])
const coastline = ref([])
const meta = ref({})

const frameIdx = ref(0)
const playing = ref(false)
const metric = ref('prob')
const activeThreshold = ref(5)
let timer = null

// 拖曳自訂位置
const dragMode = ref(false)
const dragging = ref(false)
const customFrame = ref(null)

function norm(p) {
  return {
    latitude: p.latitude != null ? p.latitude : p.lat,
    longitude: p.longitude != null ? p.longitude : p.lon,
    wind_kt: p.wind_kt != null ? p.wind_kt : null,
  }
}

async function reload() {
  const track = (props.track || []).map(norm).filter(p => p.latitude != null && p.longitude != null)
  if (track.length < 2) { frames.value = []; return }
  loading.value = true
  error.value = ''
  playing.value = false
  try {
    const res = await api.typhoonPrecipForecast({
      track,
      steps: props.steps,
      bandwidth_km: props.bandwidthKm,
      use_wind: true,
    })
    const d = res.data
    gridLat.value = d.grid_lat
    gridLon.value = d.grid_lon
    cellDeg.value = d.cell_deg || 0.25
    thresholds.value = d.thresholds
    coastline.value = d.coastline
    frames.value = d.frames
    meta.value = { n_database_hours: d.n_database_hours, bandwidth_km: d.bandwidth_km }
    if (!thresholds.value.includes(activeThreshold.value)) {
      activeThreshold.value = thresholds.value[Math.min(1, thresholds.value.length - 1)]
    }
    frameIdx.value = 0
    customFrame.value = null
    dragMode.value = false
  } catch (e) {
    error.value = e.response?.data?.detail || e.message || '降水地圖計算失敗'
    frames.value = []
  } finally {
    loading.value = false
  }
}

watch(() => props.track, reload, { immediate: true, deep: false })

// --- 投影：固定套用於台灣網格範圍 + margin ---
const projFn = computed(() => {
  if (!gridLat.value.length) return () => ({ x: W / 2, y: H / 2 })
  const latMin = Math.min(...gridLat.value) - MARGIN_DEG
  const latMax = Math.max(...gridLat.value) + MARGIN_DEG
  const lonMin = Math.min(...gridLon.value) - MARGIN_DEG
  const lonMax = Math.max(...gridLon.value) + MARGIN_DEG
  const lat0 = (latMin + latMax) / 2
  const cos0 = Math.cos((lat0 * Math.PI) / 180)
  const wx = (lon) => lon * cos0
  const wy = (lat) => -lat
  const minX = wx(lonMin), maxX = wx(lonMax)
  const minY = wy(latMax), maxY = wy(latMin)
  const spanX = maxX - minX || 1
  const spanY = maxY - minY || 1
  const scale = Math.min((W - 2 * PAD) / spanX, (H - 2 * PAD) / spanY)
  const offX = (W - scale * spanX) / 2
  const offY = (H - scale * spanY) / 2
  return (lon, lat) => ({ x: offX + (wx(lon) - minX) * scale, y: offY + (wy(lat) - minY) * scale, scale, cos0 })
})

function proj(lon, lat) { const f = projFn.value; return f(lon, lat) }

const cellW = computed(() => {
  const f = projFn.value(0, 0)
  return (cellDeg.value * (f.cos0 || 1) * (f.scale || 1)) + 1.5
})
const cellH = computed(() => {
  const f = projFn.value(0, 0)
  return (cellDeg.value * (f.scale || 1)) + 1.5
})

const currentFrame = computed(() => {
  if (customFrame.value) return customFrame.value
  return frames.value[frameIdx.value] || null
})

const view = computed(() => {
  const f = currentFrame.value
  if (!f) return { lat: 0, lon: 0, wind_kt: null, n_effective: 0 }
  return { lat: f.lat, lon: f.lon, wind_kt: f.wind_kt, n_effective: f.n_effective }
})

const maxExpected = ref(20)

// 依門檻動態決定期望降水色階上限（取全部 frame 的合理上界）
watch([frames, metric], () => {
  if (metric.value !== 'expected' || !frames.value.length) return
  let m = 0
  frames.value.forEach(f => { const x = Math.max(...f.expected); if (x > m) m = x })
  maxExpected.value = Math.max(5, Math.ceil(m))
})

// --- 色階 ---
// 降水色標：白→淺藍→藍→綠→黃→橙→紅
const STOPS = [
  [0.00, [255, 255, 255]],
  [0.12, [200, 225, 245]],
  [0.28, [120, 175, 225]],
  [0.45, [90, 180, 130]],
  [0.62, [225, 215, 90]],
  [0.80, [235, 150, 60]],
  [1.00, [200, 50, 45]],
]
function ramp(t) {
  t = Math.max(0, Math.min(1, t))
  for (let i = 1; i < STOPS.length; i++) {
    if (t <= STOPS[i][0]) {
      const [t0, c0] = STOPS[i - 1]
      const [t1, c1] = STOPS[i]
      const f = (t - t0) / (t1 - t0 || 1)
      return c0.map((c, k) => Math.round(c + (c1[k] - c) * f))
    }
  }
  return STOPS[STOPS.length - 1][1]
}
const legendGradient = computed(() => {
  const s = []
  for (let i = 0; i <= 10; i++) { const c = ramp(i / 10); s.push(`rgb(${c[0]},${c[1]},${c[2]}) ${i * 10}%`) }
  return `linear-gradient(to right, ${s.join(',')})`
})

const cells = computed(() => {
  const f = currentFrame.value
  if (!f || !gridLat.value.length) return []
  const ny = gridLat.value.length
  const nx = gridLon.value.length
  const vals = metric.value === 'prob' ? (f.prob[String(activeThreshold.value)] || f.prob[activeThreshold.value.toFixed(1)]) : f.expected
  if (!vals) return []
  const denom = metric.value === 'prob' ? 1 : maxExpected.value
  const out = []
  for (let yi = 0; yi < ny; yi++) {
    for (let xi = 0; xi < nx; xi++) {
      const idx = yi * nx + xi
      const v = vals[idx]
      const t = v / denom
      if (t <= 0.02) continue // 幾乎為 0 者不繪，讓海面留白
      const c = ramp(t)
      const p = proj(gridLon.value[xi], gridLat.value[yi])
      out.push({
        i: idx,
        x: p.x - cellW.value / 2,
        y: p.y - cellH.value / 2,
        color: `rgb(${c[0]},${c[1]},${c[2]})`,
        opacity: Math.min(0.92, 0.35 + t * 0.6),
      })
    }
  }
  return out
})

const coastPts = computed(() =>
  coastline.value.map(p => { const q = proj(p.lon, p.lat); return `${q.x},${q.y}` }).join(' ')
)
const trackPts = computed(() => {
  const t = (props.track || []).map(norm).filter(p => p.latitude != null)
  return t.map(p => { const q = proj(p.longitude, p.latitude); return `${q.x},${q.y}` }).join(' ')
})

const marker = computed(() => {
  const f = currentFrame.value
  if (!f) return null
  const p = proj(f.lon, f.lat)
  return { x: Math.max(6, Math.min(W - 6, p.x)), y: Math.max(6, Math.min(H - 6, p.y)) }
})

// --- 播放 ---
function togglePlay() {
  playing.value = !playing.value
  if (playing.value) startTimer(); else stopTimer()
}
function startTimer() {
  stopTimer()
  timer = setInterval(() => {
    frameIdx.value = (frameIdx.value + 1) % frames.value.length
  }, 650)
}
function stopTimer() { if (timer) { clearInterval(timer); timer = null } }
watch(playing, (v) => { if (!v) stopTimer() })
onBeforeUnmount(stopTimer)

// --- 拖曳颱風中心 → 即時查詢自訂位置 ---
function svgPoint(evt) {
  const rect = svgEl.value.getBoundingClientRect()
  return {
    x: ((evt.clientX - rect.left) / rect.width) * W,
    y: ((evt.clientY - rect.top) / rect.height) * H,
  }
}
function screenToLonLat(sx, sy) {
  const f = projFn.value
  const p00 = f(120, 24)
  const p10 = f(121, 24)
  const p01 = f(120, 25)
  const dLon = (sx - p00.x) / ((p10.x - p00.x) || 1)
  const dLat = (sy - p00.y) / ((p01.y - p00.y) || 1)
  return { lon: 120 + dLon, lat: 24 + dLat }
}
function startDrag() { dragging.value = true; playing.value = false; dragMode.value = true }
let dragTimer = null
function onDrag(evt) {
  if (!dragging.value) return
  const sp = svgPoint(evt)
  const ll = screenToLonLat(sp.x, sp.y)
  if (dragTimer) return
  dragTimer = setTimeout(() => { dragTimer = null }, 90)
  fetchCustom(ll.lat, ll.lon)
}
function endDrag() { dragging.value = false }
async function fetchCustom(lat, lon) {
  try {
    const res = await api.typhoonPrecipForecast({
      positions: [{ latitude: lat, longitude: lon }],
      thresholds: thresholds.value,
      bandwidth_km: props.bandwidthKm,
      use_wind: false,
    })
    customFrame.value = res.data.frames[0]
  } catch (e) { /* 忽略拖曳中的暫態錯誤 */ }
}
function exitDrag() { dragMode.value = false; customFrame.value = null; dragging.value = false }
</script>

<style scoped>
.ppm { display: flex; flex-direction: column; gap: 0.75rem; }
.ppm-head { display: flex; justify-content: space-between; align-items: center; }
.ppm-title { font-weight: 700; font-size: 1.05rem; }
.ppm-sub { font-weight: 400; font-size: 0.76rem; color: var(--text-secondary, #64748b); margin-left: 0.5rem; }
.ppm-actions .mini, .ppm-player .mini {
  border: 1px solid #cbd5e1; background: #fff; border-radius: 6px; padding: 4px 10px;
  font-size: 0.78rem; cursor: pointer; color: #334155; display: inline-flex; align-items: center; gap: 5px;
}
.ppm-actions .mini:hover { background: #f1f5f9; }
.ppm-controls { display: flex; flex-wrap: wrap; gap: 0.75rem; }
.seg { display: inline-flex; border: 1px solid #cbd5e1; border-radius: 8px; overflow: hidden; }
.seg button {
  border: 0; background: #fff; padding: 5px 11px; font-size: 0.78rem; cursor: pointer;
  color: #475569; border-right: 1px solid #e2e8f0;
}
.seg button:last-child { border-right: 0; }
.seg button.on { background: #2563eb; color: #fff; }
.map-wrap { position: relative; width: 100%; border-radius: 10px; overflow: hidden; border: 1px solid var(--border, #e2e8f0); }
.map-svg { display: block; width: 100%; height: auto; background: #eaf2fb; }
.sea { fill: #eaf2fb; }
.land { fill: none; stroke: #35507a; stroke-width: 1.6; opacity: 0.85; }
.qtrack { fill: none; stroke: #334155; stroke-width: 1.6; stroke-dasharray: 5 4; opacity: 0.6; }
.storm { cursor: grab; }
.storm:active { cursor: grabbing; }
.storm-halo { fill: rgba(220, 38, 38, 0.18); stroke: #dc2626; stroke-width: 1; }
.storm-core { fill: #dc2626; }
.storm-swirl { fill: #fff; opacity: 0.9; }
.legend {
  position: absolute; right: 10px; bottom: 10px; background: rgba(255, 255, 255, 0.93);
  border: 1px solid #e2e8f0; border-radius: 8px; padding: 7px 9px; font-size: 0.72rem; width: 180px;
}
.lg-title { font-weight: 600; margin-bottom: 4px; color: #334155; }
.lg-bar { height: 10px; border-radius: 3px; border: 1px solid #cbd5e1; }
.lg-scale { display: flex; justify-content: space-between; color: #64748b; margin-top: 2px; }
.info {
  position: absolute; left: 10px; top: 10px; background: rgba(255, 255, 255, 0.92);
  border: 1px solid #e2e8f0; border-radius: 8px; padding: 6px 9px; font-size: 0.74rem; color: #334155; line-height: 1.5;
}
.drag-hint { color: #dc2626; font-weight: 600; }
.ppm-player { display: flex; align-items: center; gap: 0.6rem; }
.ppm-player .play {
  width: 34px; height: 34px; border-radius: 8px; border: 1px solid #cbd5e1; background: #fff;
  font-size: 0.95rem; cursor: pointer; color: #334155;
}
.ppm-player .play:hover { background: #f1f5f9; }
.scrub { flex: 1; }
.frame-lbl { font-size: 0.78rem; color: #64748b; font-variant-numeric: tabular-nums; min-width: 42px; text-align: center; }
.ppm-note { font-size: 0.76rem; color: var(--text-secondary, #64748b); line-height: 1.5; }
.ppm-error { color: var(--danger, #dc2626); font-size: 0.85rem; }
.ppm-empty { color: var(--text-secondary, #64748b); text-align: center; padding: 2rem; }
.spinner-sm {
  width: 12px; height: 12px; border: 2px solid #cbd5e1; border-top-color: #2563eb;
  border-radius: 50%; display: inline-block; animation: spin 0.7s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>
