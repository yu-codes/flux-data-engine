# Coastline RRF 融合相似度方法

> 以「海岸線範圍內絕對位置相似度」為主訊號，融合 KNN 與可選降水排名的三訊號投票法。
> Leave-One-Out 評估準確率 **82.3% (163/198)**，為目前所有方法中最高。

---

## 1. 動機

先前的 `coastline`（絕對位置相似度）能找出**地圖上最貼近**的歷史颱風路徑，
但它只看路徑幾何，忽略了風速 / 氣壓 / 強度等摘要特徵，以及事件降水訊號。

`coastline_rrf` 在 `coastline` 的基礎上，用 **Reciprocal Rank Fusion (RRF)**
再納入兩組候選排名，做三訊號投票：

1. **Coastline 排名（主訊號，權重 0.80）** — 絕對位置 / Chamfer 距離
2. **KNN 排名（權重 0.20）** — 11 維摘要特徵歐式距離
3. **Rainfall 排名（可選）** — 指定地區事件降水相近優先

> 設計原則：絕對位置占極高比重（0.8），確保「畫面上最接近」主導結果；
> KNN 作為輔助修正，把幾何接近但強度/特徵明顯不同的颱風往後排。

---

## 2. 計算範圍：先裁切，再運算

所有訊號都只在 **「台灣海岸線向外擴張 buffer_km（預設 500km）」** 的範圍內計算：

- 每條颱風路徑**先裁切**出落在範圍內的點，才開始特徵擷取與相似度計算。
- Coastline：對裁切後的路徑算對稱平均最近點距離（Chamfer）。
- KNN：摘要特徵（min_distance / max_wind / 路徑 / DTW window…）皆只在範圍內計算。

`buffer_km` 可在前端調整（50–2000km），改變時自動重算參考特徵。

---

## 3. 演算法

```
給定查詢路徑 Q：
  1. 取 Coastline 排名：clip(Q) 與每條 clip(候選) 的 Chamfer 距離 → 升冪排名
  2. 取 KNN 排名：Q 的 11 維特徵向量與參考向量歐式距離 → 升冪排名
  3.（可選）取 Rainfall 排名：與 Q 預期降水量差距 → 升冪排名
  4. RRF 融合：
       score(t) = w_coast / (rrf_k + rank_coast(t))
                + w_knn   / (rrf_k + rank_knn(t))
                + w_rain  / (rrf_k + rank_rain(t))   # 可選
  5. 取分數前 k 名 → 交給類比模型做距離加權投票，輸出侵臺路徑分類
```

RRF 的好處：不需要把不同量級的距離（km vs. 標準化特徵 vs. mm）正規化到同一尺度，
只用**名次**融合，穩定且對離群值不敏感。

---

## 4. 最佳化參數

由 LOO 網格搜尋（固定 pool=60）得到：

| 參數 | 值 | 說明 |
|---|---|---|
| `weight_coastline` | **0.80** | 絕對位置主訊號（占極高比重） |
| `weight_knn` | 0.20 | KNN 輔助 |
| `weight_rainfall_rrf` | 0.08 | 降水（可選，啟用時生效） |
| `rrf_k` | 60 | RRF 平滑常數 |
| `pool_size_factor` | 12 | 候選池 = k × 12 |
| `buffer_km` | 500 | 計算範圍（海岸線外擴） |

網格搜尋摘要（部分）：

| w_coast : w_knn | rrf_k | 準確率 |
|---|---|---|
| 0.8 : 0.2 | **60** | **82.3%** ✅ |
| 0.7 : 0.3 | 20/30 | 81.8% |
| 0.75 : 0.25 | 30/60 | 81.8% |
| 0.6 : 0.4 | 10 | 81.8% |
| 0.9 : 0.1 | 60 | 78.8% |

> 觀察：coastline 權重過高（0.9）反而下降——適度的 KNN 修正有幫助；
> 但 coastline 仍須主導（0.8 最佳）。

---

## 5. 評估結果（Leave-One-Out, Cat 1–9, 198 筆）

**總準確率：82.3% (163/198)** — 所有方法最高。

| 方法 | 準確率 |
|---|---|
| **coastline_rrf（本方法）** | **82.3%** |
| rule_based | 79.8% |
| combined_rainfall | 78.8% |
| combined_optimized | 78.8% |
| coastline | 73.2% |
| combined（原版） | 72.2% |
| knn_optimized | 66.2% |

各分類準確率：

| 分類 | 正確/總數 | 準確率 |
|---|---|---|
| 1 | 21/23 | 91% |
| 2 | 24/29 | 83% |
| 3 | 24/30 | 80% |
| 4 | 19/21 | 90% |
| 5 | 27/30 | 90% |
| 6 | 29/30 | 97% |
| 7 | 4/11 | 36% |
| 8 | 2/6 | 33% |
| 9 | 13/18 | 72% |

> 主要侵臺類型（1–6）準確率 80–97%；Cat 7/8（樣本少、路徑特殊）仍是難點。

相較單一 coastline（73.2%）與 KNN（66.2%），融合後顯著提升 **+9.1pp**。

---

## 6. 使用方式

### 前端（即時預測）
即時預測頁選「⭐ 海岸線 RRF 融合（絕對位置＋KNN＋降水）」，
可調整「計算範圍（海岸線外擴 km）」與「使用降水資料」。
地圖會凸顯範圍內（實際參與計算）的路徑段。

### API
```bash
POST /api/typhoon/predict
{
  "track": [{"latitude":15,"longitude":135,"wind_kt":25}, ...],
  "method": "coastline_rrf",
  "k": 5,
  "buffer_km": 500,
  "use_rainfall": false
}
```

### 重現評估
```bash
python experiments/typhoon/all_cases/exp007_coastline_rrf/run.py
```
結果輸出於 `experiments/typhoon/all_cases/exp007_coastline_rrf/predictions/`，
並可在前端「實驗結果」頁查看。

---

## 7. 相關程式

- 相似度實作：`data_pipiline/stage05_model_training/typhoon/similarity/coastline_rrf.py`
- 絕對位置子訊號：`data_pipiline/stage05_model_training/typhoon/similarity/coastline.py`
- 海岸線 / 緩衝幾何：`data_pipiline/stage00_data_ingestion/typhoon/coastline.py`
- 管道接線：`data_pipiline/stage09_inference_pipeline/typhoon/predict.py`
- 評估腳本：`experiments/typhoon/all_cases/exp007_coastline_rrf/run.py`
