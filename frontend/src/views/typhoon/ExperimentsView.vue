<template>
  <div>
    <h1 class="page-title">預測結果</h1>
    <p class="page-subtitle">各版本預測實驗的結果記錄，按時間倒序排列</p>

    <!-- 災害類型分頁 -->
    <div class="tabs">
      <div class="tab active">颱風</div>
      <div class="tab disabled">洪水（開發中）</div>
      <div class="tab disabled">地震（開發中）</div>
    </div>

    <template v-if="runs.length">
      <div class="card experiment-card" v-for="run in runs" :key="run.run_id"
           @click="$router.push(`/typhoon/experiments/${run.run_id}`)">
        <div class="exp-header">
          <div class="exp-title">{{ run.run_id }}</div>
          <div v-if="run.meta?.description" class="exp-desc">{{ run.meta.description }}</div>
        </div>
        <div class="exp-stats" v-if="run.meta">
          <span v-if="run.meta.method" class="exp-stat">
            方法: <span class="badge badge-primary">{{ run.meta.method }}</span>
          </span>
          <span v-if="getAccuracy(run) != null" class="exp-stat">
            準確率: <strong :style="{color: getAccuracy(run) > 0.7 ? 'var(--success)' : getAccuracy(run) > 0.5 ? 'var(--warning)' : 'var(--danger)'}">
              {{ (getAccuracy(run) * 100).toFixed(1) }}%
            </strong>
          </span>
          <span v-if="getTotal(run)" class="exp-stat">{{ getCorrect(run) }}/{{ getTotal(run) }} 正確</span>
          <span v-if="run.meta.rainfall" class="badge badge-success">含降水分析</span>
        </div>
        <button class="btn btn-primary btn-sm" @click.stop="$router.push(`/typhoon/experiments/${run.run_id}`)">查看詳情</button>
      </div>
    </template>

    <div v-else class="card" style="text-align:center;padding:4rem 2rem;color:var(--text-secondary);">
      <p>尚無預測結果</p>
      <p style="font-size:0.85rem;margin-top:0.5rem;">
        執行實驗腳本：<code>python experiments/typhoon/all_cases/exp001_combined_rrf/run.py</code>
      </p>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import api from '../../api'

const runs = ref([])

function getAccuracy(run) {
  return run.meta?.results?.accuracy ?? run.meta?.accuracy ?? null
}
function getTotal(run) {
  return run.meta?.results?.total ?? run.meta?.total ?? null
}
function getCorrect(run) {
  return run.meta?.results?.correct ?? run.meta?.correct ?? 0
}

onMounted(async () => {
  try {
    const res = await api.typhoonRuns()
    runs.value = res.data
  } catch (e) {
    console.error(e)
  }
})
</script>

<style scoped>
.experiment-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 1rem;
  cursor: pointer;
  transition: transform 0.15s, box-shadow 0.15s;
}
.experiment-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 16px rgba(0,0,0,0.06);
}
.exp-header {
  flex: 1;
  min-width: 200px;
}
.exp-title {
  font-weight: 600;
  font-size: 1rem;
  margin-bottom: 0.25rem;
}
.exp-desc {
  font-size: 0.8rem;
  color: var(--text-secondary);
}
.exp-stats {
  display: flex;
  gap: 1rem;
  flex-wrap: wrap;
  align-items: center;
  font-size: 0.85rem;
  color: var(--text-secondary);
}
.btn-sm {
  padding: 0.4rem 1rem;
  font-size: 0.8rem;
}
.badge-success {
  background: #e8f5e9;
  color: #2e7d32;
}
</style>
