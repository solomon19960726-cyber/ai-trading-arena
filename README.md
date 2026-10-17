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
