# 01: 清除假價格與 Netlify 遺留

**Parent:** [規格書](../spec.md)

**What to build:** 讓系統在任何情況下都不可能向使用者顯示寫死的假價格，並移除已不使用的 Netlify serverless 部署設定與入口，使專案只保留 Render 常駐服務這一種部署方式。

**Blocked by:** None (can start immediately)

**Status:** done

- [x] 程式中不再存在任何產生假價格（mock plausible price）的函式或開關，所有呼叫點一併移除
- [x] 平台抓價失敗時，結果為「無價格／僅連結」，而非任何預設數字
- [x] Netlify 設定檔與 serverless 入口已刪除；Render 啟動方式（直接執行 FastAPI app）不受影響
- [x] 依賴清單中僅為 Netlify/Lambda 存在的套件（如 Mangum）若已無使用則移除
- [x] 新增測試：模擬所有平台失敗時，回覆中不出現任何價格數字
- [x] 既有測試全數通過（刪除僅測試假價格行為的測試案例）
