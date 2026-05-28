整體來說，你這套規則已經**具備可用性（production-ready baseline）**，但如果目標是「接近 CWA 分類穩定度 + 可用於相似颱風檢索」，目前還有幾個**關鍵結構性問題**需要補強，不是微調，而是邏輯層級的精度問題。

我直接講重點（按優先順序）：

---

# 1️⃣ Impact window 定義過粗（核心問題）

```python
in_window = distances < 500
```

### 問題

* 500km 對台灣來說太大 → 會把「還沒接近就轉向」的路徑也納入
* 導致：

  * `heading` 被污染
  * `mean_lat_near` 偏移
  * 類型 6 / 1 / 5 判斷失準

### 建議（強烈要改）

改成「雙層 window」：

```python
core_window = distances < 200
context_window = distances < 400
```

然後：

* **方向 → 用 core_window**
* **路徑趨勢 → 用 context_window**

👉 這會直接提升分類穩定度一個等級

---

# 2️⃣ 移動方向判斷過於粗糙（會誤分類 6 / 8）

現在是：

```python
dlat = exit_lat - entry_lat
dlon = exit_lon - entry_lon
```

### 問題

這是假設「單一向量」，但颱風是曲線運動

典型錯誤：

* 東側北上 → 前段偏西、後段北 → 被誤判
* S型路徑 → 完全錯

---

### 建議（標準做法）

改成「平均 heading（vector mean）」：

```python
vectors = []
for i in range(len(window_lats) - 1):
    vectors.append([
        window_lons[i+1] - window_lons[i],
        window_lats[i+1] - window_lats[i]
    ])

mean_vec = np.mean(vectors, axis=0)
dlon, dlat = mean_vec
```

👉 這個改動對：

* 類型 6（東側北上）
* 類型 8（南→北）
  影響極大

---

# 3️⃣ landfall 判斷邏輯不乾淨（會誤判 2/3/4）

```python
has_landfall = landfall_location is not None ...
passes_through = min_dist < LANDFALL_DISTANCE_KM
```

### 問題

你現在混了兩種來源：

* 資料欄位（landfall_location）
* 幾何推估（distance）

但沒有一致性策略

---

### 建議（要明確策略）

👉 建議優先順序：

```
1. 如果有 landfall_location → trust data
2. 否則用「是否穿越 bounding box」判斷
3. distance 只當 fallback
```

例如：

```python
crosses_taiwan = (
    (lons.min() < TAIWAN_EAST_LON) and
    (lons.max() > TAIWAN_WEST_LON) and
    (lats.max() > TAIWAN_SOUTH_LAT) and
    (lats.min() < TAIWAN_NORTH_LAT)
)
```

👉 比單純 distance 準很多

---

# 4️⃣ 「南部海面 vs 登陸」邏輯會互相覆蓋（順序問題）

這段：

```python
if is_south_of_taiwan:
    ...
```

會在 **landfall 邏輯之前觸發**

👉 造成：

* 有些「擦南部登陸」會被分類成 5 / 7 / 8

---

### 建議（必改）

把優先順序改成：

```
1. 類型 6（特殊 pattern）
2. 無影響（9）
3. 登陸 / 穿越（2/3/4）
4. 海面分類（1/5/7/8）
```

👉 你現在是錯的順序

---

# 5️⃣ 類型 6（東側北上）判斷不夠嚴謹

```python
east_side_points = window_lons > TAIWAN_EAST_LON - 0.5
```

### 問題

* 容易把「擦東北角」誤判為 6
* 沒限制「是否真的沿岸」

---

### 建議（強化條件）

加入：

```python
np.std(window_lons) < 1.0  # 經度變化小 → 沿岸
min_dist < 150             # 真的靠近
```

👉 才是「沿東岸」

---

# 6️⃣ 緯度帶用 mean 有風險（會錯 2/3/4）

```python
mean_lat_near = np.mean(...)
```

### 問題

* 路徑斜切 → mean 會落在中部
* 但實際是「北部登陸」

---

### 建議（更準）

改成：

```python
closest_lat
```

或：

```python
weighted by inverse distance
```

👉 「最接近點」才是分類依據

---

# 7️⃣ heading 閾值寫死（0.5）不穩定

```python
dlon < -0.5
```

### 問題

* 路徑長度不同 → 這個 threshold 不成立
* slow-moving typhoon 會失效

---

### 建議

改成「角度判斷」：

```python
angle = np.degrees(np.arctan2(dlat, dlon))

is_westward = 135 < angle or angle < -135
is_northward = 45 < angle < 135
```

👉 這才是標準做法

---

# 8️⃣ Similarity 設計有一個 conceptual bug

```python
same_cat → 再用 feature vector 距離
```

### 問題

你現在是：

> 分類 → 再做距離

但 feature vector 很可能**已經包含路徑資訊**

👉 變成：

* classification 沒有真正發揮 filtering 的價值

---

### 建議（更強設計）

改成：

```
similarity = 
  0.6 * path_similarity +
  0.3 * category_match +
  0.1 * intensity_feature
```

👉 而不是 hard filter

---

# 9️⃣ 缺少「特殊路徑」的明確定義（現在是 fallback）

```python
"特殊"
```

### 問題

現在其實是：

> 分不出來 → 特殊

但 CWA 的「特殊」是有意義的

---

### 建議

至少定義幾種：

* loop（迴旋）
* U-turn
* stall（滯留）
* crossing twice

👉 否則資料會髒

---

# 10️⃣ 信心分數目前是「寫死的」

```python
confidence: 0.75
```

### 問題

沒有根據資料

---

### 建議（很簡單但有效）

```python
confidence =
    1 - (min_dist / 500) * 0.3
    + direction_consistency * 0.3
    + landfall_flag * 0.2
```

