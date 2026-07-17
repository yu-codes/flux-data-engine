<template>
  <div>
    <h1 class="page-title">即時預測</h1>
    <p class="page-subtitle">輸入災害事件資料或選擇範例，即時查詢最相似的歷史事件</p>

    <!-- 災害類型分頁 -->
    <div class="tabs">
      <div class="tab active">颱風</div>
      <div class="tab disabled">洪水（開發中）</div>
      <div class="tab disabled">地震（開發中）</div>
    </div>

    <div class="predict-layout">
      <!-- Left: Input -->
      <div>
        <div class="card">
          <h3>預測參數</h3>
          <div class="param-grid">
            <div class="form-group" style="grid-column: 1 / -1;">
              <label>算法</label>
              <select v-model="form.method" @change="onMethodChange">
                <option value="coastline_rrf">⭐ 海岸線 RRF 融合（絕對位置＋KNN＋降水）（最高準確率）</option>
                <option value="coastline">🌀 海岸線範圍 絕對位置相似度</option>
                <option value="combined_rainfall">Combined RRF（KNN+DTW+Rule，可選降水訊號）</option>
                <option value="knn_optimized">KNN 優化版 (顯著特徵)</option>
                <option value="rule_based">Rule-Based (幾何分類)</option>
              </select>
              <div class="hint" v-if="form.method === 'coastline_rrf'">
                新方法：以「絕對位置相似度」為主（權重 0.8），用 RRF 融合 KNN 與可選的降水排名做三訊號投票。準確率最高（LOO 82.3%）。
              </div>
              <div class="hint" v-else-if="form.method === 'coastline'">
                將台灣海岸線向外擴張 n km，只在此範圍內以「絕對經緯度位置」比對路徑曲線，找出地圖上最貼近的幾條歷史颱風路徑。
              </div>
              <div class="hint" v-else>Combined RRF（α=0.1, Rule=0.4, DTW=0.5, rrf_k=30），可勾選納入事件降水相似度</div>
            </div>
            <div class="form-group">
              <label>相似數 k</label>
              <input type="number" v-model.number="form.k" min="1" max="20">
            </div>
            <div class="form-group">
              <label>計算範圍：海岸線外擴 (km)</label>
              <input type="number" v-model.number="form.buffer_km" min="50" max="2000" step="50">
              <div class="hint">所有方法只在此範圍內計算（預設 500）。改變此值會重算參考特徵，首次稍慢。</div>
            </div>
            <template v-if="showCombinedParams">
              <div class="form-group">
                <label>α (KNN 權重)</label>
                <input type="number" v-model.number="form.alpha" min="0" max="1" step="0.01">
              </div>
              <div class="form-group">
                <label>Rule 權重</label>
                <input type="number" v-model.number="form.rule_weight" min="0" max="1" step="0.01">
                <div class="hint">DTW = 1 - α - rule{{ form.use_rainfall ? ' - rain' : '' }}</div>
              </div>
              <div class="form-group">
                <label>RRF k</label>
                <input type="number" v-model.number="form.rrf_k" min="1" max="200">
              </div>
            </template>
          </div>

          <!-- 降水訊號設定 -->
          <div class="rainfall-config" v-if="showRainfallConfig">
            <label class="checkbox-row">
              <input type="checkbox" v-model="form.use_rainfall">
              <span>使用降水資料作為相似度特徵</span>
            </label>
            <div class="param-grid" v-if="form.use_rainfall">
              <div class="form-group">
                <label>降水地區</label>
                <select v-model="form.rainfall_region">
                  <option v-for="r in regions" :key="r.code" :value="r.code">{{ r.label }} ({{ r.code }})</option>
                </select>
              </div>
              <div class="form-group">
                <label>降水權重</label>
                <input type="number" v-model.number="form.rainfall_weight" min="0" max="1" step="0.05">
              </div>
              <div class="form-group" style="grid-column: 1 / -1;">
                <label>預期降水量 (mm)<span class="hint" style="font-weight:400;"> — 選填，啟用降水排序</span></label>
                <input type="number" v-model.number="form.expected_rainfall" min="0" step="10" placeholder="留空則不依降水排序類比">
              </div>
            </div>
          </div>
        </div>

        <div class="card">
          <h3>範例路徑 <span class="hint" style="font-weight:400;">（點擊載入）</span></h3>
          <div class="examples-grid">
            <div class="example-card" @click="loadExample('westward')" :class="{selected: selectedExample==='westward'}">
              <div style="font-weight:600;font-size:0.88rem;">西行穿越型</div>
              <div style="font-size:0.78rem;color:var(--text-secondary);">從東南方向西北穿越台灣</div>
            </div>
            <div class="example-card" @click="loadExample('northward')" :class="{selected: selectedExample==='northward'}">
              <div style="font-weight:600;font-size:0.88rem;">東岸北上型</div>
              <div style="font-size:0.78rem;color:var(--text-secondary);">沿台灣東岸向北移動</div>
            </div>
            <div class="example-card" @click="loadExample('south_sea')" :class="{selected: selectedExample==='south_sea'}">
              <div style="font-weight:600;font-size:0.88rem;">南海面西行型</div>
              <div style="font-size:0.78rem;color:var(--text-secondary);">通過台灣南部海面向西</div>
            </div>
            <div class="example-card" @click="loadExample('north_sea')" :class="{selected: selectedExample==='north_sea'}">
              <div style="font-weight:600;font-size:0.88rem;">北部海面型</div>
              <div style="font-size:0.78rem;color:var(--text-secondary);">通過台灣北部海面向西</div>
            </div>
          </div>
        </div>

        <div class="card">
          <h3>颱風路徑 JSON</h3>
          <textarea v-model="trackInput" rows="16"></textarea>
          <div class="hint">每個點需要 latitude, longitude 欄位；wind_kt, pressure_mb 為選填</div>
          <div style="margin-top: 1rem;">
            <button class="btn btn-accent btn-lg" @click="submitPredict" :disabled="loading">
              <span v-if="loading" class="spinner"></span>
              {{ loading ? '分析中...' : '執行預測' }}
            </button>
          </div>
        </div>
      </div>

      <!-- Right: Results -->
      <div v-if="result">
        <!-- Map projection -->
        <div class="card" v-if="result.query_track && result.query_track.length">
          <h3>路徑地圖投影 <span class="hint" style="font-weight:400;">（滾輪縮放、拖曳平移）</span></h3>
          <TyphoonMap
            :coastline="result.coastline"
            :buffer="result.buffer"
            :query-track="result.query_track"
            :similars="result.similar_typhoons"
            :buffer-km="result.buffer_km || form.buffer_km"
            :distance-unit="result.distance_unit"
          />
        </div>

        <!-- 降水機率分布地圖 -->
        <div class="card" v-if="submittedTrack.length">
          <PrecipProbMap :track="submittedTrack" />
        </div>

        <!-- Summary -->
        <div class="card result-box">
          <h2>預測結果</h2>
          <div class="result-metrics">
            <div>
              <div class="metric-label">預測分類</div>
              <div class="metric-value" style="color: var(--primary);">類型 {{ result.predicted_category }}</div>
            </div>
            <div>
              <div class="metric-label">信心度</div>
              <div class="metric-value" :style="{color: result.confidence > 0.6 ? 'var(--success)' : 'var(--warning)'}">
                {{ (result.confidence * 100).toFixed(1) }}%
              </div>
            </div>
          </div>
        </div>

        <!-- Similar Typhoons -->
        <div class="card" v-if="result.similar_typhoons?.length">
          <h3>相似颱風 Top-{{ result.similar_typhoons.length }}</h3>
          <div class="table-wrapper">
            <table class="data-table">
              <thead>
                <tr><th>排名</th><th>名稱</th><th>年份</th><th>分類</th><th>{{ result.distance_unit === 'km' ? '平均偏離' : '距離' }}</th><th>相似度</th></tr>
              </thead>
              <tbody>
                <tr v-for="(t, i) in result.similar_typhoons" :key="t.typhoon_id">
                  <td><strong>{{ i + 1 }}</strong></td>
                  <td>{{ t.name_zh }} ({{ t.name_en }})</td>
                  <td>{{ t.year }}</td>
                  <td><span class="badge badge-primary">{{ t.category }}</span></td>
                  <td>{{ result.distance_unit === 'km' ? Math.round(t.distance) + ' km' : t.distance.toFixed(3) }}</td>
                  <td :style="{color: t.score > 0.7 ? 'var(--success)' : t.score > 0.4 ? 'var(--warning)' : 'var(--text-secondary)', fontWeight: 600}">
                    {{ (t.score * 100).toFixed(1) }}%
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Category Votes -->
        <div class="card" v-if="result.category_votes && Object.keys(result.category_votes).length > 0">
          <h3>分類投票分布</h3>
          <div class="votes-list">
            <div v-for="[cat, w] in sortedVotes" :key="cat" class="vote-row">
              <div class="vote-label">類型 {{ cat }}</div>
              <div class="vote-track">
                <div class="vote-fill" :style="{width: (w * 100) + '%'}"></div>
              </div>
              <div class="vote-pct">{{ (w * 100).toFixed(1) }}%</div>
            </div>
          </div>
        </div>

        <!-- Rainfall -->
        <div class="card" v-if="result.rainfall && result.rainfall.stations">
          <h3>降水量預估（類比颱風降水推估）</h3>
          <p class="hint" v-if="form.use_rainfall" style="margin-bottom:0.75rem;">
            降水訊號已啟用 — 相似度地區：{{ result.rainfall.region_label }}（{{ result.rainfall.region }}）
          </p>
          <div class="table-wrapper">
            <table class="data-table">
              <thead>
                <tr><th>測站</th><th>平均</th><th>中位數</th><th>最小</th><th>最大</th><th>類比數</th></tr>
              </thead>
              <tbody>
                <tr v-for="(stats, station) in result.rainfall.stations" :key="station"
                    :class="{ 'row-active': stats.region === result.rainfall.region && form.use_rainfall }">
                  <td><strong>{{ station }}</strong></td>
                  <td>{{ stats.mean }} mm</td>
                  <td>{{ stats.median }} mm</td>
                  <td>{{ stats.min }} mm</td>
                  <td>{{ stats.max }} mm</td>
                  <td>{{ stats.count }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Charts -->
        <div v-if="result.charts?.length">
          <div class="card img-container" v-for="url in result.charts" :key="url">
            <img :src="url" @click="showModal(url)" loading="lazy" />
          </div>
        </div>
      </div>

      <!-- Placeholder -->
      <div v-else>
        <div class="card" style="text-align:center;padding:4rem 2rem;color:var(--text-secondary);">
          <p style="font-size:1.1rem;">選擇範例或輸入路徑資料，然後點擊「執行預測」</p>
          <p style="font-size:0.85rem;margin-top:0.5rem;">系統將找出最相似的歷史颱風並預測路徑分類</p>
        </div>
      </div>
    </div>

    <!-- Error -->
    <div v-if="error" class="card" style="border-left: 4px solid var(--danger); margin-top: 1rem;">
      <p style="color:var(--danger);font-weight:600;">錯誤：{{ error }}</p>
    </div>

    <!-- Image Modal -->
    <div v-if="modalImg" class="modal-overlay" @click="modalImg = null">
      <img :src="modalImg" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import api from '../../api'
import TyphoonMap from '../../components/TyphoonMap.vue'
import PrecipProbMap from '../../components/PrecipProbMap.vue'

const EXAMPLES = {
  westward: [
    {"latitude": 15.0, "longitude": 135.0, "wind_kt": 25, "pressure_mb": 1002},
    {"latitude": 16.5, "longitude": 132.0, "wind_kt": 35, "pressure_mb": 995},
    {"latitude": 18.0, "longitude": 129.0, "wind_kt": 50, "pressure_mb": 980},
    {"latitude": 19.5, "longitude": 126.5, "wind_kt": 65, "pressure_mb": 965},
    {"latitude": 21.0, "longitude": 124.0, "wind_kt": 75, "pressure_mb": 955},
    {"latitude": 22.5, "longitude": 122.0, "wind_kt": 80, "pressure_mb": 950},
    {"latitude": 24.0, "longitude": 120.5, "wind_kt": 70, "pressure_mb": 960},
    {"latitude": 25.5, "longitude": 119.5, "wind_kt": 55, "pressure_mb": 975},
    {"latitude": 27.0, "longitude": 119.0, "wind_kt": 40, "pressure_mb": 990},
  ],
  northward: [
    {"latitude": 18.0, "longitude": 125.0, "wind_kt": 45, "pressure_mb": 985},
    {"latitude": 19.5, "longitude": 124.0, "wind_kt": 55, "pressure_mb": 975},
    {"latitude": 21.0, "longitude": 123.0, "wind_kt": 70, "pressure_mb": 960},
    {"latitude": 22.5, "longitude": 122.5, "wind_kt": 80, "pressure_mb": 950},
    {"latitude": 24.0, "longitude": 122.5, "wind_kt": 75, "pressure_mb": 955},
    {"latitude": 25.5, "longitude": 122.0, "wind_kt": 65, "pressure_mb": 965},
    {"latitude": 27.0, "longitude": 123.0, "wind_kt": 50, "pressure_mb": 978},
    {"latitude": 28.5, "longitude": 124.0, "wind_kt": 40, "pressure_mb": 988},
  ],
  south_sea: [
    {"latitude": 16.0, "longitude": 130.0, "wind_kt": 30, "pressure_mb": 998},
    {"latitude": 17.0, "longitude": 127.0, "wind_kt": 45, "pressure_mb": 985},
    {"latitude": 18.0, "longitude": 124.0, "wind_kt": 55, "pressure_mb": 975},
    {"latitude": 19.0, "longitude": 121.0, "wind_kt": 60, "pressure_mb": 970},
    {"latitude": 19.5, "longitude": 118.0, "wind_kt": 55, "pressure_mb": 975},
    {"latitude": 20.0, "longitude": 115.0, "wind_kt": 45, "pressure_mb": 985},
    {"latitude": 20.5, "longitude": 112.0, "wind_kt": 35, "pressure_mb": 992},
  ],
  north_sea: [
    {"latitude": 22.0, "longitude": 131.0, "wind_kt": 40, "pressure_mb": 990},
    {"latitude": 23.0, "longitude": 128.0, "wind_kt": 55, "pressure_mb": 975},
    {"latitude": 24.5, "longitude": 125.0, "wind_kt": 65, "pressure_mb": 965},
    {"latitude": 25.5, "longitude": 123.0, "wind_kt": 70, "pressure_mb": 960},
    {"latitude": 26.5, "longitude": 121.0, "wind_kt": 60, "pressure_mb": 970},
    {"latitude": 27.5, "longitude": 119.0, "wind_kt": 50, "pressure_mb": 980},
    {"latitude": 28.5, "longitude": 117.0, "wind_kt": 40, "pressure_mb": 990},
  ],
}

const regions = [
  { code: 'tn', label: '臺南' },
  { code: 'kh', label: '高雄' },
]

const form = ref({
  method: 'coastline_rrf',
  k: 5,
  alpha: 0.10,
  rule_weight: 0.40,
  rrf_k: 30,
  use_rainfall: false,
  rainfall_region: 'tn',
  rainfall_weight: 0.15,
  expected_rainfall: null,
  buffer_km: 500,
})

const trackInput = ref(JSON.stringify(EXAMPLES.westward, null, 2))
const selectedExample = ref('')
const loading = ref(false)
const result = ref(null)
const error = ref('')
const modalImg = ref(null)
const submittedTrack = ref([])

const showCombinedParams = computed(() =>
  form.value.method === 'combined_rainfall'
)
// 支援降水訊號的方法（顯示降水設定）
const showRainfallConfig = computed(() =>
  form.value.method === 'combined_rainfall' ||
  form.value.method === 'coastline_rrf'
)

const sortedVotes = computed(() => {
  if (!result.value?.category_votes) return []
  return Object.entries(result.value.category_votes).sort((a, b) => b[1] - a[1])
})

function onMethodChange() {
  if (form.value.method === 'combined_rainfall') {
    form.value.alpha = 0.10
    form.value.rule_weight = 0.40
    form.value.rrf_k = 30
  }
}

function loadExample(name) {
  selectedExample.value = name
  trackInput.value = JSON.stringify(EXAMPLES[name], null, 2)
}

function showModal(url) {
  modalImg.value = url
}

async function submitPredict() {
  loading.value = true
  error.value = ''
  result.value = null

  try {
    const track = JSON.parse(trackInput.value)
    submittedTrack.value = track
    const payload = {
      track,
      method: form.value.method,
      k: form.value.k,
    }
    // 計算範圍（海岸線外擴 km）— 所有方法共用
    payload.buffer_km = form.value.buffer_km
    if (showCombinedParams.value) {
      payload.alpha = form.value.alpha
      payload.rule_weight = form.value.rule_weight
      payload.rrf_k = form.value.rrf_k
    }
    // 降水訊號（combined_rainfall / coastline_rrf 共用）
    if (showRainfallConfig.value) {
      payload.use_rainfall = form.value.use_rainfall
      if (form.value.use_rainfall) {
        payload.rainfall_region = form.value.rainfall_region
        payload.rainfall_weight = form.value.rainfall_weight
        if (form.value.expected_rainfall !== null && form.value.expected_rainfall !== '') {
          payload.expected_rainfall = form.value.expected_rainfall
        }
      }
    }
    const res = await api.typhoonPredict(payload)
    result.value = res.data
  } catch (e) {
    error.value = e.response?.data?.detail || e.message || '預測失敗'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.predict-layout {
  display: grid;
  grid-template-columns: 1fr 1.2fr;
  gap: 2rem;
  align-items: start;
}

.param-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
  margin-top: 0.75rem;
}

.examples-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem;
}

.rainfall-config {
  margin-top: 1rem;
  padding-top: 1rem;
  border-top: 1px solid var(--border, #e2e8f0);
}

.checkbox-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.9rem;
  font-weight: 600;
  cursor: pointer;
}

.checkbox-row input {
  width: 16px;
  height: 16px;
}

.row-active {
  background: rgba(56, 126, 184, 0.1);
}

.result-metrics {
  display: flex;
  gap: 3rem;
  margin-top: 1rem;
}

.metric-label {
  font-size: 0.8rem;
  color: var(--text-secondary);
}

.metric-value {
  font-size: 2rem;
  font-weight: 700;
}

.votes-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.vote-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.vote-label {
  width: 60px;
  font-weight: 600;
  font-size: 0.85rem;
}

.vote-track {
  flex: 1;
  height: 24px;
  background: #f1f5f9;
  border-radius: 12px;
  overflow: hidden;
}

.vote-fill {
  height: 100%;
  background: var(--primary);
  border-radius: 12px;
  transition: width 0.5s;
}

.vote-pct {
  width: 50px;
  text-align: right;
  font-size: 0.85rem;
}

@media (max-width: 1024px) {
  .predict-layout {
    grid-template-columns: 1fr;
  }
}
</style>
