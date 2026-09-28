# 05: Gemini 判斷類別，再由對照表選 6 個平台

**Parent:** [規格書](../spec.md)

**What to build:** 所有文字與圖片輸入一律經 Gemini 解析（移除純英數型號略過 LLM 的捷徑），使縮寫與慣用語能被展開（例如「AJ1」→ Air Jordan 1）。Gemini 輸出新增商品類別（3C 家電、美妝保養、服飾鞋包、動漫周邊/玩具、運動戶外、其他）。比價流程依靜態對照表為該類別選出 6 個平台查詢；尚未有轉接器的平台先以「僅搜尋連結」呈現。解析提示詞移除動漫專用假設，改為通用商品。預熱清單改為通用熱門商品。

**Blocked by:** 04

**Status:** done（待手動 LINE 驗證）

- [x] 不存在略過 Gemini 的解析路徑；純型號與縮寫輸入也經過 Gemini
- [x] 解析結果包含六類之一的類別欄位；無法判斷時為「其他」
- [x] 類別→平台對照表為單一、可人工編輯的靜態設定，內容與規格書一致
- [x] 卡片永遠顯示該類別的 6 個平台；無轉接器者顯示搜尋連結
- [x] 平台選擇不由 LLM 自由決定
- [x] 入口測試：六個類別各自選出正確的 6 個平台；「其他」使用預設組
- [x] 解析器測試：縮寫輸入（如 AJ1、switch2）得到展開後的商品名
- [x] 預熱清單為通用商品


**Notes（實作後）：**
- 對照表在 `services/categories.py`（`Category` 列舉＋`CATEGORY_PLATFORMS`）；這是唯一決定查哪 6 個平台的地方。`build_platforms()` 改為回傳候選平台池全部 9 個平台，比價流程入口依 AI 判斷的類別從中選出 6 個；未選中的平台不查詢。比價結果新增 `category` 欄位。
- 新增 PChome、momo、露天三個「僅連結」平台（搜尋連結在 `services/search_links.py`，尚無分潤參數）；轉接器屬 10／11／12。規格書的「Yahoo 購物」沿用既有的 `yahoo_tw`（tw.buy.yahoo.com），卡片上仍顯示「台灣 Yahoo」。
- 解析器：移除 `fast_regex_parse` 與 `CUSTOM_KEYWORDS` 字典，所有輸入一律經 Gemini。`ParsedItem.category` 限定六類（Gemini 回應 schema 為 enum），缺少或無法辨識時為「其他」。系統提示詞與圖片提示詞改為通用商品，範例改為 AJ1、switch2、小棕瓶等；動漫仍保留為六類之一。`is_anime_merch` 欄位名稱未改（語意已是「是商品」），另開票再清。
- 卡片：平台按鈕改由比價結果產生（名稱、價格、連結），日本平台（Mercari、日本雅虎、日本樂天）放第一張卡、其餘放第二張卡，沒有平台的卡片不顯示（例如「其他」只剩一張卡）。PChome 顯示為「PChome」（「24h」會被當成價格數字）。第二張卡原本寫死的「支援台灣蝦皮、淘寶與台灣 Yahoo」提示改為通用文字。卡片不再呼叫 `inject_shopee_button_to_flex`（按鈕標籤已由比價結果決定）。
- 預熱：清單改為通用熱門商品（Switch 2、PS5、AirPods Pro、Dyson 吹風機、AJ1、小棕瓶）；快取條件由「Mercari 完整卡」改為「至少一個平台查到價格」，否則 3C 等不含 Mercari 的類別永遠不會被預熱。
- 使用者看得到的差異：
  1. 平台組合依類別改變；3C／美妝／運動／其他不再出現 Mercari 與日本雅虎，也就不會出現 Mercari 完整「比價分析」卡。
  2. 以前略過 AI 的輸入（如「Switch 2」「PS5」「蝴蝶王」）現在都會呼叫 Gemini，回覆會慢約一次 AI 呼叫；Gemini 故障時這些輸入也會走 AI 降級回覆（07）。
- 既有測試中依賴 Mercari 版面的假 AI 結果，補上「動漫周邊/玩具」類別；Sony 耳機、底片相機等改為「3C 家電」並依新平台組合更新斷言。

- Code review 後未處理、留待後續：
  - `is_anime_merch` 欄位與 `ParsedAnimeItem` 別名改名（規格「移除動漫欄位語意」只完成提示詞部分）。
  - 六個類別名稱在 `Category`、欄位說明與兩段提示詞中各寫一次，可改為由 `Category` 產生。
  - 卡片按日本／其他分兩張卡（`JAPANESE_PLATFORMS` 與 `Platform.keyword_lang` 重複），並依賴舊卡片固定兩張的結構；14 換新卡片時一併處理。
  - 縮寫展開的解析器測試只能驗證「經過 Gemini、採用 Gemini 的展開結果」；實際展開品質由評測（08／16）量測。
