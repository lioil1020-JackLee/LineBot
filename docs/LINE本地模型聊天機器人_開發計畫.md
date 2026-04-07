# LINE 本地模型聊天機器人開發計畫

## 1. 文件目的

本文件用來定義 `E:\py\LineBot` 的實作方向。目標不是做概念驗證，而是做出一個可在本機 Windows 環境穩定運作、能透過 LINE 與使用者聊天、並以 **LM Studio** 作為本地模型推理端的聊天機器人。

本版計畫會參考 `E:\py\local-coding-agent` 中已存在的設計經驗，但只挑選適合本專案的部分導入，不直接複製整套 agent 架構。

---

## 2. 專案目標

### MVP 目標

1. 使用者可透過 LINE 傳送文字訊息給 Bot。
2. Bot 透過 webhook 收到訊息後，呼叫本機 **LM Studio OpenAI-compatible API** 產生回答。
3. Bot 回覆文字訊息至 LINE。
4. 對話紀錄可保存到本地端，至少支援短期 session memory。
5. 系統具備基本日誌、錯誤處理、逾時處理與健康檢查。

### 第二階段目標

1. 支援每位 LINE 使用者的短期對話上下文。
2. 加入系統提示詞管理。
3. 加入本地知識庫檢索，形成簡單 RAG。
4. 補齊管理腳本、資料清理與基本觀測能力。

### 非目標

1. 不做多代理協作系統。
2. 不做程式碼編輯型 agent。
3. 不先做語音、圖片、群組管理等延伸能力。
4. 不先做雲端部署；以本機與 tunnel 開發為主。

---

## 3. 核心技術決策

### 3.1 Web 框架

- 使用 `FastAPI`
- 原因：
  - 目前專案已經用 `FastAPI` 建好最小骨架
  - 易於擴充 webhook、health、admin API
  - 測試與結構化開發相對順手

### 3.2 LINE SDK

- 使用 `line-bot-sdk` v3
- 原因：
  - 目前專案已經是 v3 寫法
  - 後續 webhook 驗簽與 reply API 可延續既有程式

### 3.3 本地模型 Runtime

- 使用 **LM Studio**
- 使用方式：
  - 啟用 LM Studio 本地 server
  - 走 OpenAI-compatible API
  - 預設 API base：`http://127.0.0.1:1234/v1`

### 3.4 資料儲存

- MVP 使用 `SQLite`
- 用途：
  - 對話訊息紀錄
  - 使用者 session 狀態
  - 可選的 prompt/profile 設定

### 3.5 套件與環境管理

- 使用 `uv`
- 維持目前專案方式：
  - `pyproject.toml`
  - `uv.lock`
  - `uv run ...`

---

## 4. 為什麼選 LM Studio

相較於原始文件偏向 Ollama 或 llama.cpp，本專案改用 LM Studio 的理由如下：

1. LM Studio 已內建本地模型管理與啟動介面，對開發與測試更直觀。
2. 它提供 OpenAI-compatible API，應用層可用較穩定的 adapter 方式封裝。
3. 後續若想切換模型，只需調整模型名稱與參數，不必大改 Bot 架構。
4. `local-coding-agent` 的 `continue/config.yaml` 已有 `provider: lmstudio` 的實戰配置可參考，表示這條路線在本機環境是可行的。

---

## 5. 可參考 `local-coding-agent` 的部分

以下是檢視 `E:\py\local-coding-agent` 後，建議可借用的設計，不是直接整包搬：

### 5.1 可直接借鏡的設計

1. **LM Studio 設定方式**
   - 參考檔案：`E:\py\local-coding-agent\continue\config.yaml`
   - 可借用內容：
     - `apiBase=http://localhost:1234/v1`
     - chat / embed 模型分流概念
     - timeout 與 context length 的配置思路

2. **Session 持久化概念**
   - 參考檔案：`E:\py\local-coding-agent\repo_guardian_mcp\services\session_service.py`
   - 可借用內容：
     - session id 生成
     - JSON 或結構化資料持久化思路
   - 本專案建議：
     - 不照搬 JSON session 檔
     - 改成 SQLite 為主，必要時再輔以 JSON 匯出

3. **Trace / 執行摘要概念**
   - 參考檔案：`E:\py\local-coding-agent\repo_guardian_mcp\services\trace_summary_service.py`
   - 可借用內容：
     - 將 LLM 呼叫與 webhook 流程整理成可讀摘要
   - 本專案建議：
     - 簡化成 request_id、user_id、模型、token 用量、延遲、結果狀態

4. **Health report / runtime cleanup 概念**
   - 參考檔案：`E:\py\local-coding-agent\repo_guardian_mcp\services\health_report_service.py`
   - 可借用內容：
     - health report
     - 清理舊資料與觀測 runtime footprint
   - 本專案建議：
     - 簡化成 `/health`
     - 增加 `/health/detail` 或 CLI 報表
     - 後續加入資料清理腳本

### 5.2 不建議直接搬的部分

1. MCP server 與 tool registry
2. 多層 orchestrator
3. sandbox/session rollback 工作流
4. coding-agent 專用 prompts 與 continue 規則

原因很簡單：本專案是聊天 Bot，不是 repo agent。若直接搬，複雜度會暴增，而且與需求不匹配。

---

## 6. 系統架構

```text
LINE User
  -> LINE Messaging API
  -> FastAPI Webhook Server
  -> Bot Service
     -> Session Service
     -> Prompt Service
     -> LLM Service
        -> LM Studio API
     -> Message Repository
  -> SQLite
  -> Logs / Runtime Data
```

### 元件說明

1. `Webhook Layer`
   - 接收 LINE webhook
   - 驗簽
   - 萃取使用者訊息

2. `Bot Service`
   - 組合 prompt
   - 管理 session
   - 呼叫 LLM
   - 回傳 LINE reply

3. `LLM Service`
   - 封裝 LM Studio OpenAI-compatible API
   - 隔離模型名稱、timeout、token 上限等設定

4. `Session Service`
   - 依 `line_user_id` 讀寫最近 N 輪對話
   - 控制上下文長度

5. `Storage Layer`
   - SQLite 儲存訊息、session、system prompt、metadata

6. `Observability`
   - 日誌
   - 錯誤追蹤
   - latency
   - token 使用量

---

## 7. 建議專案結構

```text
LineBot/
|- docs/
|- src/linebot_app/
|  |- __init__.py
|  |- __main__.py
|  |- app.py
|  |- config.py
|  |- bot.py
|  |- routes/
|  |  \- webhook.py
|  |- services/
|  |  |- bot_service.py
|  |  |- llm_service.py
|  |  |- session_service.py
|  |  |- prompt_service.py
|  |  \- health_service.py
|  |- repositories/
|  |  |- message_repository.py
|  |  |- session_repository.py
|  |  \- prompt_repository.py
|  |- db/
|  |  |- schema.py
|  |  \- sqlite.py
|  \- models/
|     |- chat.py
|     \- llm.py
|- tests/
|- data/
|  |- app.db
|  |- logs/
|  \- knowledge/
|- scripts/
|  |- init_db.py
|  |- run_dev.ps1
|  \- cleanup_runtime.py
|- .env
|- .env.example
|- pyproject.toml
\- README.md
```

---

## 8. 設定規格

`.env` 建議欄位：

```env
LINE_CHANNEL_ACCESS_TOKEN=
LINE_CHANNEL_SECRET=

APP_HOST=127.0.0.1
APP_PORT=8000
APP_RELOAD=true

LM_STUDIO_BASE_URL=http://127.0.0.1:1234/v1
LM_STUDIO_CHAT_MODEL=qwen/qwen2.5-7b-instruct
LM_STUDIO_EMBED_MODEL=text-embedding-nomic-embed-text-v1.5
LM_STUDIO_TIMEOUT_SECONDS=90
LM_STUDIO_MAX_TOKENS=1024
LM_STUDIO_TEMPERATURE=0.7

SQLITE_PATH=data/app.db
SESSION_MAX_TURNS=8
LOG_LEVEL=INFO
```

---

## 9. 資料模型

### 9.1 sessions

欄位建議：

- `id`
- `line_user_id`
- `created_at`
- `updated_at`
- `last_message_at`
- `status`

### 9.2 messages

欄位建議：

- `id`
- `session_id`
- `role` (`user`, `assistant`, `system`)
- `content`
- `token_count`
- `created_at`
- `source` (`line`, `system`, `rag`)

### 9.3 prompts

欄位建議：

- `id`
- `name`
- `content`
- `is_active`
- `updated_at`

### 9.4 llm_logs

欄位建議：

- `id`
- `request_id`
- `session_id`
- `model_name`
- `latency_ms`
- `prompt_tokens`
- `completion_tokens`
- `total_tokens`
- `status`
- `error_message`
- `created_at`

---

## 10. API 與流程設計

### 10.1 LINE webhook 主流程

1. LINE 傳送 webhook 到 `/webhook`
2. FastAPI 驗證 `X-Line-Signature`
3. 解析 event
4. 若為文字訊息：
   - 取得 `line_user_id`
   - 建立或取得 session
   - 寫入 user message
   - 組合 system prompt + 歷史對話
   - 呼叫 LM Studio
   - 儲存 assistant message
   - 用 LINE reply API 回覆
5. 若失敗：
   - 記錄錯誤
   - 回覆簡短 fallback 訊息

### 10.2 管理與診斷 API

第一版建議提供：

- `GET /health`
- `GET /health/detail`
- `POST /admin/reload-prompt`
- `GET /admin/session/{line_user_id}`

---

## 11. LM Studio 整合方式

### 11.1 呼叫策略

LM Studio 啟動本地 server 後，本專案透過 OpenAI-compatible API 呼叫：

- `POST /chat/completions`
- 後續 RAG 可用 `POST /embeddings`

### 11.2 LLM Service 責任

`llm_service.py` 應負責：

1. 組裝請求 payload
2. 控制 timeout
3. 解析回傳內容
4. 處理模型不可用、逾時、空回應
5. 統一回傳資料格式給 `bot_service`

### 11.3 模型建議

MVP 優先使用：

1. 小中型 instruction model
2. 能在本機穩定跑到可接受延遲的模型

建議策略：

- 第一優先：7B 級模型，先換穩定性
- 第二優先：若硬體足夠，再升到更大的 Qwen 類 instruction model
- Embedding 模型在 RAG 階段再啟用

---

## 12. Prompt 與對話策略

### MVP prompt 原則

1. 回覆使用繁體中文
2. 優先簡潔、清楚、可執行
3. 不假裝知道未知資訊
4. 若問題超出能力，明確說明限制
5. 若訊息太短，可溫和追問

### Session 策略

1. 每位 LINE user 維持獨立 session
2. 預設保存最近 `8` 輪
3. 超長上下文採截斷策略
4. 後續可加入摘要記憶

---

## 13. 錯誤處理與 fallback

必做 fallback：

1. `LM Studio 未啟動`
   - 回覆：「本地模型目前未啟動，請稍後再試。」

2. `LM Studio timeout`
   - 回覆：「目前回應較慢，請稍後再試一次。」

3. `LINE reply 失敗`
   - 記錄 log，必要時補 push 或人工檢查

4. `輸入為空或非文字`
   - 回覆固定提示或忽略

5. `資料庫錯誤`
   - 不中斷 webhook 基本流程
   - 至少保留錯誤 log

---

## 14. 日誌與觀測

MVP 至少要有：

1. request id
2. line user id
3. session id
4. event type
5. model name
6. latency ms
7. token usage
8. result status
9. error stack

### 可借鏡 `local-coding-agent` 的地方

- `trace_summary_service.py` 的想法可簡化成聊天版 trace summary
- `health_report_service.py` 的想法可做成每日或手動健康報表

---

## 15. 測試策略

### 15.1 單元測試

1. webhook 驗簽失敗
2. webhook 缺 header
3. session 建立/讀取
4. prompt 組裝
5. LLM service 正常回傳
6. LLM service timeout / error

### 15.2 整合測試

1. 模擬 LINE webhook -> 回覆成功
2. 模擬 LM Studio 回應
3. SQLite 寫入成功

### 15.3 手動驗證

1. 本機啟動 FastAPI
2. 用 tunnel 對外提供 webhook URL
3. LINE Developers 設定 webhook
4. 實際傳訊息給 Bot
5. 驗證回覆速度、內容與穩定性

---

## 16. 開發階段規劃

## Phase 0：環境確認與底座整理

目標：讓目前專案骨架具備可持續開發的底座。

工作項目：

1. 整理 `src/linebot_app` 目錄
2. 建立 `services/`、`repositories/`、`db/`
3. 補 `httpx` 或 OpenAI-compatible client 依賴
4. 補 `.env.example`
5. 補 SQLite 初始化腳本
6. 確認 LM Studio 本地 server 可連線

完成標準：

1. `uv run pytest` 通過
2. `uv run linebot` 可啟動
3. `GET /health` 可回應
4. 可成功打到 LM Studio health 或 completions

## Phase 1：MVP 聊天閉環

目標：完成 LINE -> LM Studio -> LINE 的最小可用閉環。

工作項目：

1. 實作 `llm_service.py`
2. 實作 `bot_service.py`
3. 實作 SQLite message/session repository
4. 重構 `bot.py` 讓 webhook 與 service 分離
5. 加入基本 fallback 訊息
6. 補 webhook 與 llm mock 測試

完成標準：

1. LINE 傳文字後可收到模型回答
2. 每次對話可寫入 SQLite
3. 模型關閉時可回 fallback

## Phase 2：Session memory 與 prompt 管理

目標：讓 Bot 具備基本上下文能力。

工作項目：

1. 依 `line_user_id` 維護 session
2. 取最近 N 輪對話作為上下文
3. 增加 system prompt 設定與 reload
4. 加入 token/長度裁切策略

完成標準：

1. Bot 能延續短期上下文
2. system prompt 可替換
3. 上下文過長時不會直接炸掉

## Phase 3：健康檢查、觀測與清理

目標：提高可維運性。

工作項目：

1. 擴充 `/health/detail`
2. 記錄 latency 與 token usage
3. 加入 runtime cleanup script
4. 輸出簡單健康報表

完成標準：

1. 能快速判斷 LM Studio、DB、LINE 設定是否正常
2. 能看出最近錯誤與慢回應
3. 可以清理舊資料

## Phase 4：本地知識庫與 RAG

目標：讓 Bot 回答特定領域內容時更可靠。

工作項目：

1. 設計 `data/knowledge/` 匯入流程
2. 文件切塊
3. 產生 embedding
4. 查詢後把結果注入 prompt
5. 實作簡單引用或來源標記

完成標準：

1. 可針對本地文件回答
2. 回答能反映知識庫內容
3. 可控制是否啟用 RAG

---

## 17. 實作優先順序

建議照這個順序做：

1. 整理專案結構
2. 補 LM Studio adapter
3. 補 SQLite schema 與 repository
4. 完成 webhook -> bot_service -> llm_service
5. 補測試
6. 補 session memory
7. 補 health/detail 與 cleanup
8. 最後才做 RAG

---

## 18. 風險與對策

### 18.1 LM Studio 沒有啟動或模型未載入

對策：

1. 啟動時檢查 base URL
2. `/health/detail` 顯示模型可用性
3. webhook 回覆 fallback

### 18.2 本機推理太慢

對策：

1. 先用較小模型
2. 限制 `max_tokens`
3. 對 session 上下文做裁切
4. 加 timeout 與錯誤回覆

### 18.3 LINE webhook 開發不穩

對策：

1. 本機先跑通 `/health`、`/webhook`
2. 使用 ngrok 或 Cloudflare Tunnel
3. 保留 request log 方便追蹤

### 18.4 架構做太大

對策：

1. 先只做聊天 Bot 必要分層
2. 不導入 MCP、sandbox、multi-agent
3. 每個 phase 都要有可執行成果

---

## 19. 完成定義

當以下條件成立時，視為 MVP 完成：

1. 本機可用 `uv run linebot` 啟動服務
2. LINE webhook 已設定成功
3. 使用者傳文字後，Bot 會經 LM Studio 回覆
4. Session 與訊息可寫入 SQLite
5. `/health` 可檢查服務狀態
6. 至少有基本 pytest 覆蓋 webhook 與 llm service

---

## 20. 下一步落地建議

依這份計畫，下一輪實作最值得先做的是：

1. 建立 `llm_service.py`，正式接上 LM Studio
2. 建立 SQLite schema 與 repository
3. 將目前 `bot.py` 重構成 service-based flow
4. 補 `.env` 中的 LM Studio 設定欄位
5. 完成第一條真正的 LINE -> LM Studio -> LINE 回覆流程

這樣做完後，專案就不再只是 LINE webhook 骨架，而會變成真正可聊天的本地模型 Bot。
