<template>
  <div>
    <h1 class="page-title">資料分析</h1>
    <p class="page-subtitle">原始資料的統計分析與視覺化圖表</p>

    <!-- 災害類型分頁 -->
    <div class="tabs">
      <div class="tab active">颱風</div>
      <div class="tab disabled">洪水（開發中）</div>
      <div class="tab disabled">地震（開發中）</div>
    </div>

    <!-- 資料集切換 -->
    <div class="tabs" style="margin-top: 0.5rem;">
      <div class="tab" :class="{active: activeDataset === 'track'}" @click="activeDataset = 'track'">軌跡資料</div>
      <div class="tab" :class="{active: activeDataset === 'rainfall'}" @click="activeDataset = 'rainfall'">降水資料</div>
    </div>

    <!-- 軌跡分析 -->
    <div v-show="activeDataset === 'track'">
      <template v-if="trackImages.length">
        <div class="chart-grid">
          <div class="chart-item" v-for="img in trackImages" :key="img.url" @click="modalImg = img.url">
            <img :src="img.url" :alt="img.name" loading="lazy" />
            <div class="chart-caption">{{ img.name }}</div>
          </div>
        </div>
      </template>
      <div v-else class="card" style="text-align:center;padding:4rem 2rem;color:var(--text-secondary);">
        <p>尚無軌跡分析圖表</p>
        <p style="font-size:0.85rem;margin-top:0.5rem;">執行 <code>python scripts/run_analysis.py</code> 產生分析圖表</p>
      </div>
    </div>

    <!-- 降水分析 -->
    <div v-show="activeDataset === 'rainfall'">
      <template v-if="rainfallImages.length">
        <div class="chart-grid">
          <div class="chart-item" v-for="img in rainfallImages" :key="img.url" @click="modalImg = img.url">
            <img :src="img.url" :alt="img.name" loading="lazy" />
            <div class="chart-caption">{{ img.name }}</div>
          </div>
        </div>
      </template>
      <div v-else class="card" style="text-align:center;padding:4rem 2rem;color:var(--text-secondary);">
        <p>尚無降水分析圖表</p>
        <p style="font-size:0.85rem;margin-top:0.5rem;">執行 <code>python scripts/run_analysis.py</code> 產生降水分析圖表</p>
      </div>
    </div>

    <!-- Image Modal -->
    <div v-if="modalImg" class="modal-overlay" @click="modalImg = null">
      <img :src="modalImg" />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import api from '../../api'

const activeDataset = ref('track')
const trackImages = ref([])
const rainfallImages = ref([])
const modalImg = ref(null)

onMounted(async () => {
  try {
    const res = await api.typhoonAnalysis()
    trackImages.value = res.data.track_images || []
    rainfallImages.value = res.data.rainfall_images || []
  } catch (e) {
    console.error(e)
  }
})
</script>

<style scoped>
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
