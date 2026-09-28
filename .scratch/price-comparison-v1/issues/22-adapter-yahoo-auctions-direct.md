# 22: 日本 Yahoo 拍賣轉接器（直接查官網，連結導向 Buyee）

**Parent:** [規格書](../spec.md)

**What to build:** 讓對照表中包含日本 Yahoo 拍賣（`yahoo_jp`）的類別，卡片上出現真實價格。Buyee 的 Yahoo 拍賣搜尋頁被 AWS WAF 挑戰擋下（見 21），因此改為直接查 Yahoo 拍賣官網的搜尋頁取得商品與價格，但使用者點的商品連結與搜尋連結仍導向 Buyee（台灣使用者要經代購才能下標，聯盟參數也在 Buyee）。實作前先由人確認 Yahoo! JAPAN 的使用條款是否允許這種查詢；不允許就維持只給搜尋連結，並把結論記在本票。不增加任何費用。

**Blocked by:** 無（與 21 互相獨立）

**Status:** ready-for-human（先確認使用條款，確認可行後改為 ready-for-agent）

- [ ] 人工：確認 Yahoo! JAPAN 利用規約／Yahoo!オークション ガイドライン對自動取得搜尋結果的限制，結論與條文出處記在 Notes；不允許則本票改為 wontfix
- [ ] `YahooAuctionsAdapter` 實作統一介面：請求 `https://auctions.yahoo.co.jp/search/search?p=<日文關鍵字>`，只帶 `p`（見 Notes 的 robots.txt 限制），沿用 `DEFAULT_HEADERS`，須接受 gzip
- [ ] 解析每張 `li.Product`：標題、價格（日圓）、商品編號、縮圖；商品連結改寫為 Buyee 的 Yahoo 拍賣商品頁（`https://buyee.jp/item/yahoo/auction/<商品編號>`，附聯盟參數）
- [ ] 以錄下的真實回應（刪減到前幾筆）驗證解析，另錄一份無結果頁
- [ ] 被擋／格式異常／無結果時回傳對應失敗狀態（沿用 `_fetch`）
- [ ] 註冊到 `build_platforms()` 的 `yahoo_jp`，搜尋連結維持 Buyee；價格規則先用 `lowest_price` 或比照 Buyee 樂天，理由寫在 Notes
- [ ] 以 3 筆日文關鍵字實測，單一平台查詢在正常網路下於截止時間（15 秒）內完成，實測秒數寫進 Notes

**Notes：**
- 來源：21 調查時發現 Buyee 的 `/item/search/query/...`（Yahoo 拍賣搜尋）與 Mercari 一樣回 `x-amzn-waf-action: challenge`；Yahoo 拍賣官網則直接回 200。
- 實測（2026-09-27，本機、Chrome 140 User-Agent）：「呪術廻戦 五条悟 フィギュア」回 200，約 52 張 `li.Product`；伺服器端輸出的 HTML，不需要執行 JS。
- 頁面結構：每張卡片的 `a.Product__imageLink` 帶 `data-auction-id`（如 `x1245425700`）、`data-auction-title`、`data-auction-price`（目前價，日圓整數）、`data-auction-img`，`href` 為 `https://auctions.yahoo.co.jp/jp/auction/<id>`。用這些 data 屬性解析比抓顯示文字穩定。另有 `.Product__priceValue`，部分商品有兩個價格（目前價／即決價）。
- 價格語意：拍賣的「目前價」不是成交價，常見 1 円起標，拿來當最低價會嚴重低估。價格規則要處理這點（例如排除明顯的起標價、改用中位數，或有即決價時優先用即決價），實作時決定並寫明理由。
- 回應大小與時間：未壓縮約 800KB，完整下載實測 8～18 秒，會超過 15 秒截止；壓縮後（gzip）約 80KB、約 2 秒。aiohttp 預設會帶 `Accept-Encoding: gzip` 並自動解壓，實作時要確認真的有壓縮。
- robots.txt（2026-09-27）：`/search/search?p=...` 允許；但 `/search/*?*n=`、`s1=`、`o1=`、`mode=`、`type=`、`new=`、`shipping=` 等參數被禁止，所以不能用 `s1`／`o1` 請伺服器依價格排序，也不能用 `n` 改每頁筆數，排序改在本機做。`/closedsearch`（成交紀錄）大多禁止，本票不使用。
- 範圍外：Mercari 沒有類似的伺服器端 HTML 來源（官網為 JS 渲染、API 需簽章），不在本票處理，見 21。
- 相關程式：`services/platforms.py`（`_fetch`、`BuyeeRakutenAdapter` 可參考、`build_platforms` 的 `yahoo_jp`）、`services/search_links.py`（`build_buyee_yahoo_search_url`、`append_affiliate_id`）、`services/categories.py`、`tests/test_platform_adapters.py`。
