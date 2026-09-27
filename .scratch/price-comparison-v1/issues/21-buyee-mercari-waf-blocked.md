# 21: Buyee Mercari 被 AWS WAF 擋下，正式環境改為只給搜尋連結

**Parent:** [規格書](../spec.md)

**What to build:** 正式環境（非評測模式）的 Mercari 目前經 `BuyeeMercariAdapter` 直接抓 Buyee 搜尋頁，但 Buyee 對 `/mercari/*` 路徑啟用了 AWS WAF 的 JavaScript 挑戰，程式請求一律拿到 HTTP 202 空頁，任何關鍵字都回 `blocked`、0 筆。這不是關鍵字、網址或 HTML 結構改變造成的，也不是換 header 能解決的。本票在不增加任何費用、也不繞過對方防爬機制的前提下處理：先在 Render 上確認同樣被擋，接著讓正式環境的 Mercari 和日本 Yahoo 拍賣一樣只提供搜尋連結（不發請求、不佔 15 秒預算、卡片不顯示成「查詢失敗」），並保留轉接器與測試，Buyee 哪天解除挑戰時可以恢復。

**Blocked by:** 無

**Status:** done

- [x] 人工：確認 Render 上同樣被擋。~~在 Render Shell 執行 curl~~（Render Shell 僅限付費方案，改用下列方式）：目前 Render 上的版本（origin/main，c945a56）每次比價都會記一行 `[Platform] mercari: <狀態> in <毫秒> (<筆數> listings) <細節>`，這就是 Render 出口 IP、帶正式 `DEFAULT_HEADERS` 的真實請求，比 curl 更貼近正式環境。**在部署本票之前**，用 LINE 送一筆動漫周邊查詢（例如「咒術迴戰 五條悟 公仔」），到 Render 儀表板 → 服務 → Logs 搜尋 `[Platform]`，把 mercari 與 rakuten 兩行貼進 Notes（預期 `mercari: blocked ... HTTP 202` 或 `HTTP 403`、`rakuten: ok`）。本票部署後 Mercari 不再發請求，就看不到這行了。Render 預設在推送 main 後自動部署，且免費方案 log 保留時間短，所以**取得 log 前不要推送本票的 commit**
- [x] 正式環境 `build_platforms(evaluation_mode=False)` 的 `mercari` 不再掛 `BuyeeMercariAdapter`（改成 `adapter=None`，比照 `yahoo_jp`），搜尋連結與 Buyee 聯盟參數維持不變；評測模式仍用 `MercariRapidApiAdapter`
- [x] 比價入口測試（假 AI）：正式模式下 Mercari 不發出任何 HTTP 請求，卡片上 Mercari 仍有日文關鍵字的 Buyee 搜尋連結，且不顯示成失敗
- [x] `BuyeeMercariAdapter` 保留（含 202→`blocked` 的既有測試），不刪；程式註解寫明停用原因與日期，方便日後恢復
- [x] 檢查 `services/comparison.py` 中依賴 Mercari 成功結果的舊卡片欄位（`_with_legacy_card_fields`）在 Mercari 無轉接器時不出錯
- [x] 規格書中「Mercari 顯示即時價格」的描述（若有）改成正式環境只給連結、評測模式才有價格

**Notes：**
- 發現經過：20 實作後以「呪術廻戦 五条悟 フィギュア」實測，日本樂天（Buyee）20 筆，Mercari（Buyee）`blocked`；中文關鍵字同樣 `blocked`，與關鍵字語言無關。
- 重現（2026-09-27，本機、台灣住宅網路）：`search_safely(build_platforms(evaluation_mode=False)["mercari"].adapter, "呪術廻戦 五条悟 フィギュア", 10)` → `FetchStatus.BLOCKED`、`HTTP 202`、0 筆；同一關鍵字的 `BuyeeRakutenAdapter` → `OK`、20 筆。
- 原因：回應標頭 `x-amzn-waf-action: challenge`、`server: awselb/2.0`，內容是 AWS WAF 挑戰頁（載入 `*.token.awswaf.com/.../challenge.js`，需在瀏覽器執行 JS 取得 `aws-waf-token` cookie 後才放行）。這是 WAF 對特定路徑的規則，不是單純的 header 檢查：
  - 用和 Chrome 140 相同的 User-Agent 與 `DEFAULT_HEADERS` 仍是 202；不帶瀏覽器 UA（curl 預設）則是 403。
  - 被挑戰的路徑：`/mercari/search`（有無關鍵字皆同）、`/mercari/item/...`、`/item/search/query/...`（Buyee 的日本 Yahoo 拍賣搜尋）。
  - 未被挑戰：`/`、`/rakuten/shopping/search/...`（200，可正常解析）。
  - `tests/test_platform_adapters.py` 已記錄 2026-09-26 同樣拿到 202，`tests/fixtures/platforms/buyee_mercari_search.html` 是用瀏覽器取得的頁面，所以單元測試一直是綠的，但正式環境的 Mercari 從接上轉接器（04）起可能就沒有成功過。舊的 `services/scraper.py` 也已有 202/403 的 `ScrapingBlockedError`。
- 不是 JS 渲染問題：挑戰通過後 Buyee Mercari 搜尋頁是伺服器端輸出的 HTML（fixture 中商品清單直接在 `ul.item-lists li.list`），解析器本身沒問題。
- Render 上是否也被擋：尚未在 Render 實測（見第一項）。推論幾乎必然也被擋：本機住宅 IP 帶完整瀏覽器 header 都拿到挑戰，Render 的出口是雲端機房 IP，WAF 對機房 IP 通常更嚴。從雲端抓取工具測試 Buyee 所有路徑都是 403，但那是因為工具的 User-Agent 不像瀏覽器（本機用非瀏覽器 UA 也是 403），不能拿來當 Render 的證據。
- 評估過但不採用的做法：
  - 調整 header／cookie／TLS 指紋、用無頭瀏覽器（Playwright）自動通過 WAF 挑戰：屬於繞過對方防爬機制，違反 Buyee 使用條款的風險高；Render 免費方案 512MB 記憶體也跑不動 Chromium，且單次查詢會超過 15 秒預算。
  - 直接抓 Mercari 官網 `jp.mercari.com/search`：可連線（200），但是前端 JS 渲染，HTML 中沒有商品資料；背後的 `api.mercari.jp` 需要 DPoP 簽章標頭，屬未公開 API，同樣不採用。
  - 付費：評測模式已有 `MercariRapidApiAdapter`，但正式環境不增加費用是既定方向。
- 相關發現（已開 22，不在本票範圍）：日本 Yahoo 拍賣官網 `auctions.yahoo.co.jp/search/search?p=...` 是伺服器端輸出的 HTML，可直接取得（200），價格在 `.Product__priceValue`，不經 Buyee WAF。若要讓日本 Yahoo 拍賣有即時價格，可做直接抓官網、連結仍導向 Buyee 的轉接器；需先確認 robots.txt 與使用條款。Buyee 樂天目前沒被挑戰，但同一個 WAF 隨時可能擴大範圍，`BuyeeRakutenAdapter` 回 `blocked` 時要能從 log 看出來。
- 相關程式：`services/platforms.py`（`BuyeeMercariAdapter`、`_fetch` 的 202/403→`BLOCKED`、`build_platforms`）、`services/comparison.py`（`_with_legacy_card_fields`）、`services/scraper.py`（`DEFAULT_HEADERS`）、`tests/test_platform_adapters.py`。

**Notes（實作後）：**
- 驗證方式：Render 免費方案沒有 Shell，改為查 Render log 中既有的 `[Platform]` 行（見第一項）；部署前做才看得到。log 只能證明狀態碼，看不到 `x-amzn-waf-action` 標頭；標頭證據來自本機重測。Render log 結果（2026-09-27 UTC，部署版 c945a56，LINE 查詢兩次）：
  ```
  2026-09-27 05:05:23,454 [INFO] line_bot.comparison: [Platform] mercari: blocked in 452ms (0 listings) HTTP 202
  2026-09-27 05:05:23,454 [INFO] line_bot.comparison: [Platform] rakuten: ok in 1723ms (20 listings)
  2026-09-27 05:16:39,559 [INFO] line_bot.comparison: [Platform] mercari: blocked in 404ms (0 listings) HTTP 202
  2026-09-27 05:16:39,559 [INFO] line_bot.comparison: [Platform] rakuten: ok in 1788ms (20 listings)
  ```
  結論：Render 機房 IP 與本機相同，Buyee Mercari 回 HTTP 202（WAF 挑戰）、Buyee 樂天正常 20 筆，確認正式環境的 Mercari 從未取得價格。
- 本機重測（2026-09-27，台灣住宅網路，Chrome 140 UA）：`/mercari/search?keyword=test` → `HTTP/2 202`、`server: awselb/2.0`、`x-amzn-waf-action: challenge`；`/rakuten/shopping/search/category/0?query=test` → `HTTP/2 200`、`server: Apache`。
- `build_platforms(evaluation_mode=False)` 的 `mercari` 改為 `adapter=None`；搜尋連結 `_mercari_link`（Buyee、聯盟參數）不變，評測模式仍為 `MercariRapidApiAdapter`。
- 正式模式下 Mercari 狀態為 `link_only`，卡片按鈕顯示「Mercari (點擊查看)」，與日本 Yahoo 拍賣相同，不是失敗（`_platform_button` 不看狀態，所以失敗時的文字其實也一樣；測試以狀態 `link_only` 驗證）。
- `_with_legacy_card_fields` 只在 Mercari 有 `OK` 結果時呼叫；無轉接器時 `fetched` 沒有 mercari，直接略過，卡片走一般平台按鈕版本。測試 `test_mercari_is_link_only_in_production_without_any_request` 以真實平台集合（記錄所有 HTTP 請求）確認不會請求 `/mercari/`，且 `scraper_result`／`pricing` 為 None。
- 影響：正式環境原本的「完整卡片」（Mercari 中位數落地價、代表圖）只在 Mercari 有結果時出現，實際上自 04 起就不曾出現；本票後正式環境不會再走這條路徑，只有評測模式可能出現。
- 恢復方式：Buyee 解除挑戰後，把 `build_platforms()` 中正式模式的 `mercari_adapter` 改回 `BuyeeMercariAdapter(http=http)`、價格規則 `buyee_mercari_price` 仍保留在原處。
