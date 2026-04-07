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
- **假訊息 / 假新聞查證**（自動分類、主張抽取、搜尋查證、結構化回覆）
- **圖片文字辨識（OCR）**：可接收 LINE 圖片並抽取文字後回答
- **文件解析**：可接收 PDF/Word/Excel/PPT/TXT 並抽取文字後回答

## 假訊息查證功能

### 功能說明

Bot 收到群組或私訊文字後，會自動判斷是否需要查證：

| 訊息類別 | 處理方式 |
|----------|----------|
| 一般聊天（問候、閒聊） | 正常對話，不進查證流程 |
| 可查證主張（聲稱某事件、某人說了某話）| 進入查證流程 |
| 高風險訊息（醫療、災害、金融、選舉、名人謠言）| 進入查證流程，加風險提醒 |

### 查證資料流

```
使用者訊息
  └─> [1] 訊息分類（LLM）
        ├─> 一般聊天 → 繼續普通對話
        └─> 可查證 / 高風險
              └─> [2] 主張抽取（LLM）
                    ├─> 訊息太模糊 → 請使用者補充
                    └─> 抽出主張
                          └─> [3] 搜尋查證（DuckDuckGo）
                                └─> [4] 整合判讀（LLM）
                                      └─> 結構化查證報告 → 回覆 LINE
```

### 回覆格式

```
【假訊息查證】

查證結論：真 / 假 / 部分正確 / 無足夠證據 / 過時資訊 / 無法判定

核心主張：…

核心理由：
1. …
2. …

參考來源：
[標題] — https://…

可信度：高 / 中 / 低
⚠️ 風險提醒：（僅高風險訊息才出現）

---
🤖 本查證由 AI 輔助完成，可能存在判讀誤差，請自行核實重要資訊。
```

### 安全原則

- 沒有足夠證據時，**不會武斷下結論**，使用「無足夠證據」或「無法判定」。
- **不捏造來源**，只引用搜尋結果中實際出現的 URL。
- 若搜尋工具不可用，報告中**明確說明**「缺少即時查證來源，以下為模型初步判讀」。

### 後續可擴充的搜尋來源

目前預設使用 [DuckDuckGo](https://duckduckgo.com)（`duckduckgo_search` 套件）。
`FactCheckService` 的 `search_fn` 參數可替換為任意搜尋 provider，例如：

- **Tavily AI** — `pip install tavily-python`
- **SerpAPI** — `pip install google-search-results`
- **Bing Search API** — Azure Cognitive Services

替換方式：在 `app.py` 的 `_get_default_search_fn()` 回傳對應函式即可。

## 環境變數

```env
# LINE Messaging API
LINE_CHANNEL_ACCESS_TOKEN=...
LINE_CHANNEL_SECRET=...
LINE_BOT_NAME=                         # 群組中呼叫 Bot 的名稱，留空表示回應所有訊息

# LM Studio（OpenAI-compatible API）
LM_STUDIO_BASE_URL=http://127.0.0.1:1234/v1
LM_STUDIO_CHAT_MODEL=qwen/qwen3.5-9b
LM_STUDIO_EMBED_MODEL=text-embedding-nomic-embed-text-v1.5
LM_STUDIO_TIMEOUT_SECONDS=90
LM_STUDIO_MAX_TOKENS=1024
LM_STUDIO_TEMPERATURE=0.7

# 假訊息查證
FACTCHECK_ENABLED=true                 # 啟用查證功能
FACTCHECK_MAX_SEARCH_QUERIES=2         # 最多對幾個主張搜尋
FACTCHECK_MAX_RESULTS_PER_QUERY=4      # 每次搜尋保留幾筆結果

# 線上搜尋（最新資訊）
WEB_SEARCH_PROVIDER=duckduckgo         # 或 perplexity
PERPLEXITY_BASE_URL=https://api.perplexity.ai
PERPLEXITY_API_KEY=
PERPLEXITY_MODEL=sonar

# 外部模型備援（本地模型不確定時）
EXTERNAL_LLM_FALLBACK_ENABLED=false
EXTERNAL_LLM_BASE_URL=https://openrouter.ai/api/v1
EXTERNAL_LLM_API_KEY=
EXTERNAL_LLM_MODELS=openai/gpt-5-mini,google/gemini-2.5-flash
EXTERNAL_LLM_TIMEOUT_SECONDS=45

# 圖片 OCR
IMAGE_OCR_ENABLED=true

# 文件解析
FILE_PARSER_ENABLED=true
```

完整變數清單見 `.env.example`。

## 任務路由建議（你要的模式）

- 寫程式 / 推理：`LM_STUDIO_CHAT_MODEL=qwen/qwen3.5-9b`
- 查最新資訊：`WEB_SEARCH_PROVIDER=perplexity`（需設定 `PERPLEXITY_API_KEY`）
- 整理內容：仍由本地 Qwen 輸出最終回答
- 本地模型不知道時：可開 `EXTERNAL_LLM_FALLBACK_ENABLED=true`，並配置 `EXTERNAL_LLM_API_KEY`

說明：目前程式流程是「Qwen 主模型 + 搜尋工具 + 可選外部模型備援」，不會每題都打外部 API，僅在本地答案不確定時才嘗試備援。

## 圖片支援（PNG/JPG/JPEG/BMP）

- Bot 現在可接收 LINE 圖片訊息。
- 會先做 OCR 抽取圖片中的文字，再沿用既有對話/查證流程回覆。
- 若圖片文字太少或辨識失敗，會提示你改傳清晰截圖或直接貼文字。

## 文件支援（PDF/Word/Excel/PPT/TXT）

- Bot 可接收 LINE 檔案訊息（file message）。
- 支援格式：`.pdf`, `.docx`, `.xlsx`, `.xlsm`, `.pptx`, `.txt`, `.md`, `.csv`, `.tsv`。
- 會先抽取文件文字，再沿用既有對話/查證流程回覆。
- 舊版 Office 二進位格式（`.doc`, `.xls`, `.ppt`）目前不支援，請先轉為新格式。

## 限制事項

- 查證流程會串行呼叫 3 次 LLM（分類、抽取、整合），比普通對話慢（依模型速度，約 15–60 秒）。
- DuckDuckGo 搜尋可能受速率限制，高頻使用時建議換用 Tavily / SerpAPI。
- 查證結果準確度取決於：① LLM 本身的訓練資料截止日期、② 搜尋結果品質。
- 本地 LM Studio 模型（尤其 7B 以下）對 JSON 指令的遵從度較低，偶有分類失誤；失誤時安全 fallback 為回到普通對話。
- LINE 單則訊息有 5000 字元上限，過長的查證報告將被截斷。

## 專案結構

```text
.
|- src/linebot_app/
|  |- __init__.py
|  |- __main__.py
|  |- app.py
|  |- bot.py
|  |- config.py
|  |- factcheck_prompts.py    ← 新增：查證流程 prompt 模板
|  |- agent_loop.py
|  |- db/
|  |- repositories/
|  |- services/
|  |  |- factcheck_service.py ← 新增：查證服務
|  |  |- bot_service.py
|  |  └─ ...
|  \- tools/
|     |- web_search.py        ← 被查證流程重用
|     \- fetch_url.py
|- tests/
|  |- test_factcheck_service.py ← 新增
|  \- ...
|- .env.example
|- pyproject.toml
\- uv.lock
```

## 初始化

```powershell
uv sync
Copy-Item .env.example .env
```

填入 `.env` 中的 LINE Channel 資訊後即可啟動。

若要完整聊天與查證功能，請先在 LM Studio 啟用本地 API server（預設 `http://127.0.0.1:1234/v1`）。

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

