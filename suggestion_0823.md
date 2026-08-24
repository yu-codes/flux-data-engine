## 先講結論：哪些設計是對的，不要動

這些不是客套話，是我確認過、而且**建議明確保護起來**的決策：

| 決策 | 為什麼是對的 |
|---|---|
| `Model ≠ MLModel` | 10 個 provider 只有 1 個 trainable，抽象真的被實作壓力驗證過，不是文件上的口號 |
| Plugin 邊界 | app/modules/model/domain/plugin.py 的 `ExecutionContext` 不帶 session / repository / request，plugin 是純函數。這是整個專案最值錢的一條線 |
| `definition_snapshot` 真的被執行 | ExecutionService._definition_for 讓「版本 = 執行的定義」成立，而不是標籤 |
| `has_unpublished_changes` 用比較算出來、不儲存 | 狀態不會和事實打架 |
| 單一 composition root | app/core/container.py 是唯一命名具體 repository 的地方，且 API / seeder / worker 共用 |
| `RedisQueueDispatcher` 用 `after_commit` 才 push | `dispatch.py:47` 避開了 worker 讀到未 commit row 的經典 bug |
| Contract 同時描述 dataset schema 與 model 契約 | 讓 `validate_schema` 這種跨概念檢查成為可能，而不需要第二套型別系統 |

**這個專案的核心抽象是健康的。** 我找到的問題幾乎全部在核心之上一層 —— 在「平台的規模假設」與「產品的邊界」上。

---

## 高層次問題

### 1. 整個資料路徑是 `list[dict]` in-memory —— 這是產品天花板，不是效能議題

**問題**
Table 是 Arrow 的包裝，但實際上**沒有任何運算走 Arrow**：

- ExploreService.query：`table.to_rows()` 之後在 Python 做 filter / sort / 分頁
- `services.py:290`：`to_rows()` 之後逐欄掃描算統計
- `services.py:77`：`to_rows()` 之後在 Python 做 group by
- PythonTransformPlugin.execute：`rows = context.input.rows()` → 22 個標準 transform 全部是 `fn(list[dict], options) -> list[dict]`
- DatasetService.read_table：整份 Parquet 讀進來；S3 backend 的 `local_path()` 會把整個物件下載下來

**為什麼需要改善**
一個 12 步 pipeline 的實際成本是：整表讀進記憶體 → 轉成 dict list → Python 迴圈 → 轉回 Arrow → 寫 Parquet → **再整份讀回來** ×12。這不是「以後再優化」的事，它決定了這個產品能不能自稱資料平台。目前的實作在幾十萬列就會結束，而 README 第一行寫的是「通用的資料、模型與計算執行平台」。

同時 `Explore` 頁的分頁是假的分頁 —— 已經全讀進來了，`MAX_QUERY_ROWS = 5000` 只限制回傳量，不限制讀取量。

**建議方向**
1. 把 `Table` 從「Arrow 容器 + `to_rows()`」升級成真正的運算介面：`filter / select / aggregate / sort / limit / join`，以 `pyarrow.compute` 或 DuckDB（讀 Parquet 天然支援 predicate/projection pushdown）實作。
2. `ExploreService` 與 `build_series` 改成**下推**，不再 `to_rows()`。
3. Transform 契約從 `fn(rows, options) -> rows` 改成 `fn(Table, options) -> Table`；保留一個 `rows_transform()` adapter，讓現有 22 個轉換可以分批遷移而不是一次改完。
4. 中繼 dataset 不必每步都完整落地 —— 見第 4 點。

**價值** 這一項解開之後，「pipeline / explore / chart / transform」四塊同時脫離玩具規模。它是所有其他改善的前提。

---

### 2. 沒有 Workspace / Owner —— 這是「不像成熟產品」的最大單一原因

**問題**
- `Dataset`、`ModelDefinition`、`Execution`、`Visualization`、`Report` 全部**沒有 owner、沒有 created_by、沒有 project**。`entities.py:38` 完全不知道是誰送出的（只有 audit log 側面記錄）。
- 名稱是**全域唯一**的：`get_by_name` 衝突就 409。兩個人不能各自有一個叫「Sales」的 dataset，一個團隊不能有 dev/prod 兩份同名 pipeline。
- RBAC 是 `role → module → verb`（[security.py](backend/app/api/security.py#L86)），沒有資源層級授權。任何 viewer 看得到平台上所有東西。

**為什麼需要改善**
`ModelScope.STEP` 和 `DatasetOrigin.INTERMEDIATE` 這兩個列舉值的存在，本身就是這個缺口的徵狀 —— `AGENTS.md` 說「12 步 pipeline 讓 model library 48% 是管線零件」，於是加了 scope 把它藏起來。但那不是分類問題，是**命名空間問題**。用 scope 遮蔽自己製造的噪音，會在下一個資源類型再發生一次（Visualization 已經開始了：16 個颱風圖表擠在同一個列表）。

而且沒有 owner，「誰改壞了這個 model」在 audit log 之外無法回答，`Report`/`Dashboard` 也無法分享給特定人。

**建議方向**
引入 `Workspace`（或 `Project`）作為所有可命名資源的容器：唯一性約束改成 `(workspace_id, name)`，列表預設 scope 在 workspace，權限從 `role` 變成 `(user, workspace, role)`。所有資源加 `created_by`。這是一次 migration + 一層 query filter，越晚做越貴。

**價值** 這一項做完，平台從「一個人的工作台」變成「一個團隊的平台」，同時 `ModelScope` / `DatasetOrigin.INTERMEDIATE` 這類補丁可以退回它們原本應該只是「顯示偏好」的角色。

---

### 3. 「會跑很久的東西」有四種，只有一種是非同步的

**問題**

| 長時作業 | 目前如何執行 |
|---|---|
| Execution | ✅ inline 或 Redis queue |
| **PipelineRun** | ❌ 強制 inline，且 `pipelines.py:157` 直接改別的 service 的策略 |
| **Experiment run** | ❌ 在 request 內逐 trial submit |
| **Report export** | ❌ 同步渲染 |

三個問題疊在一起：
- 一個 12 步 pipeline **在 HTTP request 內同步跑完**。這是 request timeout 的必然結果。
- `PipelineService.run()` 直接改寫 `executions.dispatcher` 是隱藏的全域副作用 —— 該 `ExecutionService` 實例在同一個 request 內之後所有的 submit 都會變 inline。
- `cancel()` 對 RUNNING 的 execution **是謊言**：ExecutionService.cancel 標記 cancelled，但 worker 從不檢查，跑完之後 `mark_succeeded` 會蓋回去。
- worker 沒有 lease / heartbeat：如果 worker 在執行中 OOM，那筆 execution 永遠停在 RUNNING（recovery sweep 只撿 PENDING）。
- provider 沒有 timeout 也沒有資源上限。`optimizer` 的 grid search 可以無限跑。
- 前端**完全沒有 polling / SSE / WebSocket**，queue mode 下使用者永遠看不到完成。

**建議方向**
抽一個 `Job` 概念（或讓 PipelineRun / ExperimentRun 也實作同一個狀態機並走 dispatcher）：統一的 pending/running/terminal、統一的 queue、統一的 lease + heartbeat、cooperative cancellation token 傳進 `ExecutionContext`、per-job timeout。前端只需要一個 `GET /jobs/{id}/events`（SSE）就能同時解決四種等待。

**價值** 這是「demo 能跑」和「production 能用」之間最直接的那條線。

---

### 4. Pipeline 把 step 壓成 Model，代價比看起來大

**問題**
一個 12 步 pipeline 目前產生：12 個 `ModelDefinition` row + 12 個 `ModelVersion` + 12 個 `Execution` + 12 個 `Dataset` + 12 個 `DatasetVersion` + 12 個 `DataSchema` + 12 個 Parquet 檔。然後需要 `ModelScope.STEP` 和 `DatasetOrigin.INTERMEDIATE` 兩個列舉值 + `pipelines.py:254` + `pipelines.py:270` 兩個方法，去把自己造出來的東西藏起來、改名。

另外 graph 只能分支不能合流 —— `pipelines.py:7-10`誠實地承認了：因為每個 provider 契約是「一表進一表出」，所以沒有 join。

**為什麼需要改善**
「重用同一條執行路徑」的直覺是對的；「重用同一個**實體**」是過頭了。一個 step 的配置沒有獨立生命、不需要被搜尋、不需要版本、不需要出現在任何列表。把它變成 library 實體，就必須再發明一個機制把它變回不可見。

而 join 缺席對資料平台是硬傷 —— 那是最常見的資料操作，而現在的契約形狀讓它無法表達。

**建議方向**（架構方向，不建議立刻動手）
- Step 直接持有 `provider + configuration`（等同一個 inline 的 definition snapshot），不建立 library model。執行路徑不變 —— `ExecutionService` 本來就接受 `ModelDefinition` 物件。這樣 `ModelScope` 可以整個消失。
- 把 `ExecutionInput.table` 擴成 `inputs: dict[str, Table]`（註解已經預期這件事），讓 join-shaped provider 成為可能。
- 中繼輸出預設不 materialise 成 Dataset，改成 run 內部的 checkpoint；只有 terminal step 產生 Dataset。這樣 `DatasetOrigin.INTERMEDIATE` 也可以消失。

**價值** 一個 12 步 pipeline 從 72 個 row 降到 ~14 個；兩個補丁列舉值歸零；join 變成可能。

---

### 5. `Application` 這個概念目前兩邊都不成立

**問題**
`ApplicationKind` 有兩種：
- `BUILTIN`：`entrypoint` 指向一個前端**硬編的路由**（`/applications/typhoon`）。也就是說新增一個內建應用一定要改 frontend router、加一個 .vue、重新 build。
- `COMPOSED`：`entities.py:30` 只是 `model_ids + dataset_ids + dashboard_ids + status`。它不決定 layout、不決定給誰看、沒有自己的 URL、沒有對外分享。

所以 `Data → Model → Execution → Result → **Application**` 這條產品線的最後一段，實質上是空的。同時 `Dashboard`（tiles of viz）、`Report`（sections referencing viz/result/execution/model）、`Application`（三個 id list）三者職責高度重疊卻各有一套 CRUD。

**建議方向**
二選一，不要停在中間：
- **做實**：Application = 「一組已發佈的 Dashboard/Report + 存取控制 + 對外 URL（含 public link / embed token）+ 選填的輸入表單」。這樣它才配得上「最後一段」的位置。
- **移除**：承認它是 Dashboard 的 `published` 狀態，砍掉整個模組。

另外 `Dashboard` 與 `Report` 建議收斂成同一個 `Composition` 抽象的兩種 render target（互動 vs 匯出），而不是兩套 section/tile 模型。

**價值** 產品敘事的終點終於有東西，或至少不再有一個承諾了但沒兌現的名詞。

---

### 6. 缺少 Serving —— 模型平台最直接的價值兌現路徑

**問題**
目前唯一呼叫模型的方式是 `POST /executions`，它會寫一筆 Execution row、persist 一個 Result、可能還 materialise 一個 Dataset。這對「批次、可追溯」是完美的，對「線上、低延遲、每秒數次」完全不適用。沒有 `POST /models/{slug}/invoke`，也沒有 API key（只有給人用的 JWT）。

**為什麼需要改善**
一個模型平台的用戶會問的第一個整合問題就是「我怎麼從我的系統呼叫它」。目前的答案是「拿使用者密碼換 JWT，然後每次呼叫都在你的資料庫寫三筆 row」。

**建議方向**
明確區分兩個動詞：`submit`（可追溯、落 Execution、可排隊）與 `invoke`（同步、契約驗證後直接跑 plugin、不落 Execution 或抽樣落）。加上 service account / API key。plugin 契約完全不用改 —— `ExecutionContext` 本來就是純的。

**價值** 這是把「平台」變成「產品」最短的一條路，而且成本很低，因為底層已經是純函數。

---

### 7. Contract 的表達力已經卡住 UI 的自動生成承諾

**問題**
Contract 只有平面的 `OBJECT / TABLE / SCALAR / FREE`。沒有 nested object、沒有 array-of-object、沒有條件顯示、沒有跨欄位驗證。

結果：前端 `ContractForm` 對複雜 provider 無法產生表單，參數退化成 JSON textarea。`optimizer`（變數界限 + 約束式）、`monte-carlo`（分布定義）、`rule`（IF/THEN 規則列表）正是最需要表單的三個，也正是退化最嚴重的三個。

**為什麼需要改善**
「新增 provider 不需要改 UI」是這個架構最核心的擴充性承諾。契約表達力不足，這個承諾就只對簡單 provider 成立 —— 而簡單 provider 本來就不需要它。

**建議方向**
`FieldSpec` 加 `fields: list[FieldSpec]`（nested object）與 `item: FieldSpec`（array element），再加一個 `visible_when: {field, equals}`。這比引進 JSON Schema 便宜得多，而且完全不破壞 `domain` 零依賴的規則。`ContractForm` 對應改成遞迴元件。

---

### 8. 旗艦應用的資料走側門

**問題**
平台宣稱「所有外部格式在邊界正規化成 Dataset」，但 typhoon 引擎直接讀 `data/typhoon/preprocessed/*.json` 與 `.npz`。執行走前門（Execution / Result 一應俱全），**資料走側門**。

代價：這份資料不可版本化、不可追溯 lineage、不可換來源、部署要另外掛 volume、README 要花一整節解釋「檔案不在時會怎樣」。

**建議方向**
讓 `PluginDescriptor` 可以宣告 `required_datasets`（依 slug / tag 解析），engine 從 `DatasetVersion` 讀 `Table`。這同時把「換一批歷史紀錄重跑回測」從「換檔案重啟」變成一次平台操作 —— 那正是這個平台存在的理由。

---

### 9. 具體的層次違規（會擴散的那種）

不是 style 問題，是會長大的結構問題：

- **model/api/routes.py 785 行，`experiment_leaderboard`（L451-L534）與 `compare_experiments`（L544-L620）整段領域邏輯寫在 route function 裡** —— 「哪一次 run 代表哪個 trial」「如何排名」「metric 欄位如何發現」是核心的比較語意，卻無法被 pipeline、report 或 API 之外的任何呼叫端重用，也無法單獨測試。這直接違反專案自己的規則「Business logic never lives in a route」。應該搬進 `ExperimentService`。
- **`data` 模組裝了 Pipeline** —— Pipeline 是編排，依賴 `ExecutionService` 與 `ResultService`，於是 `data → execution` 這條反向依賴被寫進了 `container.py:146-151`。AGENTS.md 畫的依賴圖是 `data → model → execution`，實際上 data 依賴 execution。應該獨立成 `orchestration` 模組（Schedule 未來也搬過去）。
- **`model` 模組同時裝 Model / Version / Plugin 契約 / Registry / Experiment / Evaluation** —— Experiment 與 Evaluation 是「衡量」的概念，生命週期和 Model 定義不同，且依賴 Execution + Dataset。它們讓 `model` 變成事實上的樞紐模組。建議獨立成 `evaluation`。
- **`SourceType.OBJECT_STORAGE` 沒有 reader** —— `readers.py:263` 只註冊 8 個，enum 有 9 個值。AGENTS.md 明文寫「Every value in `SourceType` must have a reader」，而 `ModelType` 有對應的 `test_model_type_coverage.py` 保護，`SourceType` 沒有。要嘛補 reader，要嘛移除該值，並補上對應的測試。

---

### 10. 前端：缺三樣東西

- **型別是手寫的**（~500 行 `types/index.ts` 對應後端 pydantic）。FastAPI 已經產生 OpenAPI，接上 `openapi-typescript` 是一次性成本，之後 schema 漂移在 CI 就會被抓到，而不是在 runtime。
- **沒有任何測試**。AGENTS.md 描述的 UI 檢查方式是「需要時再重新推導那個 headless harness」—— 那等於沒有。至少該把它固化成 `npm run check:layout`。
- **沒有任何共享的資料層**。每個頁面各自 fetch，編輯 model 之後其他頁面顯示舊值，queue mode 下 execution 完成沒有任何提示。第 3 點的 SSE 加上一層薄快取可以一次解掉。

---

### 11. Seed 是產品的一部分，但也是負債

三個 seed 檔 1,675 行（`seed_typhoon_climatology.py` 679 行比大部分模組都大），住在 `app/core/`，寫死了颱風領域的 16 個 visualization、4 個 dashboard、2 個 report。

`AGENTS.md` 說「Adding or removing a built-in application must never edit a file under `app/core/`」—— 但 seed 正在 `app/core/`，而且滿是颱風。

**建議**：seed 改成宣告式 fixture（YAML/JSON）+ 一個通用 loader，颱風那份 fixture 住進 plugin 目錄。「新增內建應用」就真的不用碰 core 了。

---

### 12. 安全性：兩個需要處理的外部輸入面

- **`RestApiReader` 沒有 SSRF 防護**（[readers.py L218](backend/app/modules/data/infrastructure/readers.py#L218)）：只檢查 `http(s)://`，沒有阻擋 loopback / 私有網段 / `169.254.169.254`（雲端 metadata endpoint），也沒有限制 redirect。任何 `editor` 可以用平台當跳板探測內網並讀出結果。OWASP A10。
- **`DatabaseReader` 接受任意 `url` 與任意 `query` 原文執行**（`readers.py:189`）：`table` 有做字元檢查，但 `query` 直接 `text(query)` 執行。這在「資料來源」的語境下有其道理，但它實質上等於「任何 editor 可以對任何可達的資料庫執行任意 SQL」，這個權限層級應該被明確設計（獨立 permission、connection allow-list、唯讀強制），而不是隱含在 `DATA_WRITE` 裡。

---

## 優先改善項目

### High —— 建議優先處理

| # | 項目 | 理由 |
|---|---|---|
| 1 | **資料路徑改為運算下推**（Arrow/DuckDB，去掉全域 `to_rows()`） | 決定產品規模上限；是其他多數改善的前提 |
| 2 | **引入 Workspace/Project + resource owner** | 「不像成熟產品」的最大單一原因；越晚做 migration 越貴 |
| 3 | **統一長時作業為可排隊、可取消、有 lease 的 Job** | 目前 pipeline / experiment / report 在 request 內同步跑；cancel 對 RUNNING 是謊言；worker 掛掉會留下永久 RUNNING |
| 4 | **SSRF 與資料庫來源的權限邊界** | 可被利用的真實漏洞，不是理論風險 |
| 5 | **`experiment_leaderboard` / `compare_experiments` 從 route 搬進 service** | 已經違反專案自己的規則，而且是最容易被下一個功能複製的壞樣板 |

### Medium —— 值得改善

| # | 項目 | 理由 |
|---|---|---|
| 6 | **Contract 支援 nested / array-of-object / 條件顯示** | 直接解鎖「新增 provider 不需改 UI」對複雜 provider 也成立 |
| 7 | **Pipeline step 不再是 library Model；中繼不落 Dataset** | 消掉 `ModelScope` 與 `DatasetOrigin.INTERMEDIATE` 兩個補丁；資源數量降一個量級 |
| 8 | **加入 `invoke`（同步 serving）+ service account / API key** | 成本低、價值高，底層已是純函數 |
| 9 | **決定 `Application` 的命運**（做實成可發佈介面，或移除） | 產品線終點目前是空的；`Dashboard`/`Report`/`Application` 三者收斂 |
| 10 | **`Pipeline` 移出 `data` → `orchestration`；`Experiment/Evaluation` 移出 `model` → `evaluation`** | 修正已經存在的反向依賴，讓依賴圖回到 `AGENTS.md` 宣稱的樣子 |
| 11 | **前端型別由 OpenAPI 產生；加入 SSE 與薄快取層** | 消除型別漂移；解決 queue mode 下使用者看不到完成 |
| 12 | **plugin 可宣告 `required_datasets`，讓 typhoon 資料走前門** | 讓平台的資料承諾對旗艦應用也成立 |

### Low —— 可以之後再處理

| # | 項目 |
|---|---|
| 13 | Seed 改成宣告式 fixture，颱風那份搬進 plugin |
| 14 | 支援多輸入 provider（join），讓 pipeline graph 可以合流 |
| 15 | `SourceType.OBJECT_STORAGE` 補 reader 或移除，並補上 coverage test |
| 16 | 前端加入測試框架，把 layout 檢查 harness 固化成 npm script |
| 17 | provider 層級的 timeout / 資源上限；execution retry 上限與 dead-letter |

---

## 一句話總結

**核心抽象（Model / Execution / Result / Contract / Plugin）已經是好的，而且是被實作壓力驗證過的好。** 目前的距離不在這裡 —— 在於這個平台的所有實作都假設「一個人、一份資料、一次跑完、全部塞進記憶體」。把這三個假設（規模、租戶、非同步）逐一拿掉，它就是它文件裡描述的那個東西；不拿掉的話，它會一直是一個做得非常好的示範。

我沒有動任何程式碼。要開始的話，我建議從 High #1 或 #2 挑一個，我可以先出一份具體的實作計畫（影響的模組、migration、測試）再動手。