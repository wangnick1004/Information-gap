# 08: 評測程式與評測集格式

**Parent:** [規格書](../spec.md)

**What to build:** 建立評測程式：讀取評測集檔案，逐筆透過「比價流程入口」（與正式服務同一入口，使用真實轉接器）執行，記錄耗時、各平台狀態、相符判斷，並輸出三項指標：15 秒內回覆比例、平均取得真實價格的平台數、比對正確率（同型號同規格即正確，顏色不計），依類別與文字/圖片分組。同時定義評測集檔案格式並附 3–5 筆範例，供 09 收集時使用。

**Blocked by:** 02

**Status:** done

- [x] 評測集格式已文件化：每筆含輸入（文字或圖片檔）、類別、正確商品名稱與型號規格、至少一個正確商品連結
- [x] 附 3–5 筆範例資料，可直接跑通
- [x] 評測程式呼叫的是比價流程入口，而非另寫一套流程
- [x] 輸出三項指標，並依類別、輸入型態分組
- [x] 每筆明細可輸出成表格檔（CSV）供論文使用
- [x] 提供評測模式開關，可啟用付費 API 做對照實驗
- [x] 比對正確率的判定方式（自動比對＋可人工覆核的欄位）已說明


**Notes（實作後）：**
- 程式在 `evaluation/harness.py`，格式與判定方式寫在 `evaluation/README.md`，範例 `evaluation/sample_set.yaml`（3 筆文字）。`python -m evaluation.harness run SET --out DIR [--evaluation-mode]`；人工覆核 `records.csv` 的 `manual_correct` 後用 `summarize` 重算。
- 評測直接呼叫 `compare_prices`（每筆獨立空快取、依序執行）；`--evaluation-mode` 傳入 `build_platforms(evaluation_mode=True)` 啟用 RapidAPI。
- 自動判定目前只看 AI 理解後的關鍵字是否含全部比對詞（數字邊界區分世代／容量），**不**檢查平台上相符商品；工作票 14 完成後應在明細加入各平台相符商品，並擴充判定。
- 範例沒有圖片輸入（沒有可合法附上的商品照片）；圖片路徑由測試涵蓋，09 收集時會補上。
- 2026-09-26 實跑範例：流程可跑通，但 3 筆都在 7 秒 AI 逾時而降級——Gemini（gemini-3.6-flash）單次約 4.7 秒，超過 parser 單次 3 秒上限。放寬上限後，AI 把「airpods pro 3」理解成「AirPods Pro 2」，自動判定會判錯。
