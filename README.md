# ⚡ Flux Data Engine

多災害類比預測引擎 — 基於歷史相似度的影響導向預測系統

## 快速啟動

### 方式一：Docker Compose（推薦，用於部署）

```bash
# 一鍵啟動前後端服務
docker compose up --build -d

# 查看服務狀態
docker compose ps

# 查看 logs
docker compose logs -f

# 停止服務
docker compose down
```

啟動後：
- 前端：http://localhost:3000
- 後端 API：http://localhost:8000
- API 文件：http://localhost:8000/docs

### 方式二：本地開發模式

```bash
# 1. 安裝後端依賴
pip install -r requirements.txt

# 2. 啟動後端 (FastAPI)
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# 3. 安裝前端依賴 (另開終端)
cd frontend && npm install

# 4. 啟動前端開發伺服器
npm run dev
```

開發模式下：
- 前端：http://localhost:5173（自動 proxy API 到 8000）
- 後端 API：http://localhost:8000
- API 文件（Swagger UI）：http://localhost:8000/docs

---

## 專案結構

```
flux-data-engine/
├── api/                          # FastAPI 後端
│   ├── main.py                   # 應用程式入口
│   ├── state.py                  # 全域狀態（模型載入）
│   └── routers/                  # 路由依 domain 切分
│       ├── system.py             # [system] 健康檢查、方法列表
│       └── typhoon.py            # [typhoon] 預測、實驗管理
├── frontend/                     # Vue 3 + Vite 前端
│   ├── src/
│   │   ├── views/
│   │   │   ├── HomeView.vue
│   │   │   └── typhoon/          # 颱風 domain 頁面
│   │   ├── api/                  # API 客戶端
│   │   └── router/               # 路由設定
│   ├── vite.config.js
│   └── nginx.conf                # 生產環境 nginx 設定
├── src/                          # 核心演算法（依流水線階段命名）
│   ├── stage00_data_ingestion/   # 資料載入
│   ├── stage01_data_cleaning/    # 資料清洗
│   ├── stage02_exploratory_analysis/  # EDA
│   ├── stage03_data_preprocessing/    # 資料前處理
│   ├── stage04_feature_engineering/   # 特徵工程
│   ├── stage05_model_training/        # 模型訓練（KNN/DTW/Rule/Combined）
│   ├── stage06_hyperparameter_optimization/  # 超參數優化
│   ├── stage07_model_evaluation/      # 模型評估
│   ├── stage08_downstream_analysis/   # 下游分析（視覺化/降水）
│   └── stage09_inference_pipeline/    # 推論管線
├── data/                         # 資料集
├── experiments/                  # 實驗結果
├── scripts/                      # 工具腳本
├── docker-compose.yml            # Docker 編排
├── backend.Dockerfile            # 後端映像
└── frontend.Dockerfile           # 前端映像
```

## API 端點

| 方法 | 路徑 | Tag | 說明 |
|------|------|-----|------|
| GET | `/health` | system | 健康檢查 |
| GET | `/api/methods` | system | 列出支援的預測方法 |
| POST | `/api/typhoon/predict` | typhoon | 執行颱風路徑預測 |
| GET | `/api/typhoon/runs` | typhoon | 列出實驗歷史 |
| GET | `/api/typhoon/runs/{id}` | typhoon | 取得實驗詳情 |
| GET | `/api/typhoon/categories` | typhoon | 路徑分類說明 |

## 預測方法

| 方法 | 準確率 | 說明 |
|------|--------|------|
| Combined RRF 優化版 | **79.8%** | KNN+DTW+Rule 融合，最佳參數 |
| Rule-Based | **79.8%** | 幾何規則分類器 |
| Combined RRF 原版 | 74.7% | 預設參數融合 |
| KNN 優化版 | 63.6% | 3 顯著特徵加權 |

## 技術棧

- **後端**：Python 3.12 / FastAPI / uvicorn
- **前端**：Vue 3 / Vite / Axios
- **容器**：Docker / Docker Compose
- **演算法**：scikit-learn / dtaidistance / numpy / pandas
- **視覺化**：matplotlib / seaborn
