"""Read-only Futubull (Futu / moomoo) market data MCP server.

Exposes Futu OpenAPI quote data as MCP tools so Claude Code can fetch
snapshots, historical / current candlesticks, order books and tick data
through natural language. No trading capability is included by design.

Requires a local Futu OpenD gateway running and logged in. See README.md.

Stock code format used everywhere: "<MARKET>.<SYMBOL>", e.g.
    US.AAPL   HK.00700   SH.600519   SZ.000001
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from futu import (
    OpenQuoteContext,
    RET_OK,
    KLType,
    SubType,
    AuType,
)

FUTU_HOST = os.environ.get("FUTU_HOST", "127.0.0.1")
FUTU_PORT = int(os.environ.get("FUTU_PORT", "11111"))

mcp = FastMCP("futu-data")

# A single long-lived connection to the OpenD gateway. Created lazily so the
# server process can start even if OpenD is not up yet (tools then report a
# clear error instead of crashing on import).
_quote_ctx: OpenQuoteContext | None = None
# Codes we have already subscribed to, per sub-type, to avoid redundant
# subscribe calls (subscriptions count against the OpenD quota).
_subscribed: set[tuple[str, str]] = set()


def _ctx() -> OpenQuoteContext:
    global _quote_ctx
    if _quote_ctx is None:
        _quote_ctx = OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
    return _quote_ctx


def _df(data: Any) -> list[dict]:
    """Normalize a Futu pandas DataFrame response into JSON-friendly records."""
    if hasattr(data, "to_dict"):
        return data.to_dict(orient="records")
    return data


def _ensure_sub(codes: list[str], sub_type: str) -> str | None:
    """Subscribe to `codes` for `sub_type` if not already done.

    Several real-time interfaces (current K-line, order book, ticker, quote)
    require a prior subscription. Returns an error string on failure, else None.
    """
    todo = [c for c in codes if (c, sub_type) not in _subscribed]
    if not todo:
        return None
    ret, err = _ctx().subscribe(todo, [sub_type], subscribe_push=False)
    if ret != RET_OK:
        return f"subscribe failed: {err}"
    for c in todo:
        _subscribed.add((c, sub_type))
    return None


_KLTYPE = {
    "1m": KLType.K_1M,
    "3m": KLType.K_3M,
    "5m": KLType.K_5M,
    "15m": KLType.K_15M,
    "30m": KLType.K_30M,
    "60m": KLType.K_60M,
    "day": KLType.K_DAY,
    "week": KLType.K_WEEK,
    "month": KLType.K_MON,
}


@mcp.tool()
def get_market_snapshot(codes: list[str]) -> dict:
    """Real-time market snapshot (price, change, volume, turnover, bid/ask, etc.).

    Does NOT require a subscription. Pass up to ~400 codes.
    Example codes: ["US.AAPL", "HK.00700"].
    """
    ret, data = _ctx().get_market_snapshot(codes)
    if ret != RET_OK:
        return {"error": str(data)}
    return {"snapshots": _df(data)}


@mcp.tool()
def get_history_kline(
    code: str,
    start: str | None = None,
    end: str | None = None,
    ktype: str = "day",
    max_count: int = 200,
) -> dict:
    """Historical candlesticks (does NOT require a subscription).

    Args:
        code: e.g. "US.AAPL".
        start / end: "YYYY-MM-DD" (optional; omit to use a recent window).
        ktype: one of 1m,3m,5m,15m,30m,60m,day,week,month.
        max_count: max bars to return (paged internally).
    """
    kl = _KLTYPE.get(ktype)
    if kl is None:
        return {"error": f"invalid ktype '{ktype}'. Use one of {list(_KLTYPE)}"}
    ret, data, _page_key = _ctx().request_history_kline(
        code,
        start=start,
        end=end,
        ktype=kl,
        autype=AuType.QFQ,
        max_count=max_count,
    )
    if ret != RET_OK:
        return {"error": str(data)}
    return {"code": code, "ktype": ktype, "klines": _df(data)}


@mcp.tool()
def get_cur_kline(code: str, num: int = 100, ktype: str = "day") -> dict:
    """Latest N real-time candlesticks (requires subscription, done automatically).

    Args:
        code: e.g. "US.AAPL".
        num: number of most recent bars (max 1000).
        ktype: one of 1m,3m,5m,15m,30m,60m,day,week,month.
    """
    kl = _KLTYPE.get(ktype)
    if kl is None:
        return {"error": f"invalid ktype '{ktype}'. Use one of {list(_KLTYPE)}"}
    sub = {
        "1m": SubType.K_1M, "3m": SubType.K_3M, "5m": SubType.K_5M,
        "15m": SubType.K_15M, "30m": SubType.K_30M, "60m": SubType.K_60M,
        "day": SubType.K_DAY, "week": SubType.K_WEEK, "month": SubType.K_MON,
    }[ktype]
    if (e := _ensure_sub([code], sub)):
        return {"error": e}
    ret, data = _ctx().get_cur_kline(code, num, kl, AuType.QFQ)
    if ret != RET_OK:
        return {"error": str(data)}
    return {"code": code, "ktype": ktype, "klines": _df(data)}


@mcp.tool()
def get_order_book(code: str, num: int = 10) -> dict:
    """Real-time order book / market depth (requires subscription, done automatically).

    Args:
        code: e.g. "US.AAPL".
        num: depth levels (1-40, market dependent).
    """
    if (e := _ensure_sub([code], SubType.ORDER_BOOK)):
        return {"error": e}
    ret, data = _ctx().get_order_book(code, num=num)
    if ret != RET_OK:
        return {"error": str(data)}
    return {"order_book": data}


@mcp.tool()
def get_rt_ticker(code: str, num: int = 100) -> dict:
    """Recent tick-by-tick trades (requires subscription, done automatically).

    Args:
        code: e.g. "US.AAPL".
        num: number of most recent ticks (max 1000).
    """
    if (e := _ensure_sub([code], SubType.TICKER)):
        return {"error": e}
    ret, data = _ctx().get_rt_ticker(code, num)
    if ret != RET_OK:
        return {"error": str(data)}
    return {"code": code, "ticks": _df(data)}


@mcp.tool()
def get_market_state(codes: list[str]) -> dict:
    """Current trading session state (e.g. open / pre-market / closed) for codes."""
    ret, data = _ctx().get_market_state(codes)
    if ret != RET_OK:
        return {"error": str(data)}
    return {"states": _df(data)}


def main() -> None:
    """Console-script / module entry point. Runs over stdio for MCP clients."""
    mcp.run()


if __name__ == "__main__":
    main()
