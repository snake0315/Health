#!/usr/bin/env python3
"""隨機基準線模擬：無結構行情下「回踩 5 分 20MA」統計的期望值。

重要：這不是真實 NQ 統計。它回答的是——若價格只是帶漂移的隨機漫步
（NQ 等級的波動率、期貨時段結構、無任何均線支撐效果），同一套事件定義
會量出什麼樣的「跌破機率」與「未來 6 根平均最大漲幅」。

用途：當你用真實資料（TradingView / Futu / CSV）跑出數字後，與本基準比較：
  • 真實跌破機率「低於」基準 → 5 分 20MA 存在實際支撐效果；
  • 真實平均最大漲幅「高於」同趨勢情境的基準 → 回踩後的反彈有超額表現。
兩者若與基準相近，代表該統計大多只反映趨勢與波動率本身，而非均線的魔力。

模擬設定（兩年、美東時段結構）：
  • 5 分 K 時間軸：週日 18:00 開盤～週五 17:00 收盤、每日 17:00–18:00 休市。
  • 波動率：正常盤 5 分 σ=0.12%、盤後 σ=0.05%（近似 NQ 年化 ~20% 的量級）。
  • 報酬分布：t(4) 厚尾。
  • 趨勢情境：年化 -20% / 0% / +30% 三組，各跑多個隨機種子取平均。

執行：python backtest/nq_null_baseline.py [--seeds 5]
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from nq_ma_pullback_backtest import backtest


def make_index(start: str, end: str) -> pd.DatetimeIndex:
    """CME 期貨時段的 5 分 K 起始時間（美東）。"""
    idx = pd.date_range(start, end, freq="5min", tz="America/New_York")
    wd, hr = idx.weekday, idx.hour
    keep = ((wd <= 3) | ((wd == 4) & (hr < 17)) | ((wd == 6) & (hr >= 18))) & (hr != 17)
    return idx[keep]


def simulate(idx: pd.DatetimeIndex, annual_drift: float, seed: int,
             sigma_rth: float = 0.0012, sigma_on: float = 0.0005) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(idx)
    hhmm = idx.hour * 100 + idx.minute
    rth = (hhmm >= 930) & (hhmm < 1600)
    sigma = np.where(rth, sigma_rth, sigma_on)

    bars_per_year = n / ((idx[-1] - idx[0]).days / 365.25)
    mu = annual_drift / bars_per_year

    r = rng.standard_t(4, n) / np.sqrt(2.0) * sigma + mu  # t(4) 正規化為單位變異
    close = 20000.0 * np.exp(np.cumsum(r))
    open_ = np.empty(n)
    open_[0] = 20000.0
    open_[1:] = close[:-1]
    wick = rng.uniform(0, 0.6, (2, n)) * sigma * close
    return pd.DataFrame({
        "open": open_,
        "high": np.maximum(open_, close) + wick[0],
        "low": np.minimum(open_, close) - wick[1],
        "close": close,
    }, index=idx)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--start", default="2024-07-28")
    ap.add_argument("--end", default="2026-07-28")
    args = ap.parse_args()

    idx = make_index(args.start, args.end)
    print(f"模擬範圍：{args.start} ~ {args.end}，{len(idx)} 根 5 分 K/情境，"
          f"{args.seeds} 個種子\n")
    print("＊＊隨機基準線（非真實 NQ 統計）＊＊　條件：正常盤 0930-1600 回踩")
    hdr = f"{'年化趨勢':>8} | {'樣本/2年':>8} | {'跌破機率':>8} | {'平均最大漲幅':>12} | {'中位數':>8}"
    print(hdr)
    print("-" * len(hdr))
    for drift in (-0.20, 0.0, 0.30):
        rows = []
        for seed in range(args.seeds):
            df = simulate(idx, drift, seed)
            r = backtest(df, session="0930-1600")
            if r["n"]:
                rows.append((r["n"], r["p_break"], r["avg_gain_pct"], r["med_gain_pct"]))
        a = np.array(rows)
        m, s = a.mean(axis=0), a.std(axis=0)
        print(f"{drift:>+7.0%} | {m[0]:>8.0f} | {m[1]:>6.1f}%±{s[1]:.1f} | "
              f"{m[2]:>8.3f}%±{s[2]:.3f} | {m[3]:>7.3f}%")
    print("\n解讀：真實資料的跌破機率低於同趨勢基準 → 均線有實際支撐效果；")
    print("平均最大漲幅高於基準 → 回踩後反彈有超額表現。接近基準則多為趨勢/波動本身。")


if __name__ == "__main__":
    main()
