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

## 8. NQ 回踩 5 分 20MA 統計工具

回答「**價格在 15 分 20MA 上方時，回踩 5 分 20MA 的跌破機率**，以及**未來 6 根 5 分 K 的平均最大漲幅**」，提供兩個定義完全一致的工具：

| 檔案 | 用途 |
|---|---|
| `tradingview/nq_pullback_ma5_stats.pine` | 指標版：貼進 TradingView（NQ 5 分鐘圖）即時統計，右上角表格顯示結果（範圍受方案的圖表 K 棒上限限制） |
| `tradingview/nq_pullback_ma5_strategy.pine` | 策略版：搭配深度回測（Deep Backtesting，Premium 以上）可跑滿完整兩年；跌破機率與平均最大漲幅由交易清單 CSV 統計（出場訊號「跌破」比例、Run-up % 平均） |
| `backtest/nq_ma_pullback_backtest.py` | 本地回測，資料來源可選 Futu OpenD（`US.NQmain`）、yfinance（`NQ=F`）或 TradingView 匯出 CSV |

```bash
# yfinance（最近約 60 天 5 分 K）
python backtest/nq_ma_pullback_backtest.py --yf

# Futu OpenD（需已啟動並登入）：美股正常盤、過去兩年
python backtest/nq_ma_pullback_backtest.py --futu \
    --start 2024-07-28 --end 2026-07-28 --session 0930-1600

# TradingView 匯出 CSV，只統計美股正常盤
python backtest/nq_ma_pullback_backtest.py --csv nq_5m.csv --session 0930-1600
```

事件定義（兩邊一致、皆可調參數）：前一根 5 分 K 收盤在 15 分 20MA 上方且已連續 3 根低點高於 5 分 20MA → 當根低點觸及 5 分 20MA 記為一次「回踩」；回踩起 6 根內任一收盤價低於 5 分 20MA 記為「跌破」；最大漲幅 = 未來 6 根最高價相對回踩點（觸及當下的 MA 值）的漲幅。15 分 MA 使用已收盤的 15 分 K 值（不重繪、無未來函數）。

所有統計另依「1 小時均線排列」分成三組對照：**1H多頭** = 1H MA5 > MA10 > MA20、**1H空頭** = MA5 < MA10 < MA20、其餘 = **其他**（長度可調）。指標版表格直接分欄顯示；策略版把分組寫進進場訊號（`回踩·1H多頭` 等），交易清單匯出 CSV 後可按組統計；Python 版報表自動分組。另附 `backtest/nq_null_baseline.py` 隨機基準模擬，供真實數據對照（判斷均線是否有真實支撐效果）。

---

## 注意事項

- 本專案**刻意不提供交易功能**。若日後要加，請另接 `OpenSecTradeContext` 並加上確認機制。
- 行情權限不足時，工具會回傳富途的錯誤訊息（例如需要訂閱權限或行情卡）。
- 訂閱會佔用 OpenD 配額；本 server 已對同一代碼/類型做去重。
