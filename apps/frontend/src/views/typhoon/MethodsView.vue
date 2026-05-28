<template>
  <div>
    <h1 class="page-title">預測方法</h1>
    <p class="page-subtitle">各災害類型的預測方法說明與技術細節</p>

    <!-- 災害類型分頁 -->
    <div class="tabs">
      <div class="tab active">颱風</div>
      <div class="tab disabled">洪水（開發中）</div>
      <div class="tab disabled">地震（開發中）</div>
    </div>

    <!-- 方法切換 -->
    <div class="tabs" style="margin-top: 0.5rem;">
      <div class="tab" :class="{active: activeMethod === 'combined'}" @click="activeMethod = 'combined'">Combined RRF</div>
      <div class="tab" :class="{active: activeMethod === 'rulebased'}" @click="activeMethod = 'rulebased'">Rule-Based Classification</div>
    </div>

    <!-- Combined RRF -->
    <div v-show="activeMethod === 'combined'">
      <div class="card">
        <div class="card-header"><h3>Combined RRF（KNN + DTW + Rule-Based 排名融合）</h3></div>
        <p style="margin-bottom: 1.5rem;">結合三組獨立的排名信號（KNN 特徵距離、DTW 時序對齊、Rule-Based 幾何規則），使用 Reciprocal Rank Fusion 融合後進行加權投票預測。</p>

        <h4>三組排名信號</h4>

        <div class="pipeline-step">
          <div class="step-num">A</div>
          <div class="step-content">
            <div class="step-title">KNN 排名（摘要特徵距離）</div>
            <div class="step-desc">
              提取 11 維特徵向量：min_distance, closest_lat/lon, approach_heading, mean_wind, max_wind, mean_pressure, track_length, duration_hours, speed, curvature。
              StandardScaler 標準化後計算歐式距離，得出排名 (rank_knn)。
              <br><strong>權重:</strong> α = 0.10
            </div>
          </div>
        </div>

        <div class="pipeline-step">
          <div class="step-num">B</div>
          <div class="step-content">
            <div class="step-title">DTW 排名（時序路徑對齊）</div>
            <div class="step-desc">
              對 500km context window 內的 4 維時序 [r, θ, wind, pressure] 做 Dynamic Time Warping。<br>
              <strong>環形角度距離：</strong> d(θ₁,θ₂) = min(|θ₁-θ₂|, 2π-|θ₁-θ₂|)<br>
              <strong>物理標準化：</strong> [r/300, θ/π, wind/100, pressure/50]<br>
              <strong>DTW 維度權重：</strong> [1.0, 0.5, 1.0, 0.5] (r, θ, wind, pressure)<br>
              <strong>Sakoe-Chiba Band：</strong> 30% 限制時間彎曲量<br>
              <strong>RRF 權重:</strong> w_dtw = 0.50
            </div>
          </div>
        </div>

        <div class="pipeline-step">
          <div class="step-num">C</div>
          <div class="step-content">
            <div class="step-title">Rule-Based 排名（規則分類優先度）</div>
            <div class="step-desc">
              使用 CWA 分類規則對所有颱風預先分類。<br>
              <strong>排名規則：</strong> 與查詢颱風同分類的候選排在前面 (rank 0~N-1)，不同分類排在後面 (rank N~M-1)。
              這樣使 RRF 傾向選擇路徑型態相同的歷史颱風。<br>
              <strong>權重:</strong> rule_weight = 0.40
            </div>
          </div>
        </div>

        <div class="pipeline-step">
          <div class="step-num">RRF</div>
          <div class="step-content">
            <div class="step-title">Reciprocal Rank Fusion 融合公式</div>
            <div class="step-desc">
              <code>score(t) = α/(k+rank_knn) + w_dtw/(k+rank_dtw) + rule_weight/(k+rank_rule)</code><br>
              其中 α=0.10, w_dtw=0.50, rule_weight=0.40, k=30（平滑常數）
            </div>
          </div>
        </div>

        <div class="pipeline-step">
          <div class="step-num">D</div>
          <div class="step-content">
            <div class="step-title">Top-K 投票預測</div>
            <div class="step-desc">
              1. 取 RRF 分數最高的 K=5 筆颱風<br>
              2. 以距離倒數加權投票：w_i = 1/(distance_i + ε)<br>
              3. 選擇加權投票最多的分類作為預測結果
            </div>
          </div>
        </div>

        <h4>核心參數</h4>
        <div class="table-wrapper">
          <table class="data-table">
            <thead><tr><th>參數</th><th>值</th><th>說明</th></tr></thead>
            <tbody>
              <tr><td>alpha (w_knn)</td><td>0.10</td><td>KNN 排名權重</td></tr>
              <tr><td>rule_weight</td><td>0.40</td><td>Rule-Based 排名權重</td></tr>
              <tr><td>w_dtw</td><td>0.50</td><td>DTW 排名權重 (1 - 0.10 - 0.40)</td></tr>
              <tr><td>rrf_k</td><td>30</td><td>RRF 平滑常數</td></tr>
              <tr><td>k (Top-K)</td><td>5</td><td>最終選取的類比颱風數</td></tr>
              <tr><td>impact_radius_km</td><td>500</td><td>Context window 半徑</td></tr>
              <tr><td>DTW weights</td><td>[1.0, 0.5, 1.0, 0.5]</td><td>DTW 維度權重 [r, θ, wind, pressure]</td></tr>
            </tbody>
          </table>
        </div>

        <h4 style="margin-top: 1.5rem;">方法比較</h4>
        <div class="table-wrapper">
          <table class="data-table">
            <thead><tr><th>方法</th><th>準確率</th><th>核心技術</th><th>適用場景</th></tr></thead>
            <tbody>
              <tr>
                <td>Combined RRF 優化版</td>
                <td><span class="badge badge-success">79.8%</span></td>
                <td>KNN + DTW + Rule-Based 融合，最佳化參數</td>
                <td>推薦使用，最佳綜合表現</td>
              </tr>
              <tr>
                <td>Rule-Based</td>
                <td><span class="badge badge-success">79.8%</span></td>
                <td>路徑幾何分析 + CWA 規則</td>
                <td>可解釋性最佳，無需訓練</td>
              </tr>
              <tr>
                <td>Combined RRF 原版</td>
                <td><span class="badge badge-warning">74.7%</span></td>
                <td>KNN + DTW + Rule-Based 融合，預設參數</td>
                <td>基線方法比較</td>
              </tr>
              <tr>
                <td>KNN 優化版</td>
                <td><span class="badge badge-warning">63.6%</span></td>
                <td>3 顯著特徵加權 KNN</td>
                <td>快速計算，特徵可解釋</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Rule-Based Method -->
    <div v-show="activeMethod === 'rulebased'">
      <div class="card">
        <div class="card-header"><h3>Rule-Based Classification（幾何規則分類）</h3></div>
        <p style="margin-bottom: 1.5rem;">純規則式分類：基於 CWA 官方路徑分類定義，使用軌跡的幾何特徵（穿越判斷、登陸地點、接近方向、位置）直接判定路徑類型。結果 100% 可解釋，無需訓練資料。</p>

        <h4>分類決策樹（優先順序由高到低）</h4>

        <div class="pipeline-step">
          <div class="step-num">1</div>
          <div class="step-content">
            <div class="step-title">分類 6：沿東岸/東部海面北上</div>
            <div class="step-desc">
              不穿越台灣西側 · 最近點經度 &gt; 120.8° · Context 方向 40°~130°（北行）· 最近點緯度 &lt; 26.0° · 東側比例 &gt; 30%
            </div>
          </div>
        </div>

        <div class="pipeline-step">
          <div class="step-num">2</div>
          <div class="step-content">
            <div class="step-title">分類 8：南部海面向東北</div>
            <div class="step-desc">
              進入點緯度 &lt; 22.0° · Context 方向 &lt; 75°（東北行）· 不穿越西側 · 東移 &gt; 2.0° · 最近點經度 &gt; 121.0° · 距離 &gt; 80km
            </div>
          </div>
        </div>

        <div class="pipeline-step">
          <div class="step-num">3</div>
          <div class="step-content">
            <div class="step-title">分類 2/3/4/7/9：登陸文字精確解析</div>
            <div class="step-desc">
              <strong>Cat 2（北部）：</strong>基隆、宜蘭、蘇澳、北部 · <strong>Cat 3（中部）：</strong>花蓮、秀姑巒、台中 · <strong>Cat 4（南部）：</strong>台東、屏東、恆春<br>
              <strong>Cat 7（海峽北上）：</strong>南方進入 + 西岸登陸 + 北行 · <strong>Cat 9（特殊）：</strong>無法歸類
            </div>
          </div>
        </div>

        <div class="pipeline-step">
          <div class="step-num">4</div>
          <div class="step-content">
            <div class="step-title">分類 7：無登陸但南方北上</div>
            <div class="step-desc">
              進入點緯度 &lt; 22.0° · 最近點經度 &lt; 121.0°（西側）· Context 方向 70°~140° · 北移明顯
            </div>
          </div>
        </div>

        <div class="pipeline-step">
          <div class="step-num">5</div>
          <div class="step-content">
            <div class="step-title">分類 1/5：海面通過（距離 &gt; 100km）</div>
            <div class="step-desc">
              <strong>Cat 1（北海面）：</strong>最近點緯度 &gt; 23.5° · <strong>Cat 5（南海面）：</strong>最近點緯度 &lt; 23.5°
            </div>
          </div>
        </div>

        <div class="pipeline-step">
          <div class="step-num">6</div>
          <div class="step-content">
            <div class="step-title">分類 9：Fallback</div>
            <div class="step-desc">以上皆不符合 → 分類為特殊路徑</div>
          </div>
        </div>

        <h4 style="margin-top: 1.5rem;">幾何計算項目</h4>
        <div class="table-wrapper">
          <table class="data-table">
            <thead><tr><th>計算項目</th><th>定義</th><th>用途</th></tr></thead>
            <tbody>
              <tr><td>Haversine 距離</td><td>軌跡每點到台灣中心的大圓距離</td><td>找最近點、判斷穿越、判斷距離</td></tr>
              <tr><td>Context heading</td><td>500km window 內路徑的整體方向</td><td>判斷進出方向</td></tr>
              <tr><td>Approach heading</td><td>最近點前 3-8 步的平均方向</td><td>判斷接近方向</td></tr>
              <tr><td>Crossed to west</td><td>軌跡是否穿越 lon=120.3° 以西</td><td>區分 Cat 6 vs Cat 2/3/4</td></tr>
              <tr><td>登陸地點解析</td><td>從文字提取關鍵字判斷區域</td><td>精確判定北/中/南登陸</td></tr>
            </tbody>
          </table>
        </div>

        <h4 style="margin-top: 1.5rem;">分類條件速查表</h4>
        <div class="table-wrapper">
          <table class="data-table">
            <thead><tr><th>分類</th><th>主要條件</th><th>優先級</th></tr></thead>
            <tbody>
              <tr><td><span class="cat-badge">6</span></td><td>東側北行 + 不穿越西側</td><td>最高</td></tr>
              <tr><td><span class="cat-badge">8</span></td><td>南方東北行 + 海面</td><td>次高</td></tr>
              <tr><td><span class="cat-badge">2</span></td><td>登陸北部 或 lat≥24.0</td><td>高</td></tr>
              <tr><td><span class="cat-badge">3</span></td><td>登陸中部 或 23.0&lt;lat&lt;24.0</td><td>高</td></tr>
              <tr><td><span class="cat-badge">4</span></td><td>登陸南部 或 lat≤23.0</td><td>高</td></tr>
              <tr><td><span class="cat-badge">7</span></td><td>南方西側北上 或 海峽北上</td><td>中</td></tr>
              <tr><td><span class="cat-badge">1</span></td><td>北海面通過 (lat&gt;23.5, dist&gt;100km)</td><td>低</td></tr>
              <tr><td><span class="cat-badge">5</span></td><td>南海面通過 (lat&lt;23.5, dist&gt;100km)</td><td>低</td></tr>
              <tr><td><span class="cat-badge">9</span></td><td>以上皆不符</td><td>Fallback</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

const activeMethod = ref('combined')
</script>
