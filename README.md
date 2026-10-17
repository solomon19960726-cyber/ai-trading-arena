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
