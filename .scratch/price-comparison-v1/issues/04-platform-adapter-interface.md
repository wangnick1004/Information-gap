# 04: 平台轉接器統一介面，接上既有 6 個平台

**Parent:** [規格書](../spec.md)

**What to build:** 定義統一的「平台轉接器」介面：給定關鍵字與逾時 → 回傳商品清單（標題、價格、幣別、連結、可選縮圖）或明確的失敗狀態。把既有的 Mercari/Buyee、日本樂天、日本 Yahoo 拍賣、蝦皮、淘寶、台灣 Yahoo 抓價改為此介面，並註冊到一個平台集合供比價流程入口使用。使用者看到的卡片不變。付費第三方 API（RapidAPI）只在「評測模式」設定下啟用。

**Blocked by:** 02

**Status:** done（待手動 LINE 驗證）

- [x] 所有平台轉接器實作同一介面，回傳商品清單或失敗狀態（逾時／被擋／格式異常／無結果），不拋出未處理例外
- [x] 比價流程入口只透過平台集合呼叫轉接器，不再直接呼叫個別平台函式
- [x] RapidAPI 轉接器在正式設定下關閉，只能透過評測模式設定開啟
- [x] 每個既有平台有以錄下的真實回應（HTML/JSON 檔）驗證解析的測試（測試切入點 3）
- [x] 每個轉接器有「被擋／格式異常 → 失敗狀態」的測試
- [x] 卡片結果與重構前一致；測試全數通過


**Notes（實作後）：**
- 延續 [02](02-extract-comparison-pipeline.md) 的待辦：各平台搜尋連結（含分潤參數）已移入比價結果（`PlatformQuote.search_url`），由平台集合依關鍵字組出；卡片產生器改用結果中的連結，不再自行組連結。連結組法搬到 `services/search_links.py`（`flex_builder` 仍可匯入舊名稱）。
- 介面與平台集合在 `services/platforms.py`：`PlatformAdapter.search(keyword, timeout) -> FetchResult`（狀態：成功／無結果／逾時／被擋／格式異常／失敗）；`build_platforms()` 依卡片順序回傳 6 個平台，無轉接器者為「僅連結」。比價流程入口改收 `platforms=`（取代 02 的 `PlatformFetchers`）。
- 「既有 6 平台」中只有 Buyee Mercari、日本樂天（經 Buyee）真的有抓價；日本 Yahoo 拍賣、淘寶、台灣 Yahoo 原本就只有連結，維持「僅連結」。蝦皮原本只靠 RapidAPI，所以正式設定下改為「僅連結」。台灣 Yahoo 的真實轉接器屬 13。
- 評測模式：`EVALUATION_MODE=true` 時 Mercari 改用 Fashion Resale RapidAPI、蝦皮改用 RapidAPI；正式設定一律關閉。
- 錄下的真實回應在 `tests/fixtures/platforms/`（2026-09-26 錄製）。錄製時的發現：
  - Buyee Mercari 不經瀏覽器直接請求會回 HTTP 202（反爬蟲），正式環境 Mercari 目前幾乎都是「失敗」，卡片走僅連結版；另外舊解析器對真實頁面解析不到任何商品（頁面價格寫「YEN」），新轉接器已能解析，但仍受 202 阻擋。
  - Fashion Resale RapidAPI 指定 `platform=mercari` 仍只回 goat / ebay 商品，舊程式把它們當成 Mercari 價格顯示（「約 NT$…起」）。新轉接器只收 platform 為 mercari 的商品，因此實際上是「無結果」；「起」價顯示一併移除。
  - 蝦皮 RapidAPI 目前設定的端點（ninjaapi2，`shop_id=fe_amart`）回 HTTP 404。評測前需要換可用的端點（08／16）。
- 各平台價格計算沿用舊的雜訊剔除規則（`services/platforms.py` 的 price rule：Mercari 前 15 筆去頭尾 20%、樂天前 5 筆排除 300 日圓以下、RapidAPI 標題黑名單＋中位數 40%）；[14](14-relevance-filter-verifiable-items.md) 的相符性過濾上線後取代。
- 與重構前的差異（code review 後更正，以下使用者都看得到）：
  1. 關鍵字統一：搜尋與所有連結都用同一組關鍵字（中文：`perfected_keyword` → `keyword_zh` → 作品＋角色；日文：`search_query_ja` → `keyword_jp`）。舊版完整卡片與僅連結卡片各用不同的優先順序，因此部分平台連結、僅連結卡片上的日文關鍵字與商品名稱，在 AI 欄位不一致時會與舊版不同。刻意統一，確保「連結搜的就是比價搜的」。
  2. Buyee Mercari 若回 HTTP 200，現在能解析出商品，會出現完整「比價分析」卡片（舊版解析永遠失敗，只會出現僅連結卡片）。目前正式環境仍多被 202 擋下。
  3. 移除「起」價標示（原本只來自錯誤的 goat／ebay 資料）；蝦皮在正式設定下變為僅連結。
  4. 評測模式下 Mercari 價格為美元，因此不會出現舊版完整卡片（評測只看結構化結果，不影響）。
  5. 不再有「Buyee 失敗後重抓」的二次請求，每個平台只查一次；移除平台層級的個別快取（整個比價結果仍快取 1 小時，全數失敗的結果也一樣會被快取，與舊版相同）。
  6. 每次查詢記錄各平台狀態與耗時（User Story 42，順手加入）。
- 卡片產生器仍保留自行組連結的程式，只作為沒有比價結果連結時的備援（`main.fetch_and_generate_flex_message` 等舊呼叫端直接使用）；[14](14-relevance-filter-verifiable-items.md) 換新卡片時一併移除。
- 舊的抓價函式（`price_fetcher.py`、`services/scraper.py` 的 Buyee／台灣／淘寶搜尋、`services/lightweight_fetcher.py`）已不在比價流程中使用，只剩舊測試與 `main.fetch_and_generate_flex_message` 參照，可另開票清除。
