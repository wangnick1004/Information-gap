# 評測程式與評測集格式

評測程式（`evaluation/harness.py`）逐筆把評測集送進**比價流程入口** `services.comparison.compare_prices`
——與 LINE 機器人正式服務同一個入口、同一組真實平台轉接器——記錄每筆的耗時、各平台狀態與相符判斷，
再算出三項指標，並依類別、輸入型態（文字／圖片）分組。

## 執行

```bash
# 跑評測集（使用 .env 的 GEMINI_API_KEY，平台與正式服務相同，不啟用付費 API）
python -m evaluation.harness run evaluation/sample_set.yaml --out evaluation/results/sample

# 對照實驗：啟用付費第三方 API（RapidAPI）轉接器，需設定 RAPIDAPI_KEY / RAPIDAPI_KEY_SHOPEE
python -m evaluation.harness run evaluation/eval_set.yaml --out evaluation/results/paid --evaluation-mode

# 人工覆核 records.csv 後重算指標
python -m evaluation.harness summarize evaluation/results/sample/records.csv
```

輸出：

- `records.csv`：每筆明細一列（UTF-8 BOM，Excel 可直接開），供論文使用。
- `summary.csv`：三項指標，`group_by` 為 `overall`（整體）、`category`（人工標註類別）、`input_type`（text / image）。

每筆各自使用空的快取，依序執行（不並行），確保每筆都跑完整流程、耗時不互相干擾。

## 評測集格式（YAML）

一個 YAML 清單，每筆一個物件：

```yaml
- id: fb-001                      # 必填，全檔唯一
  input:                          # 必填，text 與 image 恰好擇一
    text: 收 sony xm5 耳機 預算 8000
    # image: images/fb-001.jpg    # 圖片路徑相對於評測集檔所在資料夾
  category: 3C 家電               # 必填：3C 家電、美妝保養、服飾鞋包、動漫周邊/玩具、運動戶外、其他
  expected:                       # 人工標註的正確答案
    name: Sony WH-1000XM5 無線降噪耳機   # 必填，正確商品名稱
    model: WH-1000XM5                     # 必填，型號規格（同型號同規格即正確，顏色不計）
    match_terms: [WH-1000XM5]             # 選填，自動比對用的詞；省略時用 model
    urls:                                 # 必填，至少一個正確商品連結
      - https://...
  source:                         # 選填（工作票 09 要求記錄）：原始來源平台與日期，不含個人資料
    platform: PTT
    date: 2026-09-20
```

格式錯誤時評測程式會在開始前停下，並指出是哪一筆（id）、哪個欄位。

## 三項指標

| 指標 | 定義 |
|---|---|
| 15 秒內回覆比例 | 耗時 ≤ 15 秒的筆數 ÷ 總筆數。耗時自呼叫比價流程入口起算（正式服務自收到訊息起算），含 AI 解析與平台查詢。 |
| 平均取得真實價格的平台數 | 每筆狀態為 `ok`（有相符最低價）的平台數之平均；逾時、查無相符、失敗、僅連結都不算。 |
| 比對正確率 | 判定為正確的筆數 ÷ 總筆數；人工覆核（`manual_correct`）優先於自動判定（`auto_correct`）。 |

比價流程拋出例外（例如圖片輸入遇到 AI 故障）時，該筆仍計入：`error` 欄記錄例外類別，耗時照算，
取得價格平台數為 0，比對判定為不正確。

## 比對正確的判定方式

**自動判定（`auto_correct`）**：AI 理解後的搜尋關鍵字（中文 `keyword_zh` 或日文 `keyword_jp`）包含
`match_terms` 的**每一個**詞即為正確。比對時忽略大小寫、全半形、空白與標點（`WH-1000XM5` = `wh1000xm5`）；比對詞後面緊接數字時不算吻合，
以區分世代與容量（`Switch` 不吻合 `Switch 2`、`230` 不吻合 `2300ml`）。

- 顏色不計：不要把顏色放進 `match_terms`。
- 規格要計：容量、尺寸、世代要分辨時，把它們列為比對詞（例如 `[SK-II, 青春露, 230]`）。
- 字母後綴不會自動區分（`iPhone 17 Pro` 會吻合 `iPhone 17 Pro Max`），這類情況靠人工覆核。
- AI 故障（降級為連結卡片）或 AI 判定與購物無關時，沒有 AI 理解結果，一律判為不正確。

自動判定只看 AI 是否把輸入理解成正確的商品，**不**檢查各平台搜到的商品是否相符。
相符性過濾（工作票 14）完成後，平台上相符商品的正確與否需靠人工覆核，或擴充判定方式。

**人工覆核（`manual_correct`）**：在 `records.csv` 的 `manual_correct` 欄填 `1`／`0`（也接受 `是`／`否`），
留空表示沿用自動判定；再以 `summarize` 重算指標。建議至少覆核所有 `auto_correct = 0` 的筆數，
以及抽查部分 `auto_correct = 1`（對照 `keyword_zh`、各平台價格與 `expected_urls`）。

## records.csv 欄位

| 欄位 | 說明 |
|---|---|
| `case_id`、`input_type`、`category`、`query` | 評測集的 id、`text`/`image`、人工標註類別、文字內容或圖片路徑 |
| `expected_name`、`expected_model`、`match_terms`、`expected_urls`、`source` | 人工標註（多值以 ` \| ` 分隔） |
| `ai_category`、`keyword_zh`、`keyword_jp` | AI 判斷的類別與搜尋關鍵字 |
| `elapsed_seconds`、`within_deadline` | 耗時（秒）與是否 ≤ 15 秒 |
| `price_platforms` | 取得真實價格的平台數 |
| `ai_unavailable`、`from_cache`、`error` | 是否降級為連結卡片、是否命中快取（評測應為 0）、例外類別 |
| `auto_correct`、`manual_correct` | 自動判定、人工覆核 |
| `<平台>_status`、`<平台>_price_twd` | 候選平台池每個平台的狀態（`ok`、`no_match`、`timeout`、`failed`、`link_only`）與相符最低價；未被該類別選中的平台留空 |
