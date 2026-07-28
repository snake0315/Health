#!/usr/bin/env python3
"""NQ 回踩 5 分 20MA 統計回測（15 分 20MA 上方過濾）。

與 tradingview/nq_pullback_ma5_stats.pine 使用完全相同的事件定義，
方便用本地資料交叉驗證 TradingView 上看到的統計：

  • 前置狀態：前一根 5 分 K 收盤在 15 分 20MA 上方，且已連續 re_arm 根
    （預設 3）低點高於 5 分 20MA。
  • 回踩事件：當根 5 分 K 低點觸及/跌到 5 分 20MA（low <= MA）。
  • 跌破：回踩當根起至未來 fwd 根（預設 6）內，任一根收盤價低於 5 分 20MA。
  • 最大漲幅：以回踩點（觸及當下的 5 分 20MA 值）為基準，
    未來 fwd 根最高價相對基準的漲幅（不含回踩當根）。

15 分 20MA 一律用「已收盤的 15 分 K」計算（對齊 Pine 的不重繪模式）。

資料來源（三選一）：
  --csv PATH   TradingView 匯出的 5 分 K CSV（需含 time/open/high/low/close 欄位）
  --yf         Yahoo Finance NQ=F（yfinance 只提供最近約 60 天的 5 分 K）
  --futu       Futu OpenD（預設代碼 US.NQmain，需本機 OpenD 已登入）

範例：
  # 美股正常盤、過去兩年（Futu OpenD 需已登入且有 CME 期貨行情權限）
  python backtest/nq_ma_pullback_backtest.py --futu \
      --start 2024-07-28 --end 2026-07-28 --session 0930-1600
  python backtest/nq_ma_pullback_backtest.py --yf --session 0930-1600
  python backtest/nq_ma_pullback_backtest.py --csv nq_5m.csv --session 0930-1600

依賴：pandas（--yf 另需 yfinance，--futu 另需 futu-api）。
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd


# ── 資料載入 ──────────────────────────────────────────────────────────────────

def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    tcol = next((c for c in ("time", "datetime", "date", "timestamp") if c in df.columns), None)
    if tcol is None:
        sys.exit("CSV 需要 time/datetime/date/timestamp 欄位")
    ts = df[tcol]
    if pd.api.types.is_numeric_dtype(ts):  # unix 秒
        idx = pd.to_datetime(ts, unit="s", utc=True)
    else:
        idx = pd.to_datetime(ts, utc=True)
    out = df[["open", "high", "low", "close"]].copy()
    out.index = idx
    return out.sort_index()


def load_yf(period: str = "60d") -> pd.DataFrame:
    import yfinance as yf

    df = yf.download("NQ=F", interval="5m", period=period, auto_adjust=False, progress=False)
    if df.empty:
        sys.exit("yfinance 沒抓到資料（NQ=F 5m 僅提供最近約 60 天）")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
    return df[["open", "high", "low", "close"]].sort_index()


def load_futu(code: str, start: str | None, end: str | None) -> pd.DataFrame:
    from futu import OpenQuoteContext, RET_OK, KLType, AuType

    ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
    frames: list[pd.DataFrame] = []
    page_key = None
    try:
        while True:
            ret, data, page_key = ctx.request_history_kline(
                code, start=start, end=end, ktype=KLType.K_5M,
                autype=AuType.QFQ, max_count=1000, page_req_key=page_key)
            if ret != RET_OK:
                sys.exit(f"futu 抓取失敗: {data}")
            frames.append(data)
            if page_key is None:
                break
    finally:
        ctx.close()
    df = pd.concat(frames, ignore_index=True)
    df.index = pd.to_datetime(df["time_key"])
    return df[["open", "high", "low", "close"]].sort_index()


# ── 回測邏輯（與 Pine 腳本一致）──────────────────────────────────────────────

def backtest(df: pd.DataFrame, ma_len: int = 20, fwd: int = 6, re_arm: int = 3,
             incl_touch_bar_break: bool = True, ref_is_ma: bool = True,
             session: str | None = None) -> dict:
    df = df.dropna().copy()
    df["ma5"] = df["close"].rolling(ma_len).mean()

    # 15 分 20MA：已收盤值（shift(1) = 只用「上一根完成的 15 分 K」為止的 MA）
    c15 = df["close"].resample("15min", label="left", closed="left").last().dropna()
    ma15 = c15.rolling(ma_len).mean().shift(1)
    bucket = df.index.floor("15min")
    df["ma15"] = ma15.reindex(bucket).to_numpy()

    # 時段過濾只限制「回踩發生的時間」；未來 fwd 根仍用連續的完整資料，
    # 與 Pine 腳本在期貨全時段圖上的行為一致。K 棒時間視為該棒起始時間，
    # 因此 "0930-1600" 涵蓋 09:30 起至 15:55 起始的棒（不含 16:00）。
    if session:  # 例如 "0930-1600"（美東時間）
        h0, h1 = session.split("-")
        ny = df.index.tz_convert("America/New_York") if df.index.tz is not None else \
            df.index.tz_localize("America/New_York")
        hhmm = ny.strftime("%H%M")
        sess_ok = (hhmm >= h0) & (hhmm < h1)
    else:
        sess_ok = [True] * len(df)

    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    ma5, ma15v = df["ma5"].to_numpy(), df["ma15"].to_numpy()

    above_cnt = 0
    events = []
    for i in range(len(df)):
        prev_above = above_cnt
        above_cnt = above_cnt + 1 if (ma5[i] == ma5[i] and l[i] > ma5[i]) else 0
        if i == 0 or ma5[i] != ma5[i] or ma15v[i] != ma15v[i]:
            continue
        touch = prev_above >= re_arm and l[i] <= ma5[i] and c[i - 1] > ma15v[i] \
            and sess_ok[i]
        if not touch or i + fwd >= len(df):
            continue
        ref = ma5[i] if ref_is_ma else c[i]
        win = slice(i + 1, i + 1 + fwd)
        broke = incl_touch_bar_break and c[i] < ma5[i]
        broke = broke or any(c[j] < ma5[j] for j in range(i + 1, i + 1 + fwd))
        gain_pts = h[win].max() - ref
        events.append({
            "time": df.index[i], "ref": ref, "broke": broke,
            "gain_pct": 100.0 * gain_pts / ref, "gain_pts": gain_pts,
            "drop_pct": 100.0 * (l[win].min() - ref) / ref,
        })

    ev = pd.DataFrame(events)
    if ev.empty:
        return {"n": 0}
    hold = ev[~ev["broke"]]
    brk = ev[ev["broke"]]
    return {
        "n": len(ev),
        "n_break": len(brk),
        "p_break": 100.0 * len(brk) / len(ev),
        "avg_gain_pct": ev["gain_pct"].mean(),
        "med_gain_pct": ev["gain_pct"].median(),
        "avg_gain_pts": ev["gain_pts"].mean(),
        "avg_gain_hold": hold["gain_pct"].mean() if len(hold) else float("nan"),
        "avg_gain_break": brk["gain_pct"].mean() if len(brk) else float("nan"),
        "avg_drop_pct": ev["drop_pct"].mean(),
        "events": ev,
    }


def report(r: dict, fwd: int) -> None:
    if r["n"] == 0:
        print("沒有符合條件的回踩事件。")
        return
    print("── NQ 回踩 5 分 20MA 統計（15 分 20MA 上方）──")
    print(f"回踩次數（樣本數）        : {r['n']}")
    print(f"跌破次數 / 跌破機率       : {r['n_break']} / {r['p_break']:.1f}%")
    print(f"未來{fwd}根 平均最大漲幅   : {r['avg_gain_pct']:.3f}%（{r['avg_gain_pts']:.1f} 點）")
    print(f"最大漲幅中位數            : {r['med_gain_pct']:.3f}%")
    print(f"守住時 平均最大漲幅       : {r['avg_gain_hold']:.3f}%")
    print(f"跌破時 平均最大漲幅       : {r['avg_gain_break']:.3f}%")
    print(f"未來{fwd}根 平均最大跌幅   : {r['avg_drop_pct']:.3f}%")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="5 分 K CSV 檔路徑（TradingView 匯出格式）")
    src.add_argument("--yf", action="store_true", help="用 yfinance 抓 NQ=F（最近約 60 天）")
    src.add_argument("--futu", action="store_true", help="用 Futu OpenD 抓資料")
    ap.add_argument("--code", default="US.NQmain", help="futu 代碼（預設 US.NQmain）")
    ap.add_argument("--start", help="開始日期 YYYY-MM-DD（futu 用）")
    ap.add_argument("--end", help="結束日期 YYYY-MM-DD（futu 用）")
    ap.add_argument("--ma-len", type=int, default=20)
    ap.add_argument("--fwd", type=int, default=6, help="未來觀察 K 棒數（預設 6）")
    ap.add_argument("--re-arm", type=int, default=3,
                    help="重新計數門檻：連續高於 5 分 MA 的根數（預設 3）")
    ap.add_argument("--session",
                    help='回踩發生時間限定美東時段，例如 "0930-1600"（正常盤）')
    ap.add_argument("--ref-close", action="store_true",
                    help="漲幅基準改用回踩當根收盤價（預設用 MA 值）")
    ap.add_argument("--dump-events", help="把每筆事件輸出成 CSV")
    args = ap.parse_args()

    if args.csv:
        df = load_csv(args.csv)
    elif args.yf:
        df = load_yf()
    else:
        df = load_futu(args.code, args.start, args.end)

    print(f"資料範圍：{df.index[0]} ~ {df.index[-1]}（{len(df)} 根 5 分 K）")
    r = backtest(df, ma_len=args.ma_len, fwd=args.fwd, re_arm=args.re_arm,
                 ref_is_ma=not args.ref_close, session=args.session)
    report(r, args.fwd)
    if args.dump_events and r["n"] > 0:
        r["events"].to_csv(args.dump_events, index=False)
        print(f"事件明細已輸出：{args.dump_events}")


if __name__ == "__main__":
    main()
