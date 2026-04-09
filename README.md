# LineBot

LineBot 是一個以 FastAPI 為 API 層、LINE Messaging API 為通道層、LM Studio 為本地模型層的 **LINE 純文字研究助理**。核心目標是「證據優先」：先查本地知識庫（RAG），必要時再做網路研究（Web Research），最後再由護欄把不可靠斷言擋下來。

## 核心能力

- LINE 純文字訊息接收與回覆
- 本地知識庫 / RAG 優先回答（可開關）
- 需要即時/最新資訊時，自動進入網路研究流程（Web Research）
- 多輪對話上下文延續（session/message）
- 回覆安全護欄（Response Guard）：避免無證據的確定句、避免把「查不到」說成「沒有」

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
	- 暴露 webhook 與最小必要 admin/health 端點

### 3) LINE 通道層

- `src/linebot_app/bot.py`：
	- 驗證 LINE webhook signature
	- 只解析 **文字訊息**（text event）
	- 處理群組 mention 規則
	- 交由 `ChatOrchestrator` 執行主流程

### 4) 對話協調層

- `src/linebot_app/services/chat_orchestrator.py`：
	- 取得 session/context
	- 呼叫 Planner 產生 `ResearchPlan`
	- Knowledge-first：先查 RAG，足夠就 grounded 回答
	- 不足再 Web research：搜尋 + 抓頁面文字摘要
	- Composer 統整證據生成答案
	- Guard 最後審視與必要改寫

### 5) 領域服務層

- `src/linebot_app/services/research_planner_service.py`：Evidence-first 規劃（輸出 `ResearchPlan` JSON）
- `src/linebot_app/services/knowledge_first_service.py`：RAG 檢索與 grounded draft
- `src/linebot_app/services/web_search_service.py`：Bing RSS 搜尋
- `src/linebot_app/services/web_research_service.py`：多 query 搜尋 + 抓頁面文字摘要（evidence）
- `src/linebot_app/services/answer_composer_service.py`：整合 evidence 生成回答
- `src/linebot_app/services/llm_service.py`：LM Studio 對話與 embedding
- `src/linebot_app/services/rag_service.py`：知識切塊與檢索
- `src/linebot_app/services/response_guard_service.py`：輸出防護與改寫

### 6) 資料層

- `src/linebot_app/db/`：SQLite schema/init
- `src/linebot_app/repositories/`：session、message、llm_log、knowledge repository

### 7) 政策與設定

- `src/linebot_app/config.py`：環境變數設定、預設值與 deprecated key 警告
- `src/linebot_app/policies/trusted_domains.txt`：可信網域清單
- `src/linebot_app/policies_loader.py`：政策檔載入器

## 訊息處理流程

1. LINE webhook 進入 `/webhook`
2. `bot.py` 解析文字事件（含群組 mention 規則）
3. `ChatOrchestrator`：
	- planner → knowledge-first → web research（必要時）→ composer → guard
4. 寫入 session/message/log，回傳 LINE reply

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

## API 一覽

### 基本

- `GET /`
- `GET /health`
- `GET /health/detail`
- `POST /webhook`

### 管理端點

- `GET /admin/llm-logs`
- `POST /admin/knowledge/reindex`

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
