#!/usr/bin/env python3
"""NQ / QQQ 回踩 5 分 20MA 事件研究（1 分 K 微結構版）。

條件（皆可調）：
  • 1 小時與 15 分鐘皆為多頭：前一根 1 分 K 收盤同時高於「已收盤」的
    1H 20MA 與 15 分 20MA。
  • 價格先連續 re_arm_min 分鐘（預設 15）低點高於 5 分 20MA，
    當根 1 分 K 低點觸及 5 分 20MA（已收盤 5 分 K 的 MA 值）→ 記為一次回踩。
  • 基準價 ref = 觸及當下的 5 分 20MA 值；觀察窗 = 之後 fwd_min 分鐘（預設 30，
    即 6 根 5 分 K）。

輸出指標（對應觀察窗內的 1 分 K）：
  • 反彈觸及 1m 10MA / 1m 20MA（高點碰到）之機率
  • 反彈「5 分收盤」突破前高（前高 = 觸及前 prior_high_min 分鐘的最高價）之機率
  • 先觸 1m 20MA vs 先破 1×ATR 停損（停損 = ref − atr_mult × 5 分 ATR(14)；
    同一根同時發生以停損計，保守處理）
  • 平均 / 中位反彈幅度 MFE、反彈幅度(ATR尺度)、平均 / 中位下插 MAE
  • 附註：5 分收盤跌破 5 分 20MA 機率（與前一版統計口徑近似）

資料來源（需 1 分 K）：
  --csv PATH        1 分 K CSV（time/open/high/low/close）
  --yf [SYMBOL]     Yahoo（NQ=F 或 QQQ；1 分 K 只有最近約 30 天，自動分段抓）
  --futu [CODE]     Futu OpenD（US.NQmain 或 US.QQQ，1 分 K 歷史較長）

範例：
  python backtest/nq_pullback_event_study.py --yf QQQ --session 0930-1600
  python backtest/nq_pullback_event_study.py --futu US.NQmain \
      --start 2024-07-28 --end 2026-07-28 --session 0930-1600
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd


# ── 資料載入（1 分 K）─────────────────────────────────────────────────────────

def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    tcol = next((c for c in ("time", "datetime", "date", "timestamp") if c in df.columns), None)
    if tcol is None:
        sys.exit("CSV 需要 time/datetime/date/timestamp 欄位")
    ts = df[tcol]
    idx = pd.to_datetime(ts, unit="s", utc=True) if pd.api.types.is_numeric_dtype(ts) \
        else pd.to_datetime(ts, utc=True)
    out = df[["open", "high", "low", "close"]].copy()
    out.index = idx
    return out.sort_index()


def load_yf_1m(symbol: str, days: int = 30) -> pd.DataFrame:
    """Yahoo 1 分 K：單次最多 8 天、最遠 30 天，分段抓後合併。"""
    from datetime import datetime, timedelta, timezone
    import yfinance as yf

    end = datetime.now(timezone.utc)
    start_lim = end - timedelta(days=min(days, 29))
    frames = []
    chunk_end = end
    while chunk_end > start_lim:
        chunk_start = max(start_lim, chunk_end - timedelta(days=7))
        df = yf.download(symbol, interval="1m", start=chunk_start, end=chunk_end,
                         auto_adjust=False, progress=False)
        if not df.empty:
            frames.append(df)
        chunk_end = chunk_start
    if not frames:
        sys.exit(f"yfinance 沒抓到 {symbol} 的 1 分 K")
    df = pd.concat(frames)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
    df = df[~df.index.duplicated(keep="first")].sort_index()
    return df[["open", "high", "low", "close"]]


def load_futu_1m(code: str, start: str | None, end: str | None) -> pd.DataFrame:
    from futu import OpenQuoteContext, RET_OK, KLType, AuType

    ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
    frames, page_key = [], None
    try:
        while True:
            ret, data, page_key = ctx.request_history_kline(
                code, start=start, end=end, ktype=KLType.K_1M,
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


# ── 事件研究 ─────────────────────────────────────────────────────────────────

def _map_confirmed(df1m: pd.DataFrame, rule: str, calc) -> np.ndarray:
    """把高週期序列（只用已收盤 K，shift(1)）對映回每根 1 分 K。"""
    grp = df1m.resample(rule, label="left", closed="left")
    ser = calc(grp).shift(1)
    return ser.reindex(df1m.index.floor(rule)).to_numpy()


def event_study(df: pd.DataFrame, ma5_len: int = 20, ma15_len: int = 20,
                h1_len: int = 20, m1_fast: int = 10, m1_slow: int = 20,
                fwd_min: int = 30, re_arm_min: int = 15, atr_len: int = 14,
                atr_mult: float = 1.0, prior_high_min: int = 60,
                session: str | None = None, ref_is_ma: bool = True) -> dict:
    df = df.dropna().copy()

    ma5c = _map_confirmed(df, "5min", lambda g: g["close"].last().dropna()
                          .rolling(ma5_len).mean())
    ma15 = _map_confirmed(df, "15min", lambda g: g["close"].last().dropna()
                          .rolling(ma15_len).mean())
    ma60 = _map_confirmed(df, "60min", lambda g: g["close"].last().dropna()
                          .rolling(h1_len).mean())

    # 5 分 ATR（Wilder），已收盤值
    df5 = df.resample("5min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    tr = pd.concat([df5["high"] - df5["low"],
                    (df5["high"] - df5["close"].shift(1)).abs(),
                    (df5["low"] - df5["close"].shift(1)).abs()], axis=1).max(axis=1)
    atr5 = tr.ewm(alpha=1.0 / atr_len, adjust=False).mean().shift(1) \
        .reindex(df.index.floor("5min")).to_numpy()

    m1f = df["close"].rolling(m1_fast).mean().to_numpy()
    m1s = df["close"].rolling(m1_slow).mean().to_numpy()
    ph = df["high"].rolling(prior_high_min).max().to_numpy()  # 含當根的前 N 分鐘最高

    if session:
        h0, h1 = session.split("-")
        ny = df.index.tz_convert("America/New_York") if df.index.tz is not None else \
            df.index.tz_localize("America/New_York")
        hhmm = ny.strftime("%H%M")
        sess_ok = (hhmm >= h0) & (hhmm < h1)
    else:
        sess_ok = np.ones(len(df), bool)

    h, l, c = (df[k].to_numpy() for k in ("high", "low", "close"))
    is5c = (df.index.minute % 5 == 4)  # 該 1 分 K 收盤 = 5 分 K 收盤

    above = 0
    events = []
    for i in range(len(df)):
        prev_above = above
        above = above + 1 if (ma5c[i] == ma5c[i] and l[i] > ma5c[i]) else 0
        if i == 0 or any(v != v for v in (ma5c[i], ma15[i], ma60[i], atr5[i], m1s[i])):
            continue
        touch = (prev_above >= re_arm_min and l[i] <= ma5c[i]
                 and c[i - 1] > ma15[i] and c[i - 1] > ma60[i] and sess_ok[i])
        if not touch or i + fwd_min >= len(df):
            continue
        ref = ma5c[i] if ref_is_ma else c[i]
        w = range(i + 1, i + 1 + fwd_min)
        stop = ref - atr_mult * atr5[i]
        t10 = t20 = bph = brk5 = False
        race = "皆未觸發"
        mfe = -np.inf
        mae = np.inf
        for j in w:
            mfe = max(mfe, h[j])
            mae = min(mae, l[j])
            if h[j] >= m1f[j]:
                t10 = True
            if h[j] >= m1s[j]:
                t20 = True
            if is5c[j] and c[j] > ph[i]:
                bph = True
            if is5c[j] and c[j] < ma5c[j]:
                brk5 = True
            if race == "皆未觸發":
                if l[j] <= stop:          # 同根同時觸發以停損計（保守）
                    race = "先破停損"
                elif h[j] >= m1s[j]:
                    race = "先觸1m20MA"
        mfe_pts, mae_pts = mfe - ref, mae - ref
        events.append({
            "time": df.index[i], "ref": ref, "atr": atr5[i],
            "mfe_pct": 100.0 * mfe_pts / ref, "mfe_pts": mfe_pts,
            "mae_pct": 100.0 * mae_pts / ref, "mae_pts": mae_pts,
            "mfe_atr": mfe_pts / atr5[i], "mae_atr": mae_pts / atr5[i],
            "touch_1m10": t10, "touch_1m20": t20, "break_ph": bph,
            "race": race, "break_5m_close": brk5,
        })

    ev = pd.DataFrame(events)
    return {"n": len(ev), "events": ev}


def report(r: dict, fwd_min: int, atr_mult: float) -> None:
    ev = r["events"]
    if not len(ev):
        print("沒有符合條件的回踩事件。")
        return
    n = len(ev)
    p = lambda m: 100.0 * m.sum() / n
    race = ev["race"].value_counts()
    rp = lambda k: 100.0 * race.get(k, 0) / n
    print(f"── 回踩 5 分 20MA 事件研究（1H＋15分皆多頭、觀察 {fwd_min} 分鐘）──")
    print(f"樣本數                          : {n}")
    print(f"反彈觸及 1m 10MA(高點碰到)      : {p(ev['touch_1m10']):.1f}%")
    print(f"反彈觸及 1m 20MA(高點碰到)      : {p(ev['touch_1m20']):.1f}%")
    print(f"反彈收盤突破前高(5分收盤)       : {p(ev['break_ph']):.1f}%")
    print(f"先觸1m20MA vs 先破{atr_mult:g}×ATR停損 : "
          f"先觸 {rp('先觸1m20MA'):.1f}% / 先停損 {rp('先破停損'):.1f}% / "
          f"皆無 {rp('皆未觸發'):.1f}%")
    print(f"平均反彈幅度 MFE                : {ev['mfe_pct'].mean():.3f}%"
          f"（{ev['mfe_pts'].mean():.2f} 點）")
    print(f"中位反彈幅度                    : {ev['mfe_pct'].median():.3f}%")
    print(f"反彈幅度(ATR尺度)               : 平均 {ev['mfe_atr'].mean():.2f} / "
          f"中位 {ev['mfe_atr'].median():.2f} ATR")
    print(f"平均下插 MAE                    : {ev['mae_pct'].mean():.3f}%"
          f"（{ev['mae_atr'].mean():.2f} ATR）")
    print(f"中位下插 MAE                    : {ev['mae_pct'].median():.3f}%"
          f"（{ev['mae_atr'].median():.2f} ATR）")
    print(f"附註：5分收盤跌破5分20MA機率    : {p(ev['break_5m_close']):.1f}%")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="1 分 K CSV 檔路徑")
    src.add_argument("--yf", nargs="?", const="NQ=F", metavar="SYMBOL",
                     help="Yahoo 代碼（預設 NQ=F，可用 QQQ；僅最近約 30 天）")
    src.add_argument("--futu", nargs="?", const="US.NQmain", metavar="CODE",
                     help="futu 代碼（預設 US.NQmain，可用 US.QQQ）")
    ap.add_argument("--start", help="開始日期 YYYY-MM-DD（futu 用）")
    ap.add_argument("--end", help="結束日期 YYYY-MM-DD（futu 用）")
    ap.add_argument("--fwd-min", type=int, default=30, help="觀察窗分鐘數（預設 30）")
    ap.add_argument("--re-arm-min", type=int, default=15,
                    help="重新計數門檻：連續高於 5 分 MA 的分鐘數（預設 15）")
    ap.add_argument("--atr-mult", type=float, default=1.0, help="停損 ATR 倍數（預設 1.0）")
    ap.add_argument("--prior-high-min", type=int, default=60,
                    help="前高回看分鐘數（預設 60）")
    ap.add_argument("--session", help='回踩發生時間限定美東時段，例如 "0930-1600"')
    ap.add_argument("--ref-close", action="store_true",
                    help="基準改用回踩當根收盤價（預設用 5 分 20MA 值）")
    ap.add_argument("--split", choices=["year", "quarter", "month"],
                    help="另外依時間區間分段輸出（年/季/月）")
    ap.add_argument("--dump-events", help="把每筆事件輸出成 CSV")
    args = ap.parse_args()

    if args.csv:
        df = load_csv(args.csv)
    elif args.yf:
        df = load_yf_1m(args.yf)
    else:
        df = load_futu_1m(args.futu, args.start, args.end)

    print(f"資料範圍：{df.index[0]} ~ {df.index[-1]}（{len(df)} 根 1 分 K）")
    r = event_study(df, fwd_min=args.fwd_min, re_arm_min=args.re_arm_min,
                    atr_mult=args.atr_mult, prior_high_min=args.prior_high_min,
                    session=args.session, ref_is_ma=not args.ref_close)
    report(r, args.fwd_min, args.atr_mult)
    if args.split and r["n"] > 0:
        ev = r["events"]
        code = {"year": "Y", "quarter": "Q", "month": "M"}[args.split]
        t = ev["time"]
        t = t.dt.tz_convert("America/New_York") if t.dt.tz is not None else t
        print(f"\n── 依區間分段（{args.split}）──")
        hdr = (f"{'區間':>8} | {'樣本':>5} | {'觸1m20MA':>8} | {'先觸vs先停損':>12} | "
               f"{'MFE中位':>8} | {'MAE中位':>8}")
        print(hdr)
        print("-" * len(hdr))
        for pkey, sub in ev.groupby(t.dt.to_period(code)):
            m = len(sub)
            tgt = 100.0 * (sub["race"] == "先觸1m20MA").sum() / m
            stp = 100.0 * (sub["race"] == "先破停損").sum() / m
            print(f"{str(pkey):>8} | {m:>5} | "
                  f"{100.0 * sub['touch_1m20'].sum() / m:>7.1f}% | "
                  f"{tgt:>5.1f}%/{stp:>5.1f}% | "
                  f"{sub['mfe_pct'].median():>7.3f}% | {sub['mae_pct'].median():>7.3f}%")
    if args.dump_events and r["n"] > 0:
        r["events"].to_csv(args.dump_events, index=False)
        print(f"事件明細已輸出：{args.dump_events}")


if __name__ == "__main__":
    main()
