# 06: 15 秒截止、依價格排序、標示價格時間

**Parent:** [規格書](../spec.md)

**What to build:** 整體回覆硬上限 15 秒（自收到訊息起算）：在上限內盡量等 6 個平台都回來，時間一到就以已取得的結果組成卡片，未完成的平台標記為「僅連結」。Gemini 解析單次上限約 3 秒，重試次數與等待計入總預算。卡片上平台依價格由低到高排序（無價格者排在後面），並顯示價格取得時間；快取命中時顯示的是原始取得時間。

**Blocked by:** 05

**Status:** done（待手動 LINE 驗證）

- [x] 任何輸入在 15 秒內一定得到回覆（以假時鐘＋假慢平台測試）
- [x] 截止時已完成的平台顯示價格，未完成者顯示「僅連結」並記錄為逾時
- [x] Gemini 解析有明確的時間上限，重試不會讓總時間超過預算
- [x] 平台依相符最低價排序；分潤設定不影響排序（有測試）
- [x] 卡片顯示價格取得時間；快取命中顯示原始時間，快取 1 小時後過期（假時鐘測試）
- [x] 日誌記錄每個平台耗時與狀態、AI 解析耗時、是否快取命中


**Notes（實作後）：**
- 時鐘：新增 `services/clock.py`（`Clock`：`now()`／`monotonic()`／`sleep()`；正式用 `system_clock`）。測試用 `tests/fakes.py` 的 `FakeClock`（虛擬時間，不真的等待）、`SlowAdapter`、`SlowParser`。`TTLCache` 可注入時間來源（`now=`）。
- 時間預算（`services/comparison.py`）：`DEADLINE_SECONDS=15`，自 webhook 收到訊息起算（`received_at` 由 `line_webhook` 經 `handle_line_events` → `handle_line_event` 傳入 `compare_prices`；圖片下載時間也計入）。保留 `REPLY_MARGIN_SECONDS=1` 給組卡片與送出回覆，平台查詢最晚在第 14 秒截止；未完成者狀態為 `TIMEOUT`（卡片為「前往 X（點擊查看）」連結）。所有平台提早完成就不等到截止。
- AI 解析：`AI_PARSE_BUDGET_SECONDS=7`（也不超過整體截止），超過拋 `AiTimeoutError`（屬 `GeminiServerError`，沿用「AI 大塞車」回覆）。`parse_fb_post` 每次呼叫上限 `attempt_timeout_seconds=3`，逾時視為暫時性錯誤重試；預設改為最多 2 次、間隔 1 秒（3＋1＋3＝7 秒，有測試確保預設值在預算內）。
- 排序：比價結果依相符最低價由低到高，無價格者依對照表順序在後；只看價格，分潤不影響（有測試）。
- 卡片：每張卡頁尾顯示「價格更新：YYYY/MM/DD HH:MM」（台灣時間）；快取命中顯示原始取得時間。
- 快取：有平台逾時的結果不快取（避免一次慢查詢讓之後一小時都只有連結）。
- 日誌：`[AI Parse]` 耗時與結果、`[Platform]` 各平台耗時與狀態（含截止時未完成）、`[Cache Hit]`（含原始取得時間）、`[Comparison]` 自收到訊息起的總耗時與各平台狀態。

- Code review 後未處理、留待後續：
  - 卡片仍依地區分「日本平台」「其他」兩張卡，只在各卡內依價格排序；跨卡的整體排序待 14 新卡片時列為驗收條件。
  - AI 解析逾時目前只回文字訊息，沒有附搜尋連結；屬 07（AI 降級回覆）。
  - 平台逾時有兩層（轉接器內 `asyncio.wait_for` 用真實時間、流程截止用注入時鐘），正式環境同時觸發，逾時 detail 文字不固定；不影響狀態。
  - 全域 `search_cache` 用 `time.time`、流程用 `system_clock.monotonic`，兩者未統一為同一個 `Clock`。
  - `FakeClock` 以「讓出 50 次」判斷其他工作已完成再推進時間，對假轉接器足夠，但不適用含真實 I/O 的測試。
  - 評測（08）需要的「15 秒內回覆比例」目前只能從日誌取得，尚無結構化欄位。
