#!/usr/bin/env python3
"""過去 N 年、每年 7 月前 K 個交易日的 SPY / QQQ 漲跌幅與波動度分析。

兩種資料來源（擇一）：

1. Futu（預設）：透過本專案 MCP server 的 `get_history_kline` 取歷史日 K，
   執行前必須先啟動並登入 OpenD 閘道（見 README）。本腳本不下單、只讀行情。
2. 離線 CSV（--from-csv）：不需要 OpenD 與 futu-api。先自行下載日 K CSV
   （Yahoo Finance 的 Historical Data 匯出，或 Stooq：
   https://stooq.com/q/d/l/?s=spy.us&i=d ），再餵給本腳本。

用法：
    # Futu 模式（先確認 OpenD 已啟動並登入）
    python analysis/july_first5.py                 # 預設：SPY/QQQ、過去 10 年、每年前 5 個交易日
    python analysis/july_first5.py --years 10 --days 5 --symbols US.SPY US.QQQ

    # 離線 CSV 模式（symbol=檔案路徑，可多組）
    python analysis/july_first5.py --from-csv SPY=spy_us_d.csv QQQ=qqq_us_d.csv

    # 另存明細
    python analysis/july_first5.py --csv out.csv

輸出欄位說明：
    daily_chg%   收盤對前一日收盤的漲跌幅（「漲跌幅」）
    range%       當日 (最高-最低)/前收，日內波動度（「波動度」代理指標）
    每年 / 全期彙總會給出：平均漲跌幅、漲跌幅標準差（波動度）、累計與平均日內振幅。
"""

from __future__ import annotations

import argparse
import csv as csvmod
import datetime as dt
import os
import statistics
import sys
from typing import Any

# 讓腳本從任何目錄執行都能找到專案根目錄的 server.py（Futu 模式才需要）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _to_float(v: Any) -> float | None:
    try:
        if v in (None, ""):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _first_july_days(rows: list[dict], days: int) -> list[dict]:
    """從按日期排序的日 K 紀錄取出 7 月前 `days` 個交易日並補齊漲跌幅/振幅。

    每筆 row 需含 date(YYYY-MM-DD)、open/high/low/close，
    可選 change_rate / last_close（Futu 自帶時優先採用）。
    """
    out: list[dict] = []
    prev_close: float | None = None
    for r in rows:
        tk = str(r.get("date", ""))[:10]
        try:
            d = dt.date.fromisoformat(tk)
        except ValueError:
            continue

        close = _to_float(r.get("close"))
        high = _to_float(r.get("high"))
        low = _to_float(r.get("low"))
        open_ = _to_float(r.get("open"))
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


# ---------------------------------------------------------------------------
# 資料來源一：Futu（get_history_kline，需 OpenD）
# ---------------------------------------------------------------------------

def _fetch_july_rows_futu(code: str, year: int, days: int) -> list[dict]:
    from server import get_history_kline  # 延後匯入：CSV 模式不需要 futu-api

    # 抓 6/20 ~ 7/20，前面留幾天緩衝好算出 7 月第一天的漲跌幅（需要前一交易日收盤）。
    res = get_history_kline(
        code, start=f"{year}-06-20", end=f"{year}-07-20", ktype="day", max_count=200
    )
    if "error" in res:
        raise RuntimeError(f"{code} {year}: {res['error']}")

    rows = sorted(res.get("klines", []), key=lambda r: str(r.get("time_key", "")))
    for r in rows:
        r["date"] = str(r.get("time_key", ""))[:10]
    return _first_july_days(rows, days)


# ---------------------------------------------------------------------------
# 資料來源二：離線 CSV（Yahoo「Historical Data」匯出或 Stooq 日 K 皆可）
# ---------------------------------------------------------------------------

def load_csv_series(path: str) -> list[dict]:
    """讀日 K CSV，回傳按日期排序的 [{date, open, high, low, close}, ...]。

    欄位名不分大小寫，需含 Date/Open/High/Low/Close（Yahoo、Stooq 格式皆符合）。
    """
    with open(path, newline="") as f:
        reader = csvmod.DictReader(f)
        if not reader.fieldnames:
            raise RuntimeError(f"{path}: 空檔案或無標題列")
        cols = {c.lower().strip(): c for c in reader.fieldnames}
        missing = [c for c in ("date", "open", "high", "low", "close") if c not in cols]
        if missing:
            raise RuntimeError(f"{path}: 缺少欄位 {missing}（實際欄位：{reader.fieldnames}）")
        rows = [
            {
                "date": str(r[cols["date"]]).strip()[:10],
                "open": r[cols["open"]],
                "high": r[cols["high"]],
                "low": r[cols["low"]],
                "close": r[cols["close"]],
            }
            for r in reader
        ]
    rows.sort(key=lambda r: r["date"])
    return rows


def _fetch_july_rows_csv(series: list[dict], year: int, days: int) -> list[dict]:
    lo, hi = f"{year}-06-15", f"{year}-07-20"
    window = [r for r in series if lo <= r["date"] <= hi]
    return _first_july_days(window, days)


# ---------------------------------------------------------------------------
# 分析與輸出
# ---------------------------------------------------------------------------

def _fmt(v: float | None, nd: int = 2) -> str:
    return "" if v is None else f"{v:.{nd}f}"


def _stats(vals: list[float]) -> tuple[float | None, float | None, float | None]:
    vals = [v for v in vals if v is not None]
    if not vals:
        return None, None, None
    mean = statistics.fmean(vals)
    std = statistics.pstdev(vals) if len(vals) > 1 else 0.0
    return mean, std, statistics.fmean([abs(v) for v in vals])


def analyse(
    symbols: list[str],
    years: list[int],
    days: int,
    csv_series: dict[str, list[dict]] | None = None,
) -> list[dict]:
    all_rows: list[dict] = []
    for code in symbols:
        print(f"\n===== {code} — 每年 7 月前 {days} 個交易日 =====")
        chg_all: list[float] = []
        for year in years:
            try:
                if csv_series is not None:
                    rows = _fetch_july_rows_csv(csv_series[code], year, days)
                else:
                    rows = _fetch_july_rows_futu(code, year, days)
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
                all_rows.append(dict(r, symbol=code, year=year))
            chgs = [r["daily_chg_pct"] for r in rows]
            m, s, _ = _stats(chgs)
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
    p.add_argument(
        "--from-csv",
        nargs="+",
        metavar="SYM=PATH",
        default=None,
        help="離線模式：symbol=日K CSV 路徑（Yahoo/Stooq 格式），可多組；"
        "指定後不連 Futu，--symbols 以這裡的 symbol 為準",
    )
    args = p.parse_args()

    end_year = args.end_year or (dt.date.today().year - 1)
    years = list(range(end_year - args.years + 1, end_year + 1))

    csv_series: dict[str, list[dict]] | None = None
    symbols = args.symbols
    if args.from_csv:
        csv_series = {}
        for spec in args.from_csv:
            sym, sep, path = spec.partition("=")
            if not sep or not sym or not path:
                p.error(f"--from-csv 需要 SYM=PATH 格式，收到：{spec!r}")
            csv_series[sym] = load_csv_series(path)
        symbols = list(csv_series)

    rows = analyse(symbols, years, args.days, csv_series=csv_series)

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
