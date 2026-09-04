# flux-data-engine

> 通用的**資料、模型與計算執行平台**。
>
> 讓異質資料被標準化、分析，並透過各種類型的可執行模型進行計算，
> 最後將結果轉化為可視化、預測或實際應用。

```text
Data → Model → Execution → Result → Application
```

**這裡的 Model 不等於 Machine Learning Model。**
Model 是「一組能接受輸入資料、執行特定計算或決策邏輯，並產生可被系統或使用者使用之輸出的可重現計算單元」。
ML 只是其中一種 Provider；**訓練是可選能力，不是模型的定義**。

---

## 1. 目前可用的功能

| 領域 | 已實作 |
|------|--------|
| **Data** | Source（CSV / Excel / JSON / NDJSON / Parquet / Database / REST API / Object Storage / Inline）→ Dataset → 不可變 DatasetVersion（Parquet）→ 自動推斷 Schema；檔案上傳；**查詢下推**（篩選／排序／聚合以 `pyarrow.compute` 執行，Explore 查詢與圖表都把投影推進 Parquet 讀取，只讀用得到的欄位）；**Pipeline**（有向無環圖，以 22 個標準 Transform 自由組合，步驟定義內嵌、可多輸入匯流，UI 依 Contract 產生參數表單；**步驟可以是另一條 Pipeline**（巢狀，存檔時擋掉循環與過深的巢狀）；**互不相依的步驟同時跑**（每個執行緒各自一個 DB session，PostgreSQL 上預設 4 條，SQLite 因單寫入者而自動關閉）) |
| **Analysis** | 欄位 Profile、多條件篩選／排序／分頁查詢、**Explore 的查詢可一鍵存成 Pipeline**（條件與排序直接變成鏈接好的步驟，可重跑、可排程）、9 種圖表型別（含 histogram／box／heatmap／stacked bar）、Visualization、可即時增刪重排的 Dashboard；每張圖都帶完整呈現資訊（軸標題、單位、副標、數值標籤、類別順序） |
| **Model** | Model Library、11 個內建 Provider（8 種 Model Type **全部**都有實作，沒有空類別）、Model Version（immutable）、**巢狀 Contract**（物件／陣列／對映欄位與條件顯示，UI 遞迴產生表單）、**Experiment（Trial 為單位：可比較 Model 或 **Pipeline**、指定參數與資料集，執行前先 check 驗證，一次送出全部 Trial）**、Leaderboard（**Experiment 宣告 `primary_direction`，RMSE 這類「越低越好」的指標不會把最差的排在第一**）、Evaluation（**跨 Experiment 比較，指標欄位由實際 run 產生**） |
| **Execution** | **Runnable 統一抽象**（`target_type` + `target_id`：Model 與 Pipeline 都能被執行、排程、比較、服務；`model_id` 只在跑 Model 時有值，跑 Pipeline 時是 `null` 而不是借用）；7 種 Execution Kind、契約驗證、logs／metrics／lineage；**inline 或 Redis 佇列**兩種執行模式；**provider 可宣告自己的 timeout 與版本**（版本寫進 lineage，同一個 snapshot 換了 provider 版本會被記下來）；**領取為單一原子條件更新**（兩個 worker 不會跑到同一筆）；**可取消**（合作式，結果落地前檢查；開放式搜尋的 provider —— optimizer 與 monte-carlo —— 在迴圈內檢查並回報 `complete`）、逾時與重試上限、失敗進 dead letter |
| **Result** | 一級 Domain 物件；table / object / classification / probability 等形態；可 materialise 成 Dataset；**Report**（可組合、可匯出 Markdown／HTML／JSON） |
| **Application** | Application（draft ⇄ published 單一生命週期，**已移除 Deployment**）；綁定 Model／Dataset／Dashboard；**有自己的頁面**（`/applications/{id}`，與分享連結共用同一個 renderer）；**綁定的 Model 會變成可用的工具** —— 依 Contract 產生表單、選它綁定的 dataset、按 Run 直接取得答案（走 `/invoke`，不落 Execution）；**分享連結**（capability URL，免帳號可讀、可隨時撤銷）；內建「颱風類比預測」應用（地圖、Top-K 路徑、降水統計） |
| **Serving** | `POST /models/{id}/invoke` 與 `POST /pipelines/{id}/invoke` 同步呼叫（不落 Execution／PipelineRun，兩者回傳同一種形狀；接受 `dataset_id`、`dataset_version_id`，pipeline 另可直接帶 `rows` 讓呼叫端把資料送進來；回傳上限 1,000 列並標示 `truncated` 與 `row_count`）；**API Key**（僅雜湊入庫、建立時顯示一次、可撤銷、可限定 workspace） |
| **Lineage** | **可查詢的血緣圖**（`GET /lineage/{kind}/{id}?direction=up\|down`）：往上回答「這個數字怎麼來的」，往下回答「我改這個來源會影響什麼」；由既有資料列即時推導，不另存一份 edge 表，因此不會與事實不一致；資料集詳情頁直接畫出來 |
| **Platform** | **Workspace（多租戶隔離，資源以 workspace 為界）**、**Project（workspace 內的工作歸檔：每個專案一個資料目錄，清單依專案過濾；`X-Project` 標頭）**、**Auth（JWT）+ RBAC（admin / editor / viewer）**、**Audit 稽核軌跡**、**Schedules（interval 或 cron；可排程 Model 或 Pipeline）**、**Job（佇列／lease／heartbeat／取消／重試上限）+ SSE 進度串流**、**Prometheus metrics（含 `flux_execution_duration_seconds{provider,kind,status}`）**、request correlation id（寫進每一行 log） |

儲存與執行皆為可插拔的 port：物件儲存可選 **本機檔案或 S3／MinIO**，
執行可選 **inline 或 Redis worker**，切換只需改環境變數，呼叫端不需修改。

---

## 2. 內建 Model Providers

| Provider | Model Type | Runtime | 可訓練 | 說明 |
|----------|-----------|---------|--------|------|
| `formula` | formula | python | ✗ | 具名運算式，如 `revenue = price * quantity` |
| `rule` | rule | rule_engine | ✗ | IF/THEN 規則引擎，含 default 與 first/all match |
| `python-transform` | custom | python | ✗ | 已審核的 Python 轉換（moving average / z-score / group aggregate） |
| `join` | custom | python | ✗ | **多輸入**：以一或多個鍵欄位合併兩張表（inner / left / outer），Pipeline 得以匯流 |
| `sklearn` | machine_learning | python | **✓** | 迴歸與分類；training execution 產生 Model Version |
| `curve-fit` | mathematical | python | ✗ | 最小平方擬合（線性／多項式／指數／冪次／對數），回報係數、R²、RMSE |
| `optimizer` | optimization | python | ✗ | 在有界變數上做網格搜尋，支援衍生量與約束式，回傳排名鄰域 |
| `monte-carlo` | simulation | python | ✗ | 以分布抽樣傳播不確定性，輸出百分位、離散度與門檻機率（seed 可重現） |
| `scorecard` | statistical | python | ✗ | 加權 0–100 綜合評分：每個成分回報自己的分數、佔比與「缺席時代表什麼」，並回報實際算到多少證據（coverage） |
| `risk-matrix` | rule | rule_engine | ✗ | 可能性 × 後果的風險矩陣。格子是資料，輸出一併帶回讀到的是哪一格 |
| `threshold-projection` | mathematical | python | ✗ | 趨勢外推到界線，回報**區間**與依據等級（calculated／estimated／inferred／unknown），不假裝精準 |
| `data-quality` | statistical | python | ✗ | 每條量測序列的品質評分：缺漏、重複、離群、卡死、位準跳動、取樣斷點、漂移；檢查項目可選 |
| `llm-reasoning` | **llm** | external_api | ✗ | 以結構化證據合成可讀判斷，每句話都必須引用證據；未設定端點時改由證據直接組成並標明來源 |
| `typhoon-analog` | statistical | python | ✗ | **颱風路徑類比預測（保留的核心演算法）** |
| `typhoon-backtest` | statistical | python | ✗ | 對歷史紀錄做 leave-one-out 回測，輸出 accuracy／macro accuracy |
| `typhoon-precip-analog` | statistical | python | ✗ | 位置條件降水機率分布 |
| `asset-condition-decision` | rule | python | ✗ | 十個分析器合成的設備維護決策（健康、風險、窗口、信心） |
| `asset-condition-evidence` | rule | python | ✗ | 同一份分析的逐條依據，供「Why?」面板與 LLM 推理使用 |
| `asset-maintenance-backtest` | statistical | python | ✗ | 在歷史評估日重跑決策政策，輸出 precision／recall／F1／平均提前天數 |

十九個 Provider 中只有一個可訓練 —— 這正是「Model ≠ MLModel」在程式碼層面的證明。
其中 `scorecard`、`risk-matrix`、`threshold-projection`、`data-quality`、`llm-reasoning`
**完全不指名任何領域**：它們是「多個量測合成一個判斷」這件事本身的通用詞彙，
設備維護只是第一個使用者。

### 2.1 標準 Transform 詞彙（Pipeline 的組成單位）

`python-transform` 提供 30 個已審核的轉換。**30 個全部以 Arrow 實作**
（`app/plugins/python_function/columnar.py`），沒有任何一個會把輸入攤成
`list[dict]`；純 Arrow 能表達的（篩選、排序、投影、去重、聚合）直接走 kernel，
需要 Python 語意的（六種時間格式、從 `"30 (m/s)"` 取數）也只讀它用到的那一欄。
實測（輸出逐列比對完全相同）：

| 情境 | 改寫前 | 改寫後 | 倍數 |
|------|--------|--------|------|
| 七個代表性轉換，50,000 列 × 40 欄 | 25.3 s | 0.81 s | **31×** |
| 真實的颱風氣候 12 步 pipeline（440 列 × 50 欄 → 215 列 × 60 欄）跑 30 次 | 220 s | 1.5 s | **143×** |

第二列才是重點：欄位越多、步驟越多，「把整張表攤成 `list[dict]`」的代價就越大 ——
440 列的資料集也能差兩個數量級。

每一個都是「一張表進、一張表出」，
並用 Contract 描述自己的參數，所以 Pipeline 的每一步都能在 UI 上以表單設定，
不需要手寫 JSON，也不需要為個別分析寫程式：

| 類別 | Transform |
|------|-----------|
| 整形 | `rename_columns`、`drop_columns`、`select_columns`、`cast_types`、`fill_missing`、`drop_duplicates`、`sort_rows`、`limit_rows` |
| 篩選與標記 | `filter_rows`、`flag_rows` |
| 衍生 | `parse_numeric`、`extract_pattern`、`datetime_parts`、`duration_between`、`bin_numeric`、`map_values` |
| 統計 | `summarise`、`group_aggregate`、`moving_average`、`zscore_outliers`、`rank_rows`、`percent_of_total` |
| 重塑 | `pivot_wider`、`unpivot_longer` |
| 時序 | `resample_time`、`rolling_stats`、`rate_of_change`、`lag_column`、`linear_trend`、`correlation` |

後兩類是**針對「列是時間上的觀測」而不是「列是分析單位」的表**加入的。
一個量測資料庫是長的（一列一個讀值），一張分析表是寬的（一列一個對象一個週期），
而原本沒有任何動詞能把前者變成後者。三個性質讓它們可以互相組合：

* **分組是明講的。** 對一張含四十台設備讀值的表做滑動平均，若不在每台設備重新
  開始就毫無意義。每個窗口型 transform 都吃 `group_by`；既有的 `moving_average`
  維持原樣不動，避免改變已在跑的 pipeline。
* **順序是明講的。** 時序資料到手時的順序是資料庫給的，不是時間。每個 transform
  先在組內依 `order_by` 排序，再把答案寫回原本那一列，因此呼叫端的列順序不變。
* **斜率有單位。** 「每筆之間上升多少」在取樣間隔改變後就不再可比，所以
  `rolling_stats`、`rate_of_change`、`linear_trend` 都回報**每小時／每天**，
  而 `linear_trend` 會在雜訊蓋過趨勢時回答 `unstable` 而不是硬給一個方向。

`GET /api/v1/transforms` 會回傳整份詞彙與各自的參數 Contract，前端的 Pipeline
建構器就是讀這份目錄來產生表單的。

---

## 2.2 圖表型別

| 型別 | 回答的問題 | 需要 |
|------|-----------|------|
| `bar` / `line` / `area` | 每個類別（或每個時間點）是多少 | x + y |
| `stacked_bar` | 每個類別的**組成** | x + y + series |
| `scatter` | 兩個變數之間的**關係** | x + y |
| `pie` | 各部分佔整體的比例 | x + y |
| `histogram` | 單一數值欄位的**分布形狀** | y（另可設 bins） |
| `box` | 每個類別的**中位數與離散度**，含 Tukey 1.5 IQR 鬚線與離群點 | x + y |
| `heatmap` | 一個量值在**兩個類別軸**上的分布 | x + series + y |

`series` 欄位可用在 bar / line / area / stacked_bar / heatmap，把同一個量值依
另一個欄位拆成多組（cohort）。`x_order` 與 `series_order` 讓序位型類別
（如 輕度／中度／強烈）依領域順序排列，而不是依字母排序。

圖表全部以原生 SVG 繪製，沒有引入任何圖表函式庫。

---

## 3. 颱風相似度演算法（完整保留）

原研究管線的演算法保留於
[`backend/app/plugins/typhoon_analog/algorithms/`](backend/app/plugins/typhoon_analog/algorithms/)。
與原始檔案逐行比對，**17 個檔案總共只有 6 行 import 敘述被改寫**（把舊的
`data_pipiline.*` 套件路徑改成本套件的相對 import），其餘完全相同 ——
包含空行、型別註記寫法與註解。這些檔案在 `pyproject.toml` 中明確排除所有風格檢查，
避免格式化工具動到它們。

### 3.1 Coastline —— 海岸線範圍內的絕對位置相似度

以「台灣海岸線外擴 `buffer_km`」框住計算範圍，在本地等距 km 平面上計算兩條路徑的
**對稱平均最近點距離（Chamfer distance）**：

```text
d(Q,C) = 0.5 × ( mean_{q∈Q} dist(q, polyline C) + mean_{c∈C} dist(c, polyline Q) )
```

距離以公里為單位、具明確幾何意義（平均偏離 km），直接對應「地圖上看起來最接近」。
相似度分數 `score = exp(-d / 150km)`。

### 3.2 Coastline RRF —— 以絕對位置為主的多排名融合（旗艦方法）

```text
score(tid) = w_coast / (rrf_k + rank_coast)
           + w_knn   / (rrf_k + rank_knn)
           + w_rain  / (rrf_k + rank_rain)     # 可選
```

預設 `w_coast = 0.80`、`w_knn = 0.20`、`rrf_k = 60`、`pool_size_factor = 12`。
絕對位置占主導，確保 Top-K 就是螢幕上最貼近的那幾條路徑；
KNN（11 維加權摘要特徵）補足強度／速度／生成位置等非幾何訊號；
降水排名可於執行期逐請求開關。

其餘保留方法：`combined_rainfall`（KNN + DTW + Rule + 降水 RRF）、
`knn_optimized`、`rule_based`（CWA 1–9 類規則分類）、`baseline`（隨機下限）。

### 3.3 在平台中的位置

颱風預測**不走側門**：前端每次查詢都經由 `ExecutionService` 送出一個
Execution，結果寫入 Result，與其他任何模型完全同一條路徑。

```text
Track ──▶ typhoon-analog Model ──▶ Prediction Execution ──▶ Result(classification)
                                                          └─▶ Executions / Results 頁面可追溯
```

---

## 4. 快速開始

### 4.0 資料需求

`data/` 目錄在 `.gitignore` 中（342 MB 的原始與前處理資料不進版控），
所以**全新 clone 不會帶有颱風資料集**。平台本身照常啟動。

目錄依 Project 切分，每個 Project 一個目錄，內含 `sources/`（來源檔）與
`uploads/`（從頁面上傳的檔案）：

```text
data/
├── HydroAnalog/sources/preprocessed/   颱風路徑類比預測
├── AssetGuard/sources/                 設備預防性維護分析
└── Demo/sources/                       開發與測試用樣本
```


| 檔案 | 缺少時的行為 |
|------|--------------|
| `data/HydroAnalog/sources/preprocessed/typhoons_overview.json` | 種子資料略過颱風 Dataset；颱風模型仍會建立，執行時回 404 並說明缺少哪個檔案 |
| `data/HydroAnalog/sources/preprocessed/precip_analog.npz` | 降水模型在 `validate()` 時發出 warning，執行時回 404 |
| `data/Demo/sources/sales.csv` | 首次啟動自動生成，不需準備 |

`backend/tests/test_typhoon_analog.py` 在資料集缺席時會自動 skip。

### 4.1 Docker（推薦，一鍵啟動）

```bash
cp .env.example .env      # 可直接用預設值
docker compose up -d
```

| 服務 | 位址 |
|------|------|
| 前端 | http://localhost:3001 |
| 後端 API | http://localhost:38000 |
| API 文件 | http://localhost:38000/docs |
| Prometheus metrics | http://localhost:38000/api/v1/metrics |
| PostgreSQL | localhost:35432 |
| Redis | localhost:36379 |
| MinIO Console | http://localhost:39001 |

> **`localhost` 打不開、`127.0.0.1` 卻可以？**
> 這是 Docker Desktop（WSL2 後端）在 Windows 上的通病，不是應用程式的問題。
> Windows 會把 `localhost` 先解析成 `::1`，而 Docker 的 `wslrelay` 有時會在
> `[::1]:<port>` 上留下一個指向舊容器的監聽，接受連線後直接 reset ——
> 症狀就是瀏覽器顯示 `ERR_CONNECTION_RESET`。
> 先用 `http://127.0.0.1:3001`；要根治就**重啟 Docker Desktop**（重建 port 對應）。
> 用 `netstat -ano | findstr :3001` 可以看到誰佔著哪個位址。

共六個容器：`postgres`、`redis`、`minio`、`backend`、`worker`、`frontend`。
啟動時後端會自動跑 `alembic upgrade head`、建立首位管理員並種入示範資料。

首次登入使用 `.env.example` 中的預設帳密（`admin@flux.local` / `flux-admin`），
登入後請立刻在右上角選單改密碼。

便利腳本：

| 腳本 | 作用 |
|------|------|
| `scripts/start.sh` | 建置並啟動整個 stack，等到 API 健康才回報 |
| `scripts/stop.sh` | 停止（`--clean` 連 volume 一起清掉） |
| `scripts/test.sh` | 後端 ruff + pytest、前端型別檢查 + build、API 型別漂移檢查、版面檢查 |

### 4.2 本機開發

```bash
# 後端
cd backend
pip install -e ".[dev]"
export FLUX_DATABASE_URL="postgresql+psycopg://flux:flux@localhost:35432/flux"
alembic upgrade head
uvicorn app.main:app --reload --port 38000

# 前端
cd frontend
npm install
npm run dev          # http://localhost:3001，/api 自動 proxy 到 38000
```

不想裝 PostgreSQL 時可用 SQLite：

```bash
export FLUX_DATABASE_URL="sqlite+pysqlite:///./var/flux.db"
```

---

## 5. Golden Path（開箱即有）

首次啟動的種子資料就是規格中的完整示範：

```text
data/Demo/sources/sales.csv
        ↓  Source (csv)
    Dataset "Sales"  ──▶ Schema (date, product, price, quantity)
        ↓  Formula Model:  revenue = price * quantity
    Calculation Execution
        ↓
    Result (table, 270 rows) ──▶ Dataset "Revenue formula result"
        ↓                              ↓
  Visualization × 2              Training Execution (sklearn)
        ↓                              ↓
  Dashboard「Sales overview」      Model Version v2 (r² ≈ 0.96)
```

同一個答案，一條路不需要訓練（Formula），另一條需要（ML）—— 這就是把 Model
抽象拉高於 MLModel 的實際好處。

另外種入：Rule 模型、Python transform 模型、颱風類比模型、颱風降水模型，
以及「設備預防性維護分析」、「Typhoon analog forecast」與「Sales analytics」
三個已發布的內建應用。

### 5.1 颱風完整範例（用真實資料跑完每一個功能）

這份範例由**外掛自己**種入，核心的 `app/core/seed.py` 不知道有颱風這回事：
資源清單寫成宣告式 fixture（`plugins/typhoon_analog/seed/resources.py`），
真正需要程式的部分則分成兩半 —— **氣候分析**（`seed/climatology.py`）與
**模型驗證**（`seed/backtests.py`）。

種子資料還會用 `data/` 底下的真實颱風紀錄，把平台每一項功能各做出一個
可以直接打開來看的範例：

```text
Dataset「Taiwan typhoon catalogue」(440 rows，型別混雜的原始紀錄)
        ↓  Pipeline「Typhoon climatology」— 12 個標準 Transform，每步一次 Execution
   cast measures → parse near-centre wind → derive season → derive lifetime
   → derive warning duration → flag landfall → band intensity → band rainfall
   → band decade → keep classified → label track category → trim narrative columns
        ↓
Dataset「Typhoon climatology output」(215 rows，具 CWA 侵臺路徑分類的颱風；
                                     以 pipeline 命名，而不是以最後一步命名)
        ↓
   Visualization × 16  ──▶ Dashboard × 4
        ├── 颱風氣候概況     年際變動、季節分布、年代強度組成、路徑佔比
        ├── 強度結構分析     風速／氣壓／生命期分布、風速–氣壓關係、各類風速盒鬚圖
        ├── 降水與路徑影響   強度–雨量盒鬚圖、雙站雨量比較、月份×路徑熱區圖、雨量分級組成
        └── 路徑與登陸特徵   生成位置散布、各類登陸比例、警報時數 vs 風速
        ↓
   Report「Taiwan typhoon climatology」（六節：資料範圍、頻率季節、強度結構、
                                        降水影響、路徑登陸、作業意涵與資料限制）

   Experiment「Analog method comparison」（比不同方法）
        ├── Coastline-RRF (k=5, 500km buffer)    accuracy 0.87
        ├── Weighted KNN (k=5)                   accuracy 0.53
        └── Random baseline                      accuracy 0.07
        ↓  各自產生 Evaluation（門檻 accuracy ≥ 0.5）
   Experiment「Coastline-RRF · k sweep」（比同一個模型的不同參數）
        ├── k = 3    accuracy 0.87
        ├── k = 5    accuracy 0.87
        └── k = 9    accuracy 0.73
   Leaderboard（Experiments 頁）依 primary metric 排名，一列一個 Trial
        ↓
   Evaluation 頁可同時選取兩個 Experiment 交叉比較
        ↓
   Schedule「Weekly typhoon re-validation」（cron `0 4 * * 1`）
        ↓
   Report「Typhoon model validation」（文字 + 指標 + 表格 + 圖表，可匯出）
```

**氣象慣例。** 強度分級採中央氣象署標準（熱帶性低氣壓 < 17.2 m/s、輕度
17.2–32.6、中度 32.7–50.9、強烈 ≥ 51.0 m/s）；雨量分級採大雨 80 mm、豪雨
200 mm、大豪雨 350 mm、超大豪雨 500 mm，但使用的是**事件累積量**而非 24 小時
累積量，因此只作量級參考。路徑分類為氣象署的侵臺路徑分類 1–9 類加特殊路徑。

**回測**是 leave-one-out：把每一個歷史颱風輪流當成查詢，用其餘的紀錄預測它的
CWA 路徑分類，再和真實標籤比對。Coastline-RRF 明顯勝過加權 KNN，兩者都遠勝
隨機基線 —— 這就是「模型驗證」在這個平台上實際長成的樣子。

### 5.2 設備預防性維護分析（第二個內建應用）

第二個應用刻意選了一個和颱風完全無關的領域，用來證明同一套抽象確實可以搬動：
`app/plugins/asset_maintenance/`，核心一行都沒改。

**它不是異常偵測。** 一個讀值偏高，可能是設備劣化、負載變高、廠房變熱，或者
感測器壞了。這四種情況的處置完全不同，而它們在單一讀值上看起來一模一樣。
所以分析是分層的，**而且不使用機器學習** —— 不是因為被禁止，而是因為對這個問題
底下這幾層真的答得出來，而且每一層都說得出自己為什麼這樣講：

```text
遙測（40 台設備 × 222 個量測點 × 120 天 × 每小時 ≈ 60 萬筆讀值）
   ↓ Pipeline ①「設備遙測條件化」 7 步
     去重 → 剔除無效讀值 → 併入運轉狀態 → 只留運轉中時段 → 併入設備主檔 → 併入環境
   ↓ Pipeline ②「設備每日狀態」 1 步（resample_time：每設備每量測每天一列）
   ↓ Model「設備量測資料品質」／「遙測取樣完整性」（兩個串流，兩組檢查）
   ↓ Pipeline ③「設備狀態特徵」 9 步
     併入響應模型 → 算出「這個負載、這個廠房溫度下應該讀到多少」→ 殘差
     → 併入門檻表 → 換算門檻進度 → 規則判定四級狀態
     → 7／21／30 天滾動統計、趨勢斜率與 R²
   ↓ Pipeline ④「設備現況快照」 2 步（每個量測最新一天 = 全機隊現況）
   ↓ Model「設備維護決策」──▶ 一列一台設備：健康、風險、窗口、信心
     Model「設備維護判斷依據」──▶ 一列一條證據：哪個分析器、什麼數字、貢獻多少
     Model「維護判斷說明（LLM）」──▶ 把證據寫成可讀的判斷，每句都要引用證據
   ↓ Visualization × 20 ──▶ Dashboard × 5
   ↓ Experiment「維護決策政策比較」（五種分析組合，對照實際故障評分）
   ↓ Schedule「每日機隊健康評估」（cron `0 5 * * *`）
   ↓ Report × 2 ──▶ Application「設備預防性維護分析」（`/applications/asset-maintenance`）
```

**十個分析器，註冊而非寫死。** `engine.py` 的 `ANALYZERS` 是一份清單：門檻、
基線、趨勢、統計、失效型態比對、運轉時數、時程政策、歷史比對、工程規則、
資料品質。而一個「政策」就是這份清單的一個子集 —— 這正是五種政策可以放進
同一個 Experiment 互相比較的原因：它們是同一個引擎的不同組合，不是不同的程式。

**三個設計決定，每一個都是「顯而易見的做法是錯的」：**

| 決定 | 為什麼 |
|------|--------|
| 界線是**相對於應有值的偏移量**，不是固定值 | 一台 45% 負載的泵浦流量 57 m³/h，90% 負載時是 108。任何抓得到葉輪磨蝕的固定流量下限，都會把前者永遠判成異常 |
| 證據的權重**可以是負的** | 一支卡住的感測器是「不要相信這個讀值」的理由，不是「情況更嚴重」的理由。只會累加關注度的系統最後會標記所有設備 |
| 派工門檻**依設備重要程度而不同** | 變壓器停了全廠停，抽風機不會。用同一個門檻等於選擇要在哪一邊犯錯 |

**工程知識是資料，不是程式。** 響應係數（溫度 ≈ 環境 + 負載效應）、門檻表、
16 條證據組合規則都是 Dataset，規則的條件是平台自己那個 allow-list 運算式求值器
執行的。改一條規則不需要改程式，也不需要重新部署。

**維修窗口不假裝精準。** 輸出的是區間加上依據等級：`calculated`（已越線，
是量到的不是推的）、`estimated`（趨勢夠穩，用斜率標準誤給出區間）、
`inferred`（在往界線走但擬合太弱，**刻意不給日期**）、`unknown`。

**資料是模擬產生的，而且說清楚。** 真實機隊沒有已知答案，沒有已知答案就無法對
決策政策評分。`datagen.py` 以固定 seed 從負載、環境與設備響應係數合成讀值，
劣化依宣告的失效模式演進，其中包含 12 台正常、10 台劣化中、11 台期間內故障並
修復、4 台**儀器故障但設備正常**（卡死、單位換算錯誤、漂移、超出量程）、
3 台剛啟用（冷啟動）。第四類是刻意放進去的：會把它們標記成待修的系統，
在現場一定會失去信任。`ground_truth.json` 只用於回測，不參與任何一次評估。

**回測比較的是五種政策，主要指標是 F1。** 機隊絕大多數時間是健康的，準確率
會讓「什麼都不標記」拿到 0.95 分。報告中同時列出精確率、召回率、特異度、
警示率與平均提前天數 —— 一個總在故障前一天才發現的系統，召回率完美而沒有價值。

---

## 6. 架構

```text
flux-data-engine/
├── backend/
│   ├── alembic/                    資料庫遷移
│   └── app/
│       ├── main.py                 FastAPI 組裝
│       ├── worker.py               背景 worker：佇列 / 排程 / 遺失工作回收
│       ├── api/                    依賴注入、模組守衛（認證授權）、路由聚合
│       ├── core/                   設定、容器（組合根）、DB、錯誤轉譯、
│       │                           可觀測性、種子資料（不知道任何領域應用）
│       ├── shared/                 共享核心：contracts / tabular(Arrow) /
│       │                           table_ops(下推) / payloads / scoping /
│       │                           storage(local + s3) / outbound(SSRF) /
│       │                           queue / tokens / errors
│       ├── modules/                模組化單體，每個模組四層
│       │   ├── data/               Source, Dataset, DatasetVersion, Schema
│       │   ├── orchestration/      Pipeline（步驟定義內嵌）, Schedule
│       │   ├── analysis/           Explore, Visualization, Dashboard
│       │   ├── model/              Model, ModelVersion, Plugin 契約, Registry
│       │   ├── evaluation/         Experiment, Trial, Evaluation, Leaderboard
│       │   ├── execution/          Execution（7 種 kind）, 同步 invoke
│       │   ├── jobs/               Job：佇列 / lease / heartbeat / 取消 / SSE
│       │   ├── results/            Result（一級物件）
│       │   ├── reporting/          Report（組合與匯出）
│       │   ├── applications/       Application（publish／unpublish／share link）
│       │   └── platform/           workspace / auth / rbac / api key /
│       │                           audit / metrics / overview
│       └── plugins/                Framework-specific 與領域實作只住在這裡
│           ├── contrib.py          外掛貢獻的路由與種子（核心據此發現）
│           ├── fixtures.py         宣告式 fixture 載入器（冪等）
│           ├── formula/  rule/  python_function/  sklearn/
│           ├── curve_fit/  optimizer/  monte_carlo/  join/
│           └── typhoon_analog/
│               ├── algorithms/     ← 保留的研究演算法
│               ├── seed/           該應用自己的種子（fixture + 兩支程式）
│               ├── engine.py       載入 / 擬合 / 快取
│               ├── plugin.py       類比預測 Provider
│               └── precip_plugin.py
├── frontend/                       Vue 3 + TypeScript + Vite + Quasar
├── data/                           每個 Project 一個目錄，內含 sources/ 與 uploads/
└── docker-compose.yml
```

每個模組內部維持 `domain / application / infrastructure / api` 四層：

- `domain/` —— 純 Python。`entities.py` 定義實體，`ports.py` 定義該模組依賴的
  Protocol（repository、reader）。不可 import SQLAlchemy、FastAPI、pydantic 或任何 ML 框架
- `application/` —— 用例服務。**只依賴 ports**，不 import infrastructure
- `infrastructure/` —— ORM、repository、reader 等技術實作，實作 domain 宣告的 ports
- `api/` —— 薄路由：解析、委派、序列化

具體實作只在**組合根**被指名：`app/api/deps.py`（HTTP 請求）與
`app/core/seed.py`（啟動種子）。其餘程式碼一律面向 ports。

這些界線由測試強制執行（見 `backend/tests/test_model_abstraction.py`）：
ML 框架不得出現在 `plugins/` 之外、domain 不得依賴基礎設施、
application 不得 import infrastructure、每個 SQL repository 必須滿足其 port。

### 依賴方向

模組是一個由下往上的堆疊，依賴只能往下指：

```text
第 4 層   orchestration    reporting    evaluation
第 3 層          execution        analysis
第 2 層             model           results
第 1 層   platform    data    applications    jobs
```

同層之間可以互相 import，往上不行。這條規則由
`backend/tests/test_module_dependencies.py` 以 AST 掃描強制執行，
新增模組必須明確放進某一層。

幾個位置值得說明：

* `Pipeline` 原本住在 `data`，卻依賴 ExecutionService 與 ResultService ——
  這是一條指回上層的邊。搬到 `orchestration` 之後圖才真的無環
* `ResultPayload` 已移到 `shared`，所以 **`model` 不依賴任何模組**：
  Plugin 契約現在只依賴 shared，插件作者不會被拖進平台的內部結構
* `applications` 放在最底層：它只持有一串 id，命名卻不需要知道它們如何運作
* `jobs` 也在最底層：它處理的工作種類是**注入**的，不是 import 來的

---

## 6.1 兩個可插拔的接縫

平台有兩處刻意做成 port + 多實作，切換只改環境變數：

### 物件儲存

```text
ObjectStore (port)
   ├── LocalObjectStore   file://key            預設
   └── S3ObjectStore      s3://bucket/key       FLUX_STORAGE_BACKEND=s3
```

DatasetVersion 的 Parquet、Result payload、模型 artifact 全部走這個 port。
S3 backend 會自動建立 bucket，並以 ETag 為鍵在本機快取需要以檔案讀取的物件
（Parquet、joblib），物件被覆寫時快取自動失效。

### 執行派送

```text
ExecutionDispatcher (port)
   ├── RunInline / InlineDispatcher   在請求中直接執行（預設）
   └── RedisQueueDispatcher           推入 Redis，worker 消費
```

佇列模式下 `POST /executions` 立刻回傳 `pending`，worker 取走後才轉為
`succeeded`。推入 Redis 的動作**延後到交易 commit 之後**，避免 worker 取到
尚未可見的資料列。worker 另有回收掃描，處理「入列失敗或 worker 中途死亡」
而滯留在 `pending` 的執行。

```bash
docker compose up -d          # 預設 inline + 本機儲存
FLUX_EXECUTION_MODE=queue FLUX_STORAGE_BACKEND=s3 docker compose up -d
```

---

## 6.15 兩個 worker 不會做同一件事

領取一筆待執行的工作是**一句條件更新**，成敗由資料庫決定：

```sql
UPDATE executions SET status = 'running', attempts = attempts + 1, ...
 WHERE id = ? AND status = 'pending'
```

`rowcount` 為 0 就代表被別人搶走了，這個 worker 直接回報現況、不做事。Job 同理。

**為什麼這是正確性問題而不是效能問題**：原本是「讀 → 檢查不是終態 → 標記 running → 寫回」，
中間有兩個空隙。而 worker 的回收掃描會把「PENDING 超過 120 秒」的工作重新入列 ——
一筆只是慢了兩分鐘才被領取的執行，會同時處於「被重新入列」與「正在執行」兩種狀態。
後果不是崩潰而是**靜默重複執行**：兩份 Result、兩份 materialise 的 Dataset、
訓練寫兩次 artifact、對外呼叫做兩次，而且沒有任何地方會說。

`tests/test_concurrent_workers.py` 用真的執行緒、真的 session、同一列資料跑這個競態；
把修正拿掉後該測試會抓到「一次提交產生兩份 Result」。

---

## 6.2 認證與授權

* 密碼以 `hashlib.scrypt` 雜湊（每筆獨立 salt、常數時間比對）
* 存取權杖是限定 HS256 的精簡 JWT；**先驗簽章再讀 claim**，演算法固定，
  因此 `alg: none` 與演算法混淆攻擊不成立
* 授權由 **(模組, HTTP 動詞)** 決定：安全動詞需該模組的 read 權限，
  其餘需 write 權限。守衛掛在 router 上，新增端點不會忘記掛

| 角色 | 權限 |
|------|------|
| `viewer` | 全平台唯讀 |
| `editor` | 讀 + 建立／執行，但不能管理帳號 |
| `admin` | 全部，含帳號與平台設定 |

首次啟動會依 `FLUX_BOOTSTRAP_ADMIN_*` 建立唯一管理員並在日誌提醒改密碼。
本機開發可設 `FLUX_AUTH_ENABLED=false`，此時守衛解析為一個合成的管理員身分。

### Workspace —— 資源的邊界

每個資源都帶 `workspace_id` 與 `created_by`，唯一性由「名稱」改成
**（workspace, 名稱）**，所以兩個 workspace 可以各有一個叫 `Sales` 的資料集。
隔離做在 **repository 層**而不是路由層：

```text
WorkspaceScoped   _stamp()   寫入時蓋上目前 workspace
                  _scoped()  查詢時自動加上 where workspace_id = ?
                  _fetch()   取單筆時，別的 workspace 的列一律當作不存在
```

如此新增一個端點不會忘記過濾 —— 忘記的唯一方法是繞過 repository。
請求以 `X-Workspace` 標頭指定，未指定則用成員身分中的預設；
種子資料與 worker 也都在一個明確的 workspace 內執行。

### Project —— 工作的歸檔

Workspace 之內再切一層 **Project**：一份工作（一個案子）擁有自己的
來源檔目錄與清單。兩者不是同一種機制，混用是這一層最容易犯的錯：

| | Workspace | Project |
|---|---|---|
| 定位 | **邊界** | **歸檔** |
| 列表 | 只列本 workspace | 只列本 project（外加未歸檔者） |
| 依 id 取單筆 | 別的 workspace 一律當作不存在 | **不拒絕** |
| 未指定時 | 落到成員的預設 workspace | 不過濾，等於看全部 |

依 id 取單筆刻意不擋，因為 Report 會引用 Dataset、Lineage 會走進 Dataset、
Application 會綁多個 Dashboard；擋下來會弄壞真的功能，而且換不到任何安全性
—— 邊界已經由 workspace 守住了。

`project_id IS NULL` 代表**共用**，會出現在**每一個** project 底下而不是都不出現。
Model 定義是平台上唯一值得跨案子重用的東西（評分卡、門檻規則、曲線擬合講的是
算術，不是機隊也不是颱風），所以 `POST /models/{id}/project` 可以把定義設為共用
或收進當前 project；執行、結果與其產出的資料集則留在工作發生的地方。

歸檔寫在 **entity** 上而不只是 row 上（`_file()`），因為 service 拿到的是 entity，
之後的 `update()` 會用 entity 重寫整列 —— 只蓋 row 的話，
`create_from_source` 產生版本後那次 update 就把剛蓋上的 project 清掉了。

檔案落在 `data/<project 目錄>/sources/`（與 `uploads/`），建立 project 時一併建立目錄；
資料集本身仍在物件儲存，鍵值依 project 分區
（`datasets/{project}/{dataset_id}/v{n}/{version_id}.parquet`），
所以本機／S3／MinIO 後端照樣可換。

請求以 `X-Project` 標頭指定。**指不到的 project 會被忽略而不是報錯** —— 這是歸檔而
不是邊界，瀏覽器裡一個過期的 id 不該讓整個平台打不開。

前端在頁首常駐 project 切換器；`Sources / Datasets / Pipelines / Explore /
Visualizations / Dashboards` 與 `Models & results` 各頁都依當前 project 過濾。
`Applications / Reports / Schedules` 不切 project：每一個都已經指名了它作用的對象。

### API Key —— 給機器的身分

`POST /api-keys` 產生的金鑰**只在建立當下回傳一次**，資料庫只存 SHA-256 雜湊，
因此外洩資料庫不等於外洩金鑰。金鑰可限定 workspace、可撤銷、可設到期，
每次使用都更新 `last_used_at`。適用於 `POST /models/{id}/invoke` 這類
機器對機器的呼叫。

### 分享連結 —— 拿到網址就是權限

已發布的 Application 可以產生一個分享連結
（前端 `/shared/{token}`，後端 `GET /api/v1/public/applications/{token}`）：
不需要帳號即可**唯讀**檢視該應用綁定的 Dashboard，看不到平台的其他任何東西，
也拿不到任何寫入端點。連結可隨時撤銷，撤銷後舊網址立即失效。
這是平台上唯一不需憑證的路由，因此獨立掛載在 `public_router`，
「哪些路由不需要憑證」讀一行就能回答。

> **上線前必改**：`FLUX_SECRET_KEY`（簽發權杖用）與管理員密碼。
> 仍是預設值時後端啟動會發出 WARNING。

---

## 7. 技術棧

| 層 | 技術 | 狀態 |
|----|------|------|
| Frontend | Vue 3 + TypeScript + Vite + Quasar | ✔ |
| Backend | Python 3.11+ / FastAPI | ✔ |
| ORM / Migration | SQLAlchemy 2 / Alembic | ✔ |
| Database | PostgreSQL（metadata 與交易狀態） | ✔ |
| Data Processing | Apache Arrow（`shared/tabular.py`） | ✔ |
| Analytical Storage | Apache Parquet（所有 DatasetVersion） | ✔ |
| Object Storage | ObjectStore port，兩個 backend：本機檔案 / S3・MinIO | ✔ |
| Queue | Redis + 背景 worker（`FLUX_EXECUTION_MODE=queue`） | ✔ |
| Scheduler | worker 內的排程迴圈（interval 與 cron） | ✔ |
| Auth | JWT（HS256）+ scrypt 密碼雜湊 + RBAC | ✔ |
| Observability | Prometheus text exposition（HTTP + **execution** 直方圖）、request id（進每一行 log）、可選 JSON log（`FLUX_LOG_FORMAT=json`）、稽核軌跡 | ✔ |
| ML Runtime | scikit-learn（僅在 `plugins/sklearn/`） | ✔ |
| ML Tracking | MLflow | 未引入（核心不得依賴） |
| Container | Docker / Docker Compose | ✔ |

大量資料一律走 Parquet + 物件儲存，PostgreSQL 只存 metadata。

---

## 8. 已知邊界（誠實清單）

原先標示為「未實作」的 Pipelines、Reports、Schedules、S3 backend、Redis worker、
Auth/RBAC **均已實作完成**；「Pipeline 只分支不匯流」由 `join` Provider 解決，
「沒有多租戶」由 Workspace 解決。目前仍存在的邊界如下：

| 項目 | 現況 |
|------|------|
| **SSO** | 有 Workspace 與成員角色，但沒有 OAuth／OIDC／SAML；機器身分走 API Key |
| **Pipeline 的平行只在一個行程內** | Pipeline 已是完整的 Runnable：可被執行、排程、`/invoke`、被 Experiment 比較、被另一條 Pipeline 巢狀，互不相依的步驟也會同時跑。但那是同一個行程裡的執行緒，不是分散到多個 worker：一次 run 仍是一個工作單元，`pipeline_max_parallel_steps` 是它的上界 |
| **查詢下推止於 Arrow** | 篩選／排序／聚合走 `pyarrow.compute`，Parquet 只讀需要的欄位（Explore 查詢與圖表都把投影推進讀取）；但沒有查詢規劃器，也沒有 row-group 層級的 predicate pushdown 或跨檔案分割裁剪 —— 一次讀取仍是「整份（選定欄位）進記憶體」。**這是刻意的**：不為此引入新的技術棧 |
| **颱風回測不可中途取消** | 回測的迴圈在保留的研究演算法內（`plugins/typhoon_analog/algorithms/`），而那些檔案的規則是逐行保留、不得改寫，因此無法插入 `should_stop()` 檢查。以 `MAX_SAMPLE = 250` 限制上界代替 |
| **切換 storage backend 不會搬移既有資料** | DatasetVersion 記錄的是完整 URI（`file://` 或 `s3://`）。切換後舊資料需自行搬移，否則讀取會明確報錯 |
| **MLflow** | 未引入。核心 Domain 不得依賴，若要接入應作為 `plugins/sklearn/` 的 tracking 附加 |
| **Dashboard 與 Report 沒有合併** | 兩者看似都是「一堆視覺化的容器」，但一個是可互動的格線、一個是有順序且可匯出三種格式的文件。硬合成一個 `Composition` 只會讓兩邊都變難用，因此**刻意保持分開** |
| **`DatasetOrigin.INTERMEDIATE`** | 仍保留在 enum 中，供歷史資料列使用；新的 Pipeline 已不再產生中間 Dataset（步驟定義內嵌，只有終端步驟落地） |

---

## 9. 測試與檢查

```bash
cd backend
pytest -q            # 687 tests
ruff check app tests

cd ../frontend
npm run build        # vue-tsc 型別檢查 + production build
npm run types:check  # 手寫 TS 型別 vs OpenAPI schema，漂移就報錯
npm run check:pages  # 逐頁開啟：任何 4xx/5xx、console 錯誤、未捕捉例外、
                     # 或畫面上出現失敗訊息，都算失敗
npm run check:layout # 以無頭 Chrome 掃全部頁面 × 明暗兩主題，找版面問題
```

前端另有一層薄快取（`src/api/cache.ts`）：只快取「開著這個分頁期間不會變」的
詞彙型端點（provider 目錄、transform 詞彙、圖表型別），五分鐘 TTL、
多個元件同時要求時共用同一個請求，**任何寫入或切換 workspace 就整份丟掉**。
會被編輯的資料一律不快取。

**`check:pages` 是為了一件事而存在**：一個頁面可以完全壞掉，卻通過這個專案其他所有檢查。
`check:layout` 量的是幾何，所以它會對一個「內容只有一則失敗請求」的頁面回報「沒有版面問題」；
單元測試用 `create_all` 自己建 schema，所以它永遠不會發現**應用程式實際連的那個資料庫比程式碼舊一個 migration** ——
`/experiments` 與 `/evaluation` 就是這樣對每一位使用者回 500，而其他檢查全部說平台很好。
它問的是使用者唯一在意的問題：我打開這一頁，它有沒有壞。

`types:check` 從 `/openapi.json` 讀出 schema，和 `src/types/` 的手寫型別比對，
少欄位、多欄位、型別對不上都會指名道姓。`check:layout` 走遍所有路由，
檢查水平溢出、重疊、被裁切的文字與對比不足；兩者都在缺少相依（後端沒起、
沒有 Chrome）時明說跳過，而不是假裝通過。

主要測試分類：

- `test_golden_path.py` —— CSV → Dataset → Schema → Formula → Execution → Result → Chart
- `test_training_execution.py` —— 訓練產生不可變 Model Version；未訓練即預測會被拒絕
- `test_model_abstraction.py` —— **架構規則**：ML 框架不得出現在 `plugins/` 之外；
  domain 不得依賴基礎設施；application 不得 import infrastructure；repository 必須滿足其 port
- `test_typhoon_analog.py` —— Chamfer 距離對稱性、buffer 幾何、RRF 權重主導性、
  六種方法皆可回答軌跡查詢、演算法目錄不得反向依賴平台，
  以及三種典型 CWA 路徑（北部西行 / 南部西行 / 東岸北上）能各自取回同族類比
- `test_auth_and_rbac.py` —— 登入、偽造 token 拒絕、三種角色的讀寫界線、帳號管理
- `test_pipelines.py` —— 圖驗證（環、懸空參照、重複命名）、分支拓撲、
  資料集逐步串接、失敗步驟會取消其下游、queue 模式下仍能完成
- `test_runnable_coverage.py` —— **每一種 Runnable 都要能做同樣的事**：
  逐一斷言 `RunnableKind` 的每個值都能被執行、被列出、被 `/invoke`、
  被 Experiment 比較。新增第三種 Runnable 時，這個檔案會先失敗，
  而不是讓它只會做其中一件事就悄悄上線
- `test_pipeline_nesting.py` —— 步驟可以是另一條 Pipeline：巢狀 run 是自己的一筆
  run（不是被攤平）、內層失敗會讓外層步驟失敗、自我巢狀與互相巢狀在存檔時被擋下
- `test_pipeline_parallel_steps.py` —— 哪些步驟可以同時跑（`waves()`），
  以及它們真的同時跑（同一批確實有兩個以上在執行中）；巢狀步驟永遠留在呼叫端的
  session；SQLite 只有一個寫入者，因此那裡預設關閉
- `test_reports_and_schedules.py` —— 章節即時解析、壞參照只影響該章節、
  三種匯出格式、cron/interval 觸發器、到期排程觸發並重排、稽核與 metrics
- `test_storage_and_dispatch.py` —— 兩個 storage backend 滿足同一 port、
  路徑越權防護、queue 推送延後到 commit 之後
- `test_experiment_leaderboard.py` —— Experiment leaderboard 依 primary metric 排名、
  未評估的模型排在最後、每個模型只取最新一筆 Evaluation
- `test_runnable_experiments.py` —— Experiment 以 Trial 為單位：同一個模型可用不同
  參數出現兩次、check 會指出 provider 找不到的欄位與已刪除的模型、不可執行的
  Experiment 在送出前就被拒絕、一次 run 送出全部 Trial、比較表的指標欄位由實際
  run 產生、重跑不會產生重複列、無 Evaluation 時 leaderboard 仍以量測值成列
- `test_application_lifecycle.py` —— Application 只有 draft ⇄ published 一種生命週期、
  composed 應用有自己的頁面（`/applications/{id}/view`）且有 dashboard 就能發布、
  builtin 仍必須指定 entrypoint、
  沒有 entrypoint 不得發布、Deployment 端點已完全移除
- `test_chart_types.py` —— histogram 分桶不重不漏、box 的五數綜合與 Tukey 鬚線、
  heatmap 需要兩個類別軸、series 拆分、序位排序覆蓋字母排序、篩選運算子的型別寬容
- `test_standard_transforms.py` —— 22 個標準 Transform 的行為與拒絕條件，
  以及「多步串接不需中間程式碼」的組合性
- `test_dashboard_tiles.py` —— 建立後新增／調寬／重排／移除圖表，格線自動重排不留空洞
- `test_pipeline_composition.py` —— 以標準 Transform 逐步組出一條 Pipeline（UI 的實際路徑）、
  每步只加它承諾的欄位、產出可直接畫圖、參數對不上資料時只讓該步失敗且訊息可讀
- `test_model_versioning.py` —— **version 就是 definition**：pin 住的版本重跑結果不變、
  兩個版本可區分、未 pin 則跑 current version、未發布的修改會被回報（但改描述不算）、
  deprecate 不影響既有執行；capabilities 取代 type 判斷
- `test_model_type_coverage.py` —— **平台宣稱的 8 種 Model Type 都必須有 provider**；
  線性擬合能還原 y=3x+5、最佳化能找到已知極值並回報被約束剔除的候選數、
  模擬同 seed 可重現且直方圖涵蓋每一次試驗；無效設定在「建立時」就被拒絕
- `test_resource_scope.py` —— **一條 pipeline 只留下它的產出**：步驟定義內嵌之後，
  十二步的 pipeline 不再產生十二個 Model 與十二個 Dataset，因此
  `ModelScope` 與那兩趟事後重貼標籤的服務都被刪掉了
- `test_module_dependencies.py` —— **依賴方向**：以 AST 掃描每個模組的 import，
  只准往下層指；新增模組必須明確放進某一層
- `test_table_operations.py`、`test_columnar_transforms.py` —— **改寫不得改變行為**：
  被刪掉的 row-based 實作原封不動留在 `tests/row_oracles.py` 當 oracle，
  22 個 Arrow 版必須在一張刻意雜亂的表上（千分位數字、null、重複列、兩種時間格式）
  給出完全相同的答案；並直接斷言「沒有任何一個轉換會把輸入攤成 row」
- `test_query_pushdown.py` —— **量測工作量而不只看結果**：計算真正被物化成 dict
  的列數與從磁碟讀出的欄數，因為舊實作也回傳正確的那一頁，只是把整份資料都建了出來
- `test_outbound_security.py` —— SSRF 防護（拒絕私有網段與 metadata 端點、限制轉址
  次數）、DatabaseReader 只讀單一 SELECT 且權限獨立
- `test_workspaces.py` —— 兩個 workspace 可有同名資源、彼此看不見、
  **拿著別人的 id 直接查也讀不到**
- `test_projects.py` —— Project 是歸檔不是邊界：切換 project 換掉清單內容、
  **拿著 id 直接查不會被拒絕**、未歸檔者在每個 project 都看得到、
  指不到的 `X-Project` 被忽略而不是報錯；一次執行留下的 Execution／Result／
  輸出 Dataset 全部歸在工作發生的那個 project；Model 定義可設共用或收進專案
- `test_jobs.py` —— 背景工作：入列、lease、heartbeat、取消、重試上限、
  SSE 進度串流；worker 死亡後工作會被回收
- `test_execution_lifecycle.py` —— 取消不會被覆寫（結果落地前檢查）、
  卡在 RUNNING 的執行會被 heartbeat 判定失聯
- `test_serving.py` —— 同步 invoke 只回答案；API Key 只存雜湊、可撤銷、可限定 workspace
- `test_application_sharing.py` —— 分享連結是 capability：給了什麼、拒絕什麼、
  撤銷之後會怎樣
- `test_pipeline_join.py` —— 多輸入步驟讓 Pipeline 可以匯流；鍵欄位型別不一致會被對齊，
  缺欄位的錯誤訊息會說出是哪一邊少了哪一欄
- `test_provider_contracts.py` —— **契約不得與程式漂移**：每個 Provider 宣告的
  Contract 必須和它實際讀取的設定一致（這條測試抓出 optimizer 宣告成陣列卻當對映讀）
- `test_source_type_coverage.py` —— 宣稱的每一種 SourceType 都必須有 reader
- `test_required_datasets.py` —— 外掛用 `required_datasets` 宣告它需要的資料，
  資料因此走前門（版本化、可追溯），而不是直接開檔案
- `test_concurrent_workers.py` —— **真的開兩個執行緒搶同一列**：一次提交只能產生
  一份 Result、輸掉的 worker 回報現況而不是丟例外、已完成的工作不會被回收掃描復活
- `test_migrations.py` —— **遷移與模型必須一致**：`upgrade head` 產生的 schema 要和
  ORM 完全對得上（這條抓出 `import_all_orm_models` 漏掉一半模組）、每一支遷移都能
  downgrade 回 base、重新命名欄位不會弄丟資料
- `test_lineage.py` —— dashboard 能一路回溯到來源檔案、source 能說出誰讀它、
  被 pipeline 產生的 dataset 不是死路、containment 邊在兩個方向畫的是同一個方向
- `test_seed_fixtures.py` —— 宣告式 fixture：以名稱互相引用、重跑不產生重複、
  一個區段壞掉不會拖垮其他區段，以及**核心不得知道任何領域應用的存在**

也可一次跑完全部：`bash scripts/test.sh`

---

## 10. 開發規則（給人與 AI Agent）

見 [`AGENTS.md`](AGENTS.md)。核心七條：

1. Model 不等於 ML —— 禁止把 `Model` 當成 `MLModel` 的別名
2. Training 不是 Model 的必要生命週期
3. Prediction 只是 Execution 的一種
4. Core Domain 不得依賴 ML 框架
5. Framework-specific 程式碼只能住在 `plugins/`
6. Schema First —— 每個 Model 都要有 input / parameter / output 契約
7. Result 是一級 Domain 物件，不能只 `return dict`

---

## 授權

見 [LICENSE](LICENSE)。
