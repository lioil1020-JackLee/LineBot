# LineBot

以 `uv` 建立的 LINE Bot 專案，使用 FastAPI 搭配 `line-bot-sdk` v3，並透過 LM Studio 的 OpenAI-compatible API 產生回覆。

已支援：

- LINE -> LM Studio -> LINE 文字聊天
- SQLite 持久化 session/messages
- system prompt 熱重載與持久化
- LLM 呼叫日誌與健康檢查
- 本地知識庫重建索引與簡易 RAG
- 上下文長度裁切策略（MAX_CONTEXT_CHARS）
- RAG 來源標註（回覆附參考來源）

## 專案結構

```text
.
|- src/linebot_app/
|  |- __init__.py
|  |- __main__.py
|  |- app.py
|  |- bot.py
|  |- config.py
|  |- db/
|  |- repositories/
|  \- services/
|- tests/
|  \- test_app.py
|- .env.example
|- .python-version
|- pyproject.toml
\- uv.lock
```

## 初始化

```powershell
uv sync
Copy-Item .env.example .env
```

填入 `.env` 中的 LINE Channel 資訊後即可啟動。

若要完整聊天功能，請先在 LM Studio 啟用本地 API server（預設 `http://127.0.0.1:1234/v1`）。

## 啟動

```powershell
uv run linebot
```

本機健康檢查:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/health/detail
```

## 開發指令

```powershell
uv run pytest
uv run ruff check .
uv run python scripts/init_db.py
uv run python scripts/ingest_knowledge.py
uv run python scripts/cleanup_runtime.py --llm-log-days 7
uv run python scripts/health_report.py
```

## Webhook 路徑

LINE Messaging API webhook:

```text
POST /webhook
GET /
```

管理端點:

```text
POST /admin/reload-prompt
GET /admin/session/{line_user_id}
GET /admin/llm-logs
GET /admin/model
POST /admin/model
POST /admin/knowledge/reindex
GET /admin/knowledge/status
```

模型切換範例:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/admin/model -ContentType "application/json" -Body '{"chat_model":"qwen3-coder-30b-a3b-instruct"}'
```
