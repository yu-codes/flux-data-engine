# 一、先確立「這個專案應該成為什麼」

README 與 `AGENTS.md` 宣告的核心公式是：

```
Data → Model → Execution → Result → Application
Output = Model(Input, Parameters, Context)
```

從這個公式往回推，這個平台真正要成為的是：**任何可描述、可版本化的計算單元，都能被註冊、被執行、被留下證據、被組合成給別人使用的東西**。ML 只是其中一格。

以這個標準衡量，目前的實作大約完成了 **Data → Model → Execution → Result 這四段的 80%，而 Application 這一段只完成了 20%**，並且 `Model` 這個抽象在中途被分岔成了兩個平行世界（Model 與 Pipeline）。這兩件事是後面所有建議的主軸。

---

# 二、目前是對的，不要動

先明確說明，避免下面的建議被誤讀成全面翻修。以下幾項是這個專案明顯高於同類專案的地方：

| 決策 | 為什麼是對的 |
|---|---|
| **Model ≠ MLModel** | 這不是紙上抽象。11 個 provider 裡只有 1 個 trainable，抽象被實作壓力驗證過了。`test_model_type_coverage.py` 強制每個 `ModelType` 都要有 provider，這是很少見的自律。 |
| **Contract 作為單一 schema 原語** | `contracts.py` 同時描述 dataset schema 與 model 參數，因此 `ContractForm.vue` 可以遞迴生成任何 provider 的表單，新增 provider 不需要寫任何前端。這是整個專案最有槓桿的設計。 |
| **`definition_snapshot` = 執行的定義** | 版本不是標籤而是快照，`has_unpublished_changes` 用比對而非人工狀態計算。這解掉了「重跑舊版本得到新答案」這個很難察覺的 bug 類別。 |
| **單一 composition root** | `container.py` 是唯一命名 concrete repository 的地方，API / seeder / worker 共用。 |
| **Workspace scoping 放在 repository** | `scoping.py` 的 `_fetch` 拒絕跨 workspace 的直接 id 查詢——這是大多數專案會漏掉的那一半。 |
| **架構規則測試（fitness functions）** | `test_module_dependencies.py` 用 `ast` 走訪 import 圖擋上行依賴、`test_seed_fixtures.py` 擋 core 出現 "typhoon"。架構規則被自動化，而不是寫在文件裡靠自律。 |
| **jobs 在依賴堆疊最底層 + handler 注入** | 佇列不知道它跑什麼，這讓它保持是佇列而不是變成單一用途的排程器。 |
| **Arrow-native 資料路徑 + row oracle 測試** | 22 個 transform 全 columnar，並保留 row-based 版本當 oracle 對照。這是把效能改寫做對的方式。 |
| **安全邊界** | SSRF 檢查、SQL 單語句守衛、Formula AST allow-list、scrypt、HS256-only、單一 public route。密度高於絕大多數同規模專案。 |

這些不需要重新思考。

---

# 三、值得改善的項目

## H1 — Pipeline 是第二個 Model，但沒有 Model 的任何能力

### 問題

`Pipeline` 完全符合平台自己的公式：它有輸入（dataset）、有參數、有輸出（dataset）、有可版本化的定義。但它被實作成一個與 `Model` 平行的頂層概念，於是它拿不到 Model 已有的任何東西：

- **不能被排程**：`schedules.py` 的 `Schedule.model_id` 是必填，`ScheduleService.create` 還會呼叫 `self.executions.models.get(model_id)` 驗證。要排程一條每日 ETL pipeline 是做不到的。
- **不能被比較**：`ExperimentTrial` 只接受 `model_id`。無法比較兩條不同的資料處理流程。
- **不能被服務**：`/invoke` 只認 model。
- **不能被巢狀**：`PipelineStep` 只接受 `model_id` 或 inline provider，不能是另一條 pipeline。
- **沒有版本**：Model 有 `definition_snapshot`，Pipeline 沒有。改了 pipeline 之後，舊的 `PipelineRun` 說不出當時跑的是什麼——這正是 Model 已經修好、Pipeline 還沒修的同一個 bug。
- **多一套平行狀態機**：`PipelineRun` / `StepRun` 是 `Execution` / `Result` 的第二次實作，兩套各自的 status、error、metrics、取消語意。

更嚴重的是執行模型：`services.py` 對每個 step 都用 `force_inline=True`。整條 DAG 綁在單一 worker 的單一執行緒上。因此無法平行執行獨立分支、無法只重跑失敗的 step、無法快取已完成的 step、一條 12 步 pipeline 的失敗代價是全部重來。

### 為什麼需要改善

這不是「多寫一點程式」的問題，而是抽象漏了一層。每加一個橫向能力（排程、比較、服務、快取、重試、權限、血緣）都要為 Model 與 Pipeline 各實作一次。目前是 2 份，未來只要再出現第三種可執行物件（例如「一組模型的 ensemble」、「一個 LLM agent 流程」），成本就是 3 份。

### 建議方向

引入一個 **Runnable** 概念，讓 `Execution` 指向 runnable 而非 model：

```
Runnable  ─┬─ Model      (已有)
           └─ Pipeline   (加上 version / snapshot)
```

具體有兩種力度：

- **輕量做法**：把 Pipeline 包成一個 `composite` provider（`app/plugins/composite/`），DAG 存在 `configuration` 裡。Schedule / Experiment / Serving 一行都不用改就全部生效。
- **完整做法**：`Execution.target_type + target_id` 取代 `model_id`，orchestration 只負責「推進 DAG」——每個 step 是一個獨立的 Execution，`run()` 只排下一批 ready 的 step。這順帶讓 pipeline 可以平行、可續跑、可只重算下游。

無論哪種，`PipelineRun` / `StepRun` 應該退化成 Execution 的一個聚合視圖，而不是第二套實體。

### 預期價值

一次解掉排程、比較、服務、巢狀、續跑、平行六個限制；刪掉一整套平行狀態機；讓「加一種可執行物件」變成加一個 provider。

---

## H2 — Application 只是一組連結，平台最有價值的能力沒有出海口

### 問題

`Application` 目前的實質內容是：`model_ids[] + dataset_ids[] + dashboard_ids[] + status + share_token`，而 `rendering.py` 只渲染 dashboard。也就是說 **composed application 唯一能給終端使用者的東西是靜態圖表**。

但平台最有價值的能力是「輸入參數 → 執行模型 → 得到答案」。這個能力的所有零件都已經存在：`ContractForm.vue` 能遞迴渲染任何 provider 的輸入契約、`/invoke` 能同步取得答案、`ResultPayload` 能描述輸出。**它們只服務於建立模型的管理員，不服務於使用應用的人。**

最能說明問題的證據是 typhoon：它是這個平台唯一一個真正有使用者的應用，而它必須是 `BUILTIN`——自己的 `routes.py`、自己的 `TyphoonPage.vue`、自己在 `contrib.py` 註冊 router。換句話說，**唯一成功的應用是繞過應用機制寫死的**。這跟「Model ≠ MLModel 用 10 個非 ML provider 驗證」是同一種檢驗，而這次沒有通過。

### 為什麼需要改善

`ApplicationKind.BUILTIN` 這個後門的存在，意味著任何真實應用都會走它。長期下來 `app/plugins/*/routes.py` 會變成第二個 API 層，而 `contrib.py` 的 router 擴充點會從「例外」變成「常態」——那時 `app/` 不指涉任何 domain 這條規則會在形式上成立、在實質上失效。

### 建議方向

把 Application 從「一組 id」改成**由 page 組成、page 由 block 組成**：

```
Application → Page[] → Block[]
Block kind: dashboard | chart | text | model_form | result_view | table
```

`model_form` block 綁一個 model（或 H1 之後：一個 runnable）+ 它的 input contract，直接複用 `ContractForm` 與 `/invoke`。

**驗收標準應該定得很明確**：typhoon 應用能被 declarative 地重建成一個 COMPOSED application，`plugins/typhoon_analog/routes.py` 與 `TyphoonPage.vue` 可以刪掉。做不到就代表抽象還不夠。

### 預期價值

Application 從「一組連結」變成產品的出海口；built-in 後門可以收掉；`/shared/{token}` 分享的東西從靜態圖表變成可互動的工具，這是分享連結真正的價值所在。

---

## H3 — 兩個 worker 可能執行同一個 Execution（無悲觀鎖／樂觀鎖）

### 問題

整個 codebase 沒有任何 `with_for_update`、`version_id_col` 或條件更新。`services.py` 的 `run()` 流程是：讀取 → 檢查 `is_terminal` → `mark_running()` → `update()`。這中間沒有原子性。

同時 `worker.py` 的復原掃描會把「PENDING 超過 120 秒」的 execution 重新入列。一個因為 GC、網路延遲或多 worker 競爭而慢了兩分鐘才被領取的 execution，會同時存在於「被重新入列」與「正在被執行」兩個狀態。

後果不是崩潰而是**靜默重複執行**：兩份 Result、兩份 materialised Dataset、訓練任務寫兩次 artifact、外部呼叫做兩次。

### 為什麼需要改善

這是唯一一個我認為屬於「正確性」而非「設計」的項目。目前 `execution_mode` 預設 `inline` 且開發時通常單 worker，所以還沒有暴露；一旦多開 worker 就會出現，而且很難從症狀回推原因。

### 建議方向

領取改成一次原子的條件更新（`UPDATE ... SET status='running', attempts=attempts+1 WHERE id=? AND status='pending'`，檢查 rowcount），Job 同理。並補一個並發測試——`tests/` 目前 409 個測試裡沒有任何一個開兩個 worker。

### 預期價值

讓 queue 模式從「單 worker 才安全」變成真的可水平擴展。

---

## M1 — 「一個輸出如何被呈現」被實作了四次

### 問題

- `Result` 有 11 種 `ResultKind`，但只有 TABLE 能被 materialise、能畫圖、能進 report
- `Visualization` 綁 `dataset_id` 或 `result_id`（兩個來源，兩條路徑）
- `ReportSection` 有 7 種 kind，`services.py` 的 `_resolve()` 是一個 90 行、7 分支的方法
- `DashboardTile` 只能放 visualization

每新增一種輸出型態（例如影像、地理、時間序列、模型解釋），要改四個地方。`_resolve()` 的長度不是風格問題，是這個缺失抽象的症狀。

### 建議方向

引入統一的 `RenderedBlock`：`(source_ref, view_spec) → rendered`。`ReportSection`、`DashboardTile`、Application `Block` 都成為它的容器。這同時是 H2 的地基——兩件事應該一起做。

### 預期價值

新增輸出型態的成本從 4 處降到 1 處；報表、儀表板、應用三者的呈現能力自動對齊（目前是三套不同的子集）。

---

## M2 — Metric 沒有契約，所以「比較」站不住

### 問題

`Experiment` 用 `primary_metric` 這個字串去排序 trial 的 `metrics` dict，而 `metrics` 是 provider 自由回傳的。因此：

- 比較兩個不同 provider 的模型時，沒有任何東西保證兩邊的 `rmse` 是同一個定義
- 排序寫死「高分優先」，但 RMSE 是越低越好——方向沒有被宣告
- `leaderboard()` 需要 60 行去 merge `Evaluation` 與 `Execution` 兩個 metric 來源
- `ExecutionKind.EVALUATION` 存在於列舉裡但**沒有任何 provider 實作它**，代表「評分」這件事本身沒有變成一個可插拔的計算單元——它被寫死成 `EvaluationService.record()`

### 建議方向

- `PluginDescriptor` 增加 `metrics_contract`：每個 metric 宣告 name / direction / unit / higher_is_better
- 把「評分」做成 evaluator provider（輸入 = 預測 + 真值，輸出 = metrics）。`Evaluation` 就變成一次 Execution 的結果，而不是第三種記錄輸出的方式
- leaderboard 從「merge 兩個來源」變成「讀 evaluation execution 的 result」

### 預期價值

比較從「字串巧合」變成「契約保證」；`ExecutionKind.EVALUATION` 這個空承諾被兌現（照 `test_model_type_coverage.py` 的精神，應該也要有一個 `test_execution_kind_coverage`）。

---

## M3 — Plugin 契約只描述資料形狀，不描述執行特性

### 問題

`PluginDescriptor` 完整描述了輸入／參數／輸出／設定的形狀，但完全沒有描述：預期耗時、記憶體、是否需要 GPU、是否可平行、是否為純函數（可快取）、失敗是否可重試、plugin 版本。

因此所有 execution 共用同一個 lease（600s）、同一個 timeout（900s）、同一個 retry 上限（3）。一個 3 秒的 formula 與一條 40 分鐘的回測被當成同一種東西。

更關鍵的是**逾時與取消完全靠 provider 自律**：`context.should_stop()` 是合作式的，平台沒有強制手段。一個沒有寫檢查迴圈的 plugin 會無限期佔住 worker。`RuntimeKind.CONTAINER` 與 `EXTERNAL_API` 在列舉裡但沒有實作——也就是「執行不信任的程式碼」這條路還沒開。

### 為什麼需要改善

插件架構的價值在於第三方能寫 plugin。目前的信任模型是「所有 plugin 都是我們寫的、都很守規矩」。這在 built-in 階段成立，一旦要接受外部 plugin 就完全不成立，而屆時要補的是執行模型而不是一個欄位。

### 建議方向

- `PluginDescriptor` 加 `version`、`resources`（memory/gpu/timeout hint）、`pure`（可快取）、`parallelisable`
- 執行層用 process 隔離 + 硬性 timeout（`should_stop()` 保留為讓 provider 優雅早停的機制，而不是唯一機制）
- `CONTAINER` runtime 作為不信任程式碼的實際路徑
- `ModelVersion` 記錄 provider key **與 version**，執行時不符則標記——目前的可重現性只保證到 definition，不保證到執行環境（sklearn 升版後同一個 snapshot 會給出不同答案，而記錄上看不出來）

---

## M4 — Dataset 只有「全量重讀 → 新版本」一種語意，而血緣不可查詢

### 問題

`refresh()` 重讀整個 source 產生一個新版本。沒有增量、沒有分區、沒有 upsert。對「資料平台」這個定位而言：

- 每日更新的來源會產生 N 份完整副本
- 讀取路徑是「整份讀成一個 Arrow Table 再寫 Parquet」，資料量上限被單機記憶體綁住
- 沒有 view / 邏輯資料集的概念——「A 的過濾結果」只能靠 pipeline 物化成 B

另一半是血緣。`lineage` 目前只是散落在 Dataset / Execution / Model 各自的一個 JSON dict（14 個檔案裡的 31 處），**沒有任何可查詢的血緣 API**。使用者無法問「這個 dashboard 上的數字是怎麼來的」，也無法問「我改這個 source 會影響什麼」。對一個以可追溯性為賣點的平台，這是核心能力而不是加分項。

### 建議方向

- Dataset 增加 refresh policy（replace / append / partition key），並在 H1 完成後讓 refresh 本身可被排程
- `lineage` 從 JSON 欄位升級為一張 edge 表（`from_type/from_id → to_type/to_id → via_execution_id`），加一個 `/lineage/{type}/{id}?direction=up|down` 圖形端點，前端已有 `PipelineGraph.vue` 可以直接複用

### 預期價值

血緣圖是這個平台目前擁有全部資料、卻沒有拿出來用的東西——所有的 `lineage` dict 已經寫進去了，只是查不出來。這是投入產出比最高的一項。

---

## M5 — 產品流程：核心敘事沒有被做成流程

### 問題

側邊欄 4 組 17 項，且組織方式很好（按工作流不是按模組）。但要走完 README 第一行那條路徑（上傳 → 建模型 → 執行 → 看結果 → 做圖 → 組應用）要經過 8 個頁面與多個對話框，而**這條路徑沒有被做成一個引導流程**。`DashboardPage` 上有一張 Data → Model → Execution → Result → Application 的圖，但它是裝飾而非導引。目前的 onboarding 策略是「seed 一個 typhoon 範例，讓使用者自己去看」。

另外兩個斷層：

- **Explore 只能 filter / sort / chart**，不能 join、不能寫查詢式。任何組合操作都得離開 Explore 跳去 Pipeline 重新設定一次，而 Explore 裡剛做好的過濾條件不能一鍵變成 pipeline step。
- **Executions / Results / Evaluation 三個獨立列表**在使用者心智裡都是「發生過的事」，卻是三個入口。Results 已經有 `execution_id` 反向連結，實務上使用者要在兩個列表之間來回對照。

### 建議方向

- 一條可跳過的 guided first-run（不是取代 seeded example，是補上「我自己的資料怎麼走一遍」）
- Explore 加一個出口：「把目前的過濾／選欄存成 pipeline step」或「存成新 dataset」
- Executions / Results 合併為單一「Runs」視圖（一列一次執行，展開看結果），Evaluation 在 M2 完成後自然併入

---

## L1 — 幾個服務方法過長

`ReportService._resolve()`（90 行 / 7 分支）、`ExecutionService.submit()`（90 行）、`ExperimentService.compare()`（80 行）。

**但我不建議單獨處理這些。** 它們是 M1、H1、M2 的症狀而非原因——`_resolve()` 的 7 個分支正是「呈現層被實作四次」的那一份；抽象修好之後它們會自然縮短。單獨拆分只會把同樣的複雜度分散到更多檔案裡。

## L2 — 可觀測性差最後一哩

有 Prometheus（uptime / HTTP 計數 / 延遲直方圖）、audit trail、correlation id。缺的是：

- correlation id 產生了但**沒有進 log**
- log 是純文字不是 JSON，不利於彙整
- **沒有 execution 層級的指標**。對一個以「執行」為核心的平台，`flux_execution_duration_seconds{provider, kind, status}` 比 HTTP 指標重要得多——目前無法回答「哪個 provider 最慢」「失敗率趨勢如何」

## L3 — 測試缺口

409 個測試，架構規則測試是明顯亮點。缺口按重要性排：

1. **並發測試**（見 H3，這條其實應該算 High）
2. **Migration 測試**：14 個 alembic migration 沒有任何 upgrade/downgrade 驗證，而其中有 `demote_pipeline_intermediates` 這種會動既有資料的
3. 前端無單元測試（但有 `check:layout` 這個很務實的替代）
4. 效能測試：`test_query_pushdown.py` 驗證了「碰到多少行多少欄」，這其實比 timing 測試更穩健，算是有覆蓋

---

# 四、優先改善項目

### High — 建議優先處理

| # | 項目 | 核心理由 |
|---|---|---|
| **1** | **H3 — Execution／Job 領取加原子性** | 唯一的正確性問題。修復成本小（條件更新 + 一個測試），不修則 queue 模式無法水平擴展且失敗是靜默的。 |
| **2** | **H1 — 統一 Runnable 抽象（Pipeline 成為可執行物件）** | 這是架構的主要裂縫。不修，每個橫向能力都要做兩次；修了，排程／比較／服務／巢狀／續跑／平行一次全開。 |
| **3** | **H2 — Application 由 page/block 組成，含 model_form** | 產品承諾與實作落差最大處。驗收標準明確：typhoon 能被 declarative 重建、built-in 後門可收掉。 |

> 三者有順序關係：H3 獨立可先做；H1 是 H2 的地基（block 綁 runnable 比綁 model 更有價值），但 H2 也可先用現有 model 做出第一版。

### Medium — 值得改善

| # | 項目 | 核心理由 |
|---|---|---|
| **4** | **M4 — 血緣圖可查詢** | 資料已經全部寫進去了，只差一張 edge 表與一個端點。投入產出比最高。 |
| **5** | **M1 — 統一 RenderedBlock** | 呈現層目前實作四次；是 H2 的地基，建議與 H2 合併規劃。 |
| **6** | **M2 — Metric 契約化 + evaluator provider** | 讓「比較」從字串巧合變成契約保證，並兌現 `ExecutionKind.EVALUATION` 這個空承諾。 |
| **7** | **M3 — Plugin 宣告執行特性 + 版本 + 硬性隔離** | 決定這個平台能不能接受第三方 plugin。現在不急，但要在開放前解決，且屆時要動的是執行模型不只是欄位。 |
| **8** | **M5 — Guided first-run 與 Explore→Pipeline 出口** | 概念數量對第一次使用者偏高，而核心敘事沒有被做成流程。 |
| **9** | **M4 下半 — Dataset refresh policy（增量／分區）** | 「資料平台」定位的必要條件，但可以等有真實大資料需求時再做。 |

### Low — 可以之後再處理

| # | 項目 | 核心理由 |
|---|---|---|
| **10** | **L2 — execution 指標 + correlation id 進 log + JSON log** | 有現成基礎設施，補上成本低但不阻擋任何東西。 |
| **11** | **L3 — Migration 測試** | 隨 migration 數量增加而變重要。 |
| **12** | **L1 — 長方法拆分** | 建議**不要單獨做**，等 H1/M1/M2 完成後再看還剩什麼。 |
| **13** | **API 版本協商** | `/api/v1` 目前只是前綴。單一部署、前後端同時發佈，暫時不成問題。 |

---

## 最後一點觀察

這個專案有一個很少見的特質：**它的架構規則是可執行的**（`test_module_dependencies.py`、`test_model_type_coverage.py`、`test_seed_fixtures.py`、`check:layout`）。AGENTS.md 裡的每一條規則幾乎都有對應的測試在守。

因此我的建議是，上面每一項改善都**先寫規則測試再改實作**，跟這個專案既有的做法一致。例如：

- H1 → `test_runnable_coverage.py`：每個 Runnable 型別都能被 Schedule / Experiment / Serving 接受
- H2 → `test_application_composition.py`：typhoon 應用能以 COMPOSED 形式表達，且 `app/plugins/*/routes.py` 為空
- M2 → `test_execution_kind_coverage.py`：每個 `ExecutionKind` 至少有一個 provider

這樣改完之後，抽象不會在下一次趕工時退回去——那正是這個 codebase 目前最值得保護的東西。