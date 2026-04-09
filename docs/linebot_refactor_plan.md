# Line Bot 重構與刪減修改計畫

## 文件目的

本文件針對目前 `LineBot` 專案的現況，提出一份完整、可執行、以「減法式重構」為核心的修改計畫。目標不是在現有架構上持續補規則與補功能，而是將專案收斂為一個更專注、更可靠的 **LINE 文字型研究助理**。

新的產品方向如下：

- 專注於 **LINE 文字對話**
- 優先使用 **本地知識庫 / RAG** 回答
- 若知識庫不足，能夠 **自動上網搜尋、驗證、整合後回答**
- 回答風格要像一位 **博學、條理清楚、重視證據的博士型助理**
- 移除效果差、維護成本高、與新目標不一致的功能

這份文件包含：

1. 重構目標與原則
2. 現況問題診斷
3. 明確的刪除與保留範圍
4. 新架構藍圖
5. 模組與檔案調整建議
6. 分階段實作計畫
7. 測試與驗收標準
8. 舊代碼清理策略
9. 風險與注意事項

---

## 一、重構總目標

### 1.1 產品定位調整

現有專案像是一個「功能很多但主軸不夠聚焦的多功能 bot 平台」。

重構後的新定位應是：

> **一個具備知識庫優先、必要時可自動上網研究、並以可靠性與整合能力為核心的 LINE 文字型智慧助理。**

它不是：

- 圖片/檔案多模態助理
- 程式碼讀寫工具
- 任務管理平台
- 多種專門領域硬編規則的綜合平台

### 1.2 核心能力定義

重構後僅保留四條主線能力：

1. **LINE 文字訊息接收與回覆**
2. **知識庫檢索與回答**
3. **網路搜尋、驗證與整合回答**
4. **多輪對話記憶與回覆安全護欄**

### 1.3 重構設計原則

整體設計應遵守以下原則：

- **減法優先**：先刪掉不需要的功能，再強化核心流程
- **證據優先**：凡涉及時效性或事實性資訊，優先檢索證據再回答
- **知識來源分流**：區分「模型可直接答」與「必須查資料才能答」
- **單一責任**：一個 service 只做一件主要事情
- **誠實失敗**：查不到時明確說查不到，不得亂編
- **易測試**：新的架構必須能做清楚的單元測試與整合測試

---

## 二、現況主要問題診斷

### 2.1 專案主問題不是單點 bug，而是系統複雜度過高

目前專案已經累積大量功能與條件分支，導致：

- 功能太多，真正核心流程被稀釋
- `BotService` 承擔過多責任
- `app.py` 過度膨脹，組裝與生命週期管理混在一起
- 許多能力彼此耦合，修改一處容易影響其他流程
- 回答品質受 routing 與 fallback 行為拖累

### 2.2 回答邏輯過度依賴硬編規則與 fallback

目前的系統有明顯的「先分流、再套模板」傾向。例如：

- weather/market/realtime 類題目走特殊 route
- 特定 domain 由關鍵字判定
- 沒查到資料時 fallback 為一般 LLM 回答

這會導致兩個嚴重後果：

1. 新領域永遠加不完規則
2. 查不到資料時，模型容易產生未驗證的事實性斷言

### 2.3 網路搜尋不是正式的一級能力，而像附加 fallback

目前網路搜尋多半像是：

- 某些題型嘗試搜一下
- 若搜不到，就退回一般回覆

這會導致系統「看起來有查網路」，但實際上對於：

- 今天的賽程
- 最新新聞
- 即時狀態
- 目前職位/價格/比分

這些高時效問題仍然可能回答錯誤。

### 2.4 多模態與 code 功能佔據太多複雜度，但不符合新目標

你已明確表示：

- 不想保留圖片讀取功能
- 不想保留檔案讀取功能
- 不想保留讀寫 code 功能

這表示原本加入的 OCR、文件解析、code-call、agent/tool-oriented 設計，現在都成為：

- 維護成本
- 測試負擔
- 心智負擔
- prompt 汙染來源

### 2.5 系統真正缺的是「研究規劃能力」，不是更多規則

系統若要變聰明，不應該是一直補：

- 棒球要查
- 天氣要查
- 股票要查
- 演唱會要查
- CEO 要查
- 法規要查

而是應該有一層能判斷：

> 這題如果沒有外部證據，就不應該直接回答。

這就是之後要新增的 **Research Planner / Evidence-first routing**。

---

## 三、重構後的目標產品形態

### 3.1 新產品一句話定義

> 一個以本地知識庫為優先，必要時會自己上網搜尋、驗證、整理後再回答的 LINE 文字研究助理。

### 3.2 理想對話能力

新系統應具備以下能力：

- 對一般知識題，直接給出高品質、結構清楚的回答
- 對公司/內部資料題，優先從知識庫找答案
- 對即時資訊題，自動判斷要查網路
- 對無法確定的題目，能誠實表達不確定性
- 對查到的資料，能做整合、比較、摘要與分析
- 對多輪對話，能延續上下文但不失去事實邊界

### 3.3 理想人格風格

回答風格應朝以下方向調整：

- 像「博士型通才」而不是「客服型 bot」
- 有條理、有邏輯、有總結能力
- 用字成熟穩定，不誇張、不空泛
- 知道什麼時候直接回答，什麼時候要先找資料
- 找不到資料時不亂猜、不裝懂

---

## 四、明確刪除範圍

本節列出應從專案中刪除或停用的功能。

### 4.1 圖片 OCR 功能：全部移除

#### 刪除原因

- 效果不佳
- 與新產品方向不一致
- 增加外部依賴與失敗情境
- 對目前核心目標幫助有限

#### 刪除內容

- `ImageOCRService`
- 所有圖片事件處理分支
- 圖片下載 / OCR / fallback 文案
- 系統 prompt 中「可讀圖片」能力描述
- 與圖片格式、blob 下載、OCR 結果拼接相關程式碼

### 4.2 檔案解析功能：全部移除

#### 刪除原因

- 效果不理想
- 增加 parser 相依與大量 edge cases
- 與新目標「文字型研究助理」不一致

#### 刪除內容

- `DocumentParserService`
- file event 分支
- PDF / DOCX / XLSX / PPTX / TXT 解析入口
- 檔案處理 prompt 與 fallback 訊息
- 系統 prompt 中「可讀文件」能力描述

### 4.3 讀寫 code / coding assistance：全部移除

#### 刪除原因

- 不符合新產品定位
- 會污染系統能力宣告與 prompt 設計
- 增加 agent/tool call 複雜度

#### 刪除內容

- `coding_assistance_enabled`
- 與 `<code-call>`、tool call、code block 特別清理相關邏輯
- coding-oriented guard
- 任何「可讀寫程式碼」的能力描述
- 與 code agent 相關的 prompt、router、內部標記

### 4.4 Agent loop / 自動工具循環：全部移除

#### 刪除原因

- 複雜度太高
- 不利於維護與測試
- 容易把系統推向不透明、不可控的回答流程

#### 刪除內容

- `run_agent_loop` 相關呼叫
- `_AGENT_AUTO_SEARCH`、`_AGENT_MAX_TOOL_ROUNDS` 之類的 agent 配置
- 依賴多輪工具呼叫的流程
- 與 agent/tool state 相關的結構與 prompt 常數

### 4.5 task / profile memory 產品功能：先移除

#### 刪除原因

- 不是現階段產品核心
- 會讓 app 初始化與 admin endpoint 複雜化
- 對回答品質提升的直接效益不如 planner/search 流程

#### 刪除內容

- `ProfileMemoryService`
- `TaskMemoryService`
- `SessionTaskRepository`
- 相關 admin route
- profile/task 讀寫流程

### 4.6 market / weather 等硬編特殊主題路由：降級或移除

#### 刪除原因

- 目前最大問題是「每個 domain 都想獨立特判」
- 新架構應該改為由 planner 決定是否 search，而不是在主流程中堆滿主題規則

#### 建議處理方式

- 若這些 service 很穩定，可先保留工具層，但從主路由移除
- 不再讓 market/weather 成為架構主軸中的特例路徑
- 之後一律經 planner → search/reasoning 決策流程

### 4.7 過度複雜的 prompt 模板與能力宣告：大幅縮減

#### 刪除原因

- prompt 常數過多會使系統行為不透明
- 很多 prompt 只是為了舊功能存在
- 風格控制過強，反而干擾核心判斷

#### 刪除內容

- 與圖片/檔案/code/agent 相關的 prompt
- 過度細碎的回答節奏 prompt
- 與舊 fallback 鏈相關的 prompt 常數
- 與非核心能力相關的 capability 宣告

---

## 五、明確保留範圍

以下為重構後建議保留的核心元件。

### 5.1 Session / Message 儲存

#### 保留原因

- 多輪對話必須依賴 session 與歷史訊息
- 這是所有高品質對話能力的基礎

#### 建議保留元件

- `SessionRepository`
- `MessageRepository`
- `SessionService`

### 5.2 LLM Service

#### 保留原因

- 仍需模型做回答整合、planner JSON、知識摘要等任務

#### 但責任要縮小

重構後它不應再承擔過多隱性 routing 邏輯，而應專注於：

- 生成最終回答
- 生成 research plan
- 生成摘要/記憶內容

### 5.3 知識庫 / RAG

#### 保留原因

- 你希望「若知識庫有答案，優先用知識庫」
- 這是區分本地知識與外部網路研究的重要基礎

#### 建議保留元件

- `KnowledgeRepository`
- `RAGService`
- 相關 indexing / reindex 功能

### 5.4 Web Search Service

#### 保留原因

- 這是重構後系統是否真的會「自己查資料」的關鍵
- 必須從附加功能升級為正式一級能力

### 5.5 Response Guard Service

#### 保留原因

- 即使有 planner 與 search，仍需要最後一層防線
- 尤其對於 today/latest/current 類問題，必須阻止未驗證的確定句

#### 重構後新任務

- 禁止無證據的時效性斷言
- 禁止把「查不到」說成「沒有」
- 禁止硬編數字、日期、比分、身份職稱等
- 在必要時將語氣改為保守且誠實

### 5.6 基本健康檢查與 LLM log

#### 建議保留

- `/health`
- `/health/detail`
- LLM request log / error log

#### 保留原因

- 這些對日常營運、排錯非常重要
- 成本相對低，價值高

---

## 六、新架構藍圖

### 6.1 新架構核心流程

重構後的主要流程建議如下：

```text
LINE text message
  -> BotController / ChatOrchestrator
  -> SessionService 取得上下文
  -> ResearchPlannerService 產生查詢計畫
  -> KnowledgeFirstService 先查知識庫
  -> 若知識不足，交給 WebResearchService 搜尋外部資料
  -> AnswerComposerService 統整證據並生成回答
  -> ResponseGuardService 最後檢查與修正風險語句
  -> 寫回 MessageRepository
  -> Reply LINE
```

### 6.2 模組職責分工

#### A. `BotController` 或 `ChatOrchestrator`

職責：

- 接收純文字輸入
- 取得 session 與 recent context
- 呼叫 planner
- 根據 plan 決定走 knowledge / web / direct reasoning
- 組合最終回覆
- 寫入 user / assistant message

不應做的事：

- 不應內嵌大量 domain-specific if/else
- 不應自己處理太多工具細節
- 不應持有巨大 prompt 常數集合

#### B. `ResearchPlannerService`

這是新的核心。

職責：

- 判斷這題是否需要外部資料
- 判斷是否應先查知識庫
- 產生查詢字串
- 定義 freshness 需求
- 標記是否禁止未驗證斷言

輸出不應是文字答案，而應是結構化的 `ResearchPlan`。

#### C. `KnowledgeFirstService`

職責：

- 根據問題與上下文檢索本地知識庫
- 評估命中品質
- 若證據足夠，提供 grounded answer 草稿
- 若不足，明確回傳 evidence insufficient

#### D. `WebResearchService`

職責：

- 依 planner 給的 query 做多輪搜尋
- 做 query rewrite / expansion
- 去除重複與低品質結果
- 做 freshness / trust / answerability 檢查
- 回傳整理後的 web evidence

#### E. `AnswerComposerService`

職責：

- 將知識庫結果或網路搜尋結果整理成最終答案
- 產出具有邏輯、摘要、比較、分析能力的回答
- 保持風格一致

#### F. `ResponseGuardService`

職責：

- 若外部證據不足，禁止確定句
- 修正高風險斷言
- 避免 hallucination 式的結論
- 把錯誤 fallback 改成誠實的不確定表述

---

## 七、Research Planner 設計

### 7.1 為什麼 Planner 是必要的

如果沒有 planner，系統就只能：

- 一直補關鍵字規則
- 或一律先答，再失敗 fallback

這兩種都不夠穩。

Planner 的作用是讓系統在「回答之前」先思考：

- 這題靠內知識能不能回答？
- 這題需不需要外部證據？
- 應先查本地知識還是直接查網路？
- 應該用哪些搜尋查詢？
- 若找不到證據，能否仍然回答？

### 7.2 Planner 的基本輸出結構

建議建立一個 `ResearchPlan`，例如：

```python
from pydantic import BaseModel
from typing import Literal, List

class ResearchPlan(BaseModel):
    route: Literal["knowledge_direct", "search_then_answer", "direct_reasoning"]
    needs_external_info: bool
    needs_knowledge_base: bool
    freshness: Literal["none", "recent", "today", "realtime"]
    search_queries: List[str]
    forbid_unverified_claims: bool
    answer_style: Literal["concise", "balanced", "deep"]
```

### 7.3 Planner 的判斷依據

Planner 可以結合兩層：

#### 第一層：輕量 heuristics

用於快速提高高風險題目的 search bias，例如命中：

- 今天、現在、目前、最新、即時
- 比分、賽程、價格、股價、匯率、天氣
- 誰是 CEO、目前總統、上映資訊、活動時間

#### 第二層：LLM planner prompt

讓模型根據：

- 使用者問題
- 最近上下文
- 今日日期
- 可用工具

輸出結構化 JSON，而不是直接輸出答案。

### 7.4 Planner 的幾個典型判斷案例

#### 案例 A：一般知識

問題：什麼是鋼骨結構？

Planner 應輸出：

- `route = direct_reasoning`
- `needs_external_info = false`
- `forbid_unverified_claims = false`

#### 案例 B：知識庫問題

問題：我們知識庫裡對某個 SOP 的說明是什麼？

Planner 應輸出：

- `route = knowledge_direct`
- `needs_knowledge_base = true`
- `needs_external_info = false`

#### 案例 C：即時資訊問題

問題：今天有什麼棒球賽？

Planner 應輸出：

- `route = search_then_answer`
- `needs_external_info = true`
- `freshness = today`
- `forbid_unverified_claims = true`
- `search_queries = [...]`

---

## 八、Knowledge-first 流程設計

### 8.1 知識庫優先原則

系統回答應遵守以下優先序：

1. **若知識庫已有足夠答案，直接基於知識庫回答**
2. **若知識庫不足，再進入 web research**
3. **若網路也不足，誠實表達查無可靠資料**

### 8.2 知識庫命中判定

知識庫不能只是「查到一點字就算有答案」，應加入命中品質判定，例如：

- chunk relevancy score
- 多個 chunk 是否一致
- 是否足以支持完整回答
- 是否只是部分提及但不足以下結論

### 8.3 知識庫回答的產出規範

如果知識庫證據足夠，回答應：

- 直接給結論
- 適度整理與重寫
- 若必要，可點出是根據知識庫內容整理
- 避免明顯機械式貼 chunk

---

## 九、Web Research 流程設計

### 9.1 為什麼要從「搜尋」升級為「研究」

單純搜尋只會得到一堆連結；研究流程則包含：

- 該搜什麼
- 搜到後要不要相信
- 結果夠不夠回答
- 若結果互相衝突怎麼處理
- 最後怎麼綜合成回答

### 9.2 WebResearchService 的必要能力

#### A. Query rewrite

不能只把使用者原句直接丟去搜。

例如：

- 使用者：今天有什麼棒球賽？
- 可展開為：
  - 今日 棒球 賽程
  - CPBL 今日賽程
  - MLB schedule today
  - NPB schedule today

#### B. Multi-query search

一個問題應允許多組查詢並行或依序執行。

#### C. Result filtering

必須過濾：

- 過舊資料
- 重複結果
- 低品質來源
- 與問題不真正相關的頁面

#### D. Freshness check

若問題是 today/latest/current 類，就必須確認：

- 頁面是否對應今日或近期
- 是否不是舊新聞/舊文章
- 是否真的能支持當前事實性回答

#### E. Trust / source quality

應盡量偏好：

- 官方網站
- 可靠新聞媒體
- 專業資料站
- 已知高品質來源

#### F. Answerability check

不是有搜尋結果就能回答，還要判斷：

- 這些結果是否足夠支持結論
- 是否只找到模糊線索
- 是否不足以回答具體問題

### 9.3 Web evidence 的回傳形式

WebResearchService 不應直接回一段最終答案，而應回：

- evidence list
- source metadata
- freshness assessment
- confidence / sufficiency
- extracted summary facts

這樣 `AnswerComposerService` 才能做更穩定的整合。

---

## 十、Answer Composer 設計

### 10.1 回答生成的責任切分

AnswerComposer 不應負責決定「要不要查」，而是負責：

- 把 evidence 組成高品質回答
- 控制回答結構與風格
- 針對不同情境輸出恰當模板

### 10.2 三種輸出情境

#### A. 知識庫已足夠

回答方式：

- 直接用知識庫內容整合回答
- 可附上「根據知識庫整理」的語氣

#### B. 網路研究已足夠

回答方式：

- 先給結論
- 再給條列重點 / 原因 / 分析
- 若有多來源，可簡述整合依據

#### C. 證據不足

回答方式：

- 明確說目前沒有查到足夠可靠資料
- 視情況建議縮小範圍
- 不得下具體事實結論

### 10.3 風格目標

回答應該：

- 有邏輯層次
- 優先給結論
- 再補理由與分析
- 不空泛、不過度客套
- 像專業研究助理，而不是模板客服

---

## 十一、Response Guard 設計

### 11.1 Guard 的新定位

Guard 不應只是修字句，而應是最後一道 **事實風險控制層**。

### 11.2 必擋類型

#### A. 無證據的時效性斷言

例如：

- 今天沒有棒球賽
- 現在沒有活動
- 這家公司目前 CEO 是某人
- 今天台北不會下雨

若沒有可靠證據，不可輸出。

#### B. 把「查不到」說成「沒有」

這是目前系統非常需要修正的錯誤。

錯誤例子：

- 我找不到資料 → 今天沒有賽事
- 沒有外部結果 → 這件事不存在

#### C. 未驗證數字/日期/比分/價格

涉及：

- 金額
- 時間
- 日期
- 成績
- 賽程
- 即時職位

都要特別保守。

### 11.3 Guard 的輸出策略

當 evidence 不足時，應自動轉成類似表述：

- 我目前沒有查到足夠可靠的資料，因此不能直接下結論。
- 這題需要即時外部資訊；目前搜尋結果不足以確認。
- 我可以再縮小範圍幫你查，例如指定聯盟、地區或時間。

---

## 十二、檔案與模組重構建議

### 12.1 建議保留並重寫的檔案

- `app.py`
- `bot.py`
- 現有 `bot_service.py`（建議之後拆分）
- session/message repositories
- knowledge / rag service
- web search service
- response guard service

### 12.2 建議新增的模組

- `services/research_planner_service.py`
- `services/knowledge_first_service.py`
- `services/web_research_service.py`
- `services/answer_composer_service.py`
- `services/chat_orchestrator.py`（或重構後的 `bot_service.py`）

### 12.3 建議刪除或停用的模組

- `services/image_ocr_service.py`
- `services/document_parser_service.py`
- `agent_loop.py`
- code-oriented service / helper / parser
- task/profile memory 相關模組
- 與圖片/檔案/code 相關的 prompt 模板檔

---

## 十三、`app.py` 修改計畫

### 13.1 現況問題

`app.py` 目前過度膨脹，可能同時做了：

- settings 初始化
- 資料庫初始化
- repositories 實例化
- services 實例化
- API routes 註冊
- 各種 admin route
- 外部功能 wiring

這使得：

- 測試困難
- 啟動副作用多
- 後續維護不容易

### 13.2 重構目標

把 `app.py` 收斂為：

- app 啟動入口
- dependency wiring
- 核心 route 註冊

### 13.3 建議保留的 route

- `/health`
- `/health/detail`
- `/admin/knowledge/reindex`
- `/admin/llm-logs`
- `/webhook`

### 13.4 建議刪除的 route

- 與 profile memory 相關 route
- 與 task memory 相關 route
- 與圖片/檔案處理相關 route
- 非必要的 model runtime 切換 route
- 已不屬於新產品主軸的 admin route

### 13.5 建議做法

建立一個單獨的 wiring / bootstrap 模組，例如：

- `bootstrap.py`
- `container.py`

由它建立：

- repositories
- llm service
- rag service
- web search service
- planner service
- knowledge first service
- web research service
- answer composer service
- response guard service
- chat orchestrator

---

## 十四、`bot.py` 修改計畫

### 14.1 角色收斂

`bot.py` 應只保留通道層責任：

- 驗證 LINE webhook signature
- 解析 text event
- 處理群組 mention 規則
- 呼叫 chat orchestrator
- 將文字回覆發回 LINE

### 14.2 應刪除內容

- image event 分支
- file event 分支
- blob download
- OCR / document parse 前處理
- 與多模態能力宣告有關的回應

### 14.3 修改後的結果

`bot.py` 會變得明顯更小、更穩定，也更容易測試。

---

## 十五、`BotService` / Orchestrator 重寫計畫

### 15.1 為何不能再讓 `BotService` 繼續膨脹

目前這類 service 很容易變成：

- intent classifier
- capability registry
- fallback engine
- search orchestrator
- prompt warehouse
- memory manager
- safety layer
- answer rewriter

全部混在同一個類別裡。

這會讓：

- 任何修改都很危險
- 很難做清楚的單元測試
- 任何 bug 都不容易定位

### 15.2 新設計

把原本的巨型 `BotService` 拆為：

- `ChatOrchestrator`
- `ResearchPlannerService`
- `KnowledgeFirstService`
- `WebResearchService`
- `AnswerComposerService`
- `ResponseGuardService`

### 15.3 ChatOrchestrator 的單一責任

它只負責協調流程：

1. 取得 session/context
2. 產生 plan
3. 先查 knowledge
4. 不夠再查 web
5. 組合回答
6. 經 guard 修正
7. 存檔並回傳

---

## 十六、舊代碼刪除策略

### 16.1 原則：確定不用就直接刪

不要：

- 留大量 `legacy_` 模組
- 註解掉整段不刪
- 保留未來可能用到的 dead code

這會讓專案長期處於半重構狀態。

### 16.2 建議的全域搜尋關鍵字

在實作清理前，先全 repo 搜尋以下關鍵字：

- `image`
- `ocr`
- `file`
- `parser`
- `document`
- `pdf`
- `docx`
- `xlsx`
- `pptx`
- `coding`
- `code_call`
- `tool_call`
- `agent`
- `task`
- `profile`
- `weather`
- `market`

### 16.3 清理步驟建議

1. 列出所有引用點
2. 標記哪些屬於必刪功能
3. 先刪 import 與 route
4. 再刪 service/module
5. 再刪 prompt 常數與設定欄位
6. 最後修 tests 與文件

### 16.4 Prompt dead code 清理

很多專案在重構時只刪程式碼，忘了刪 prompt 與 capability 宣告，結果模型仍然：

- 以為自己會讀圖片
- 以為自己能處理檔案
- 以為自己會寫 code

這些都要在 prompt 層一起刪掉。

---

## 十七、設定檔與環境變數重整

### 17.1 應刪除的設定

- OCR 相關設定
- document parser 相關設定
- coding assistance 相關設定
- agent loop 相關設定
- task/profile memory 相關設定

### 17.2 建議保留或新增的設定

- `LLM_PROVIDER`
- `LLM_MODEL`
- `KNOWLEDGE_ENABLED`
- `WEB_SEARCH_ENABLED`
- `WEB_SEARCH_MAX_RESULTS`
- `WEB_SEARCH_TIMEOUT`
- `PLANNER_ENABLED`
- `MAX_CONTEXT_MESSAGES`
- `REQUIRE_MENTION_IN_GROUP`
- `RESPONSE_STYLE_DEFAULT`

### 17.3 設定收斂原則

設定不應再分散於大量功能旗標，而應聚焦在：

- 模型
- 知識庫
- 搜尋
- 對話上下文
- 群組互動規則
- 護欄

---

## 十八、分階段實作計畫

### Phase 1：瘦身與刪減（優先）

#### 目標

先把專案從「功能過多的平台」縮成「單純文字 bot」。

#### 任務

- 移除 image/file event handling
- 移除 OCR / document parser
- 移除 coding assistance
- 移除 agent loop
- 移除 task/profile memory
- 移除圖片/檔案/code 相關 prompt
- 收斂 `bot.py` 與 `app.py`

#### 交付成果

- 系統只處理文字訊息
- 專案結構明顯簡化
- 核心流程更清楚

### Phase 2：導入 Planner

#### 目標

讓系統從 rule-based routing 升級為 evidence-first planning。

#### 任務

- 新增 `ResearchPlannerService`
- 定義 `ResearchPlan` schema
- 在 orchestrator 中導入 planner
- 高風險題型先由 planner 決定是否 search

#### 交付成果

- 問題進來後先有「研究計畫」
- 不再大量依賴 domain 關鍵字硬編 route

### Phase 3：強化 Web Research

#### 目標

讓系統真正會「查資料後再回答」。

#### 任務

- 加入 query rewrite
- 支援 multi-query search
- 實作 freshness / trust / answerability check
- 統一 evidence 輸出格式

#### 交付成果

- 對即時問題能穩定查詢
- 查不到時能正確失敗，而不是亂答

### Phase 4：回答品質與風格收尾

#### 目標

把回答風格收斂成專業、成熟、清楚的博士型助理。

#### 任務

- 新增 `AnswerComposerService`
- 縮減 prompt，保留風格主軸
- 強化 response guard
- 調整多輪對話上下文策略

#### 交付成果

- 回答更有層次
- 風格更穩定
- 不再像 template bot

---

## 十九、測試計畫

### 19.1 單元測試

#### A. Planner 測試

驗證：

- 一般知識題不 search
- 即時題會 search
- 知識庫題優先走 knowledge
- planner query 輸出合理

#### B. KnowledgeFirstService 測試

驗證：

- 命中高相關 chunk 時能直接回答
- 命中不足時回傳 insufficient

#### C. WebResearchService 測試

驗證：

- query rewrite 正常
- 多 query 結果可整併
- freshness check 能排除舊資料
- answerability check 能判定不足

#### D. ResponseGuardService 測試

驗證：

- 無證據時不得輸出「今天沒有」等句型
- 會把高風險答案改寫成保守形式

### 19.2 整合測試

#### 類型 A：一般知識題

例如：
- 什麼是鋼骨結構？
- FastAPI 跟 Flask 差異是什麼？

預期：
- 不查網路也能正確回答

#### 類型 B：知識庫題

例如：
- 我們知識庫裡怎麼定義某 SOP？

預期：
- 優先走 RAG / knowledge

#### 類型 C：即時題

例如：
- 今天有什麼棒球賽？
- 現在台北天氣如何？

預期：
- 自動 search
- 不得直接憑空回答

#### 類型 D：查不到的題

例如：
- 某個很冷門、沒資料的即時資訊

預期：
- 說明查不到可靠資料
- 不亂編答案

#### 類型 E：多輪追問

例如：
- 今天有什麼棒球賽？
- 那中職呢？
- 哪一場比較值得看？

預期：
- 能延續上下文
- 能根據已查資料繼續整理

---

## 二十、驗收標準

重構完成後，至少應達到以下標準：

### 20.1 功能面

- 只處理 LINE 純文字訊息
- 知識庫可正常檢索回答
- 網路搜尋可被 planner 正確觸發
- 無證據時不亂答

### 20.2 品質面

- 回答更有結構與分析感
- 即時題回答錯誤率顯著下降
- fallback 不再把查不到說成沒有
- 系統能力宣告與實際能力一致

### 20.3 工程面

- `app.py` 與 `bot.py` 顯著瘦身
- 不再有大量 dead code
- 主要流程可用單元測試覆蓋
- service 責任清楚，便於後續維護

---

## 二十一、風險與注意事項

### 21.1 一次刪太多功能可能影響舊測試

這是正常現象。這次重構本來就不是「保留所有舊行為」，而是明確改產品方向，因此：

- 舊測試若是為圖片/檔案/code/task/profile 而存在，應一起刪除
- 不要為了讓舊測試綠燈而保留舊設計

### 21.2 Planner 不是萬能，需要和 heuristic 搭配

Planner 很重要，但不能完全放棄基礎 heuristics。對於高風險資訊：

- today / latest / current
- price / score / weather / schedule / identity

仍應以少量硬規則提高 search 與 guard 強度。

### 21.3 搜尋成功不等於答案可靠

要避免新的錯誤模式：

- 只要有搜尋結果就回答
- 沒有做 freshness / trust / answerability 檢查

### 21.4 風格不要壓過事實流程

「像博士」不等於回答很長，也不等於用很多艱深詞。真正重要的是：

- 先有正確的知識來源判斷
- 再有清楚的分析與整合

---

## 二十二、最終建議結論

這次修改不應被視為「幫現有 bot 再補一些功能」，而應視為一次 **產品重新聚焦與架構收斂**。

正確方向不是：

- 再加更多 if/else
- 再補更多 domain-specific route
- 再保留一堆未來可能會用到的舊功能

而是：

1. **大砍非核心功能**
2. **保留文字對話、知識庫、搜尋、記憶、護欄**
3. **新增 planner，改成 evidence-first routing**
4. **讓 web search 成為正式研究流程的一部分**
5. **讓回答建立在證據與整合能力上，而不是模板與猜測上**

若這份計畫照順序落地，最終會把專案從：

> 功能很多但不夠穩定的 bot 平台

轉成：

> 一個更專注、更可靠、更像真正研究助理的 LINE 文字智慧系統

---

## 二十三、建議下一步

建議依序執行以下三件事：

1. 先做 **Phase 1 瘦身與刪減**，不要急著加新功能
2. 瘦身完成後，再導入 **ResearchPlannerService**
3. 最後才強化 **WebResearchService** 與回答風格

這樣可以避免在舊架構的複雜度上繼續堆新邏輯。
