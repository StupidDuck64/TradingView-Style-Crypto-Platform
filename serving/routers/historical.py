import asyncio
import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from serving.connections import get_trino_connection

router = APIRouter(prefix="/api", tags=["historical"])

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,20}$")


def _validate(symbol: str) -> str:
    s = symbol.strip().upper()
    if not _SYMBOL_RE.match(s):
        raise HTTPException(400, "Invalid symbol")
    return s


def _query_trino_hourly(
    symbol: str, start_ms: int, end_ms: int, limit: int,
) -> list[dict]:
    """Query coin_klines_hourly via Trino for a date range."""
    conn = get_trino_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                kline_start AS open_time,
                open, high, low, close, volume
            FROM crypto_lakehouse.coin_klines_hourly
            WHERE symbol = ?
              AND kline_start >= ?
              AND kline_start < ?
            ORDER BY kline_start
            LIMIT ?
            """,
            (symbol, start_ms, end_ms, limit),
        )
        rows = cur.fetchall()
        return [
            {
                "openTime": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            }
            for r in rows
        ]
    finally:
        conn.close()


def _query_trino_historical(
    symbol: str, start_ms: int, end_ms: int, limit: int,
) -> list[dict]:
    """Fallback: query the historical_hourly table (backfilled from Binance)."""
    conn = get_trino_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                open_time,
                open, high, low, close, volume
            FROM crypto_lakehouse.historical_hourly
            WHERE symbol = ?
              AND open_time >= ?
              AND open_time < ?
            ORDER BY open_time
            LIMIT ?
            """,
            (symbol, start_ms, end_ms, limit),
        )
        rows = cur.fetchall()
        return [
            {
                "openTime": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            }
            for r in rows
        ]
    finally:
        conn.close()


@router.get("/klines/historical")
async def get_historical_klines(
    symbol: str,
    startTime: int = Query(..., description="Range start in epoch milliseconds"),
    endTime: int = Query(..., description="Range end in epoch milliseconds"),
    limit: int = Query(500, ge=1, le=5000),
):
    """
    Query Iceberg cold storage via Trino for historical OHLCV candles
    within a specific date range.  Returns hourly candles.

    Response shape matches ``/api/klines``:
    ``[{"openTime": ms, "open": float, "high": float, "low": float, "close": float, "volume": float}]``
    """
    symbol = _validate(symbol)

    if endTime <= startTime:
        raise HTTPException(400, "endTime must be greater than startTime")

    # Cap range to 1 year
    max_range_ms = 365 * 24 * 3600 * 1000
    if endTime - startTime > max_range_ms:
        raise HTTPException(400, "Date range cannot exceed 1 year")

    # Try coin_klines_hourly first (Flink-aggregated)
    candles = []
    try:
        candles = await asyncio.to_thread(
            _query_trino_hourly, symbol, startTime, endTime, limit,
        )
    except Exception:
        pass

    # Fallback to historical_hourly (Binance backfill)
    if not candles:
        candles = await asyncio.to_thread(
            _query_trino_historical, symbol, startTime, endTime, limit,
        )

    return candles
