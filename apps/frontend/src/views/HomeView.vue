<template>
  <div>
    <section class="hero">
      <h1 class="page-title">Flux Data Engine</h1>
      <p class="page-subtitle">多災害類比預測系統 — 基於歷史事件相似度的災害影響分析平台</p>
    </section>

    <!-- 災害類型分頁 -->
    <div class="tabs">
      <div class="tab active">颱風</div>
      <div class="tab disabled">洪水（開發中）</div>
      <div class="tab disabled">地震（開發中）</div>
    </div>

    <!-- 系統架構 -->
    <div class="card">
      <div class="card-header"><h3>系統架構</h3></div>
      <div class="arch-flow">
        <div class="arch-node data-node">
          <div class="arch-label">Data Layer</div>
          <div class="arch-detail">207 筆颱風 · 1958-2025<br>IBTrACS + CWA 路徑分類<br>+ 颱風事件雨量</div>
        </div>
        <div class="arch-arrow">→</div>
        <div class="arch-node feature-node">
          <div class="arch-label">Feature Extraction</div>
          <div class="arch-detail">極座標轉換 · Impact Window<br>11 維摘要特徵 + 時序路徑</div>
        </div>
        <div class="arch-arrow">→</div>
        <div class="arch-node sim-node">
          <div class="arch-label">Similarity Engine</div>
          <div class="arch-detail">KNN · DTW · Rule-Based<br>RRF 融合排名</div>
        </div>
        <div class="arch-arrow">→</div>
        <div class="arch-node pred-node">
          <div class="arch-label">Prediction</div>
          <div class="arch-detail">Top-K 類比投票<br>路徑分類 + 降水預測</div>
        </div>
      </div>
    </div>

    <!-- 資料概覽 -->
    <div class="card">
      <div class="card-header"><h3>資料概覽</h3></div>
      <div class="grid grid-3" style="gap: 1.5rem; margin-bottom: 1.5rem;">
        <div class="mini-stat">
          <span class="mini-stat-value">207</span>
          <span class="mini-stat-label">颱風總數 (1958-2025)</span>
        </div>
        <div class="mini-stat">
          <span class="mini-stat-value">9</span>
          <span class="mini-stat-label">CWA 侵臺路徑類型</span>
        </div>
        <div class="mini-stat">
          <span class="mini-stat-value">440</span>
          <span class="mini-stat-label">事件降水紀錄</span>
        </div>
      </div>

      <div class="grid grid-2" style="gap: 1rem;">
        <div class="card" style="border-left: 4px solid var(--primary); margin: 0;">
          <h4>颱風軌跡資料</h4>
          <ul style="margin: 0.5rem 0 0 1.5rem; font-size: 0.88rem; line-height: 1.7; color: var(--text-secondary);">
            <li>來源：IBTrACS + CWA 路徑分類</li>
            <li>207 筆完整軌跡記錄</li>
            <li>包含風速、氣壓、座標、時序</li>
            <li>198 筆具有分類標籤 (Cat 1-9)</li>
          </ul>
        </div>
        <div class="card" style="border-left: 4px solid #377eb8; margin: 0;">
          <h4>颱風事件雨量</h4>
          <ul style="margin: 0.5rem 0 0 1.5rem; font-size: 0.88rem; line-height: 1.7; color: var(--text-secondary);">
            <li>來源：CWA 颱風事件雨量統計</li>
            <li>440 筆降水紀錄（207 筆已配對）</li>
            <li>測站：臺南、高雄</li>
            <li>單位：事件累積降水量 (mm)</li>
          </ul>
        </div>
      </div>
    </div>

    <!-- CWA 路徑分類表 -->
    <div class="card">
      <div class="card-header"><h3>CWA 侵臺路徑分類（9 類）</h3></div>
      <div class="table-wrapper">
        <table class="data-table">
          <thead><tr><th>路徑類型</th><th>說明</th><th>樣本數</th></tr></thead>
          <tbody>
            <tr><td><span class="cat-badge">1</span></td><td>通過台灣北部海面向西移動</td><td>23</td></tr>
            <tr><td><span class="cat-badge">2</span></td><td>通過台灣北部（含登陸）</td><td>29</td></tr>
            <tr><td><span class="cat-badge">3</span></td><td>通過台灣中部（含登陸）</td><td>30</td></tr>
            <tr><td><span class="cat-badge">4</span></td><td>通過台灣南部（含登陸）</td><td>21</td></tr>
            <tr><td><span class="cat-badge">5</span></td><td>通過台灣南部海面向西移動</td><td>30</td></tr>
            <tr><td><span class="cat-badge">6</span></td><td>沿台灣東岸或東部海面北上</td><td>30</td></tr>
            <tr><td><span class="cat-badge">7</span></td><td>通過台灣海峽北上</td><td>11</td></tr>
            <tr><td><span class="cat-badge">8</span></td><td>通過台灣南端海面向東北移動</td><td>6</td></tr>
            <tr><td><span class="cat-badge">9</span></td><td>特殊路徑或對台灣無侵襲但有影響</td><td>18</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 系統狀態 -->
    <div class="card">
      <div class="card-header"><h3>系統狀態</h3></div>
      <div v-if="status" style="font-size: 0.9rem;">
        <p>服務狀態：<span class="badge badge-success">{{ status.status }}</span></p>
        <p style="margin-top: 0.5rem;">版本：{{ status.version }}</p>
        <p style="margin-top: 0.5rem;">已載入模型：{{ status.models_loaded?.join(', ') || '—' }}</p>
      </div>
      <div v-else>
        <p class="hint">連線中...</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import api from '../api'

const status = ref(null)

onMounted(async () => {
  try {
    const res = await api.health()
    status.value = res.data
  } catch (e) {
    status.value = { status: 'error', version: '-', models_loaded: [] }
  }
})
</script>
