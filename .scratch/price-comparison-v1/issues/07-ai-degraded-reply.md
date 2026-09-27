# 07: AI 額度用完或故障時的降級回覆

**Parent:** [規格書](../spec.md)

**What to build:** 當 Gemini 失敗（伺服器錯誤、速率限制、額度用完、逾時）時，不再只回一段錯誤文字，而是以使用者原始輸入作為關鍵字，回覆一張包含「其他」類 6 個平台搜尋連結的卡片，並附簡短說明。

**Blocked by:** 06

**Status:** done（待手動 LINE 驗證）

- [x] Gemini 各類失敗（5xx、429、額度用完、逾時）都回覆降級卡片
- [x] 降級卡片使用原始輸入作為搜尋關鍵字，列出「其他」類的 6 個平台搜尋連結
- [x] 卡片上有一句說明 AI 暫時無法使用
- [x] 降級回覆也遵守 15 秒上限
- [x] 入口測試涵蓋各類 Gemini 失敗


**Notes（實作後）：**
- 降級在比價流程入口（`services/comparison.py`）處理：文字輸入遇到任何 `GeminiAPIError`（5xx、429／額度用完、其他 API 錯誤、`AiTimeoutError`）時，回傳 `ai_unavailable=True` 的結果：原始文字當關鍵字、類別「其他」、6 平台皆為 `LINK_ONLY`，不查平台、不快取。評測走同一入口，可從此欄位統計 AI 故障次數。
- 卡片（`build_comparison_flex`）：`ai_unavailable` 時卡片本文最上方顯示 `AI_UNAVAILABLE_NOTICE`，alt text 改為說明，不顯示「價格更新」時間（沒有查價）。
- 15 秒上限：沿用 06 的 AI 解析上限（7 秒且不超過整體截止），降級不查平台，組卡片即回覆。
- 例外：**圖片輸入**沒有可用的關鍵字，AI 故障時仍回文字訊息（伺服器忙／查詢人數較多；其他 AI 錯誤回 AI 忙碌訊息）。若要圖片也回連結卡片，需另定關鍵字來源。
- 移除 `test_integration.py` 兩個舊的「AI 故障回文字」測試，改由 `test_webhook_background.py` 參數化測試涵蓋。
- Code review 留待後續：`ComparisonResult` 以旗標區分結果種類（正常／與購物無關／AI 故障），日後可整理成明確型別；AI 設定錯誤（如缺 API key）也會降級成連結卡片，只留 warning log。
