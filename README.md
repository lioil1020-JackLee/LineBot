# LineBot

本專案是一個以本機模型為核心的 LINE 聊天機器人，使用 FastAPI + line-bot-sdk v3，並透過 LM Studio 的 OpenAI-compatible API 進行推理。

核心能力：

- 一般對話（LINE -> LM Studio -> LINE）
- SQLite 對話紀錄與 session 管理
- Prompt 熱重載與持久化
- RAG（本地知識庫分塊、重建索引、來源標註）
- 假訊息查證流程（分類 -> 主張抽取 -> 搜尋 -> 整合判讀）
- 圖片 OCR（LINE image message）
- 文件解析（PDF / DOCX / XLSX / PPTX / TXT / CSV / TSV / MD）
- 健康檢查與 LLM 呼叫記錄

## 快速開始

### 1. 安裝依賴

```powershell
uv sync --group dev
```

### 2. 建立環境檔

```powershell
Copy-Item .env.example .env
```

至少要填：

- `LINE_CHANNEL_ACCESS_TOKEN`
- `LINE_CHANNEL_SECRET`

若使用本機模型，請確認 LM Studio API 已可連線：

- `LM_STUDIO_BASE_URL=http://127.0.0.1:1234/v1`

### 3. 啟動

```powershell
uv run linebot
```

### 4. 健康檢查

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/health/detail
```

## 常用指令

```powershell
# 測試
uv run pytest -q

# 靜態檢查
uv run ruff check .

# 初始化資料庫
uv run python scripts/init_db.py

# 重建知識庫索引
uv run python scripts/ingest_knowledge.py

# 清理舊資料（例如 LLM logs）
uv run python scripts/cleanup_runtime.py --llm-log-days 7

# 產生健康報告
uv run python scripts/health_report.py
```

## 主要環境變數

完整清單請看 [.env.example](.env.example)。

### LINE

- `LINE_CHANNEL_ACCESS_TOKEN`
- `LINE_CHANNEL_SECRET`
- `LINE_BOT_NAME`（群組中只回應提及名稱的訊息）

### 應用

- `APP_HOST`（預設 `0.0.0.0`）
- `APP_PORT`（預設 `8000`）
- `APP_RELOAD`（開發時可設 `true`）

### LM Studio

- `LM_STUDIO_BASE_URL`
- `LM_STUDIO_EXE_PATH`（可選，用於嘗試自動啟動 LM Studio）
- `LM_STUDIO_CHAT_MODEL`
- `LM_STUDIO_EMBED_MODEL`
- `LM_STUDIO_TIMEOUT_SECONDS`

### 功能開關

- `RAG_ENABLED`
- `FACTCHECK_ENABLED`
- `IMAGE_OCR_ENABLED`
- `FILE_PARSER_ENABLED`
- `AGENT_ENABLED`
- `EXTERNAL_LLM_FALLBACK_ENABLED`

## API 端點

### 基礎

- `GET /`
- `POST /webhook`
- `GET /health`
- `GET /health/detail`

### 管理

- `POST /admin/reload-prompt`
- `GET /admin/session/{line_user_id}`
- `GET /admin/llm-logs`
- `GET /admin/model`
- `POST /admin/model`
- `POST /admin/knowledge/reindex`
- `GET /admin/knowledge/status`

## 專案結構

```text
.
|- src/linebot_app/
|  |- app.py
|  |- bot.py
|  |- config.py
|  |- agent_loop.py
|  |- factcheck_prompts.py
|  |- db/
|  |- repositories/
|  |- services/
|  \- tools/
|- scripts/
|- docs/
|- tests/
|- .env.example
|- pyproject.toml
\- linebot_clean_onefile.spec
```

## 打包

請參考 [docs/打包指南.md](docs/打包指南.md)。

快速指令：

```powershell
.\scripts\build.ps1
```

## 專案清理與維運建議

- 將本機產物加入 `.gitignore`（例如 `MagicMock/`、快取目錄）。
- 定期執行 `cleanup_runtime.py`，避免資料檔持續膨脹。
- 每次變更後先跑 `pytest` 與 `ruff check`。
- 文件變更應與程式同步提交，避免規格與實作脫節。

