# LineBot

這是一個以 FastAPI + LINE Messaging API + LM Studio 為核心的 LINE Bot 專案，支援：

- 一般問答與多輪上下文
- 天氣與台股即時查詢
- 基本知識庫檢索
- LINE 圖片 OCR 與文件內容擷取
- 回答防呆與事實查核流程

## 啟動方式

1. 安裝依賴

```powershell
uv sync --group dev
```

2. 建立環境變數

```powershell
Copy-Item .env.example .env
```

3. 至少設定以下欄位

- `LINE_CHANNEL_ACCESS_TOKEN`
- `LINE_CHANNEL_SECRET`
- `LM_STUDIO_BASE_URL`
- `LM_STUDIO_CHAT_MODEL`
- `SQLITE_PATH`

4. 啟動服務

```powershell
uv run linebot
```

5. 檢查健康狀態

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/health/detail
```

## 測試

```powershell
uv run pytest -q
uv run ruff check .
```

## 維運指令

```powershell
uv run init-db
uv run ingest-knowledge
uv run health-report
uv run export-metrics-report
uv run cleanup-runtime --llm-log-days 7
uv run run-eval --eval-path data/evals/general_qa.jsonl
```

- `init-db`：初始化 SQLite schema
- `ingest-knowledge`：重建知識庫索引
- `health-report`：輸出健康狀態與近期 LLM log
- `export-metrics-report`：匯出 metrics JSON 報表
- `cleanup-runtime`：清理過舊的執行期資料
- `run-eval`：離線評估問答品質

## 主要 API

- `GET /`
- `GET /health`
- `GET /health/detail`
- `POST /webhook`
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
