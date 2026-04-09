# LineBot

LineBot 是一個以 FastAPI 為 API 層、LINE Messaging API 為通道層、LM Studio 為本地模型層的助理系統。專案目前採用「策略分流 + API-first 回答 + 防呆守門」的新架構，目標是讓回覆可用、可追蹤、且盡量降低幻覺風險。

## 核心能力

- 一般問答與多輪對話延續
- 主題意圖分流（general、weather、market、realtime-sensitive）
- 天氣與台股查詢的 API-first 回覆
- 可信來源過濾後的即時資訊整理
- RAG 本地知識檢索（可開關）
- LINE 圖片 OCR 與檔案內容解析（PDF/DOCX/XLSX/PPTX/TXT）
- 回答防護（response guard）與事實查核流程（fact-check）
- Session memory / profile / tasks 管理與觀測 API

## 架構總覽

### 1) 啟動與入口

- `src/linebot_app/__main__.py`：支援直接以 Python 模組進入。
- `src/linebot_app/__init__.py`：主入口 `main()`，依設定選擇：
	- Windows tray 模式
	- 一般 uvicorn API 服務

### 2) API 組裝層

- `src/linebot_app/app.py`：
	- 建立 FastAPI app
	- 初始化 DB
	- 組裝 repositories/services
	- 暴露 webhook 與 admin/health 端點

### 3) LINE 通道層

- `src/linebot_app/bot.py`：
	- 驗證 LINE webhook signature
	- 解析 text/image/file 事件
	- 圖片走 OCR，檔案走 parser，再交由 BotService

### 4) 對話協調層

- `src/linebot_app/services/bot_service.py`：
	- 主路由與策略中樞
	- 依 `answer_policy` 做意圖分流
	- 整合 session/context、grounded 回答、RAG、fact-check、response guard
	- 蒐集 policy metrics 供 `/admin/metrics` 觀察

### 5) 領域服務層

- `src/linebot_app/services/answer_policy.py`：文字意圖判定
- `src/linebot_app/services/grounded_reply_service.py`：天氣/市場/查詢型問題的來源導向回答
- `src/linebot_app/services/weather_service.py`：Open-Meteo 地理編碼與天氣摘要
- `src/linebot_app/services/market_service.py`：TWSE/Yahoo 報價整合
- `src/linebot_app/services/web_search_service.py`：Bing RSS 搜尋與來源排序
- `src/linebot_app/services/llm_service.py`：LM Studio 對話與 embedding
- `src/linebot_app/services/rag_service.py`：知識切塊與檢索
- `src/linebot_app/services/response_guard_service.py`：輸出防護與改寫
- `src/linebot_app/services/factcheck_service.py`：查證流程

### 6) 資料層

- `src/linebot_app/db/`：SQLite schema/init
- `src/linebot_app/repositories/`：session、message、llm_log、knowledge、memory、task 等 repository

### 7) 政策與設定

- `src/linebot_app/config.py`：環境變數設定、預設值與 deprecated key 警告
- `src/linebot_app/policies/trusted_domains.txt`：可信網域清單
- `src/linebot_app/policies_loader.py`：政策檔載入器

## 訊息處理流程

1. LINE webhook 進入 `/webhook`
2. `bot.py` 解析事件（文字/圖片/檔案）
3. 視事件型態先做 OCR 或檔案抽文
4. `BotService` 執行意圖分流
5. 依題型走 API-first / grounded / RAG / 一般 LLM 回覆
6. 進入 response guard 與必要後處理
7. 寫入 session/log/memory/task，回傳 LINE reply

## 快速開始

### 1. 安裝依賴

```powershell
uv sync --group dev
```

### 2. 建立環境變數

```powershell
Copy-Item .env.example .env
```

### 3. 最小必要設定

- `LINE_CHANNEL_ACCESS_TOKEN`
- `LINE_CHANNEL_SECRET`
- `LM_STUDIO_BASE_URL`
- `LM_STUDIO_CHAT_MODEL`
- `SQLITE_PATH`

### 4. 啟動服務

```powershell
uv run linebot
```

### 5. 健康檢查

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/health/detail
```

## 重要環境變數

### LINE / App

- `LINE_CHANNEL_ACCESS_TOKEN`
- `LINE_CHANNEL_SECRET`
- `LINE_BOT_NAME`
- `LINE_GROUP_REQUIRE_MENTION`
- `APP_HOST`
- `APP_PORT`
- `APP_RELOAD`
- `TRAY_UI_ENABLED`

### LM Studio

- `LM_STUDIO_BASE_URL`
- `LM_STUDIO_EXE_PATH`
- `LM_STUDIO_CHAT_MODEL`
- `LM_STUDIO_EMBED_MODEL`
- `LM_STUDIO_TIMEOUT_SECONDS`
- `LM_STUDIO_GUARD_TIMEOUT_SECONDS`
- `LM_STUDIO_MAX_TOKENS`
- `LM_STUDIO_TEMPERATURE`

### Storage / Session

- `SQLITE_PATH`
- `SESSION_MAX_TURNS`
- `SESSION_MEMORY_ENABLED`
- `SESSION_MEMORY_TRIGGER_MESSAGES`
- `SESSION_MEMORY_WINDOW_MESSAGES`
- `SESSION_MEMORY_MAX_CHARS`

### Grounding / Search / RAG

- `WEB_SEARCH_ENABLED`
- `WEB_SEARCH_BACKEND`
- `WEB_SEARCH_TIMEOUT_SECONDS`
- `RAG_ENABLED`
- `KNOWLEDGE_DIR`
- `RAG_TOP_K`
- `RAG_CHUNK_SIZE`
- `RAG_CHUNK_OVERLAP`

### Safety / Capability 開關

- `RESPONSE_GUARD_ENABLED`
- `RESPONSE_GUARD_REWRITE_ENABLED`
- `RESPONSE_GUARD_MAX_INPUT_CHARS`
- `FACTCHECK_ENABLED`
- `FACTCHECK_MAX_SEARCH_QUERIES`
- `FACTCHECK_MAX_RESULTS_PER_QUERY`
- `IMAGE_OCR_ENABLED`
- `FILE_PARSER_ENABLED`
- `AGENT_ENABLED`

## API 一覽

### 基本

- `GET /`
- `GET /health`
- `GET /health/detail`
- `POST /webhook`

### 管理端點

- `POST /admin/reload-prompt`
- `GET /admin/session/{line_user_id}`
- `GET /admin/session/{line_user_id}/memory`
- `GET /admin/session/{line_user_id}/profile`
- `GET /admin/session/{line_user_id}/tasks`
- `GET /admin/llm-logs`
- `GET /admin/metrics`
- `GET /admin/model`
- `POST /admin/model`
- `POST /admin/knowledge/reindex`
- `GET /admin/knowledge/status`

## CLI 維運指令

```powershell
uv run init-db
uv run ingest-knowledge
uv run health-report
uv run export-metrics-report
uv run cleanup-runtime --llm-log-days 7
uv run run-eval --eval-path data/evals/general_qa.jsonl
```

- `init-db`：初始化 SQLite schema
- `ingest-knowledge`：重建知識索引
- `health-report`：輸出健康狀態與近期 LLM log
- `export-metrics-report`：匯出 metrics 報表
- `cleanup-runtime`：清理舊 llm logs
- `run-eval`：離線案例評估

## 測試與品質

```powershell
uv run pytest -q
uv run ruff check .
```

目前測試模組涵蓋：

- agent loop
- answer policy
- app/webhook
- bot service
- fact-check
- llm service
- rag service
- response guard
- weather service
- web search service

## 打包

```powershell
uv run build-onedir
uv run build-onefile
```

- onedir 輸出：`dist/linebot-app/linebot-app.exe`
- onefile 輸出：`dist/linebot-app.exe`

如需完整流程，請參考 `docs/打包指南.md`。
