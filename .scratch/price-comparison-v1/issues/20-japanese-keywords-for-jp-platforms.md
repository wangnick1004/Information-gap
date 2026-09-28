# 20: 日本平台一律使用 AI 產生的日文關鍵字

**Parent:** [規格書](../spec.md)

**What to build:** 修正 LINE bot 以中文自然語言查詢時，日本平台（Mercari、日本 Yahoo 拍賣、日本樂天，皆經 Buyee）拿到的仍是中文關鍵字、因此查不到任何商品的錯誤。Gemini 解析必須為每筆查詢產生真正的日文關鍵字（例如「咒術迴戰」→「呪術廻戦」、「桌球拍」→「卓球ラケット」），日本平台的查詢與搜尋連結都改用這個日文關鍵字；只有 AI 真的沒給出可用日文時才退回，且退回行為要在 log 中看得到。

**Blocked by:** 無（目前 09 依賴本票）

**Status:** done（待手動 LINE 驗證）

- [x] 先重現：以數筆中文自然語言查詢（例如「想買排球少年影山趴娃」「小棕瓶 50ml」「蝴蝶王桌球拍」）走比價入口，記錄 Gemini 原始輸出與 `⚡ [Search Keywords]` log，確認中文是在哪一層（AI 未填日文欄位／填成中文／程式退回邏輯）進入日本平台，寫進 Notes
- [x] 解析結果的日文關鍵字欄位收斂為單一欄位，並在 Gemini 結構化輸出中設為必填（目前 `jp_keyword`、`keyword_jp`、`search_query_ja` 三個欄位皆為選填且預設空字串）
- [x] 日文關鍵字需含假名或為日文慣用寫法；只有品牌／型號英數字的情況（如 `Nintendo Switch 2`）視為合法
- [x] AI 給的日文關鍵字若為空或是繁體中文，不得默默改用中文關鍵字（目前 `services/parser.py` 的 `clean_jp = clean_zh`、`services/comparison.py` `_keywords` 的 `or keyword_zh`）；改為重問一次或採用 `item_type` 等日文欄位，並留 warning log
- [x] 比價入口測試（假 AI）：日本平台收到的是日文關鍵字、台灣平台收到的是中文關鍵字；卡片上日本平台的搜尋連結也是日文
- [x] 解析器測試：AI 回傳日文欄位為中文或空白時的處理符合上述規則
- [x] 快取：修正前以中文關鍵字存入的比價結果不再被回傳（清除或更換快取鍵版本）
- [ ] 手動 LINE 驗證：3 筆中文查詢在日本平台都有查到商品或至少搜尋連結結果非空

**Notes：**
- 發現經過：開發者在 LINE 以中文查詢時，蝦皮等中文平台正常，日本三個平台全部查無結果。
- 相關程式：`services/parser.py`（`ParsedItem`、`SYSTEM_INSTRUCTION`、關鍵字清理與退回）、`services/comparison.py`（`_keywords`、依 `Platform.keyword_lang` 分派關鍵字）、`services/platforms.py`（日本平台 `keyword_lang="ja"`）。
- 範圍外：AI 故障時的降級卡片（07）沒有 AI 可翻譯，日本平台仍用原始輸入；是否另找免費翻譯來源另開票討論。圖片輸入的各語言關鍵字由 19 處理，但本票的欄位收斂與不退回中文規則同樣適用於圖片。
- 為何擋住 09：09 的評測集要量測日本平台的查得率與價格，若日本平台一直拿中文關鍵字，收集與標註的結果沒有意義。

**Notes（實作後）：**
- 重現結果（2026-09-27，gemini-3.8-flash）：舊的輸出格式裡 `jp_keyword`、`keyword_jp`、`search_query_ja`、`zh_keyword`、`keyword_zh` 全為選填，「咒術迴戰 五條悟 公仔」有時整組回 null、只填 `perfected_keyword`；解析器接著把使用者原文同時當中文與日文關鍵字，比價入口也把空的日文退回中文，日本平台因此拿到中文。同一查詢另一次則正常，屬於不穩定。
- Gemini 改用獨立的輸出格式 `GeminiOutput`（`services/parser.py`）：`zh_keyword`、`jp_keyword`、`perfected_keyword`、`reasoning`、`category` 必填，沒有重複欄位。改版後 10 筆中文查詢（含上述問題查詢 3 次）日文關鍵字全部正確。
- 內部的 `ParsedItem` 暫時保留 `keyword_jp`／`search_query_ja` 等舊欄位名（約 200 處引用，測試與卡片共用），解析器輸出時兩者一致；整併另行處理。
- `is_japanese_keyword`：含假名，或無漢字（品牌／型號）即合法；只有漢字時必須與中文關鍵字、完整商品名、使用者原文都不同（例如「呪術廻戦 五条悟」可，照抄中文不可）。無法辨識「和中文不同、但仍是中文」的漢字詞，屬已知限制。
- 日文不合格時：以 `JAPANESE_KEYWORD_PROMPT` 單獨再問一次（沿用單次 AI 呼叫上限），仍不行改用日文的 `item_type`，最後才退回中文；每一步都有 `[Japanese Keyword]` warning log。補問的錯誤全部吞掉，不觸發整體重試。
- 比價入口 `_keywords` 不再默默以中文代替日文，會留 `[Search Keywords] no Japanese keyword` warning。
- 實際查詢：日本樂天（Buyee）以「呪術廻戦 五条悟 フィギュア」查到 20 筆、以「咒術迴戰 五條悟 公仔」0 筆；「ハイキュー 影山飛雄 もちもちマスコット」7 筆、中文 0 筆。
- 快取：比價快取為行程內記憶體（`services/cache.py`），重新部署即清空，不需換快取鍵。
- 另發現：Buyee Mercari 轉接器在本機不論中日文關鍵字都回 `blocked`，與本票無關，需另開票確認 Render 上是否也被擋。
- 另發現：本機實測 Gemini 單次回應約 2.3～4.2 秒，06 設定的單次 3 秒上限偶爾會逾時重試；本票未更動時間設定。

