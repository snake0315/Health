#!/usr/bin/env python3
"""過去 N 年、每年 7 月前 K 個交易日的 SPY / QQQ 漲跌幅與波動度分析。

資料來源用的是本專案的 Futu MCP server（`get_history_kline`），所以執行前
必須先啟動並登入 OpenD 閘道（見 README）。本腳本不下單、只讀行情。

用法：
    # 先確認 OpenD 已啟動並登入
    python analysis/july_first5.py                 # 預設：SPY/QQQ、過去 10 年、每年前 5 個交易日
    python analysis/july_first5.py --years 10 --days 5 --symbols US.SPY US.QQQ
    python analysis/july_first5.py --csv out.csv   # 另存明細 CSV

輸出欄位說明：
    daily_chg%   收盤對前一日收盤的漲跌幅（= Futu 的 change_rate，「漲跌幅」）
    range%       當日 (最高-最低)/前收，日內波動度（「波動度」代理指標）
    每年 / 全期彙總會給出：平均漲跌幅、漲跌幅標準差（波動度）、平均日內振幅。
"""

from __future__ import annotations

import argparse
import csv as csvmod
import datetime as dt
import os
import statistics
import sys
from typing import Any

# 讓腳本從任何目錄執行都能找到專案根目錄的 server.py。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 直接重用本專案 MCP server 的唯讀行情工具。
from server import get_history_kline  # noqa: E402


def _to_float(v: Any) -> float | None:
    try:
        if v in (None, ""):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _fetch_july_rows(code: str, year: int, days: int) -> list[dict]:
    """回傳某年 7 月「前 days 個交易日」的 K 線紀錄（已排序、已補齊漲跌幅）。"""
    # 抓 6/20 ~ 7/20，前面留幾天緩衝好算出 7 月第一天的漲跌幅（需要前一交易日收盤）。
    start = f"{year}-06-20"
    end = f"{year}-07-20"
    res = get_history_kline(code, start=start, end=end, ktype="day", max_count=200)
    if "error" in res:
        raise RuntimeError(f"{code} {year}: {res['error']}")

    rows = sorted(res.get("klines", []), key=lambda r: str(r.get("time_key", "")))

    out: list[dict] = []
    prev_close: float | None = None
    for r in rows:
        tk = str(r.get("time_key", ""))[:10]
        try:
            d = dt.date.fromisoformat(tk)
        except ValueError:
            continue

        close = _to_float(r.get("close"))
        high = _to_float(r.get("high"))
        low = _to_float(r.get("low"))
        open_ = _to_float(r.get("open"))
        # Futu 歷史 K 線通常自帶 change_rate / last_close；缺了就用前一交易日補算。
        chg = _to_float(r.get("change_rate"))
        last_close = _to_float(r.get("last_close")) or prev_close
        if chg is None and last_close and close is not None:
            chg = (close - last_close) / last_close * 100.0

        if d.month == 7:
            range_pct = None
            if high is not None and low is not None and last_close:
                range_pct = (high - low) / last_close * 100.0
            out.append(
                {
                    "date": tk,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "daily_chg_pct": chg,
                    "range_pct": range_pct,
                }
            )
            if len(out) >= days:
                break
        prev_close = close if close is not None else prev_close

    return out


def _fmt(v: float | None, nd: int = 2) -> str:
    return "" if v is None else f"{v:.{nd}f}"


def _stats(vals: list[float]) -> tuple[float | None, float | None, float | None]:
    vals = [v for v in vals if v is not None]
    if not vals:
        return None, None, None
    mean = statistics.fmean(vals)
    std = statistics.pstdev(vals) if len(vals) > 1 else 0.0
    return mean, std, statistics.fmean([abs(v) for v in vals])


def analyse(symbols: list[str], years: list[int], days: int) -> list[dict]:
    all_rows: list[dict] = []
    for code in symbols:
        print(f"\n===== {code} — 每年 7 月前 {days} 個交易日 =====")
        chg_all: list[float] = []
        for year in years:
            try:
                rows = _fetch_july_rows(code, year, days)
            except RuntimeError as e:
                print(f"  {year}: 取得失敗 -> {e}")
                continue
            if not rows:
                print(f"  {year}: 無資料")
                continue

            print(f"  {year}")
            print(f"    {'date':<12}{'close':>10}{'daily_chg%':>12}{'range%':>10}")
            for r in rows:
                print(
                    f"    {r['date']:<12}{_fmt(r['close']):>10}"
                    f"{_fmt(r['daily_chg_pct']):>12}{_fmt(r['range_pct']):>10}"
                )
                r2 = dict(r, symbol=code, year=year)
                all_rows.append(r2)
            chgs = [r["daily_chg_pct"] for r in rows]
            m, s, a = _stats(chgs)
            cum = sum(c for c in chgs if c is not None)
            print(
                f"    -> 平均漲跌 {_fmt(m)}% | 波動度(std) {_fmt(s)}% | "
                f"累計 {_fmt(cum)}% | 平均振幅 "
                f"{_fmt(_stats([r['range_pct'] for r in rows])[0])}%"
            )
            chg_all.extend(c for c in chgs if c is not None)

        m, s, _ = _stats(chg_all)
        print(
            f"  === {code} 全期({years[0]}-{years[-1]}) 合計 {len(chg_all)} 個交易日："
            f"平均漲跌 {_fmt(m)}% | 波動度(std) {_fmt(s)}% ==="
        )
    return all_rows


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+", default=["US.SPY", "US.QQQ"])
    p.add_argument("--years", type=int, default=10, help="往回幾年（不含尚未完整的當年）")
    p.add_argument("--days", type=int, default=5, help="每年 7 月取前幾個交易日")
    p.add_argument("--end-year", type=int, default=None, help="最後一個分析年份（預設=去年）")
    p.add_argument("--csv", default=None, help="把明細另存為 CSV 檔")
    args = p.parse_args()

    end_year = args.end_year or (dt.date.today().year - 1)
    years = list(range(end_year - args.years + 1, end_year + 1))

    rows = analyse(args.symbols, years, args.days)

    if args.csv and rows:
        cols = ["symbol", "year", "date", "open", "high", "low", "close",
                "daily_chg_pct", "range_pct"]
        with open(args.csv, "w", newline="") as f:
            w = csvmod.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k) for k in cols})
        print(f"\n已輸出明細：{args.csv}（{len(rows)} 列）")


if __name__ == "__main__":
    main()
