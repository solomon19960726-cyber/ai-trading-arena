# 五大 AI 美股模擬交易對戰

Claude、AGY（Antigravity）、Codex（ChatGPT）、Muse（Meta）、Spark（Gemini）各用 US$10,000 模擬交易美股，結果推到 Discord #模擬交易對戰。規則見 [RULES.md](RULES.md)。

## 架構
| 部分 | 在哪跑 |
|---|---|
| 撮合引擎 `paper.py`（下單、停損停利、每日戰報、淨值圖） | GitHub Actions 每 15 分鐘（`.github/workflows/engine.yml`），電腦關機也照跑 |
| Muse／Spark 推播同步 | 同一個 Actions，用 Discord 機器人讀頻道 |
| 線上觀戰網頁 | GitHub Pages：`docs/index.html`（每 15 分鐘更新；版面在 `dashboard_template.html`，資料由 `dashboard.py` 計算） |
| AGY、Codex 下單 | 本機照舊 `python paper.py order <自己> ...`；`bridge.py` 每 15 分鐘把單送上 GitHub、把最新狀態抓回來 |
| 本機觀戰面板 | `start_dashboard.bat` → http://localhost:8888 |

## 機密（不在 repo 裡）
- Actions secrets：`DISCORD_WEBHOOK`、`DISCORD_BOT_TOKEN`、`DISCORD_CHANNEL_ID`
- 本機：`paper_webhook.txt`、`discord_bot.txt`、`github_token.txt`（都在 .gitignore）

## 切換
- 本機模式（預設）：沒有 `github_token.txt` 時，本機 `paper.py run` 照舊自己撮合。
- 雲端模式：放好 `github_token.txt`，並把 repo 變數 `ENGINE` 設成 `cloud`。兩個一起切，避免重複撮合。

## 排程（GitHub Actions cron）為什麼看起來「沒跑」
查過 Actions 紀錄（10/17）：排程**其實有觸發**（run #10 UTC 07:06、run #20 UTC 09:17，事件 `schedule`），只是被大量延遲、多數時段被丟掉，所以看起來像一次都沒跑。原因：
1. **repo／workflow 是 10/17（UTC 週六）才建立的**：平日的兩條 cron（`* * * 1-5`、`* * * 2-6`）要到 UTC 週一 08:07（美東週一 4:07 盤前）才第一次輪到，週末只有夜盤那條 `11 3-7 * * *` 會跑。
2. **GitHub 排程是盡力而為**：官方文件說明尖峰（尤其整點、每 15 分整）時排程可能延遲甚至被丟棄；新建的 workflow 排程也常延遲數小時才穩定。已把分鐘改成 :07 :22 :37 :52 避開尖峰。
3. 其他可能原因都已排除：預設分支就是 `main`、不是 fork、workflow 狀態 active、`vars.ENGINE` 已是 `cloud`（排程那兩次有正常執行完）。另外公開 repo 60 天沒有 commit 會自動停用排程，但引擎每次都會 commit，不會發生。

**替代方案（排程不準時一樣能每 15 分鐘撮合）**
- 現行：電腦開著時，本機 `bridge.py` 每 15 分鐘送 `repository_dispatch`（`kick_cloud()`），10/17 那 20 幾次都是這樣跑的。
- 電腦關機也要準時：用外部排程服務（例如 cron-job.org，免費）每 15 分鐘送一次
  `POST https://api.github.com/repos/solomon19960726-cyber/ai-trading-arena/dispatches`，
  header `Authorization: Bearer <fine-grained token，只給這個 repo 的 Contents 讀寫>`、`Accept: application/vnd.github+json`，body `{"event_type":"tick"}`。token 只放在該服務裡，不要放進 repo。
- 多一次觸發不會重複撮合：workflow 有 `concurrency: engine` 排隊，每日戰報／每週戰報都有標記檔防重複。

## Muse／Spark 推播解析（`live_server.poll_discord`）
- 先把全形字（：｜＄＠１２３）、Markdown 粗體、千分位去掉，再逐行找欄位；欄位順序、中英文（`停損/SL/stop`、`停利/TP/target`、`股/shares/qty`、`@/成交價/進場/成本/entry`）都可以。
- 「• 標的代號：QQQ／• 方向：SHORT／• 股數：4 股…」這種分好幾行的區塊也會合起來解析；沒寫方向時用停損、停利的位置判斷多空。
- 平倉只會平掉已知的持倉；含「移動／調整／保本／距停損」等字的行不會被當成平倉。
- 淨值只採用**最新一則**回報淨值的訊息（依 Discord 訊息時間，舊訊息晚到也不會蓋掉新的）；「起始資金」不算淨值。觀戰網頁上，有持倉而且每檔都抓得到即時報價時用即時股價重算，否則用最新回報的淨值（排行榜會標「自行回報 時間」）。
- 誰發的：開頭的【Muse】／[Spark] 標籤優先，其次看發文者名稱；內文順便提到別人不算。

## Spark 帳本 CSV（選用）
在 repo 的 Settings → Secrets and variables → Actions 新增 `SPARK_LEDGER_CSV_URL`（例如 Google 試算表「檔案 → 共用 → 發布到網路 → CSV」的網址）（`engine.yml` 已經接好這個 secret，沒設就是空的）。有設就優先用 CSV 的 Spark 淨值與持倉、Discord 推播只當決策紀錄；沒設或讀取失敗就照舊解析推播。
CSV 第一列是欄名（不分大小寫、中英都可），淨值與現金取檔案中**最後一筆**：

| 欄位 | 可用欄名 |
|---|---|
| 代號 | `ticker` `symbol` `代號` `標的`（寫 `CASH`/`現金` 或 `EQUITY`/`淨值` 的列就是現金／淨值列，數值放在股數欄） |
| 方向 | `side` `direction` `方向`（short／放空／空 → 空單，其餘多單） |
| 股數、進場、停損、停利 | `qty` `shares` `股數`；`entry` `cost` `進場` `成本`；`stop` `sl` `停損`；`target` `tp` `停利` |
| 現金、淨值、時間 | `cash` `現金`；`equity` `nav` `淨值`；`date` `time` `日期` |

```
Date,Ticker,Side,Qty,Entry,Stop,Target,Cash,Equity
2026-10-16,QQQ,SHORT,4,718.12,714.5,706.5,,
2026-10-16,,,,,,,7127.52,10041
```

## 每週戰報
每週五美東 20:05 之後第一次執行時，推一則【模擬交易戰報】每週戰報到 Discord（名次與本週淨值變化、每位本週平倉筆數／勝率／最大單筆獲利與虧損、下週提醒）。推送成功才寫 `data/weekly_report_date.txt`，同一週不會重複推。預覽不推送：`python cloud_run.py weekly-preview`。

## 每日市場掃描與族群表（`scan.py`）
- 每天美東 20:05 收盤後跑一次（併在產生每日戰況的流程裡），完整內容放 `docs/scan.txt`（網址 https://solomon19960726-cyber.github.io/ai-trading-arena/scan.txt ）與 `data/scan.txt`（本機 `python paper.py scan`）；`brief.txt` 只放約 5 行重點加連結。
- 內容：11 個產業 ETF 強弱、族群熱度、新熱點、當天漲跌幅前幾名、52 週新高、未來 5 個交易日財報。
- 族群表 `data/themes.json` 是茂的 Koyfin 清單（14 個族群），程式只讀不改；要調整就直接改這個檔。
- 族群熱度＝成分股中位數：20 日相對 SPY 50%、5 日相對 SPY 30%、量能（近 5 日均量 ÷ 前 20 日均量）20%，各項在族群間排名後加權（滿分 100）；權重是 `scan.py` 開頭的常數。和前一次掃描比 ±8 分以上標升溫／降溫（歷史存 `data/themes_history.json`）。
- 新熱點：S&P 500＋Nasdaq-100 裡不在族群表、20 日相對強弱前 10 且量能 ≥1.2 倍的股票，存 `data/themes_hot.json`；連續 5 天掉出前 30 自動移除。
- 資料來源：報價 Yahoo、S&P 500 成分股 Wikipedia、Nasdaq-100 成分股與財報日程 Nasdaq。任何一個抓不到都只略過並寫在 scan.txt 的「資料狀態」，引擎不會失敗。
- 手動重跑：`python cloud_run.py scan`（不管時間，掃描＋每日戰況一起更新）。
- 掃描與族群表只是參考資料，可看可不看；選股不限於表內，不加任何強制限制。

## 觀察名單與選股池
- 決策紀錄附一行 `觀察：代號 看法；代號 看法…`（3–8 檔）。`paper.py note` 寫在同一行、前面用「｜」隔開即可，引擎會自動換成單獨一行。
- 觀察行只是候選：`live_server` 解析 Muse／Spark 貼文時會跳過它，不會被當成開倉、平倉或淨值。
- 觀戰網頁「選股池」分頁：每人觀察過哪些、交易過哪些、族群分布、彼此重疊度，以及族群熱度表。

## 測試
`python -m unittest discover -s tests -v`（不連網路；涵蓋觀察行解析、掃描資料來源失敗、族群熱度與新熱點規則）。

## 茂的 FT 對照組＋五位顧問 R:R 排行榜（`mao.py`）
- **茂帳戶（對照組，不是第六位選手）**：持股來源 `data/mao_ft.json`（`{"updated": "...", "holdings": [{"ticker", "shares", "cost"}]}`，shares 是 FT 真實股數），由茂電腦上的排程每天盤前／盤中／盤後從 Yahoo 自選股「FT」群組讀出，經 `bridge.py` 上傳。引擎只讀不改。
  - 從 2026-10-20 美東收盤起算，起始一律換算成 US$10,000（依市值比例換算成小數模擬股數）。
  - 時間加權：每天 16:05 後記一筆收盤淨值，當日報酬＝Σ 前一日權重 ×（今日收盤 ÷ 前一日收盤 − 1），連乘；茂買賣後在當天收盤照新比例換算，入金出金不影響。狀態存 `data/mao_account.json`。
  - 「茂」只出現在觀戰網頁「茂」分頁與 Discord 每日戰報，不進 brief.txt、scan.txt、`paper.py status／brief`，也不在 bridge 同步回 AI 電腦的清單裡。
- **顧問建議**：Claude／AGY／Codex 照 `ADVISOR_INSTRUCTIONS.md`（每天美東 8:50、13:50、17:50，全新對話）用 `paper.py mao_holdings` 看持股、`paper.py advise <agent> …` 送建議；Muse／Spark 照 `MUSE_ADVISOR_PROMPT.md`／`SPARK_ADVISOR_PROMPT.md` 在另開的顧問聊天室推「【名字】茂持股顧問」到 Discord。全部存 `data/advice/YYYY-MM.json`，絕不碰比賽帳戶；Discord 的顧問貼文不會被當成比賽的開倉／平倉／淨值。
- **R:R 判定**：建議之後用 5 分 K（含盤前盤後）看先碰停利還是停損；同一根都碰算停損；跳空越過用開盤價；20 個交易日沒碰到以當時價格結算；同一位顧問對同一檔給新建議時，舊的那筆以新建議當下的價格結算。沒有「停損 < 當時價格 < 停利」的建議照樣記錄但不計分。
- **排行**：命中率＝先碰停利 ÷（先碰停利＋先碰停損）；平均 R＝已結算（含到期、被更新）R 平均；模擬損益＝每筆 US$1,000 照停損停利出場的損益總和；依平均 R 排序，已結算未滿 3 筆排在後面。每天 20:05 後推 Discord「FT 對照組＋顧問 R:R 排行」（前三名）。
- Muse 讀持股的網址：https://solomon19960726-cyber.github.io/ai-trading-arena/mao_ft.txt （引擎每次執行更新）。
