# 台股波段選股（GitHub 自動執行版）

每個交易日 18:00（台灣時間），GitHub 會自動執行選股程式：

1. 從臺灣證券交易所、櫃買中心抓當天的股價、本益比、法人買賣超與財報資料
2. 依固定規則篩選（價值低估＋均值回歸＋法人買超）
3. 追蹤過去每一檔推薦的停利（+10%）、停損（-5%）、時間到（20 個交易日）結果
4. 產生一個手機也能看的網頁：`https://<你的帳號>.github.io/<repo 名稱>/`

## 第一次設定（約 10 分鐘，用電腦操作）

1. **註冊 GitHub**：到 https://github.com 註冊免費帳號。
2. **建立 repo**：右上角「+」→「New repository」
   - Repository name：例如 `tw-swing-screener`
   - 選 **Public**（免費方案的 GitHub Pages 只支援公開 repo，網頁只有篩選結果，沒有個人資料）
   - 按「Create repository」
3. **上傳程式**：在新 repo 頁面點「uploading an existing file」，把這個資料夾裡的檔案與資料夾拖進去（包含 `.github` 資料夾），按「Commit changes」。
   - **`data` 資料夾不用上傳**（檔案太多，網頁一次最多 100 個），第一次執行時程式會自己抓。
   - 如果 `.github` 資料夾拖不上去（隱藏資料夾），改用「Add file → Create new file」，檔名輸入 `.github/workflows/daily.yml`，把內容貼上；`backtest.yml` 也一樣。
4. **開啟寫入權限**：Settings → Actions → General → 最下方「Workflow permissions」選 **Read and write permissions** → Save。
5. **開啟網頁**：Settings → Pages →「Build and deployment」的 Source 選 **Deploy from a branch**，Branch 選 `main`、資料夾選 `/docs` → Save。
6. **第一次手動執行**：Actions 頁籤 → 左側「每日選股」→ 右邊「Run workflow」。
   第一次要抓約 75 個交易日的資料，大約 15 分鐘；之後每天只抓一天，1～2 分鐘。
7. 跑完後等 1～2 分鐘，打開 `https://<你的帳號>.github.io/<repo 名稱>/`，把它加到手機主畫面就像 App 一樣。

## 跑回測（選用，一次就好）

Actions → 左側「回測」→「Run workflow」→ 輸入年數（預設 2）。
第一次要抓大量歷史資料，2 年約 1.5～2 小時，跑完網頁上會多一個「歷史回測」區塊。

## 調整規則

所有條件都在 `strategy.py` 最上面的 `RULES`，在 GitHub 網頁上點檔案 → 鉛筆圖示就能直接改，例如：

```python
"pe_max": 15,        # 本益比上限
"take_profit": 0.10, # 停利 +10%
"stop_loss": 0.05,   # 停損 -5%
```

## 在自己電腦執行（選用）

```bash
pip install -r requirements.txt
python screener.py
```

產生的網頁在 `docs/index.html`。

## 檔案說明

| 檔案 | 用途 |
|---|---|
| `strategy.py` | 選股規則、技術指標（KD、RSI）、停利停損模擬 |
| `data.py` | 抓證交所／櫃買中心資料並快取在 `data/` |
| `screener.py` | 每日主程式 |
| `backtest.py` | 歷史回測 |
| `report.py` | 產生網頁 |
| `data/picks.csv` | 所有推薦紀錄與追蹤結果（可用 Excel 開） |
| `.github/workflows/` | GitHub 自動排程設定 |

## 已知限制

- **財報 EPS 用「最新一期」**，月營收用「最新月份或今年累計年增率」，不是嚴格的「近四季逐季比較」。近四季 EPS 為正改用「本益比 > 0」判斷（證交所的本益比就是用近四季 EPS 計算，虧損時不會顯示）。
- **回測沒有財報條件**：拿不到歷史當下的財報與營收，所以回測只套用本益比、淨值比、技術面和法人條件，結果會略為樂觀或悲觀。
- **時間停損日是估計值**：只排除週末，沒排除國定假日。
- 證交所請求太頻繁會被暫時封鎖，程式已限速。如果 GitHub 某天執行失敗，隔天會自動補抓。
- 本程式依固定規則自動產生結果，僅供研究參考，不構成投資建議。
