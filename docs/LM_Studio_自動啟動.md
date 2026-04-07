# LM Studio 自動啟動功能

## 概述

當執行 Line Bot 時，如果 LM Studio 模型服務未啟動，應用可以**自動檢測並啟動** LM Studio。

## 配置方式

### 1. 設定 LM Studio 可執行檔路徑

在 `.env` 文件中加入 `LM_STUDIO_EXE_PATH`：

```env
LM_STUDIO_EXE_PATH=C:\Users\lioil\AppData\Local\LM-Studio\bin\LM-Studio.exe
```

### 2. Windows 常見路徑

- **標準安裝位置**：
  ```
  C:\Users\<Username>\AppData\Local\LM-Studio\bin\LM-Studio.exe
  ```

- **可從 LM Studio 應用程式內找到**：
  1. 開啟 LM Studio
  2. 點擊「About」或「設定」
  3. 查看可執行檔路徑

### 3. 驗證路徑是否正確

```powershell
# PowerShell 檢查
Test-Path "C:\Users\lioil\AppData\Local\LM-Studio\bin\LM-Studio.exe"

# 返回 True 表示路徑正確
```

## 工作流程

### 啟動順序

1. **應用啟動時**
   ```
   應用啟動 → 檢查 LM_STUDIO_EXE_PATH 配置
   ```

2. **如果配置了 exe 路徑**
   ```
   嘗試連接 http://127.0.0.1:1234/v1 
   ↓
   連線成功 ✓ → 使用現有 LM Studio
   連線失敗 ✗ → 自動啟動 LM Studio
   ```

3. **啟動後等待初始化**
   ```
   最多等待 30 秒
   ↓
   模型加載完成 ✓ → 應用正常運行
   超時 ✗ → 應用仍可運行（降級模式）
   ```

### 啟動日誌

應用啟動時會輸出狀態訊息：

```
✓ LM Studio 已在執行              # LM Studio 已經運行
🚀 啟動 LM Studio: ...            # 正在啟動
✓ LM Studio 啟動成功 (12.3s)      # 啟動完成，耗時 12.3 秒
❌ 啟動 LM Studio 失敗: ...        # 啟動失敗的原因
```

## 使用場景

### 場景 1：首次執行（LM Studio 未啟動）

```powershell
# 執行 Line Bot（onefile）
& "E:\py\LineBot\dist\linebot-app.exe"

# 輸出：
# 🚀 啟動 LM Studio: C:\Users\lioil\AppData\Local\LM-Studio\bin\LM-Studio.exe
# ✓ LM Studio 啟動成功 (15.2s)
# INFO: Uvicorn running on http://127.0.0.1:8000
```

### 場景 2：LM Studio 已在運行

```powershell
# 執行 Line Bot（onefile）
& "E:\py\LineBot\dist\linebot-app.exe"

# 輸出：
# ✓ LM Studio 已在執行
# INFO: Uvicorn running on http://127.0.0.1:8000
```

### 場景 3：無 exe_path 配置（手動啟動）

```env
# .env 中無 LM_STUDIO_EXE_PATH 或為空
LM_STUDIO_EXE_PATH=
```

應用啟動時只會檢查連接，不會自動啟動。需要手動啟動 LM Studio。

## 常見問題

### Q1: exe 路徑無效時會怎樣？

**A:** 應用會打印警告訊息並继续运行：

```
⚠️  LM Studio exe 不存在: <path>
```

此時應用仍會嘗試連接 `http://127.0.0.1:1234`，若 LM Studio 已在執行則可繼續使用。

### Q2: 啟動超時了怎樣？

**A:** 如果 LM Studio 在 30 秒內未啟動，應用會記錄警告但繼續啟動：

```
❌ LM Studio 在 30s 內未啟動
```

這通常表示：
- LM Studio 需要更長時間初始化（例如首次綁定模型）
- exe 路徑不正確
- 系統資源不足

**解決方法**：
1. 手動啟動 LM Studio 並確認模型已加載
2. 檢查 exe 路徑正確性
3. 增加 max_wait_seconds 參數（需代碼修改）

### Q3: 能改變等待超時時間嗎？

**A:** 可以在應用啟動前修改 `app.py` 中的代碼：

```python
# app.py lifespan 函數
llm_service.try_start_lm_studio(max_wait_seconds=60)  # 改為 60 秒
```

### Q4: 我想禁用自動啟動

**A:** 直接刪除或留空 `LM_STUDIO_EXE_PATH`：

```env
# .env
LM_STUDIO_EXE_PATH=
```

或在 `.env` 中完全移除此行。

## 技術細節

### 實現位置

- **配置**: [config.py](../src/linebot_app/config.py) 
  - `lm_studio_exe_path` 設定

- **邏輯**: [services/llm_service.py](../src/linebot_app/services/llm_service.py)
  - `try_start_lm_studio()` 方法
  - `is_available()` 連接檢測

- **初始化**: [app.py](../src/linebot_app/app.py)
  - `lifespan()` 函數中調用啟動邏輯

### 相關代碼

```python
# 檢查和啟動 LM Studio
result = llm_service.try_start_lm_studio(max_wait_seconds=30)
# 返回 True: 已連接或成功啟動
# 返回 False: 連接失敗且無法啟動
```

## 套用到打包後的 .exe

如果使用 PyInstaller 打包的 exe，自動啟動功能也會正常工作：

```powershell
# onedir 版本
E:\py\LineBot\dist\linebot-app\linebot-app.exe

# onefile 版本
E:\py\LineBot\dist\linebot-app.exe
```

兩個版本都包含相同的自動啟動邏輯。

## 測試

驗證功能正常運行：

```bash
# 執行測試套件
pytest tests/ -v

# 目前測試數量會隨專案調整，請以當下 pytest 輸出為準
# tests/test_llm_service.py 中有相關測試
```

---

**最後更新**: 2026 年 4 月 7 日  
**功能版本**: v0.2.0+ (含自動啟動支持)
