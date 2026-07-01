# Futu Data MCP (唯讀行情)

把 **Futubull / 富途牛牛 (Futu / moomoo)** 的市場行情數據，透過 **MCP** 接到 **Claude Code**。
本專案**只做唯讀行情**（快照、歷史/即時 K 線、盤口、逐筆、市場狀態），**不含任何下單交易功能**。

```
Claude Code ──MCP──> server.py (futu-api) ──TCP──> OpenD 閘道 ──> 富途伺服器
```

---

## 1. 前置需求

| 項目 | 說明 |
|---|---|
| 富途牛牛帳號 | **免費註冊即可**（手機/Email）。富途已取消「必須開戶」限制，可直接登入 OpenD。 |
| OpenD 閘道 | 必裝。本地閘道程式，登入後把請求中轉到富途伺服器。 |
| Python | 3.10+ |
| 行情權限 | 延遲行情免費；**美股即時目前免費**（推廣期）；港股/A 股即時看地區與身份。 |

---

## 2. 安裝 & 啟動 OpenD

1. 下載 OpenD：<https://www.futunn.com/download/OpenAPI>（或 moomoo 對應頁面）。
2. 編輯 `FutuOpenD.xml`：
   ```xml
   <login_account>你的牛牛帳號</login_account>
   <login_pwd>你的密碼</login_pwd>
   <api_ip>127.0.0.1</api_ip>
   <api_port>11111</api_port>
   ```
3. 啟動 OpenD，確認看到登入成功與 `Listening ... 11111` 字樣。

> OpenD 必須跑在能登入你富途帳號的機器上（通常是你本機）。

---

## 3. 安裝本專案

### 一鍵安裝（建議，macOS / Linux）

先確認 OpenD 已啟動並登入，然後：

```bash
./setup.sh
```

腳本會自動：檢查 Python 3.10+ → 建立 venv 並安裝 → 產生 `.env` → 測試 OpenD 連線 → 接上 Claude Code。

### 手動安裝（替代方案）

```bash
# 建議用 uv
uv venv && source .venv/bin/activate
uv pip install -e .

# 或用 pip
pip install -e .

# 設定連線（可選，預設就是 127.0.0.1:11111）
cp .env.example .env
```

---

## 4. 接到 Claude Code

在專案目錄執行：

```bash
claude mcp add futu-data -- uv run --directory /絕對路徑/到/這個repo futu-data-mcp
```

或手動加到 MCP 設定（`~/.claude.json` 的 `mcpServers`）：

```json
{
  "mcpServers": {
    "futu-data": {
      "command": "uv",
      "args": ["run", "--directory", "/絕對路徑/到/這個repo", "futu-data-mcp"],
      "env": { "FUTU_HOST": "127.0.0.1", "FUTU_PORT": "11111" }
    }
  }
}
```

啟動順序：**先開 OpenD（已登入）→ 再開 Claude Code**。

---

## 5. 可用工具 (tools)

| 工具 | 說明 | 需訂閱 |
|---|---|---|
| `get_market_snapshot(codes)` | 即時快照（價、漲跌、量、買賣盤等） | 否 |
| `get_history_kline(code, start, end, ktype, max_count)` | 歷史 K 線 | 否 |
| `get_cur_kline(code, num, ktype)` | 最新即時 K 線 | 自動 |
| `get_order_book(code, num)` | 即時盤口/檔位 | 自動 |
| `get_rt_ticker(code, num)` | 逐筆成交 | 自動 |
| `get_market_state(codes)` | 交易時段狀態 | — |

`ktype` 可用：`1m,3m,5m,15m,30m,60m,day,week,month`
股票代碼格式：`<市場>.<代號>`，例如 `US.AAPL`、`HK.00700`、`SH.600519`、`SZ.000001`。

---

## 6. 試用範例（在 Claude Code 裡直接說）

- 「用 futu-data 看 AAPL 現在的報價」
- 「抓 US.NVDA 最近 60 根日 K 線」
- 「00700.HK 現在的買賣盤口」

---

## 7. 本機快速自測（不經 Claude）

確認 OpenD 已啟動後：

```bash
python -c "from server import get_market_snapshot; print(get_market_snapshot(['US.AAPL']))"
```

---

## 範例分析：每年 7 月前幾個交易日的 SPY / QQQ

`analysis/july_first5.py` 用上面的 `get_history_kline` 工具，統計**過去 N 年、
每年 7 月前 K 個交易日**的 SPY / QQQ **每日漲跌幅**與**波動度**（日內振幅 + 漲跌幅標準差）。

> 需先啟動並登入 OpenD（同一般用法），因為資料是即時向富途伺服器取得的。

```bash
# 預設：US.SPY / US.QQQ、過去 10 年、每年 7 月前 5 個交易日
python analysis/july_first5.py

# 自訂範圍並輸出明細 CSV
python analysis/july_first5.py --years 10 --days 5 \
    --symbols US.SPY US.QQQ --csv july_first5.csv
```

輸出欄位：`daily_chg%`＝收盤對前一日收盤漲跌幅；`range%`＝當日 (最高-最低)/前收
的日內波動度。每年與全期會另給平均漲跌、漲跌幅標準差（波動度）與平均振幅。

## 注意事項

- 本專案**刻意不提供交易功能**。若日後要加，請另接 `OpenSecTradeContext` 並加上確認機制。
- 行情權限不足時，工具會回傳富途的錯誤訊息（例如需要訂閱權限或行情卡）。
- 訂閱會佔用 OpenD 配額；本 server 已對同一代碼/類型做去重。
