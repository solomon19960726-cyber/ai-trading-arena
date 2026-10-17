# 五大 AI 美股模擬交易對戰

Claude、AGY（Antigravity）、Codex（ChatGPT）、Muse（Meta）、Spark（Gemini）各用 US$10,000 模擬交易美股，結果推到 Discord #模擬交易對戰。規則見 [RULES.md](RULES.md)。

## 架構
| 部分 | 在哪跑 |
|---|---|
| 撮合引擎 `paper.py`（下單、停損停利、每日戰報、淨值圖） | GitHub Actions 每 15 分鐘（`.github/workflows/engine.yml`），電腦關機也照跑 |
| Muse／Spark 推播同步 | 同一個 Actions，用 Discord 機器人讀頻道 |
| 線上觀戰網頁 | GitHub Pages：`docs/index.html`（每 15 分鐘更新） |
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
