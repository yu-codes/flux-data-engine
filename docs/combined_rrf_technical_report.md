# Combined RRF 颱風路徑類比預測方法 — 技術報告

## 摘要

本報告描述一套基於 Reciprocal Rank Fusion (RRF) 的颱風路徑類比預測方法。該方法融合三種獨立的相似度排名信號——KNN 特徵距離排名、DTW 時序對齊排名、Rule-Based 幾何規則排名——透過 RRF 機制產生最終綜合排名，再以 Top-K 投票預測颱風侵臺路徑類型。在 198 筆具有 CWA 分類標籤的颱風上達到 **79.8% Leave-One-Out 準確率**。

此外，系統提供**可選的第四個排名信號——降水相似度（Rainfall）**：當啟用 `use_rainfall` 時，依指定地區（`rainfall_region`，如 `tn`=臺南、`kh`=高雄）的事件降水量將相近降水的歷史颱風納入相似度排名，作為以「降水影響」為導向的類比檢索。關閉時行為與原三信號版本完全一致。

> **資料源統一**：自本版起，全系統（loader / 相似度 / 推論 / 後端）統一讀取單一資料源 `data/typhoon/preprocessed/typhoons_overview.json`。軌跡取自 `path.position_intensity`，事件降水取自 `event_rain_tn` / `event_rain_kh`（依地區）。不再依賴舊的 `typhoons_with_tracks.json` 與 CSV 降水檔。

---

## 1. 問題定義

**目標**：給定一條颱風軌跡（時序座標 + 風速 + 氣壓），預測其 CWA 侵臺路徑類型（9 類）。

**挑戰**：
- 樣本量僅 198 筆（有分類標籤者），不適合深度學習
- 軌跡長度不一致（6~80 個觀測點）
- 需要同時考量軌跡幾何形狀（方向、位置）與氣象強度（風速、氣壓）

**資料集**：
| 項目 | 規格 |
|------|------|
| 來源 | `typhoons_overview.json`（IBTrACS 軌跡 + CWA 路徑分類 + 事件降水） |
| 載入範圍 | 有路徑分類（1–9 或「特殊」）且具軌跡者，共 207 筆；其中 198 筆為 Cat 1–9 |
| 軌跡欄位 | `path.position_intensity`：(timestamp_utc, latitude, longitude, wind_kt, pressure_mb) |
| 降水欄位 | `event_rain_tn`（臺南）、`event_rain_kh`（高雄），各約 198 筆有值 |
| 時間 | 1958–2025 |
| 分類 | 9 類 CWA 侵臺路徑 |
| 觀測 | 6h 間隔 |

> 載入過濾條件 `分類非空 ∧ 具軌跡點` 與舊資料集 `typhoons_with_tracks.json` 的 207 筆評估池完全一致，故 79.8% 基準維持不變。

---

## 2. 系統架構

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Feature        │     │  Similarity     │     │  Prediction     │
│  Extraction     │ ──→ │ Engine (×3 +R)  │ ──→ │  (Top-K Vote)   │
└─────────────────┘     └─────────────────┘     └─────────────────┘
        │                       │
        ▼                       ▼
┌─────────────────┐     ┌─────────────────┐
│ 11-dim Summary  │     │ Rank Fusion     │
│ + 4-dim Series  │     │ (RRF)           │
└─────────────────┘     └─────────────────┘
```

### 2.1 特徵提取 (Feature Extraction)

從原始軌跡提取兩類特徵：

#### A. 摘要特徵（11 維向量）

以台灣中心 (23.5°N, 121.0°E) 為參考點，在 **500km impact window** 內計算：

| # | 特徵名稱 | 計算方式 | 物理意義 |
|---|---------|---------|---------|
| 1 | min_distance_to_taiwan | Haversine 最近距離 (km) | 颱風與台灣的最接近程度 |
| 2 | closest_lat | 最近點緯度 | 通過位置（北/中/南） |
| 3 | closest_lon | 最近點經度 | 通過位置（東/西） |
| 4 | approach_heading | 進入 impact window 時的方向角 | 接近方向 |
| 5 | mean_wind | impact window 內平均風速 (kt) | 平均強度 |
| 6 | max_wind | 全軌跡最大風速 (kt) | 峰值強度 |
| 7 | mean_pressure | impact window 內平均氣壓 (mb) | 系統深度 |
| 8 | track_length | 全軌跡累積距離 (km) | 路徑長度 |
| 9 | duration_hours | 全軌跡持續時間 (h) | 持續時間 |
| 10 | speed | track_length / duration_hours | 平均移速 |
| 11 | curvature | 路徑曲率中位數 | 路徑彎曲程度 |

#### B. 時序特徵（4 維 × T 步）

在 500km context window 內，對每個觀測點計算極座標：

$$\mathbf{x}_t = \left[ \frac{r_t}{300}, \frac{\theta_t}{\pi}, \frac{w_t}{100}, \frac{p_t}{50} \right]$$

其中：
- $r_t$：到台灣中心的距離 (km)，除以 300 標準化
- $\theta_t$：相對於台灣中心的方位角 (rad)，除以 π 標準化
- $w_t$：風速 (kt)，除以 100 標準化
- $p_t$：氣壓偏差 (|1013 - pressure|)，除以 50 標準化

---

## 3. 三組排名信號

### 3.1 Signal A: KNN 排名 (Feature-Space Distance)

**方法**：對 11 維摘要特徵做 StandardScaler 標準化後，計算查詢颱風與所有歷史颱風的歐式距離。

$$d_{\text{KNN}}(q, c) = \left\| \frac{\mathbf{f}_q - \boldsymbol{\mu}}{\boldsymbol{\sigma}} - \frac{\mathbf{f}_c - \boldsymbol{\mu}}{\boldsymbol{\sigma}} \right\|_2$$

按距離由小到大排序得到 $\text{rank}_{\text{KNN}}(c)$。

**特性**：
- 計算快速 ($O(N)$ per query)
- 捕捉全域統計特徵（強度、位置、速度）
- 忽略時序路徑形狀

### 3.2 Signal B: DTW 排名 (Time-Series Alignment)

**方法**：對 context window 內的 4 維標準化時序做 Dynamic Time Warping。

**距離函式**（維度加權歐式距離，含環形角度處理）：

$$d(\mathbf{x}_i, \mathbf{y}_j) = \sqrt{\sum_{d=1}^{4} w_d \cdot \delta_d(x_{i,d}, y_{j,d})^2}$$

其中：
- $\delta_d$ 為各維度的距離函式
- 角度維度使用環形距離：$\delta_\theta(\theta_1, \theta_2) = \min(|\theta_1 - \theta_2|, 2\pi - |\theta_1 - \theta_2|) / \pi$
- 其他維度使用直接差：$\delta_d(a, b) = |a - b|$

**DTW 遞迴：**

$$D(i, j) = d(\mathbf{x}_i, \mathbf{y}_j) + \min \begin{cases} D(i-1, j) \\ D(i, j-1) \\ D(i-1, j-1) \end{cases}$$

**約束條件**：Sakoe-Chiba band = 30%（限制 warp path 在對角線 ±30% 範圍內）

**維度權重向量**：$\mathbf{w} = [1.0, 0.5, 1.0, 0.5]$

權重決定方法（見第 5 節）：透過網格搜索 10 組候選權重，在 60 筆隨機取樣上評估 LOO 準確率，選取最佳組合。

### 3.3 Signal C: Rule-Based 排名 (Geometric Classification Priority)

**方法**：使用 CWA 官方路徑分類規則，對查詢颱風執行規則式分類，然後根據「是否同類」進行排名。

**排名邏輯**：
1. 用規則將查詢颱風分類為 category $C_q$
2. 所有歷史颱風中，分類 = $C_q$ 的排在前面（rank 0 ~ N-1）
3. 分類 ≠ $C_q$ 的排在後面（rank N ~ M-1）
4. 同一類內依距離排序

**效果**：強制 RRF 傾向選擇路徑型態相同的歷史颱風，形成一種「先驗篩選」。

### 3.4 Signal D: Rainfall 排名（可選，降水影響相似度）

**動機**：路徑分類聚焦於軌跡幾何，但實務防災更關心「降水影響」。本信號將指定地區的事件降水量作為相似度的一個輸入維度，使類比檢索可同時對齊「降水規模相近」的歷史颱風。

**啟用方式**：`use_rainfall=True`，並以 `rainfall_region` 指定地區。地區對應由 stage00 的 `RAINFALL_REGIONS` 集中定義，目前支援 `tn`（臺南，對應 `event_rain_tn`）、`kh`（高雄，對應 `event_rain_kh`），新增地區僅需在該設定加入一筆即可，全系統自動沿用。

**排名邏輯**：
1. 取得查詢颱風在該地區的降水量 $R_q$（評估／既有颱風為已知值；前端新颱風則由使用者選填「預期降水量」）
2. 所有歷史颱風依 $|R_q - R_c|$ 由小到大排序得到 $\text{rank}_{\text{rain}}(c)$
3. 無該地區降水資料者排於最後

**降級行為**：若未提供查詢降水量（如前端未填預期降水），降水項自動略過，權重回歸至 DTW，結果等同未啟用降水。

---

## 4. RRF 融合

### 4.1 Reciprocal Rank Fusion 公式

$$\text{score}(c) = \frac{\alpha}{k + \text{rank}_{\text{KNN}}(c)} + \frac{w_{\text{DTW}}}{k + \text{rank}_{\text{DTW}}(c)} + \frac{w_{\text{rule}}}{k + \text{rank}_{\text{rule}}(c)} + \underbrace{\frac{w_{\text{rain}}}{k + \text{rank}_{\text{rain}}(c)}}_{\text{僅 use\_rainfall=True}}$$

其中：
- $\alpha = 0.10$（KNN 權重）
- $w_{\text{rule}} = 0.40$（Rule-Based 權重）
- $w_{\text{rain}}$（Rainfall 權重，預設 0.15；`use_rainfall=False` 時為 0）
- $w_{\text{DTW}} = 1 - \alpha - w_{\text{rule}} - w_{\text{rain}}$（剩餘配給 DTW）
- $k = 30$（平滑常數）

當 `use_rainfall=False` 時 $w_{\text{rain}}=0$、$w_{\text{DTW}}=0.50$，公式退化為原三信號版本（與 79.8% 基準完全相同）。啟用降水時，DTW 權重讓出 $w_{\text{rain}}$ 給降水信號。

### 4.2 RRF 的設計考量

**為何使用 RRF 而非加權分數融合？**
- 三種方法的分數量綱完全不同（歐式距離、DTW 距離、二元分類），無法直接比較
- RRF 只使用排名順序，天然不受量綱影響
- RRF 的平滑常數 $k$ 讓頭部排名差異被放大，尾部差異被壓縮

**平滑常數 $k$ 的作用**：
- $k$ 越大，不同排名間的分數差異越小（更平滑）
- $k$ 越小，頭部排名（rank 1-5）的影響被放大
- 經網格搜索，$k=30$ 在本資料集上表現最佳

---

## 5. 參數搜索方法

### 5.1 搜索策略

採用 **Leave-One-Out (LOO) 評估 + 網格搜索**：

```
對每組候選參數組合 (α, rule_weight, rrf_k):
    correct = 0
    for each typhoon t in dataset:
        排除 t 後，計算其 RRF 排名
        取 Top-5，多數投票
        if predicted == actual_category(t):
            correct += 1
    accuracy = correct / total
```

### 5.2 DTW 維度權重搜索

| 候選組合 [r, θ, wind, pressure] | LOO 準確率 |
|------|------|
| [1.0, 1.0, 1.0, 1.0] | 基線 |
| [1.0, 0.5, 1.0, 0.5] | **最佳** |
| [2.0, 1.0, 1.0, 0.5] | 次佳 |
| [1.0, 1.0, 1.5, 0.5] | 略低 |

**結論**：距離 (r) 與風速 (wind) 對分類最重要，角度 (θ) 與氣壓 (pressure) 貢獻較少，給予 0.5 倍權重。

### 5.3 RRF 權重搜索

搜索空間：
- $\alpha \in \{0.05, 0.10, 0.15, 0.20, 0.25, 0.30\}$
- $w_{\text{rule}} \in \{0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50\}$
- $k \in \{10, 20, 30, 60, 100\}$
- $w_{\text{DTW}} = 1 - \alpha - w_{\text{rule}}$（約束條件）

**最佳結果**：$\alpha=0.10, w_{\text{rule}}=0.40, w_{\text{DTW}}=0.50, k=30 \Rightarrow 79.8\%$

**關鍵發現**：
1. Rule-Based 權重最大 (0.40)：其幾何分類在路徑判定上具有核心優勢
2. DTW 次之 (0.50)：時序對齊捕捉路徑形狀的細微差異
3. KNN 最小 (0.10)：11 維摘要特徵在分類任務上資訊量有限
4. $k=30$ 優於 $k=60$：適度強調頭部排名差異有利於分類

### 5.4 KNN 特徵分析

透過 Pearson + Spearman 相關性分析，篩選出與路徑分類最相關的特徵：

| 特徵 | Spearman ρ | 意義 |
|------|-----------|------|
| min_distance_to_taiwan | -0.384 | 越接近台灣越可能登陸（Cat 2/3/4） |
| is_landfall | 0.296 | 是否登陸是強分類信號 |
| mean_angle | -0.208 | 通過方位角區分東/西路徑 |

---

## 6. 預測流程

```
輸入: 颱風軌跡 T = [(lat, lon, wind, pressure, time), ...]

Step 1: 特徵提取
    f_summary = extract_11d_features(T)        # 11 維摘要
    f_series  = extract_4d_series(T, 500km)    # 4D × T 時序

Step 2: 三路排名
    ranks_knn  = KNN_ranking(f_summary, database)      # O(N)
    ranks_dtw  = DTW_ranking(f_series, database)       # O(N × T²)
    ranks_rule = Rule_ranking(T, database)             # O(N)

Step 3: RRF 融合
    for each candidate c:
        score(c) = 0.10/(30+rank_knn[c]) + 0.50/(30+rank_dtw[c]) + 0.40/(30+rank_rule[c])

Step 4: Top-K 投票
    top_5 = sort_by_score(candidates)[:5]
    for each t in top_5:
        weight_t = 1 / (distance_t + ε)
        votes[category(t)] += weight_t
    prediction = argmax(votes)

輸出: predicted_category, confidence, similar_typhoons
```

---

## 7. 評估結果

### 7.1 Leave-One-Out 準確率

| 方法 | 準確率 | 正確/總計 |
|------|--------|----------|
| **Combined RRF 優化版** | **79.8%** | 158/198 |
| Rule-Based (獨立) | 79.8% | 158/198 |
| Combined RRF 原版 | 74.7% | 148/198 |
| KNN 優化版 | 63.6% | 126/198 |

### 7.2 分析

- Combined RRF 優化版與純 Rule-Based 準確率相當，但提供額外價值：
  - 輸出相似颱風列表（可供防災參考）
  - 輸出信心度分數
  - 輸出降水量推估
- Rule-Based 為確定性分類（固定規則 → 固定結果），Combined RRF 為類比式（提供 Top-K 候選）
- KNN 優化版在只用 3 個特徵的情況下仍達 63.6%，驗證特徵選擇的有效性

### 7.3 優化前後對比

| 參數 | 原版 | 優化版 |
|------|------|--------|
| α (KNN) | 0.13 | 0.10 |
| Rule Weight | 0.25 | 0.40 |
| DTW Weight | 0.62 | 0.50 |
| rrf_k | 60 | 30 |
| **準確率** | **74.7%** | **79.8%** |

優化版將 Rule-Based 權重從 0.25 提升至 0.40，反映其在路徑分類上的核心貢獻。

### 7.4 降水信號對路徑分類的影響

啟用降水信號後，類比檢索會傾向選擇「降水規模相近」的颱風。由於降水量是颱風影響的**下游結果**而非軌跡幾何信號，將其納入會略微犧牲純路徑分類準確率（屬預期現象）——其價值在於提供以降水影響為導向的類比，而非提升分類準確率：

| 設定 | 地區 | $w_{\text{rain}}$ | LOO 準確率 |
|------|------|------|------|
| 關閉（基準） | — | 0 | 79.8% (158/198) |
| 啟用 | tn（臺南） | 0.15 | 78.3% (155/198) |
| 啟用 | tn（臺南） | 0.30 | 77.3% (153/198) |
| 啟用 | kh（高雄） | 0.15 | 77.3% (153/198) |

**使用建議**：若任務目標為路徑分類，維持 `use_rainfall=False`；若目標為「找出降水影響相近的歷史颱風」，則啟用降水信號並以較小權重（0.15）平衡幾何與降水。

---

## 8. 局限性與未來方向

### 8.1 局限性

1. **樣本量有限**：198 筆有分類標籤的颱風，部分類別樣本極少（Cat 8 僅 6 筆）
2. **評估方式**：LOO 在小樣本下可能高估/低估，但已是最合理的選擇
3. **Rule-Based 的上限**：RRF 中 Rule-Based 的排名依賴規則分類器本身的準確性

### 8.2 未來方向

- 增加更多災害類型（洪水、地震）的類比模組
- 引入集成學習（Ensemble）替代 RRF 做排名融合
- 加入雷達回波或衛星雲圖特徵提升時序表徵能力
- 探索 Transfer Learning 從其他西太平洋颱風資料擴增訓練集

---

## 附錄 A: 完整參數表

| 類別 | 參數 | 值 | 決定方式 |
|------|------|-----|---------|
| RRF | α (KNN weight) | 0.10 | 網格搜索 |
| RRF | rule_weight | 0.40 | 網格搜索 |
| RRF | w_dtw | 0.50 | 1 - α - rule_weight |
| RRF | rrf_k | 30 | 網格搜索 |
| RRF | use_rainfall | False | 是否啟用降水信號 |
| RRF | rainfall_region | tn | 降水地區（tn/kh，可擴充） |
| RRF | rainfall_weight | 0.15 | 啟用時的降水信號權重 |
| DTW | 維度權重 [r, θ, w, p] | [1.0, 0.5, 1.0, 0.5] | 網格搜索 |
| DTW | Sakoe-Chiba band | 30% | 經驗設定 |
| DTW | 標準化因子 | [300, π, 100, 50] | 物理量綱 |
| KNN | 特徵數 | 11 | 完整摘要 |
| KNN | 標準化 | StandardScaler | 標準做法 |
| Feature | impact_radius_km | 500 | 氣象經驗 |
| Predict | Top-K | 5 | 經驗設定 |
| Predict | 投票權重 | 1/(distance + ε) | 距離倒數 |

## 附錄 B: 搜索空間與收斂性

網格搜索共評估 $6 \times 7 \times 5 = 210$ 組合（排除 w_dtw < 0 的無效組合）。

前 5 名結果：

| α | Rule | DTW | rrf_k | 準確率 |
|---|------|-----|-------|-------|
| 0.10 | 0.40 | 0.50 | 30 | 79.8% |
| 0.10 | 0.35 | 0.55 | 30 | 79.3% |
| 0.15 | 0.40 | 0.45 | 30 | 79.3% |
| 0.10 | 0.45 | 0.45 | 30 | 78.8% |
| 0.05 | 0.40 | 0.55 | 30 | 78.8% |

觀察到 rrf_k=30 一致優於其他值，且 rule_weight 在 0.35~0.45 範圍內表現穩定。
