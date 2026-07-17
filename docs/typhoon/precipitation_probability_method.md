# 颱風降水機率分布預測方法（Track-relative Analog Ensemble）

> 對應功能：即時預測頁「降水機率分布地圖」
> 相關程式：
> - 前處理：[`scripts/build_precip_composite.py`](../../scripts/build_precip_composite.py)
> - 模型：[`data_pipiline/stage08_downstream_analysis/typhoon/precip_analog.py`](../../data_pipiline/stage08_downstream_analysis/typhoon/precip_analog.py)
> - API：`POST /api/typhoon/precipitation_forecast`（[`backend/routers/typhoon.py`](../../backend/routers/typhoon.py)）
> - 前端：[`frontend/src/components/PrecipProbMap.vue`](../../frontend/src/components/PrecipProbMap.vue)

---

## 1. 目標

給定一條查詢颱風路徑（一連串颱風中心經緯度，可含近中心風速），
估計**台灣各地在颱風接近過程中，降水達到各強度門檻的機率分布**，
並以近似動畫的方式，隨颱風中心位置的移動連續呈現機率場的演變。

輸出兩種可切換的量：

- **超越機率** $P(R \ge \tau)$：某地小時降水達到門檻 $\tau$（mm/hr）的機率。
- **期望降水強度** $E[R]$：某地的期望小時降水量（mm/hr）。

---

## 2. 資料來源

| 資料 | 內容 | 來源 |
|------|------|------|
| 降水場 | ERA5 `total_precipitation`（tp），每小時，0.25° | CDS / ERA5 reanalysis-era5-single-levels |
| 颱風路徑 | 侵臺颱風 best-track（IBTrACS 對齊），3 小時 | `data/typhoon/cleaned/typhoons_cleaned.json` |

ERA5 降水網格涵蓋台灣周邊 **27（緯）× 23（經）= 621** 個格點
（緯度 20–26.5°N、經度 118–123.5°E，解析度 0.25°）。
`tp` 原始單位為公尺、屬每小時累積量，換算 `mm/hr = tp × 1000`。

共 **207 個颱風**、每個颱風下載其生命期涵蓋月份的每小時降水場。

---

## 3. 前處理與時空對齊

前處理由 [`scripts/build_precip_composite.py`](../../scripts/build_precip_composite.py) 完成，
產出類比集合資料庫 `data/typhoon/preprocessed/precip_analog.npz`。

### 3.1 時間對齊（temporal alignment）

best-track 為 3 小時解析度，ERA5 為每小時。採用**時間線性內插**，
將颱風中心 $(\phi, \lambda)$ 與近中心風速沿時間內插到每個 ERA5 整點：

$$
\phi(t) = \phi_k + (\phi_{k+1}-\phi_k)\,\frac{t-t_k}{t_{k+1}-t_k},\quad t_k \le t < t_{k+1}
$$

線性內插是氣象學界對颱風路徑加密的標準作法（IBTrACS、R-CLIPER 皆採此法），
在 3 小時內颱風近似等速移動的假設下誤差可忽略。

### 3.2 空間對齊（spatial alignment）

降水場**保留在絕對台灣網格上**，不做「以颱風為中心」的旋轉平移。
理由：台灣降水受**地形（中央山脈）**強烈調控，同一颱風相對位置在迎風面
與背風面降水差異極大。若採純 storm-relative 疊合會抹除地形雨特徵。
因此本方法改為「**保留絕對網格、以颱風位置為條件**」的作法（見第 4 節），
等同「以颱風位置為條件的降水氣候學」(position-conditioned rainfall climatology)。

### 3.3 樣本篩選與壓縮

- 僅保留颱風中心距台灣中心 $\le 1200$ km 的時刻（涵蓋接近全過程，含「乾樣本」，
  使遠距離時的低降水機率也能被正確估計）。
- 降水場量化為 `uint16 = round(mm/hr × 100)`，壓縮後資料庫約 **17 MB**、
  共約 **29,836 個「颱風-小時」樣本**，可完整載入記憶體供即時查詢。

資料庫欄位：

| 欄位 | 形狀 | 說明 |
|------|------|------|
| `grid_lat`, `grid_lon` | (27,), (23,) | 台灣網格座標（緯度由南到北遞增）|
| `storm_lat`, `storm_lon` | (H,) | 每個樣本時刻的颱風中心位置 |
| `storm_wind` | (H,) | 近中心風速 (kt)，缺值 NaN |
| `field` | (H, 27, 23) | 每小時降水場 `uint16`（mm/hr × 100）|
| `typhoon_id` | (H,) | 樣本所屬颱風編號 |

---

## 4. 演算法：軌跡相對類比集合（Track-relative Analog Ensemble）

核心概念：**給定颱風位於位置 $S$，台灣某地的降水分布，近似於歷史上
「颱風曾位於 $S$ 附近」時，該地降水的經驗分布。**

### 4.1 類比權重

對查詢颱風位置 $S=(\phi_S,\lambda_S)$，計算資料庫中每個歷史樣本 $i$ 的颱風中心
$S_i$ 與 $S$ 的**大圓距離** $d_i = \mathrm{haversine}(S, S_i)$，
以**高斯核**給定權重：

$$
w_i = \exp\!\left(-\tfrac{1}{2}\left(\frac{d_i}{h}\right)^2\right)
$$

其中 $h$ 為位置頻寬（預設 150 km），並在 $d_i > 3h$ 處硬截斷以加速。

（可選）若查詢提供近中心風速 $V$，再乘上**強度相似度**權重，
使類比更偏向強度相近的歷史時刻：

$$
w_i \leftarrow w_i \cdot \exp\!\left(-\tfrac{1}{2}\left(\frac{V_i - V}{\sigma_V}\right)^2\right),\quad \sigma_V = 25\ \text{kt}
$$

### 4.2 機率與期望值估計

對每個台灣格點 $g$，以核加權的經驗分布估計：

$$
\hat{P}(R_g \ge \tau \mid S) = \frac{\sum_i w_i \,\mathbb{1}[R_{i,g} \ge \tau]}{\sum_i w_i},
\qquad
\hat{E}[R_g \mid S] = \frac{\sum_i w_i \, R_{i,g}}{\sum_i w_i}
$$

其中 $R_{i,g}$ 為樣本 $i$ 在格點 $g$ 的小時降水量。

有效樣本數（衡量估計可靠度）：

$$
n_\text{eff} = \frac{\left(\sum_i w_i\right)^2}{\sum_i w_i^2}
$$

前端會顯示 $n_\text{eff}$；典型接近台灣時約有數千個有效類比樣本，統計上相當穩健。

### 4.3 沿路徑的動態呈現

查詢路徑（折線）以**累積大圓弧長等分**內插為 $N$ 個等距位置（frame）：

$$
\{S_1, S_2, \dots, S_N\}
$$

對每個 $S_j$ 各算一次機率場，前端即可逐格播放，形成颱風移動時降水機率場
連續演變的動畫；使用者亦可**直接拖曳颱風中心**至任意位置，即時查詢該位置
對應的機率場（拖曳模式呼叫同一 API 的單點查詢）。

---

## 5. 學理依據

本方法建立在三個成熟的氣象統計基礎上：

1. **Analog Ensemble（AnEn）** — Delle Monache et al. (2013), *Monthly Weather Review*,
   「Probabilistic Weather Prediction with an Analog Ensemble」。以歷史相似情況的
   觀測分布作為機率預報，是本方法的核心框架。

2. **R-CLIPER 降水氣候模式** — Tuleya et al. (2007), *Weather and Forecasting*。
   熱帶氣旋降水可用「以氣旋為參考的歷史降水氣候學」估計。本方法將其 1D 徑向剖面
   推廣為 2D、且**以絕對網格保留地形**的版本。

3. **核密度加權的條件氣候學（conditional climatology）** — 以高斯核對颱風位置加權，
   等價於估計 $P(R \mid \text{颱風位置})$ 的條件機率密度，是無母數統計的標準作法。

保留絕對台灣網格的設計，反映台灣颱風降水以**地形強迫**為主導的物理事實
（迎風面顯著增強、背風面減弱），是本方法相對於純 storm-relative 疊合的關鍵優點。

---

## 6. API 介面

`POST /api/typhoon/precipitation_forecast`

請求（擇一提供 `track` 或 `positions`）：

```json
{
  "track": [{"latitude": 22.5, "longitude": 122.0, "wind_kt": 80}, ...],
  "positions": [{"latitude": 23.5, "longitude": 121.0}],
  "steps": 20,
  "thresholds": [1, 5, 10, 20, 30],
  "bandwidth_km": 150,
  "use_wind": true
}
```

回應（重點欄位）：

```json
{
  "grid_lat": [...], "grid_lon": [...], "grid_shape": [27, 23], "cell_deg": 0.25,
  "thresholds": [1, 5, 10, 20, 30],
  "n_database_hours": 29836,
  "coastline": [{"lat": .., "lon": ..}, ...],
  "frames": [
    {
      "step": 0, "lat": 22.5, "lon": 122.0, "wind_kt": 80.0,
      "n_analogs": 10207, "n_effective": 3940.8,
      "expected": [ /* 621 個格點的期望降水 mm/hr */ ],
      "prob": { "5.0": [ /* 621 個格點 P(R>=5) */ ], ... }
    }
  ]
}
```

格點值以 row-major 攤平（`idx = yi * 23 + xi`，`yi` 對應 `grid_lat`、`xi` 對應 `grid_lon`）。

---

## 7. 前端呈現

[`PrecipProbMap.vue`](../../frontend/src/components/PrecipProbMap.vue) 以 SVG 等距投影
繪製台灣海岸線與降水機率熱區，特點：

- **連續場外觀**：對熱區套用高斯模糊濾鏡，使離散格點呈現連續機率場的視覺效果。
- **雙模式切換**：超越機率 $P(R\ge\tau)$（可選門檻）／期望降水 $E[R]$。
- **動畫播放**：沿颱風路徑逐格播放（播放／暫停／時間軸拖桿）。
- **可拖曳颱風中心**：拖曳標記至任意位置，即時查詢該位置的機率場。
- **色階**：白→淺藍→藍→綠→黃→橙→紅（降水標準色帶）；並顯示有效類比樣本數。

---

## 8. 參數與限制

| 參數 | 預設 | 說明 |
|------|------|------|
| `bandwidth_km` | 150 | 位置高斯核頻寬；越小越貼近局部、樣本越少 |
| `thresholds` | 1,5,10,20,30 mm/hr | 小時降水強度門檻 |
| `steps` | 20 | 路徑內插動畫格數 |
| `use_wind` | true | 是否加入強度相似度加權 |
| 樣本保留半徑 | 1200 km | 前處理時保留的颱風-台灣距離上限 |

**限制與未來工作：**

- 目前以**小時瞬時**降水強度估計；可延伸為事件**累積雨量**的機率分布。
- 未考慮颱風**移動速度／方向**與**環境駛流**等條件，可作為額外的類比篩選維度。
- ERA5（0.25°, ~27 km）對極端地形降水仍偏平滑；如需更細緻可改用高解析度重分析或雷達估計。
- 類比樣本量對罕見的強降水門檻（如 ≥30 mm/hr）較少，機率估計不確定性較高，
  介面已提供 $n_\text{eff}$ 供判讀可靠度。

---

## 9. 重建流程

```bash
# 1. （已完成）下載 ERA5 降水資料
python scripts/download_era5_precipitation.py

# 2. 建立類比集合資料庫（產出 precip_analog.npz，約 17 MB）
python scripts/build_precip_composite.py

# 3. 啟動後端（啟動時自動載入資料庫）
uvicorn backend.main:app --host 0.0.0.0 --port 38000

# 4. 前端即時預測頁 → 執行預測 → 檢視「降水機率分布地圖」
```
