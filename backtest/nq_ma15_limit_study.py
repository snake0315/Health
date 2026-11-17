#!/usr/bin/env python3
"""1H 與 15 分同方向時，回到 15 分 20MA 掛 limit 進場 → 價格回到 5 分 20MA 的機率。

事件定義（多單；空單完全對稱）：
  • 方向：最近一根「已收盤」15 分 K 收在 15 分 20MA 之上，且最近一根已收盤 1H K
    收在 1H 20MA 之上（--slope 另要求兩條 MA 皆上彎）。
  • 結構：當下 5 分 20MA 在 15 分 20MA 之上（目標價在進場價上方，才有「回到」可言）。
  • 掛單：limit 買單掛在 15 分 20MA（已收盤 15 分 K 的 MA 值，事前可知）。
    價格須先連續 --re-arm-min 分鐘（預設 30）低點高於 15 分 20MA，
    之後 1 分 K 低點觸及（--fill-ticks 可要求穿價 N tick）→ 成交一次。
    成交價 = min(該分鐘開盤, limit)（跳空開在 limit 之下則以開盤價成交）。
  • 目標：之後任一分鐘高點 ≥ 當下的 5 分 20MA（已收盤 5 分 K 的 MA 值）。
    成交當根不計目標（無法確認先後，保守處理）。
  • 停損：進場價 − k × 5 分 ATR(14)（Wilder，已收盤值）。成交當根即計停損；
    同一分鐘同時觸及目標與停損 → 以停損計（保守）。
  • 觀察窗：事件須發生在 --session（預設 0930-1600 美東），追蹤到當日 16:00 收盤。

輸出：
  • 回到 5 分 20MA 的機率（15/30/60/120 分鐘內、當日收盤前；不論是否先停損）
  • 先回到 5 分 20MA vs 先被 k×ATR 停損（多個 k）
  • 進場到目標的距離、到達所需時間、到達前的最大逆向（MAE）
  • 依年度分段、多空分開
  • --null：用相同時間軸、相同波動率的隨機漫步 1 分 K 跑同一套規則，
    作為「純機械性」基準（判斷均線是否真有吸引/支撐效果）

資料：--data 可讀 .parquet / .csv 的 1 分 K（欄位 DateTime_UTC 或 DateTime_ET 或
time/datetime/timestamp，加上 open/high/low/close，大小寫不拘）。
未調整的連續合約會自動偵測季換月跳空並做比例式回溯調整（--no-roll-adjust 關閉）。

範例：
  python backtest/nq_ma15_limit_study.py --data nq_1m.parquet
  python backtest/nq_ma15_limit_study.py --data nq_1m.parquet --since 2023-11-22 --null
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

NY = "America/New_York"


# ── 資料 ─────────────────────────────────────────────────────────────────────

def load_bars(path: str, roll_adjust: bool = True) -> pd.DataFrame:
    df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)
    lower = {c.lower(): c for c in df.columns}
    if "datetime_utc" in lower:
        idx = pd.to_datetime(df[lower["datetime_utc"]], utc=True)
    elif "datetime_et" in lower:
        idx = pd.to_datetime(df[lower["datetime_et"]]).dt.tz_localize(
            NY, ambiguous="infer", nonexistent="shift_forward").dt.tz_convert("UTC")
    else:
        tcol = next((lower[c] for c in ("time", "datetime", "timestamp", "date") if c in lower), None)
        if tcol is None:
            sys.exit("找不到時間欄位（DateTime_UTC / DateTime_ET / time / datetime / timestamp）")
        ts = df[tcol]
        idx = pd.to_datetime(ts, unit="s", utc=True) if pd.api.types.is_numeric_dtype(ts) \
            else pd.to_datetime(ts, utc=True)
    out = pd.DataFrame({k: df[lower[k]].to_numpy(float) for k in ("open", "high", "low", "close")},
                       index=pd.DatetimeIndex(idx))
    out = out[~out.index.duplicated()].sort_index().dropna()
    if roll_adjust:
        out, rolls = adjust_rolls(out)
        if rolls:
            print(f"已偵測並回溯調整 {len(rolls)} 次換月跳空："
                  + ", ".join(f"{d:%Y-%m-%d}({g:+.2f}%)" for d, g in rolls))
    return out


def _gap_minutes(index: pd.DatetimeIndex) -> np.ndarray:
    """與前一根的間隔分鐘數（第一根為 inf）；不受 datetime 解析度（ns/us/s）影響。"""
    return np.r_[np.inf, np.diff(index.as_unit("ns").asi8) / 6e10]


def adjust_rolls(df: pd.DataFrame, thresh: float = 0.002) -> tuple[pd.DataFrame, list]:
    """偵測季換月（3/6/9/12 月 6–22 日、美東 00:00、盤中連續）的跳空，比例式回溯調整。"""
    et = df.index.tz_convert(NY)
    g = np.log(df["open"].to_numpy()) - np.log(df["close"].shift(1).to_numpy())
    gap_min = _gap_minutes(df.index)
    cand = ((et.hour == 0) & (et.minute <= 1) & np.isin(et.month, [3, 6, 9, 12])
            & (et.day >= 6) & (et.day <= 22) & (gap_min <= 3) & (np.abs(g) > thresh))
    pos = np.flatnonzero(cand)
    if not len(pos):
        return df, []
    q = pd.PeriodIndex(et[pos], freq="Q")
    picks = pd.Series(np.abs(g[pos]), index=pos).groupby(q).idxmax().to_numpy()
    factor = np.ones(len(df))
    for p in picks:                       # 換月前的價格全部乘上 exp(跳空)
        factor[:p] *= np.exp(g[p])
    adj = df.copy()
    for k in ("open", "high", "low", "close"):
        adj[k] = df[k].to_numpy() * factor
    return adj, [(et[p], 100 * g[p]) for p in picks]


def make_null(df: pd.DataFrame, seed: int = 0) -> pd.DataFrame:
    """同時間軸、分時段波動率對齊的零漂移隨機漫步 1 分 K。

    每分鐘報酬取 t(4) 厚尾；高低點用布朗橋極值的精確分布抽樣（連續路徑），
    因此掛單價（limit / 停損 / 目標）的成交是精確的。若改用離散步長合成路徑，
    價格穿越掛單價時會「越過頭」，使每筆被系統性扣分，基準期望值偏離 0。
    """
    rng = np.random.default_rng(seed)
    et = df.index.tz_convert(NY)
    hhmm = et.hour * 100 + et.minute
    rth = (hhmm >= 930) & (hhmm < 1600)
    lr = np.diff(np.log(df["close"].to_numpy()), prepend=np.nan)
    contiguous = _gap_minutes(df.index) == 1.0
    sig = np.where(rth, np.nanstd(lr[contiguous & rth]), np.nanstd(lr[contiguous & ~rth]))
    x = rng.standard_t(4, len(df)) / np.sqrt(2.0) * sig        # 每分鐘對數報酬
    logc = np.log(df["close"].iloc[0]) + np.cumsum(x)
    logo = np.r_[np.log(df["close"].iloc[0]), logc[:-1]]
    u1, u2 = rng.random(len(df)), rng.random(len(df))
    up = (x + np.sqrt(x ** 2 - 2 * sig ** 2 * np.log(u1))) / 2  # 布朗橋最大值 ≥ max(0, x)
    dn = (x - np.sqrt(x ** 2 - 2 * sig ** 2 * np.log(u2))) / 2  # 布朗橋最小值 ≤ min(0, x)
    return pd.DataFrame({"open": np.exp(logo), "high": np.exp(logo + up),
                         "low": np.exp(logo + dn), "close": np.exp(logc)}, index=df.index)


# ── 研究 ─────────────────────────────────────────────────────────────────────

def _conf(close: pd.Series, rule: str, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """回傳每根 1 分 K 當下可知的：已收盤高週期收盤價、其 n 期 SMA、前一期 SMA。"""
    g = close.resample(rule, label="left", closed="left").last().dropna()
    ma = g.rolling(n).mean()
    key = close.index.floor(rule)
    pick = lambda s: s.reindex(key).to_numpy()
    return pick(g.shift(1)), pick(ma.shift(1)), pick(ma.shift(2))


def study(df: pd.DataFrame, ma_len: int = 20, re_arm_min: int = 30, atr_len: int = 14,
          stops=(0.5, 1.0, 1.5, 2.0), session: str = "0930-1600", slope: bool = False,
          fill_ticks: int = 0, tick: float = 0.25) -> pd.DataFrame:
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    cl = df["close"]
    _, ma5, _ = _conf(cl, "5min", ma_len)
    c15, ma15, ma15p = _conf(cl, "15min", ma_len)
    c60, ma60, ma60p = _conf(cl, "60min", ma_len)

    b5 = df.resample("5min", label="left", closed="left").agg(
        {"high": "max", "low": "min", "close": "last"}).dropna()
    tr = pd.concat([b5["high"] - b5["low"], (b5["high"] - b5["close"].shift()).abs(),
                    (b5["low"] - b5["close"].shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / atr_len, adjust=False).mean().shift(1) \
        .reindex(df.index.floor("5min")).to_numpy()

    et = df.index.tz_convert(NY)
    hhmm = et.hour * 100 + et.minute
    s0, s1 = (int(x) for x in session.split("-"))
    in_sess = (hhmm >= s0) & (hhmm < s1)
    # 每根 K 所屬交易日的時段最後一根位置（追蹤到收盤）
    day = np.asarray(et.normalize().asi8)
    pos = np.arange(len(df))
    last = pd.Series(np.where(in_sess, pos, -1)).groupby(day).transform("max").to_numpy()

    up_ok = (c15 > ma15) & (c60 > ma60)
    dn_ok = (c15 < ma15) & (c60 < ma60)
    if slope:
        up_ok &= (ma15 > ma15p) & (ma60 > ma60p)
        dn_ok &= (ma15 < ma15p) & (ma60 < ma60p)
    valid = ~(np.isnan(ma5) | np.isnan(ma15) | np.isnan(ma60) | np.isnan(atr))
    off = fill_ticks * tick

    events = []
    above = below = 0
    for i in range(len(df)):
        if not valid[i]:
            above = below = 0
            continue
        pa, pb = above, below
        above = above + 1 if l[i] > ma15[i] else 0
        below = below + 1 if h[i] < ma15[i] else 0
        if not in_sess[i] or last[i] <= i:
            continue
        for side, armed, hit, ok, tgt_ok in (
                (1, pa, l[i] <= ma15[i] - off, up_ok[i], ma5[i] > ma15[i]),
                (-1, pb, h[i] >= ma15[i] + off, dn_ok[i], ma5[i] < ma15[i])):
            if armed < re_arm_min or not hit or not ok or not tgt_ok:
                continue
            events.append(_simulate(i, side, o, h, l, c, ma5, ma15, atr, last[i], stops,
                                    df.index))
    return pd.DataFrame(events)


def _simulate(i, side, o, h, l, c, ma5, ma15, atr, end, stops, index) -> dict:
    lim = ma15[i]
    entry = min(o[i], lim) if side == 1 else max(o[i], lim)
    a = atr[i]
    seg = slice(i + 1, end + 1)
    if side == 1:
        tgt_hits = np.flatnonzero(h[seg] >= ma5[seg])
        adverse = lambda j0, j1: entry - l[j0:j1].min()
        favor = h[i + 1:end + 1].max() - entry
        stop_hit = lambda k: np.flatnonzero(l[i:end + 1] <= entry - k * a)
    else:
        tgt_hits = np.flatnonzero(l[seg] <= ma5[seg])
        adverse = lambda j0, j1: h[j0:j1].max() - entry
        favor = entry - l[i + 1:end + 1].min()
        stop_hit = lambda k: np.flatnonzero(h[i:end + 1] >= entry + k * a)
    t = tgt_hits[0] + 1 if len(tgt_hits) else None          # 距成交的分鐘數
    j_end = i + t + 1 if t is not None else end + 1
    # 到達時實際獲利：以 5 分 20MA 價位出場；若該分鐘開盤已越過目標（已收盤 5 分 MA 每 5 分鐘
    # 跳一次值，常直接跳到價格另一側），則以開盤價成交。目標會往價格移動，通常小於進場時距離。
    if t is not None:
        j = i + t
        px = max(ma5[j], o[j]) if side == 1 else min(ma5[j], o[j])
        gain = side * (px - entry) / a
    else:
        gain = np.nan
    close_r = side * (c[end] - entry) / a                     # 收盤平倉的損益（ATR）
    ev = {
        "time": index[i], "side": "多" if side == 1 else "空", "entry": entry, "atr": a,
        "dist_pts": abs(ma5[i] - entry), "dist_atr": abs(ma5[i] - entry) / a,
        "t_min": t, "gain_at_tgt_atr": gain, "mae_to_tgt_atr": max(adverse(i, j_end), 0.0) / a,
        "mfe_day_atr": favor / a,
    }
    for k in stops:
        s = stop_hit(k)
        s_t = s[0] if len(s) else None                        # 0 = 成交當根
        ev[f"stop_t_{k:g}"] = s_t
        # 同一分鐘同時觸及：若該分鐘「開盤」已越過目標，目標確定先發生；否則無法判定 → 以停損計
        tie_tgt_first = (t is not None and s_t == t and
                         (o[i + t] >= ma5[i + t] if side == 1 else o[i + t] <= ma5[i + t]))
        if t is not None and (s_t is None or t < s_t or tie_tgt_first):
            ev[f"race_{k:g}"], ev[f"R_{k:g}"] = "目標", gain
        elif s_t is not None:
            # 停損價；成交當根之後若開盤已跳空穿越停損，以開盤價成交（滑價）
            sp = entry - side * k * a
            if s_t > 0:
                sp = min(sp, o[i + s_t]) if side == 1 else max(sp, o[i + s_t])
            ev[f"race_{k:g}"], ev[f"R_{k:g}"] = "停損", side * (sp - entry) / a
        else:
            ev[f"race_{k:g}"], ev[f"R_{k:g}"] = "皆無", close_r
    return ev


# ── 報表 ─────────────────────────────────────────────────────────────────────

def summarize(ev: pd.DataFrame, stops, horizons=(15, 30, 60, 120)) -> dict:
    n = len(ev)
    t = ev["t_min"]
    out = {"n": n}
    for H in horizons:
        out[f"p{H}"] = 100 * (t <= H).sum() / n
    out["p_day"] = 100 * t.notna().sum() / n
    for k in stops:
        vc = ev[f"race_{k:g}"].value_counts()
        out[f"tgt_{k:g}"] = 100 * vc.get("目標", 0) / n
        out[f"stp_{k:g}"] = 100 * vc.get("停損", 0) / n
        out[f"R_{k:g}"] = ev[f"R_{k:g}"].mean()
    out["gain_med"] = ev["gain_at_tgt_atr"].median()
    out["dist_atr"] = ev["dist_atr"].median()
    out["dist_pts"] = ev["dist_pts"].median()
    out["t_med"] = t.median()
    out["mae_med"] = ev.loc[t.notna(), "mae_to_tgt_atr"].median()
    out["mae_p80"] = ev.loc[t.notna(), "mae_to_tgt_atr"].quantile(0.8)
    return out


def report(ev: pd.DataFrame, stops, title: str) -> None:
    print(f"\n══ {title} ══")
    if ev.empty:
        print("沒有符合條件的事件。")
        return
    rows = [("全部", ev)] + [(s, ev[ev["side"] == s]) for s in ("多", "空")]
    S = {name: summarize(sub, stops) for name, sub in rows if len(sub)}
    cols = list(S)
    line = lambda label, fmt: print(f"{label:<26}" + "".join(f"{fmt(S[c]):>14}" for c in cols))
    print(f"{'':<26}" + "".join(f"{c:>14}" for c in cols))
    line("樣本數", lambda s: f"{s['n']}")
    for H in (15, 30, 60, 120):
        line(f"{H} 分鐘內回到5分20MA", lambda s, H=H: f"{s[f'p{H}']:.1f}%")
    line("當日收盤前回到5分20MA", lambda s: f"{s['p_day']:.1f}%")
    for k in stops:
        line(f"先回5MA / 先破{k:g}×ATR", lambda s, k=k: f"{s[f'tgt_{k:g}']:.0f}% / {s[f'stp_{k:g}']:.0f}%")
    line("目標距離 中位（ATR）", lambda s: f"{s['dist_atr']:.2f}")
    line("目標距離 中位（點）", lambda s: f"{s['dist_pts']:.1f}")
    line("到達時實際獲利 中位（ATR）", lambda s: f"{s['gain_med']:.2f}")
    for k in stops:
        line(f"期望值/筆（{k:g}×ATR停損，ATR）", lambda s, k=k: f"{s[f'R_{k:g}']:+.3f}")
    line("到達時間 中位（分鐘）", lambda s: f"{s['t_med']:.0f}")
    line("到達前逆向 MAE 中位（ATR）", lambda s: f"{s['mae_med']:.2f}")
    line("到達前逆向 MAE 80分位（ATR）", lambda s: f"{s['mae_p80']:.2f}")


def by_period(ev: pd.DataFrame, freq: str = "Y") -> None:
    if ev.empty:
        return
    print(f"\n── 依{'年' if freq == 'Y' else '季'}分段（多空合計）──")
    print(f"{'區間':>8} | {'樣本':>5} | {'30分內':>6} | {'收盤前':>6} | {'先回5MA/先破1ATR':>17} | {'距離ATR':>6}")
    per = ev["time"].dt.tz_convert(NY).dt.tz_localize(None).dt.to_period(freq)
    for p, sub in ev.groupby(per):
        s = summarize(sub, (1.0,))
        print(f"{str(p):>8} | {s['n']:>5} | {s['p30']:>5.1f}% | {s['p_day']:>5.1f}% | "
              f"{s['tgt_1']:>7.1f}% / {s['stp_1']:>5.1f}% | {s['dist_atr']:>6.2f}")


def compare_by_distance(ev: pd.DataFrame, nev: pd.DataFrame) -> None:
    """依「進場到 5 分 20MA 的距離（ATR）」分桶比較真實 vs 隨機基準，
    並把基準按真實資料的距離分布重新加權——目標越近本來就越容易到，
    不控制距離的話，兩邊距離分布不同會讓比較失真。"""
    if ev.empty or nev.empty:
        return
    bins = [0, 0.5, 1.0, 1.5, 2.0, 3.0, np.inf]
    labels = ["<0.5", "0.5-1", "1-1.5", "1.5-2", "2-3", ">3"]
    metrics = {
        "30分內": lambda d: (d["t_min"] <= 30).mean() * 100,
        "收盤前": lambda d: d["t_min"].notna().mean() * 100,
        "先回5MA(1ATR停損)": lambda d: (d["race_1"] == "目標").mean() * 100,
        "期望值(1ATR停損)": lambda d: d["R_1"].mean(),
    }
    fmt = lambda m, v, w: (f"{v:>+{w}.3f}" if m.startswith("期望值") else f"{v:>{w - 1}.1f}%")
    br = pd.cut(ev["dist_atr"], bins, labels=labels)
    bn = pd.cut(nev["dist_atr"], bins, labels=labels)
    print("\n── 依目標距離分桶：真實 vs 隨機基準（距離 = 進場價到 5 分 20MA，ATR 單位）──")
    print(f"{'距離':>6} | {'真實樣本':>6} | {'基準樣本':>6} | "
          + " | ".join(f"{m:>17}" for m in metrics))
    w_real = br.value_counts(normalize=True)
    weighted = {m: 0.0 for m in metrics}
    for lab in labels:
        r, nn = ev[br == lab], nev[bn == lab]
        cells = []
        for m, f in metrics.items():
            rv = f(r) if len(r) else np.nan
            nv = f(nn) if len(nn) else np.nan
            if len(nn):
                weighted[m] += w_real.get(lab, 0) * nv
            cells.append(f"{fmt(m, rv, 7)} vs {fmt(m, nv, 6)}")
        print(f"{lab:>6} | {len(r):>6} | {len(nn):>6} | " + " | ".join(f"{c:>17}" for c in cells))
    print(f"{'合計':>6} | {len(ev):>6} | {'加權':>6} | " + " | ".join(
        f"{fmt(m, f(ev), 7)} vs {fmt(m, weighted[m], 6)}" for m, f in metrics.items()))
    print("（左＝真實、右＝隨機基準；「合計」列的基準已按真實資料的距離分布加權；期望值單位 ATR）")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, help="1 分 K .parquet / .csv")
    ap.add_argument("--since", help="只統計此日期（美東）之後的事件，例如 2023-11-22")
    ap.add_argument("--until", help="只統計此日期（美東，含）之前的事件")
    ap.add_argument("--session", default="0930-1600", help="事件時段（美東），預設 0930-1600")
    ap.add_argument("--re-arm-min", type=int, default=30)
    ap.add_argument("--fill-ticks", type=int, default=0, help="成交需穿價幾 tick（預設 0 = 觸價即成交）")
    ap.add_argument("--slope", action="store_true", help="方向另要求 15 分與 1H 20MA 同向斜率")
    ap.add_argument("--null", action="store_true", help="另跑隨機漫步基準")
    ap.add_argument("--null-seeds", type=int, default=3, help="隨機基準的種子數（樣本合併）")
    ap.add_argument("--no-roll-adjust", action="store_true")
    ap.add_argument("--dump-events", help="輸出事件明細 CSV")
    args = ap.parse_args()

    stops = (0.5, 1.0, 1.5, 2.0)
    df = load_bars(args.data, roll_adjust=not args.no_roll_adjust)
    print(f"資料：{df.index[0].tz_convert(NY):%Y-%m-%d} ~ {df.index[-1].tz_convert(NY):%Y-%m-%d}，"
          f"{len(df):,} 根 1 分 K")

    def run(frame):
        ev = study(frame, re_arm_min=args.re_arm_min, stops=stops, session=args.session,
                   slope=args.slope, fill_ticks=args.fill_ticks)
        if ev.empty:
            return ev
        tt = ev["time"].dt.tz_convert(NY)
        if args.since:
            ev = ev[tt >= pd.Timestamp(args.since, tz=NY)]
            tt = ev["time"].dt.tz_convert(NY)
        if args.until:
            ev = ev[tt < pd.Timestamp(args.until, tz=NY) + pd.Timedelta(days=1)]
        return ev.reset_index(drop=True)

    cond = "1H＋15分同向" + ("＋MA同向斜率" if args.slope else "") + \
        f"、{args.session} 回到15分20MA limit進場"
    span = f"{args.since or '起'} ~ {args.until or '迄'}"
    ev = run(df)
    report(ev, stops, f"真實資料｜{cond}｜{span}")
    by_period(ev)
    if args.dump_events and not ev.empty:
        ev.to_csv(args.dump_events, index=False)
        print(f"\n事件明細：{args.dump_events}")
    if args.null:
        nev = pd.concat([run(make_null(df, seed)) for seed in range(args.null_seeds)],
                        ignore_index=True)
        report(nev, stops, f"隨機漫步基準 ×{args.null_seeds} 種子（同時間軸、同分時段波動率、"
                           f"無均線效果）｜{span}")
        compare_by_distance(ev, nev)


if __name__ == "__main__":
    main()
