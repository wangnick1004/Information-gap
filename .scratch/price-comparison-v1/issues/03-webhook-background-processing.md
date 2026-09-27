# 03: Webhook 先回應 LINE，再背景比價

**Parent:** [規格書](../spec.md)

**What to build:** 收到 LINE 訊息並通過簽章驗證後，立即回應 LINE 伺服器 200，比價在背景執行，完成後用 reply token 回覆卡片。避免 webhook 逾時與 LINE 重送造成的重複回覆。

**Blocked by:** 02

**Status:** done（待手動 LINE 驗證）

- [x] 合法 webhook 請求在比價完成前即回應 200
- [x] 比價於背景完成後，以該事件的 reply token 回覆卡片
- [x] 同一事件（LINE 重送，具相同 webhook event id）不會被處理兩次
- [x] 背景處理中的任何例外都被捕捉並回覆降級訊息，不會讓服務崩潰
- [x] 處理中動畫（loading animation）仍在比價開始時顯示
- [x] webhook 切入點測試：驗證立即回 200、背景完成後回覆、重送事件只回覆一次
