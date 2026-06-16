"""
Flux Data Engine — FastAPI Backend

啟動：uvicorn backend.main:app --host 0.0.0.0 --port 8000
"""

import sys
from pathlib import Path
from contextlib import asynccontextmanager

# 確保專案根目錄在 sys.path 中
ROOT_DIR = Path(__file__).parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.routers import system
from backend.routers import typhoon

# === 全域狀態管理 ===
from backend.state import app_state


@asynccontextmanager
async def lifespan(app: FastAPI):
    """啟動時預載所有模型"""
    app_state.initialize()
    yield
    app_state.cleanup()


app = FastAPI(
    title="Flux Data Engine API",
    description="多災害類比預測引擎 — 基於歷史相似度的影響導向預測系統",
    version="3.0.0",
    lifespan=lifespan,
)

# CORS (允許前端 Vue 開發伺服器)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "http://frontend:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 靜態檔案 (實驗結果圖片)
EXPERIMENTS_DIR = Path(__file__).parent.parent / "experiments"
if EXPERIMENTS_DIR.exists():
    app.mount(
        "/static/experiments",
        StaticFiles(directory=str(EXPERIMENTS_DIR)),
        name="experiments",
    )

# 註冊路由
app.include_router(system.router)
app.include_router(typhoon.router)
