<template>
  <div>
    <div style="display:flex;align-items:center;gap:1rem;margin-bottom:1.5rem;">
      <router-link to="/typhoon/experiments" class="btn btn-secondary">← 返回</router-link>
      <h1 class="page-title" style="margin:0;">預測結果</h1>
    </div>

    <template v-if="detail">
      <!-- Stats Bar -->
      <div class="stats-bar">
        <div class="stat-item">
          <div class="stat-value"><span class="badge badge-primary">{{ meta.method || 'N/A' }}</span></div>
          <div class="stat-label">方法</div>
        </div>
        <div class="stat-item">
          <div class="stat-value" :style="{color: accuracy > 0.7 ? 'var(--success)' : accuracy > 0.5 ? 'var(--warning)' : 'var(--danger)'}">
            {{ (accuracy * 100).toFixed(1) }}%
          </div>
          <div class="stat-label">總準確率</div>
        </div>
        <div class="stat-item">
          <div class="stat-value">{{ correct }}/{{ total }}</div>
          <div class="stat-label">正確/總數</div>
        </div>
        <div class="stat-item">
          <div class="stat-value">{{ params.k || 5 }}</div>
          <div class="stat-label">k 值</div>
        </div>
      </div>

      <!-- Parameter Config -->
      <div class="card">
        <h3 class="card-title">參數配置</h3>
        <table class="data-table">
          <thead>
            <tr><th>參數</th><th>值</th></tr>
          </thead>
          <tbody>
            <tr v-for="(value, key) in params" :key="key">
              <td><code>{{ key }}</code></td>
              <td>{{ formatValue(value) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="meta.config_source" style="margin-top:0.75rem;font-size:0.8rem;color:var(--text-secondary);">
          配置來源：<code>{{ meta.config_source }}</code>
        </p>
      </div>

      <!-- Per Category Accuracy -->
      <div class="card">
        <h3 class="card-title">各分類準確率</h3>
        <table class="data-table">
          <thead>
            <tr>
              <th>分類</th>
              <th>正確</th>
              <th>總數</th>
              <th>準確率</th>
              <th style="width:30%"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="cat in sortedCategories" :key="cat.category">
              <td><span class="cat-badge">{{ cat.category }}</span> 類型 {{ cat.category }}</td>
              <td>{{ cat.correct }}</td>
              <td>{{ cat.total }}</td>
              <td>{{ (cat.accuracy * 100).toFixed(1) }}%</td>
              <td>
                <div class="progress-bar">
                  <div class="progress-fill" :style="{width: (cat.accuracy * 100) + '%', background: getAccuracyColor(cat.accuracy)}"></div>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Chart Tabs -->
      <div class="tabs" style="margin-top:2rem;">
        <div class="tab" :class="{active: activeTab === 'classification'}" @click="activeTab = 'classification'">
          分類分析圖表
        </div>
        <div class="tab" :class="{active: activeTab === 'rainfall'}" @click="activeTab = 'rainfall'">
          降水分析
        </div>
      </div>

      <!-- Classification Charts -->
      <div v-show="activeTab === 'classification'">
        <div v-if="classificationImages.length" class="chart-grid">
          <div class="chart-item" v-for="img in classificationImages" :key="img.url" @click="modalImg = img.url">
            <img :src="img.url" :alt="img.name" loading="lazy" />
            <div class="chart-caption">{{ img.name }}</div>
          </div>
        </div>
        <div v-else class="card" style="text-align:center;padding:3rem;color:var(--text-secondary);">
          無分類圖表
        </div>
      </div>

      <!-- Rainfall Charts -->
      <div v-show="activeTab === 'rainfall'">
        <!-- Rainfall Summary -->
        <div v-if="meta.rainfall" class="card" style="margin-top:1.5rem;margin-bottom:1.5rem;">
          <h3 class="card-title">降水預測摘要</h3>
          <div class="stats-bar" style="margin-bottom:0.75rem;">
            <div class="stat-item">
              <div class="stat-value">{{ formatMAE(meta.rainfall.overall_mae, '臺南') }} mm</div>
              <div class="stat-label">臺南 MAE</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ formatMAE(meta.rainfall.overall_mae, '高雄') }} mm</div>
              <div class="stat-label">高雄 MAE</div>
            </div>
          </div>
          <p style="font-size:0.85rem;color:var(--text-secondary);">
            有降水資料的預測：{{ meta.rainfall.total_with_data }} / {{ total }} 筆
          </p>
        </div>

        <div v-if="rainfallImages.length" class="chart-grid">
          <div class="chart-item" v-for="img in rainfallImages" :key="img.url" @click="modalImg = img.url">
            <img :src="img.url" :alt="img.name" loading="lazy" />
            <div class="chart-caption">{{ img.name }}</div>
          </div>
        </div>
        <div v-else class="card" style="text-align:center;padding:3rem;color:var(--text-secondary);">
          無降水圖表
        </div>
      </div>
    </template>

    <!-- Loading -->
    <div v-else class="card" style="text-align:center;padding:4rem;">
      <p style="color:var(--text-secondary);">載入中...</p>
    </div>

    <!-- Image Modal -->
    <div v-if="modalImg" class="modal-overlay" @click="modalImg = null">
      <img :src="modalImg" @click.stop />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../../api'

const props = defineProps({ runId: String })
const detail = ref(null)
const activeTab = ref('classification')
const modalImg = ref(null)

const meta = computed(() => detail.value?.meta || {})
const params = computed(() => meta.value?.parameters || {})
const accuracy = computed(() => meta.value?.results?.accuracy ?? meta.value?.accuracy ?? 0)
const total = computed(() => meta.value?.results?.total ?? meta.value?.total ?? 0)
const correct = computed(() => meta.value?.results?.correct ?? meta.value?.correct ?? 0)

const classificationImages = computed(() => detail.value?.images || [])
const rainfallImages = computed(() => detail.value?.rainfall_images || [])

const sortedCategories = computed(() => {
  const perCat = meta.value?.results?.per_category || meta.value?.per_category || {}
  return Object.entries(perCat)
    .map(([cat, data]) => ({
      category: cat,
      correct: data.correct,
      total: data.total,
      accuracy: data.total > 0 ? data.correct / data.total : 0,
    }))
    .sort((a, b) => parseInt(a.category) - parseInt(b.category))
})

function formatValue(val) {
  if (Array.isArray(val)) return `[${val.join(', ')}]`
  return String(val)
}

function formatMAE(mae, station) {
  if (!mae) return 'N/A'
  const v = mae[station]
  return v != null ? Number(v).toFixed(1) : 'N/A'
}

function getAccuracyColor(acc) {
  if (acc >= 0.8) return 'var(--success)'
  if (acc >= 0.5) return 'var(--warning)'
  return 'var(--danger)'
}

onMounted(async () => {
  try {
    const res = await api.typhoonRunDetail(props.runId)
    detail.value = res.data
  } catch (e) {
    console.error(e)
  }
})
</script>

<style scoped>
.stats-bar {
  display: flex;
  gap: 1.5rem;
  flex-wrap: wrap;
  padding: 1.5rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  margin-bottom: 1.5rem;
}
.stat-item {
  text-align: center;
  flex: 1;
  min-width: 100px;
}
.stat-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--primary);
}
.stat-label {
  font-size: 0.8rem;
  color: var(--text-secondary);
  margin-top: 0.25rem;
}
.progress-bar {
  height: 8px;
  background: var(--bg);
  border-radius: 4px;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.3s;
}
.chart-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
  gap: 1.5rem;
  margin-top: 1.5rem;
}
.chart-item {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  cursor: pointer;
  transition: transform 0.15s, box-shadow 0.15s;
}
.chart-item:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(0,0,0,0.08);
}
.chart-item img {
  width: 100%;
  display: block;
}
.chart-caption {
  padding: 0.6rem 1rem;
  font-size: 0.8rem;
  color: var(--text-secondary);
  text-align: center;
  border-top: 1px solid var(--border);
}
</style>
